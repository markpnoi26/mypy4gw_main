"""Interact with a gadget, and know whether you actually got there.

`BT.Player.InteractTarget` issues one `Player.Interact` through the same ACTION
queue the party's skills are competing for, and returns SUCCESS whatever happens
next. Nothing downstream can tell a chest that opened from one the packet never
reached. The coroutine helper `Routines.Yield.Agents.InteractWithGadgetXY` gets
this right — it polls the distance to the target and re-issues until it closes —
but that loop was never ported to the behaviour tree layer, and every BT bot that
touches a gadget has been going without it.

What is observable here is *reaching interact range*, not *receiving a reward*:
a dungeon reward chest does not despawn when opened, and no readable
reward-window signal exists in this tree yet. So a confirm here means the
approach worked, and no more than that.
"""

from __future__ import annotations

import math
import time

from Core.Agent import Agent
from Core.AgentArray import AgentArray
from Core.Player import Player
from Core.Py4GWcorelib import Console
from Core.Py4GWcorelib import ConsoleLog
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Sources.marks_sources import gadget_choice

SOURCE = "gadget_interact"

# Interacting with a gadget is what makes the client walk you to it, so re-issuing
# is the approach, not just a retry. Under a second and the queue never drains.
REISSUE_INTERVAL_MS = 1000.0
POLL_INTERVAL_MS = 100
DEFAULT_TOLERANCE = 200.0
# Walking distance, not cast time. Generous on purpose, and never load-bearing:
# expiry means "could not confirm the approach", so callers wrap this.
DEFAULT_TIMEOUT_MS = 15_000

# Player.ChangeTarget is asynchronous; the framework's own targeting nodes settle
# for this long before anything reads the target back.
TARGET_AFTERCAST_MS = 250


def now_ms() -> float:
    return time.monotonic() * 1000.0


def candidates_near(x: float, y: float, radius: float) -> list[gadget_choice.Candidate]:
    """Cheap XY filter first, so gadget ids are only read for what is in range."""
    in_range = AgentArray.Filter.ByDistance(AgentArray.GetGadgetArray(), (x, y), radius)
    found = []
    for agent_id in in_range or []:
        gx, gy = Agent.GetXY(agent_id)
        found.append(
            gadget_choice.Candidate(
                agent_id=int(agent_id),
                gadget_id=int(Agent.GetGadgetID(agent_id) or 0),
                x=gx,
                y=gy,
            )
        )
    return found


def log_gadgets_in_range(x: float, y: float, radius: float = 400.0) -> BehaviorTree:
    """Dump every gadget id near a point. Always SUCCESS — this is a probe.

    Drop it in front of an interact that is doing nothing and it prints what is
    actually standing there, which is the whole diagnosis in one line.
    """

    def dump(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        found = candidates_near(x, y, radius)
        where = f"({x:.0f}, {y:.0f}) r={radius:.0f}"
        if not found:
            ConsoleLog(SOURCE, f"No gadgets near {where}.", Console.MessageType.Warning, log=True)
            return BehaviorTree.NodeState.SUCCESS
        for candidate in sorted(found, key=lambda c: gadget_choice.gap(c, (x, y))):
            ConsoleLog(
                SOURCE,
                f"gadget near {where}: id={candidate.gadget_id} agent={candidate.agent_id} "
                f"dist={gadget_choice.gap(candidate, (x, y)):.0f}",
                Console.MessageType.Info,
                log=True,
            )
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name="LogGadgetsInRange", action_fn=dump, aftercast_ms=0))


def target_gadget(
    x: float,
    y: float,
    key: str,
    radius: float = DEFAULT_TOLERANCE,
    wanted_ids=(),
    log: bool = True,
) -> BehaviorTree:
    """Target the gadget we meant and remember which one that was.

    The id goes on the blackboard under `key` rather than being read back from
    `Player.GetTargetID()` later, because HeroAI is free to retarget the moment
    combat options come back on.
    """

    def choose(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        agent_id, reason = gadget_choice.pick(candidates_near(x, y, radius), (x, y), wanted_ids)
        node.blackboard[key] = agent_id
        if not agent_id:
            ConsoleLog(SOURCE, f"TargetGadget({key}): {reason}", Console.MessageType.Warning, log=True)
            return BehaviorTree.NodeState.FAILURE
        Player.ChangeTarget(agent_id)
        if log:
            ConsoleLog(SOURCE, f"TargetGadget({key}): {reason}", Console.MessageType.Info, log=True)
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(
        BehaviorTree.ActionNode(name=f"TargetGadget({key})", action_fn=choose, aftercast_ms=TARGET_AFTERCAST_MS)
    )


def interact_until_in_range(
    key: str,
    tolerance: float = DEFAULT_TOLERANCE,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> BehaviorTree:
    """Re-issue the interact until the gap to the target closes.

    `WaitUntilNode` pins the rung it sits on, so no shared latch is needed to stop
    the ladder falling through to a sibling while this is still approaching.
    """
    reach = {"issued_ms": 0.0, "first_gap": -1.0, "last_gap": -1.0}

    def arm(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        reach["issued_ms"] = 0.0
        reach["first_gap"] = -1.0
        reach["last_gap"] = -1.0
        return BehaviorTree.NodeState.SUCCESS

    def issue(target: int) -> None:
        reach["issued_ms"] = now_ms()
        Player.ChangeTarget(target)
        Player.Interact(target, False)

    def reached(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        target = int(node.blackboard.get(key, 0) or 0)
        if not target:
            return BehaviorTree.NodeState.FAILURE

        px, py = Player.GetXY()
        tx, ty = Agent.GetXY(target)
        distance = math.hypot(tx - px, ty - py)
        if reach["first_gap"] < 0:
            reach["first_gap"] = distance
        reach["last_gap"] = distance

        # A caller that walked to the gadget's coordinates is ALREADY inside
        # tolerance on the first poll. Confirming there would report success
        # having never interacted with anything — the exact silent miss this
        # node exists to remove. The first interact is unconditional.
        if reach["issued_ms"] == 0.0:
            issue(target)
            return BehaviorTree.NodeState.RUNNING

        if distance <= tolerance:
            return BehaviorTree.NodeState.SUCCESS

        if now_ms() - reach["issued_ms"] >= REISSUE_INTERVAL_MS:
            issue(target)
        return BehaviorTree.NodeState.RUNNING

    def report(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        target = int(node.blackboard.get(key, 0) or 0)
        gadget_id = int(Agent.GetGadgetID(target) or 0) if target else 0
        ConsoleLog(
            SOURCE,
            f"InteractUntilInRange({key}) never reached the gadget: target={target} "
            f"gadget_id={gadget_id} dist={reach['first_gap']:.0f}->{reach['last_gap']:.0f} "
            f"tolerance={tolerance:.0f}",
            Console.MessageType.Warning,
            log=True,
        )
        return BehaviorTree.NodeState.FAILURE

    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=f"InteractUntilInRange({key})",
            children=[
                BehaviorTree.ActionNode(name=f"ArmReach({key})", action_fn=arm, aftercast_ms=0),
                BehaviorTree.SelectorNode(
                    name=f"ReachGadget({key})",
                    children=[
                        BehaviorTree.WaitUntilNode(
                            condition_fn=reached,
                            throttle_interval_ms=POLL_INTERVAL_MS,
                            timeout_ms=timeout_ms,
                            name=f"ReachGadget({key}): wait",
                        ),
                        BehaviorTree.ActionNode(
                            name=f"ReachGadget({key}): report",
                            action_fn=report,
                            aftercast_ms=0,
                        ),
                    ],
                ),
            ],
        )
    )


def interact_gadget(
    x: float,
    y: float,
    key: str,
    radius: float = DEFAULT_TOLERANCE,
    wanted_ids=(),
    tolerance: float = DEFAULT_TOLERANCE,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    log: bool = True,
) -> BehaviorTree:
    """Target the right gadget, then interact until you are standing at it.

    Deliberately does no walking. Callers own their movement, and a bot that
    pauses its route around a fight loses that pause key if a generic node takes
    the travelling over.
    """
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=f"InteractGadget({key})",
            children=[
                target_gadget(x, y, key, radius=radius, wanted_ids=wanted_ids, log=log),
                interact_until_in_range(key, tolerance=tolerance, timeout_ms=timeout_ms),
            ],
        )
    )
