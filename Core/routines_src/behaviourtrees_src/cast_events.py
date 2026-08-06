"""Cast outcome nodes driven by the native agent-event stream.

The engine already tells us when a skill activated, finished, was interrupted or
started recharging. Reading that beats polling for a proxy and guessing a
deadline: the stream terminates the wait, so no window has to discriminate
between "slow" and "never happened".

Everything here is inert until `TRACKER.is_live()` — one player event must have
actually arrived. Callers gate on it so a silent stream falls back to whatever
they did before rather than stalling.
"""

from __future__ import annotations

import PySystem

from ...CombatEvents import CombatEvents
from ...py4gwcorelib_src.BehaviorTree import BehaviorTree
from ...py4gwcorelib_src.LiveClock import GetLiveTimestamp

NodeState = BehaviorTree.NodeState

PENDING = "pending"
FINISHED = "finished"
INTERRUPTED = "interrupted"
STOPPED = "stopped"

# Must stay under the shortest phase a build might spend casting — a pinned
# Selector starves every rung below it for this long. Expiry demotes the tracker,
# so it costs one cast, once, not once per pass.
RESOLVE_BACKSTOP_MS = 2500


def now_ms() -> int:
    return int(GetLiveTimestamp())


class CastTracker:
    """One in-flight player cast, resolved by events.

    Only the player is tracked and only one cast can be open, so a finish or
    interrupt arriving for our agent resolves whatever is armed. That sidesteps
    the skill-id correlation entirely — the id is recorded for diagnostics, not
    used to match.
    """

    def __init__(self) -> None:
        self.subscribed = False
        self.player_events = 0
        self.resolving_events = 0
        self.demoted = False
        self.armed_skill = 0
        self.armed_at = 0
        self.armed_is_instant = False
        self.state = ""
        self.reported_skill = 0
        self.recharging: dict[int, int] = {}

    def player_id(self) -> int:
        from ...Player import Player

        return Player.GetAgentID()

    def ensure_subscribed(self) -> None:
        if self.subscribed:
            return
        self.subscribed = True
        CombatEvents.Activate()
        CombatEvents.OnSkillActivated(self.handle_activated)
        CombatEvents.OnSkillFinished(self.handle_finished)
        CombatEvents.OnSkillInterrupted(self.handle_interrupted)
        CombatEvents.OnSkillStopped(self.handle_stopped)
        CombatEvents.OnSkillRechargeStarted(self.handle_recharge_started)
        CombatEvents.OnSkillRecharged(self.handle_recharged)

    def is_live(self) -> bool:
        """Only engine events that can END any wait count as proof.

        Excluded on purpose:
        - activations, which cannot resolve anything;
        - recharge, because helpers._create_estimated_recharge SYNTHESISES it
          locally for untracked agents. Counting it lets our own fabricated
          event convince us the engine is talking to us, and then every
          non-instant cast waits for a SKILL_FINISHED that never comes.

        Once demoted, stay demoted for the session. Re-promoting on the next
        event just flaps between paths.
        """
        return self.resolving_events > 0 and not self.demoted

    def mine(self, agent_id: int) -> bool:
        if agent_id != self.player_id():
            return False
        self.player_events += 1
        return True

    def mine_resolving(self, agent_id: int) -> bool:
        if not self.mine(agent_id):
            return False
        self.resolving_events += 1
        return True

    def demote(self, reason: str) -> None:
        """Stop trusting the stream. The timed path is always correct, so a wait
        that expired is reason enough to stop taking the verified one."""
        from ...Py4GWcorelib import Console
        from ...Py4GWcorelib import ConsoleLog

        if not self.demoted:
            ConsoleLog(
                "CastEvents",
                f"timed casts for the rest of this session: {reason} "
                f"(player events seen: {self.player_events}, resolving: {self.resolving_events})",
                Console.MessageType.Warning,
            )
        self.demoted = True
        self.state = ""

    def arm(self, skill_id: int) -> None:
        from ...GlobalCache import GLOBAL_CACHE

        self.ensure_subscribed()
        activation = GLOBAL_CACHE.Skill.Data.GetActivation(int(skill_id)) or 0.0
        self.armed_is_instant = activation <= 0.0
        self.armed_skill = int(skill_id)
        self.armed_at = now_ms()
        self.state = PENDING
        self.reported_skill = 0

    def resolve(self, outcome: str, skill_id: int) -> None:
        if self.state != PENDING:
            return
        self.state = outcome
        self.reported_skill = int(skill_id or 0)

    def handle_activated(self, agent_id: int, skill_id: int, target_id: int) -> None:
        self.mine(agent_id)

    def handle_finished(self, agent_id: int, skill_id: int) -> None:
        if not self.mine_resolving(agent_id):
            return
        self.resolve(FINISHED, skill_id)

    def handle_interrupted(self, agent_id: int, skill_id: int) -> None:
        if not self.mine_resolving(agent_id):
            return
        self.resolve(INTERRUPTED, skill_id)

    def handle_stopped(self, agent_id: int, skill_id: int) -> None:
        if not self.mine_resolving(agent_id):
            return
        self.resolve(STOPPED, skill_id)

    def handle_recharge_started(self, agent_id: int, skill_id: int, recharge_ms: int) -> None:
        # mine(), not mine_resolving(): this event may be locally synthesised, so
        # it is not evidence the engine is delivering anything.
        if not self.mine(agent_id):
            return
        self.recharging[int(skill_id)] = now_ms() + int(recharge_ms or 0)
        # Instant skills never emit SKILL_FINISHED, so recharge has to close them.
        # Everything else must wait for FINISHED: helpers._create_estimated_recharge
        # fabricates this event at ACTIVATION time for untracked agents, so treating
        # it as completion would release the wait while the cast is still running.
        if int(skill_id) == self.armed_skill and self.armed_is_instant:
            self.resolve(FINISHED, skill_id)

    def handle_recharged(self, agent_id: int, skill_id: int) -> None:
        if not self.mine(agent_id):
            return
        self.recharging.pop(int(skill_id), None)

    def is_recharging(self, skill_id: int) -> bool:
        expiry = self.recharging.get(int(skill_id))
        if expiry is None:
            return False
        if now_ms() >= expiry:
            self.recharging.pop(int(skill_id), None)
            return False
        return True

    def outcome(self) -> str:
        return self.state


TRACKER = CastTracker()


def tracker_is_live() -> bool:
    TRACKER.ensure_subscribed()
    return TRACKER.is_live()


class BTCastEvents:
    """BT leaves over the agent-event stream."""

    @staticmethod
    def IsLive() -> BehaviorTree.ConditionNode:
        return BehaviorTree.ConditionNode(name="CastEventsLive", condition_fn=tracker_is_live)

    @staticmethod
    def IsRecharging(skill_id: int) -> BehaviorTree.ConditionNode:
        return BehaviorTree.ConditionNode(
            name=f"IsRecharging({skill_id})",
            condition_fn=lambda: TRACKER.is_recharging(skill_id),
        )

    @staticmethod
    def WasInterrupted() -> BehaviorTree.ConditionNode:
        return BehaviorTree.ConditionNode(
            name="WasInterrupted",
            condition_fn=lambda: TRACKER.outcome() in (INTERRUPTED, STOPPED),
        )

    @staticmethod
    def HasActivated() -> BehaviorTree.ConditionNode:
        return BehaviorTree.ConditionNode(
            name="HasActivated",
            condition_fn=lambda: TRACKER.outcome() != "",
        )

    @staticmethod
    def Resolve(name: str = "Resolve", backstop_ms: int = RESOLVE_BACKSTOP_MS) -> BehaviorTree.WaitUntilNode:
        """RUNNING until the stream says finished (SUCCESS) or interrupted/stopped
        (FAILURE). The timeout is a dropped-packet backstop, not a discriminator —
        it must be long enough that it never decides an outcome."""

        started = {"at": 0}

        def check() -> BehaviorTree.NodeState:
            if started["at"] == 0:
                started["at"] = now_ms()
            outcome = TRACKER.outcome()
            if outcome == FINISHED:
                started["at"] = 0
                return NodeState.SUCCESS
            if outcome in (INTERRUPTED, STOPPED):
                started["at"] = 0
                return NodeState.FAILURE
            if now_ms() - started["at"] >= backstop_ms:
                started["at"] = 0
                TRACKER.demote(f"{name} saw no resolving event in {backstop_ms}ms")
                return NodeState.FAILURE
            return NodeState.RUNNING

        return BehaviorTree.WaitUntilNode(
            condition_fn=check,
            throttle_interval_ms=0,
            timeout_ms=backstop_ms * 2,
            name=name,
        )

    @staticmethod
    def CastAndResolve(skill_id: int, name: str = "", target_agent_id: int = 0, backstop_ms: int = RESOLVE_BACKSTOP_MS):
        """Fire a skill and wait for the engine's verdict on it."""
        from ...GlobalCache import GLOBAL_CACHE
        from ...Player import Player
        from ..Checks import Checks

        label = name or f"Skill{skill_id}"

        def ready() -> bool:
            """Deliberately the same gates as BT.Skills.CastSkillID, in the same
            order. Adding `CanCast()` here looks like an improvement and is not:
            it is False for the whole activation of the previous skill, so in a
            Sequence of casts every leg after the first fails and aborts the
            chain. The ACTION queue is what serialises back-to-back casts."""
            if not Checks.Map.IsExplorable():
                return False
            if not Checks.Skills.HasEnoughEnergy(Player.GetAgentID(), skill_id):
                return False
            slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(skill_id)
            if not 1 <= slot <= 8:
                return False
            return Checks.Skills.IsSkillSlotReady(slot)

        def fire() -> bool:
            slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(skill_id)
            if not 1 <= slot <= 8:
                return False
            TRACKER.arm(skill_id)
            GLOBAL_CACHE.SkillBar.UseSkill(slot, target_agent_id=target_agent_id, aftercast_delay=0)
            return True

        return BehaviorTree.SequenceNode(
            name=f"CastAndResolve:{label}",
            children=[
                BehaviorTree.ConditionNode(name=f"{label}Ready", condition_fn=ready),
                BehaviorTree.ConditionNode(name=f"{label}Fire", condition_fn=fire),
                BTCastEvents.Resolve(name=f"{label}Resolve", backstop_ms=backstop_ms),
            ],
        )
