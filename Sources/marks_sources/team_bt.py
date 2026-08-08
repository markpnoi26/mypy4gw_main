"""Leader-side building blocks for team BT bots driven through HeroAI.

Extracted from the Voltaic Spear team bot so the SoO bot (and the next dungeon
after it) does not carry its own copy. Everything here assumes the same shape:
the leader runs the route, HeroAI's fight zone drives everyone else, and
anything only one account may do at a time goes through `team_turns`.
"""

import time

import PyImGui
import PySystem

from Core import GLOBAL_CACHE
from Core import Map
from Core import Player
from Core import Routines
from Core import SharedCommandType
from Core.Agent import Agent
from Core.AgentArray import AgentArray
from Core.enums_src.GameData_enums import Allegiance
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Core.routines_src.BehaviourTrees import BT
from Sources.marks_sources import fight_awareness
from Sources.marks_sources import gadget_interact
from Sources.marks_sources import team_turns

# Movement nodes read ONE pause key, and the HeroAI branch owns PAUSE_MOVEMENT.
FIGHT_HOLD_KEY = "FIGHT_HOLD"
# Player.Move goes through the ACTION queue the party's skills are also using.
REPOSITION_INTERVAL_MS = 1500.0
CHEST_TURNS_KEY = "chest_turns"
# Generous on purpose: expiry here means "could not confirm", and every wait that
# uses it is wrapped so it cannot decide whether the step counted.
TEAM_ACK_TIMEOUT_MS = 90_000

# The Move node budgets wall-clock time per waypoint, but a BottingTree-level
# pause (knockdown, danger) freezes the planner WITHOUT crediting that pause
# back — measured live: a 2m45s knockdown pause expired a 15s budget and the
# resulting FAILURE stopped the whole run. Retrying re-paths from wherever the
# leader stands, which is also the right answer for a plain stall. The outer
# budget exists only so a truly stuck leader eventually surfaces as a FAILURE.
WALK_RETRY_TIMEOUT_MS = 600_000

NPC_TARGET_AFTERCAST_MS = 250

TURN_CFG = team_turns.TurnConfig()


def fight_gate(hold_key: str = FIGHT_HOLD_KEY) -> BehaviorTree:
    """Hold the route while the party is fighting; follow it back when it withdraws.

    Writes its own pause key rather than PAUSE_MOVEMENT because services tick
    AFTER the planner, and the HeroAI branch rewrites PAUSE_MOVEMENT at the top of
    every tick — before the planner reads it. A service write to that key is
    clobbered before anything can act on it, so the hold key carries it forward
    instead, one frame behind.
    """
    state = {"last_move_ms": 0.0}

    def tick_gate(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        snapshot = fight_awareness.read()
        stance = fight_awareness.stance(snapshot)
        blackboard = node.blackboard
        blackboard["FIGHT_STANCE"] = stance.name
        blackboard["FIGHT_REASON"] = fight_awareness.describe(snapshot)
        blackboard[hold_key] = stance is not fight_awareness.Stance.CLEAR or bool(
            blackboard.get("PAUSE_MOVEMENT", False)
        )

        target = fight_awareness.reposition_target(snapshot, Player.GetXY())
        if target is None:
            return BehaviorTree.NodeState.RUNNING

        # Re-issuing a move to the same point is idempotent, so this wants rate
        # limiting rather than a latch on an observed change.
        now = time.monotonic() * 1000.0
        if now - float(state["last_move_ms"]) < REPOSITION_INTERVAL_MS:
            return BehaviorTree.NodeState.RUNNING
        Player.Move(target[0], target[1])
        state["last_move_ms"] = now
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(name="FightGate", action_fn=tick_gate, aftercast_ms=0))


def optional(subtree: BehaviorTree, name: str, source: str) -> BehaviorTree:
    """Let a step expire or miss without dropping the rung.

    A FAILURE anywhere in the planner Sequence stops the whole bot — the planner
    sets `started = False` on it. That is the right answer for a wrong map and
    the wrong one for a wait that merely could not confirm what it was watching
    for. The Selector still pins on RUNNING, so this shortens nothing.

    The fallback LOGS. A wait that quietly gave up is the hardest kind of run to
    read afterwards, because the bot carries on looking healthy.
    """

    def carry_on(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        PySystem.Console.Log(
            source,
            f"{name} did not complete - continuing anyway.",
            PySystem.Console.MessageType.Warning,
        )
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(
        BehaviorTree.SelectorNode(
            name=name,
            children=[
                subtree,
                BehaviorTree.ActionNode(
                    name=f"{name}: continue anyway",
                    action_fn=carry_on,
                    aftercast_ms=0,
                ),
            ],
        )
    )


def walk(x: float, y: float, pause_key: str = FIGHT_HOLD_KEY, tolerance: float = 50.0) -> BehaviorTree:
    """`tolerance` matters when the destination is an NPC's own coordinates:
    its collision circle keeps the mover outside the default 50, so walks that
    end on an agent pass Range.Touch here."""
    return BehaviorTree(
        BehaviorTree.RepeaterUntilSuccessNode(
            child=BT.Movement.Move(x=x, y=y, tolerance=tolerance, pause_flag_key=pause_key),
            timeout_ms=WALK_RETRY_TIMEOUT_MS,
            name=f"Walk({x:.0f}, {y:.0f})",
        )
    )


def walk_path(points: list[tuple[float, float]], label: str, pause_key: str = FIGHT_HOLD_KEY) -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=label,
            children=[walk(x, y, pause_key) for x, y in points],
        )
    )


def walk_and_exit(
    x: float, y: float, target_map_id: int, label: str, pause_key: str = FIGHT_HOLD_KEY, timeout_ms: int = 60_000
) -> BehaviorTree:
    """Composed here rather than using MoveAndExitMap, which offers no pause key."""
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=label,
            children=[
                walk(x, y, pause_key),
                BT.Map.WaitforMapLoad(map_id=target_map_id, log=True, timeout=timeout_ms),
            ],
        )
    )


def chest_target_key(chest: str) -> str:
    return f"chest_target_id:{chest}"


def chest_started_key(chest: str) -> str:
    return f"chest_turns_started:{chest}"


def follower_emails(sender_email: str) -> list[str]:
    return [
        email
        for email in (
            str(getattr(account, "AccountEmail", "") or "") for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []
        )
        if email and email != sender_email
    ]


def account_is_busy(email: str) -> bool:
    """Any active message for that account — the order itself, and the loot pass
    HeroAI queues behind it. Waiting on the interact alone releases the chest
    while the account is still picking up."""
    index, message = GLOBAL_CACHE.ShMem.PreviewNextMessage(email)
    return index != -1 and message is not None


def team_opens_chest_in_turn(chest: str, source: str, cfg: team_turns.TurnConfig = TURN_CFG) -> BehaviorTree:
    """One account at a time. The chest is a mutex — a second account interacting
    while the first still has it open gets nothing, and reports success anyway.

    Keyed by chest name so a run with more than one of them cannot have the
    second step read the first one's target or its already-started flag.

    The receiver already snapshots and disables HeroAI options around its walk
    and interact, then restores them — see Widgets/Panels/Messaging.py. Do not
    add a combat toggle here; it would fight that snapshot.
    """
    state = team_turns.TurnState()

    def tick_turns(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        target = int(node.blackboard.get(chest_target_key(chest), 0) or 0)
        sender_email = str(Player.GetAccountEmail() or "")
        if not target or not sender_email:
            return BehaviorTree.NodeState.FAILURE

        started_key = chest_started_key(chest)
        if not node.blackboard.get(started_key):
            team_turns.begin(state, follower_emails(sender_email))
            node.blackboard[started_key] = True

        busy = account_is_busy(state.current) if state.current else False
        verdict = team_turns.next_turn(state, cfg, int(time.monotonic() * 1000.0), busy)
        node.blackboard[CHEST_TURNS_KEY] = f"{chest}: {team_turns.summary(state)}"

        if verdict is team_turns.Turn.START:
            GLOBAL_CACHE.ShMem.SendMessage(
                sender_email,
                state.current,
                SharedCommandType.InteractWithTarget,
                (float(target), 0.0, 0.0, 0.0),
            )
            PySystem.Console.Log(
                source,
                f"{chest} turn: {state.current} ({team_turns.remaining(state)} left).",
                PySystem.Console.MessageType.Info,
            )
            return BehaviorTree.NodeState.RUNNING

        if verdict is team_turns.Turn.FINISHED:
            node.blackboard[started_key] = False
            PySystem.Console.Log(
                source,
                f"{chest} turns complete: {team_turns.summary(state)}.",
                PySystem.Console.MessageType.Success,
            )
            return BehaviorTree.NodeState.SUCCESS

        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(
        BehaviorTree.ActionNode(name=f"TeamOpensChestInTurn({chest})", action_fn=tick_turns, aftercast_ms=0)
    )


NPC_SCAN_POLL_MS = 500
# The agent array only holds what the client is currently aware of. Right after
# a map load, a step jump, or a long walk, the NPC joins it a beat later than
# the step that wants it — a one-tick scan reads that as "nobody there" and
# skips a quest turn-in. Measured live: Shandra unfound at her own confirmed
# coordinates one second after tree start. The community bot retried for the
# same reason. Expiry means "could not see the NPC", and callers wrap this.
NPC_SCAN_TIMEOUT_MS = 10_000


def target_npc(
    x: float,
    y: float,
    key: str,
    radius: float = 300.0,
    source: str = "team_bt",
    timeout_ms: int = NPC_SCAN_TIMEOUT_MS,
    wanted_enc: str = "",
) -> BehaviorTree:
    """Target the nearest NPC to a point and remember which one that was.

    The id goes on the blackboard under `key` rather than being read back from
    `Player.GetTargetID()` later, because HeroAI is free to retarget the moment
    combat options come back on. The scan keeps looking until the NPC shows up
    or the window expires.

    `wanted_enc` is the agent's encoded name in the runtime format of
    `Agent.GetEncNameStrByID(..., literal=True)` — readable synchronously from
    agent memory, unlike display names, which need a request round trip. With
    it set, identity does the matching and the radius is only a search area —
    the chest lesson: a decoy cannot win the scan, so tight buys nothing.
    """

    def enc_matches(agent_id: int) -> bool:
        # Case-insensitive: the encoded hex is not case-normalised (the
        # framework's own docstring example is mixed-case `\x171C\x8fE8`), so a
        # bare `==` against an uppercase capture can silently never match.
        return Agent.GetEncNameStrByID(agent_id, literal=True).lower() == wanted_enc.lower()

    def choose(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        agent_id = 0
        if wanted_enc:
            # Identity, not classification: scan the FULL agent array, because
            # quest NPCs are not always in GetNPCMinipetArray (measured live —
            # Shandra unfound there while standing on her). This is the shape of
            # the framework's own Agent.GetAgentIDByEncString.
            for candidate in AgentArray.GetAgentArray() or []:
                candidate = int(candidate)
                if Agent.IsValid(candidate) and enc_matches(candidate):
                    agent_id = candidate
                    break
        else:
            # Full array, NOT GetNPCMinipetArray: blessing NPCs are not in it
            # either (measured live — the L1 blessing giver, agent 13, owner 0,
            # sat unfound there while the dump saw it 50u away). The nearest
            # agent that is a real NPC (IsNPC: login number 0, which the party
            # players fail) and unowned (owner 0, which their minipets fail) is
            # the one the bot walked to talk to.
            agents = AgentArray.Filter.ByDistance(AgentArray.GetAgentArray(), (x, y), radius)
            agents = AgentArray.Sort.ByDistance(agents, (x, y))
            for candidate in agents or []:
                candidate = int(candidate)
                if not Agent.IsValid(candidate):
                    continue
                if not Agent.IsNPC(candidate):  # players carry a login number
                    continue
                if int(Agent.GetOwnerID(candidate) or 0) != 0:  # minipets, pets, minions
                    continue
                # IsNPC + unowned still includes ENEMIES (login 0, owner 0) — a
                # shrine near a mob would otherwise target the mob. A blessing or
                # quest giver is Ally or Neutral, never Enemy.
                if int(Agent.GetAllegiance(candidate)[0]) == int(Allegiance.Enemy):
                    continue
                agent_id = candidate
                break
        node.blackboard[key] = agent_id
        if not agent_id:
            return BehaviorTree.NodeState.RUNNING
        Player.ChangeTarget(agent_id)
        return BehaviorTree.NodeState.SUCCESS

    def report(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        wanted = f" matching enc '{wanted_enc}'" if wanted_enc else ""
        PySystem.Console.Log(
            source,
            f"TargetNPC({key}): no agent{wanted} within {radius:.0f} of ({x:.0f}, {y:.0f}) "
            f"after {timeout_ms / 1000:.0f}s. Nearest agents:",
            PySystem.Console.MessageType.Warning,
        )
        # Dump what IS standing here so a wrong enc/coords shows its own true
        # values instead of failing blind.
        nearby = AgentArray.Sort.ByDistance(AgentArray.GetAgentArray() or [], (x, y))
        for candidate in list(nearby)[:6]:
            candidate = int(candidate)
            cx, cy = Agent.GetXY(candidate)
            PySystem.Console.Log(
                source,
                f"  agent {candidate} at ({cx:.0f}, {cy:.0f}) owner={Agent.GetOwnerID(candidate)} "
                f"enc='{Agent.GetEncNameStrByID(candidate, literal=True)}'",
                PySystem.Console.MessageType.Info,
            )
        return BehaviorTree.NodeState.FAILURE

    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=f"TargetNPC({key})",
            children=[
                BehaviorTree.SelectorNode(
                    name=f"TargetNPC({key}): find",
                    children=[
                        BehaviorTree.WaitUntilNode(
                            condition_fn=choose,
                            throttle_interval_ms=NPC_SCAN_POLL_MS,
                            timeout_ms=timeout_ms,
                            name=f"TargetNPC({key}): wait",
                        ),
                        BehaviorTree.ActionNode(name=f"TargetNPC({key}): report", action_fn=report, aftercast_ms=0),
                    ],
                ),
                BT.Player.Wait(NPC_TARGET_AFTERCAST_MS),
            ],
        )
    )


def target_npc_by_name(name_fragment: str, key: str, radius: float = 2500.0, source: str = "team_bt") -> BehaviorTree:
    def choose(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        origin = Player.GetXY()
        npcs = AgentArray.Filter.ByDistance(AgentArray.GetNPCMinipetArray(), origin, radius)
        npcs = AgentArray.Sort.ByDistance(npcs, origin)
        wanted = name_fragment.lower()
        agent_id = 0
        for npc_id in npcs or []:
            try:
                npc_name = Agent.GetNameByID(int(npc_id))
            except Exception:
                continue
            if npc_name and wanted in npc_name.lower():
                agent_id = int(npc_id)
                break
        node.blackboard[key] = agent_id
        if not agent_id:
            PySystem.Console.Log(
                source,
                f"TargetNPCByName({key}): no '{name_fragment}' within {radius:.0f}.",
                PySystem.Console.MessageType.Warning,
            )
            return BehaviorTree.NodeState.FAILURE
        Player.ChangeTarget(agent_id)
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(
        BehaviorTree.ActionNode(
            name=f"TargetNPCByName({key})", action_fn=choose, aftercast_ms=NPC_TARGET_AFTERCAST_MS
        )
    )


def leader_takes_dialog(key: str, dialog_id: int, source: str = "team_bt") -> BehaviorTree:
    """Interact with the remembered agent, then send the dialog — the same
    shape the followers' SendDialogToTarget handler runs: reach the agent
    first, dialog second.

    The interact is re-issued until the gap to the agent has observably closed
    (`interact_until_in_range` is agent-generic), because a dialog sent on a
    fixed delay races the walk — the leader standing far away fires it before
    any dialog window exists, and nothing happens.
    """

    def send_dialog(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        Player.SendDialog(dialog_id)
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=f"LeaderTakesDialog({key})",
            children=[
                gadget_interact.interact_until_in_range(key),
                # The dialog window trails arrival by a server round trip.
                BT.Player.Wait(1_000),
                BehaviorTree.ActionNode(
                    name=f"SendDialog({hex(dialog_id)})", action_fn=send_dialog, aftercast_ms=1000
                ),
            ],
        )
    )


def broadcast_dialog_to_team(
    key: str, dialog_id: int, source: str = "team_bt", refs_key: str = "shared_refs"
) -> BehaviorTree:
    """Order every follower to take a dialog from the remembered agent.

    Dialogs are not a mutex, so this goes out to everyone at once. Refs land on
    the blackboard in the shape `BT.Shared.WaitCommandDispatch` reads, so the
    stock wait can confirm the batch drained.
    """

    def send(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        target = int(node.blackboard.get(key, 0) or 0)
        sender_email = str(Player.GetAccountEmail() or "")
        if not target or not sender_email:
            return BehaviorTree.NodeState.FAILURE
        refs = []
        for email in follower_emails(sender_email):
            message_index = int(
                GLOBAL_CACHE.ShMem.SendMessage(
                    sender_email,
                    email,
                    SharedCommandType.SendDialogToTarget,
                    (float(target), float(dialog_id), 0.0, 0.0),
                )
            )
            refs.append((email, message_index))
        node.blackboard[refs_key] = refs
        node.blackboard[f"{refs_key}_command"] = int(SharedCommandType.SendDialogToTarget)
        PySystem.Console.Log(
            source,
            f"Dialog {hex(dialog_id)} sent to {len(refs)} followers ({key}).",
            PySystem.Console.MessageType.Info,
        )
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name=f"BroadcastDialog({key})", action_fn=send, aftercast_ms=250))


def team_takes_dialog(
    label: str,
    x: float,
    y: float,
    dialog_id: int,
    source: str,
    radius: float = 300.0,
) -> BehaviorTree:
    """Leader targets the NPC nearest a point, takes the dialog, then the whole
    team is ordered to take it too. Callers wrap this in `optional` when a missed
    blessing must not end the run."""
    key = f"npc_target:{label}"
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=label,
            children=[
                target_npc(x, y, key, radius=radius, source=source),
                leader_takes_dialog(key, dialog_id, source=source),
                broadcast_dialog_to_team(key, dialog_id, source=source),
                optional(
                    BT.Shared.WaitCommandDispatch(
                        command=SharedCommandType.SendDialogToTarget,
                        timeout_ms=TEAM_ACK_TIMEOUT_MS,
                        log=True,
                    ),
                    f"{label}: team ack",
                    source,
                ),
            ],
        )
    )


def wipe_restart_service(step_name: str, source: str) -> BehaviorTree:
    """Any defeat — a real wipe or the bot's own end-of-run resign — restarts
    the run from `step_name` once the party is back in an outpost.

    This REPLACES the framework's PartyWipeRecoveryService, which restarts from
    whatever step was running when the party went down. For a route bot both of
    its answers are wrong: a mid-dungeon step restarted in an outpost walks
    dungeon coordinates on the wrong map, and a deliberate resign inside the
    reset step restarts the reset step itself — measured live as the bot
    standing in Vlox's Falls waiting five minutes for an Arbor Bay map load.
    The suppression flag is re-asserted every tick because a planner restart
    clears the blackboard.
    """
    state = {"latched": False}

    def tick_recovery(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        node.blackboard["party_wipe_recovery_suppressed"] = True
        wiped = bool(Routines.Checks.Party.IsPartyWiped() or GLOBAL_CACHE.Party.IsPartyDefeated())

        if not state["latched"]:
            if wiped:
                state["latched"] = True
                PySystem.Console.Log(
                    source,
                    f"Party down - restarting from '{step_name}' once the outpost loads.",
                    PySystem.Console.MessageType.Warning,
                )
            return BehaviorTree.NodeState.RUNNING

        if Map.IsMapReady() and Map.IsOutpost() and GLOBAL_CACHE.Party.IsPartyLoaded():
            state["latched"] = False
            node.blackboard["restart_step_name_request"] = step_name
            PySystem.Console.Log(
                source,
                f"Outpost loaded - restarting run from '{step_name}'.",
                PySystem.Console.MessageType.Info,
            )
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(name="WipeRestart", action_fn=tick_recovery, aftercast_ms=0))


def hold_for_restart(name: str) -> BehaviorTree:
    """Terminal node for a reset step that ends in a resign: the wipe-restart
    service replaces the planner tree out from under it, so all this does is
    stay RUNNING and say so. If the restart never comes, the step name sitting
    visibly unfinished IS the diagnostic."""

    def hold(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(name=name, action_fn=hold, aftercast_ms=0))


def draw_fight_tab() -> None:
    snapshot = fight_awareness.read()
    stance = fight_awareness.stance(snapshot)
    PyImGui.text(f"Stance: {stance.name}")
    PyImGui.text(fight_awareness.describe(snapshot))
    PyImGui.separator()

    if snapshot is None:
        PyImGui.text("No fight zone publishing. HeroAI leader-side only.")
        return

    health = fight_awareness.party_health(snapshot)
    # Third positional is size_arg_y in the current PyImGui binding; the overlay
    # string goes FOURTH. Passing it third crashes the whole script on tab open.
    PyImGui.progress_bar(health, -1.0, 0.0, f"{health:.0%}")
    PyImGui.text(f"Zone state: {snapshot.get('state', '?')}")
    PyImGui.text(f"Alive {snapshot.get('party_alive', '?')} / dead {snapshot.get('party_dead', '?')}")
    PyImGui.text(f"Health steps: {snapshot.get('health_steps_used', 0)}/{snapshot.get('health_max_steps', 0)}")
    PyImGui.text(f"Given ground: {float(snapshot.get('given_ground', 0.0)):.0f}u")
    anchor = fight_awareness.anchor(snapshot)
    if anchor is not None:
        PyImGui.text(f"Anchor: {anchor[0]:.0f}, {anchor[1]:.0f}")

    PyImGui.separator()
    # The escape route is a HARD GATE on withdrawing, not one input among
    # several: no route, or one shorter than the give-ground margin, and the
    # party cannot back up at all however bad its health gets. This is the line
    # to read first when a retreat does not happen.
    for line in fight_awareness.retreat_blockers(snapshot):
        PyImGui.text(line)

    if stance is fight_awareness.Stance.CLEAR:
        return
    PyImGui.separator()
    PyImGui.text("Route is HELD - the leader is not advancing.")
