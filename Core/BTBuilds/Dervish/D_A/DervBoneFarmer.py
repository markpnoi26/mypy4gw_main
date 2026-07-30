from __future__ import annotations

import time

from Core import GLOBAL_CACHE
from Core import Agent
from Core import BTBuildMgr
from Core import Key
from Core import Keystroke
from Core import Player
from Core import Profession
from Core import Range
from Core import Routines
from Core import Skill
from Core import Weapon
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Core.routines_src.BehaviourTrees import BT
from Core.routines_src.behaviourtrees_src import cast_events
from HeroAI.bt.bt_engine import HeroAIBTEngine

# Name substring matches — case-insensitive. Handy for readability but requires
# the name string-table lookup to have resolved; unnamed enemies won't match.
ENEMY_BLACKLIST_NAMES = {"blood song", "destruction", "charr axemaster"}

# Encoded-string matches — locale-independent and available as soon as the agent
# loads (before the name resolves). Copy the exact strings from the diagnostic
# log printed by WaitForAreaClearOrDeath, e.g. "\\x171C\\x8FE8".
ENEMY_BLACKLIST_ENC_STRINGS: set[str] = set()


def is_blacklisted_enemy(agent_id: int) -> bool:
    enc = Agent.GetEncNameStrByID(agent_id, literal=False)
    if enc and enc in ENEMY_BLACKLIST_ENC_STRINGS:
        return True
    name = Agent.GetNameByID(agent_id)
    if not name:
        return False
    name_lower = name.lower()
    return any(needle in name_lower for needle in ENEMY_BLACKLIST_NAMES)


class DervBuildFarmStatus:
    Setup = 'setup'
    Prepare = 'prepare'
    Kill = 'kill'
    Loot = 'loot'
    Wait = 'wait'


NON_COMBAT_PHASES = {
    DervBuildFarmStatus.Setup,
    DervBuildFarmStatus.Loot,
    DervBuildFarmStatus.Wait,
}

# Escape hatch for the chain's per-leg buff wait. Long enough to cover a 1s cast
# plus round trip; on expiry the chain continues rather than aborting.
ENCHANT_CONFIRM_TIMEOUT_MS = 2500


def sequence(name: str, *children) -> BehaviorTree:
    return BehaviorTree(BehaviorTree.SequenceNode(name=name, children=list(children)))


def selector(name: str, *children) -> BehaviorTree:
    return BehaviorTree(BehaviorTree.SelectorNode(name=name, children=list(children)))


def condition(name: str, fn) -> BehaviorTree.ConditionNode:
    return BehaviorTree.ConditionNode(name=name, condition_fn=fn)


def action(name: str, fn, aftercast_ms: int = 0) -> BehaviorTree.ActionNode:
    return BehaviorTree.ActionNode(name=name, action_fn=fn, aftercast_ms=aftercast_ms)


def succeeder(name: str) -> BehaviorTree.SucceederNode:
    return BehaviorTree.SucceederNode(name=name)


def optional(cast_tree: BehaviorTree, name: str = "Optional") -> BehaviorTree:
    """Absorb child FAILURE so the parent Sequence doesn't abort."""
    return selector(name, cast_tree, succeeder(f"{name}:Skip"))


class DervBoneFarmer(BTBuildMgr):
    ADRENALINE_CONFIRM_TIMEOUT_MS = 1500

    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Derv Bone Farmer",
            required_primary=Profession.Dervish,
            required_secondary=Profession.Assassin,
            template_code='OgCjkqqLrSYiihdftXjhOXhX0kA',
            is_combat_automator_compatible=False,
            required_skills=[
                GLOBAL_CACHE.Skill.GetID("Signet_of_Mystic_Speed"),
                GLOBAL_CACHE.Skill.GetID("Pious_Fury"),
                GLOBAL_CACHE.Skill.GetID("Grenths_Aura"),
                GLOBAL_CACHE.Skill.GetID("Vow_of_Silence"),
                GLOBAL_CACHE.Skill.GetID("Crippling_Victory"),
                GLOBAL_CACHE.Skill.GetID("Reap_Impurities"),
                GLOBAL_CACHE.Skill.GetID("Vow_of_Piety"),
                GLOBAL_CACHE.Skill.GetID("I_Am_Unstoppable"),
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))

        self.signet_of_mystic_speed = self.skills[0]
        self.pious_fury = self.skills[1]
        self.grenths_aura = self.skills[2]
        self.vow_of_silence = self.skills[3]
        self.crippling_victory = self.skills[4]
        self.reap_impurities = self.skills[5]
        self.vow_of_piety = self.skills[6]
        self.i_am_unstoppable = self.skills[7]

        self.status: str = DervBuildFarmStatus.Wait
        self.attack_pending: tuple[int, int, float] | None = None

    def has_buff(self, skill_id: int) -> bool:
        return bool(Routines.Checks.Effects.HasBuff(Player.GetAgentID(), skill_id))

    def current_adrenaline(self, skill_id: int) -> int:
        slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(skill_id)
        if not (1 <= slot <= 8):
            return -1
        data = GLOBAL_CACHE.SkillBar.GetSkillData(slot)
        if data is None:
            return -1
        return int(getattr(data, "adrenaline_a", 0) or 0)

    def attack_cast_settled(self) -> bool:
        """One latch shared by both adrenaline attacks. The client keeps reporting the
        pre-cast adrenaline for a few frames, and a per-skill latch would just make the
        selector fall through and fire the other attack in the same breath. Times out
        because an interrupted cast never spends adrenaline."""
        if self.attack_pending is None:
            return True

        skill_id, value_at_cast, marked_at = self.attack_pending
        if self.current_adrenaline(skill_id) < value_at_cast:
            self.attack_pending = None
            return True

        if (time.monotonic() - marked_at) * 1000 >= self.ADRENALINE_CONFIRM_TIMEOUT_MS:
            self.attack_pending = None
            return True

        return False

    def has_enough_adrenaline(self, skill_id: int) -> bool:
        if not Routines.Checks.Skills.CanCast():
            return False
        if not self.attack_cast_settled():
            return False

        required = int(Skill.Data.GetAdrenaline(skill_id) or 0)
        if required <= 0:
            return False

        current = self.current_adrenaline(skill_id)
        if current < required:
            return False

        self.attack_pending = (skill_id, current, time.monotonic())
        return True

    def filtered_enemies_in_range(self):
        px, py = Player.GetXY()
        arr = Routines.Agents.GetFilteredEnemyArray(px, py, Range.Spellcast.value)
        return [aid for aid in arr if not is_blacklisted_enemy(aid)], px, py

    def cast_body(self, name: str, skill_id: int, aftercast_ms: int, verified: bool = True) -> BehaviorTree:
        """Event-driven cast when the agent-event stream is delivering, otherwise
        the fixed-aftercast path this build has always used.

        The two branches are mutually exclusive on purpose. A plain Selector
        would re-cast through the legacy branch whenever the event branch
        reported an interrupt.

        `verified=False` for attack skills: ATTACK_SKILL_FINISHED only arrives
        when the swing lands (1.5s on a scythe), and holding RUNNING that long
        pins the Engagement sequence — which retargets upstream of the attack.
        Their adrenaline gate already latches against a re-fire.
        """
        if not verified:
            return BT.Skills.CastSkillID(skill_id, aftercast_delay=aftercast_ms, log=False)

        return selector(
            f"Cast:{name}",
            sequence(
                f"Verified:{name}",
                condition("CastEventsLive", cast_events.tracker_is_live),
                BT.CastEvents.CastAndResolve(skill_id, name=name),
            ),
            sequence(
                f"Timed:{name}",
                condition("CastEventsDark", lambda: not cast_events.tracker_is_live()),
                BT.Skills.CastSkillID(skill_id, aftercast_delay=aftercast_ms, log=False),
            ),
        )

    def cast_gated(self, name: str, skill_id: int, gate_fn, aftercast_ms: int, verified: bool = True) -> BehaviorTree:
        return sequence(
            f"Gated:{name}",
            condition(f"Gate:{name}", gate_fn),
            self.cast_body(name, skill_id, aftercast_ms, verified=verified),
        )

    def cast_plain(self, name: str, skill_id: int, aftercast_ms: int) -> BehaviorTree:
        return self.cast_body(name, skill_id, aftercast_ms)

    def swap_to_scythe(self) -> BehaviorTree:
        def needs_scythe() -> bool:
            return Agent.GetWeaponType(Player.GetAgentID())[0] != Weapon.Scythe

        def press_f1(node) -> BehaviorTree.NodeState:
            Keystroke.PressAndRelease(Key.F1.value)
            return BehaviorTree.NodeState.SUCCESS

        return sequence(
            "SwapToScythe",
            condition("NeedsScythe", needs_scythe),
            action("PressF1", press_f1, aftercast_ms=100),
        )

    def swap_to_shield_set(self) -> BehaviorTree:
        def needs_shield() -> bool:
            return Agent.GetWeaponType(Player.GetAgentID())[0] == Weapon.Scythe

        def press_f2(node) -> BehaviorTree.NodeState:
            Keystroke.PressAndRelease(Key.F2.value)
            return BehaviorTree.NodeState.SUCCESS

        return sequence(
            "SwapToShieldSet",
            condition("NeedsShield", needs_shield),
            action("PressF2", press_f2, aftercast_ms=750),
        )

    def outpost_guard(self) -> BehaviorTree:
        return sequence(
            "OutpostGuard",
            condition("NotExplorable", lambda: not Routines.Checks.Map.IsExplorable()),
            succeeder("SkipInOutpost"),
        )

    def non_combat_guard(self) -> BehaviorTree:
        return sequence(
            "NonCombatGuard",
            condition("InNonCombatPhase", lambda: self.status in NON_COMBAT_PHASES),
            optional(self.swap_to_shield_set(), name="OptionalShieldSwap"),
        )

    def prepare_branch(self) -> BehaviorTree:
        return sequence(
            "PrepareBranch",
            condition("InPrepare", lambda: self.status == DervBuildFarmStatus.Prepare),
            selector(
                "PreparePriority",
                self.cast_gated(
                    "VowOfPiety",
                    self.vow_of_piety,
                    lambda: not self.has_buff(self.vow_of_piety),
                    aftercast_ms=750,
                ),
                self.cast_gated(
                    "GrenthsAura",
                    self.grenths_aura,
                    lambda: not self.has_buff(self.grenths_aura),
                    aftercast_ms=100,
                ),
                self.cast_gated(
                    "VowOfSilence",
                    self.vow_of_silence,
                    lambda: (self.has_buff(self.grenths_aura) and self.has_buff(self.vow_of_piety)),
                    aftercast_ms=100,
                ),
            ),
        )

    def wait_for_buff(self, name: str, skill_id: int) -> BehaviorTree.ConditionNode:
        """Hold until the enchantment is actually up.

        A ConditionNode, not an ActionNode — ActionNode delivers its result a tick
        late. SUCCESS on expiry, never FAILURE: this chain has to finish, so a buff
        we could not read must not abort the legs after it.
        """
        started = {"at": 0.0}

        def check() -> BehaviorTree.NodeState:
            if started["at"] == 0.0:
                started["at"] = time.monotonic()
            if self.has_buff(skill_id):
                started["at"] = 0.0
                return BehaviorTree.NodeState.SUCCESS
            if (time.monotonic() - started["at"]) * 1000 >= ENCHANT_CONFIRM_TIMEOUT_MS:
                started["at"] = 0.0
                return BehaviorTree.NodeState.SUCCESS
            return BehaviorTree.NodeState.RUNNING

        return condition(f"Landed:{name}", check)

    def chain_leg(self, name: str, skill_id: int) -> BehaviorTree:
        """Cast, then wait for the buff before the next leg is even queued.

        aftercast_delay=0 because the wait is the spacing now. Queuing the legs
        100ms apart is what let Vow of Silence's UseSkill land while Grenth's Aura
        was still in its 1s cast, clipping it.
        """
        return sequence(
            f"Chain:{name}",
            self.cast_body(name, skill_id, aftercast_ms=0, verified=False),
            self.wait_for_buff(name, skill_id),
        )

    def refresh_chain(self) -> BehaviorTree:
        """PF → GA → VoS. PF strips VoS as its cost, so only commit when GA and VoS
        are both off cooldown — otherwise the chain would strip VoS and stall."""

        def ga_and_vos_ready() -> bool:
            return Routines.Checks.Skills.IsSkillIDReady(self.grenths_aura) and Routines.Checks.Skills.IsSkillIDReady(
                self.vow_of_silence
            )

        return sequence(
            "RefreshChain",
            condition(
                "NotCastingAndGA_VoSReady", lambda: not Routines.Checks.Skills.InCastingProcess() and ga_and_vos_ready()
            ),
            self.chain_leg("PiousFury", self.pious_fury),
            self.chain_leg("GrenthsAura", self.grenths_aura),
            self.chain_leg("VowOfSilence", self.vow_of_silence),
        )

    def engagement(self) -> BehaviorTree:
        def enemy_present() -> bool:
            enemies, _, _ = self.filtered_enemies_in_range()
            return bool(enemies)

        def interact_nearest(node) -> BehaviorTree.NodeState:
            enemies, px, py = self.filtered_enemies_in_range()
            if not enemies:
                return BehaviorTree.NodeState.FAILURE
            nearest = min(
                enemies,
                key=lambda aid: ((Agent.GetXY(aid)[0] - px) ** 2 + (Agent.GetXY(aid)[1] - py) ** 2),
            )
            Player.Interact(nearest, False)
            return BehaviorTree.NodeState.SUCCESS

        return sequence(
            "Engagement",
            # Skills outrank attacking. Without this the Selector drops here the tick
            # after RefreshChain queues its last cast, and the attack clips the
            # enchantment that is still activating.
            condition("NotCasting", lambda: not Routines.Checks.Skills.InCastingProcess()),
            condition("EnemyPresent", enemy_present),
            optional(self.swap_to_scythe(), name="OptionalScytheSwap"),
            action("InteractNearest", interact_nearest, aftercast_ms=100),
            selector(
                "AdrenalineAttackChoice",
                self.cast_gated(
                    "CripplingVictory",
                    self.crippling_victory,
                    lambda: self.has_enough_adrenaline(self.crippling_victory),
                    aftercast_ms=200,
                    verified=False,
                ),
                self.cast_gated(
                    "ReapImpurities",
                    self.reap_impurities,
                    lambda: self.has_enough_adrenaline(self.reap_impurities),
                    aftercast_ms=200,
                    verified=False,
                ),
                succeeder("AutoAttackFallthrough"),
            ),
        )

    def kill_branch(self) -> BehaviorTree:
        return sequence(
            "KillBranch",
            condition("InKill", lambda: self.status == DervBuildFarmStatus.Kill),
            selector(
                "KillPriority",
                self.cast_gated(
                    "SignetOfMysticSpeed",
                    self.signet_of_mystic_speed,
                    lambda: (self.has_buff(self.vow_of_silence) and not self.has_buff(self.signet_of_mystic_speed)),
                    aftercast_ms=250,
                ),
                self.cast_plain("IAmUnstoppable", self.i_am_unstoppable, aftercast_ms=100),
                self.refresh_chain(),
                self.engagement(),
            ),
        )

    def build_rotation_tree(self) -> BehaviorTree:
        return selector(
            "DervBoneRotation",
            self.outpost_guard(),
            self.non_combat_guard(),
            self.prepare_branch(),
            self.kill_branch(),
            succeeder("Idle"),
        )
