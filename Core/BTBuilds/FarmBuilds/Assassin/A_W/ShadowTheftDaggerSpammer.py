"""BT port of Builds/Assassin/A_W/ShadowTheftDaggerSpammer.py — Voltaic Spear team farm.

=============================================================================
FarmBuild. Caller: Bots/marks_coding_corner/VoltaicSpearTeamFarm.py
=============================================================================

WHY THIS IS NOT DECOMPOSED INTO A TREE

This build is the furthest from a rotation of the four farm ports — 75 yield
points, and it is a *phase machine driving the keyboard*, not a skill ladder:

1. IT HAS ITS OWN STATUS STATE MACHINE.
   BuildStatus.Wait / Pull / Kill, advanced from inside the routine itself
   (Pull sets self.status = Kill when enemies appear). The farm script also
   writes self.status directly. A Selector cannot own that.

2. IT DRIVES KEYBINDS, NOT THE CAST API.
   Every skill goes through Routines.Yield.Keybinds.UseSkill(slot) with
   explicit Interact() and TargetPriorityTarget() handshakes between casts.
   Ordering of those keystrokes is the build. Reordering breaks the chain.

3. IT CONTAINS BLOCKING LOOPS.
   `while not Routines.Agents.GetFilteredEnemyArray(...) and elapsed < 40`
   in Pull, and `while not IsSkillIDUsable(jagged_strike)` in the spike. Those
   are multi-second waits inside one logical step.

4. IT ENCODES ATTACK CHAINS WITH TUNED INTER-CAST DELAYS.
   Jagged Strike -> Fox Fangs -> Death Blossom with 200/200/250 ms spacing,
   repeated in two variants (fast path via Exhausting Assault, slow path via
   Fox Fangs). The delays are the combo timing.

So the routine is hosted verbatim under one BT node via BldMgrBT.drive(),
which advances it one step per frame and reports RUNNING until done.

-----------------------------------------------------------------------------
BUG FOUND IN THE LEGACY BUILD — BEHAVIOUR DIFFERENCE IN THIS PORT
-----------------------------------------------------------------------------
Legacy uses `Keystroke`, `Key` (lines 191/196/201) and `ActionQueueManager`
(line 206) but NEVER IMPORTS THEM. Its module imports stop at Weapon and
HeroAI_Build. So:

  * swap_to_bow / swap_to_shield_set / swap_to_dagger raise NameError every
    time the current weapon differs from the wanted one — i.e. every actual
    swap. The generator dies and the caller sees the exception.
  * The non-explorable early-return path raises NameError on
    ActionQueueManager().ResetAllQueues().

This port ADDS the missing imports so the routine actually runs. That is a
deliberate behaviour change: legacy crashed on swap, this does not. If the farm
script was silently relying on those crashes (unlikely, but it is your farm),
that is the thing to check first when comparing runs.

-----------------------------------------------------------------------------
`self.priority_target` NOTE
-----------------------------------------------------------------------------
CombatServices already defines `priority_target` (initialised to 0 by
init_combat_services). This build reuses that attribute name for its own
locked target and sets it to None when clear. The semantics differ from
CombatServices' usage but nothing else reads it on a BldMgrBT instance, so the
collision is harmless — flagged so it is not mistaken for shared state later.

DUNGEON_MODEL_IDS is retained for reference even though only the subset in
find_shadow_theft_target's priority_order is actually consulted, matching legacy.
"""

from Core import ActionQueueManager
from Core import Agent
from Core import BldMgrBT
from Core import GLOBAL_CACHE
from Core import Key
from Core import Keystroke
from Core import Player
from Core import Profession
from Core import Range
from Core import Routines
from Core import ThrottledTimer
from Core import Weapon
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ....nodes import cast, rotation_tree

DUNGEON_MODEL_IDS = {
    6493: "Stone Summit Dominator",
    6495: "Stone Summit Contaminator",
    6496: "Stone Summit Blasphemer",
    6497: "Stone Summit Warder",
    6498: "Stone Summit Priest",
    6499: "Stone Summit Defender",
    6500: "Stone Summit Cleaver",
    6502: "Stone Summit Pounder",
    6503: "Stone Summit Demolisher",
    6504: "Stone Summit Marksman",
    6505: "Stone Summit Distracter",
    6506: "Stone Summit Zealot",
    6507: "Stone Summit Summoner",
    6512: "Modniir Priest",
    6514: "Modniir Berserker",
    6515: "Modniir Hunter",
    6798: "Wretched Wolf",
}

# Shadow Theft target ranking — lower is higher priority.
SHADOW_THEFT_PRIORITY = {
    6499: 1,  # Stone Summit Defender
    6512: 2,  # Modniir Priest
    6498: 3,  # Stone Summit Priest
    6495: 4,  # Stone Summit Contaminator
    6497: 5,  # Stone Summit Warder
    6507: 6,  # Stone Summit Summoner
    6493: 7,  # Stone Summit Dominator
}


class BuildStatus:
    Kill = 'kill'
    Wait = 'wait'
    Pull = 'pull'


class AssassinShadowTheftDaggerSpammer(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Assassin Shadow Theft Dagger Spammer",
            required_primary=Profession.Assassin,
            required_secondary=Profession.Warrior,
            template_code="OwFjUNd8ITPPOMMMHMvl0k6Pk1A",
            is_combat_automator_compatible=False,
            required_skills=[
                GLOBAL_CACHE.Skill.GetID("Exhausting_Assault"),
                GLOBAL_CACHE.Skill.GetID("Jagged_Strike"),
                GLOBAL_CACHE.Skill.GetID("Fox_Fangs"),
                GLOBAL_CACHE.Skill.GetID("Death_Blossom"),
                GLOBAL_CACHE.Skill.GetID("Asuran_Scan"),
                GLOBAL_CACHE.Skill.GetID("I_Am_Unstoppable"),
                GLOBAL_CACHE.Skill.GetID("Critical_Eye"),
                GLOBAL_CACHE.Skill.GetID("Shadow_Theft"),
            ],
        )
        if match_only:
            return
        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))

        (
            self.exhausting_assault,
            self.jagged_strike,
            self.fox_fangs,
            self.death_blossom,
            self.asuran_scan,
            self.i_am_unstoppable,
            self.critical_eye,
            self.shadow_theft,
        ) = self.skills

        self.blind = GLOBAL_CACHE.Skill.GetID("Blind")

        self.status = BuildStatus.Wait
        self.priority_target = None

        self.last_asuran_scan_target = None
        self.last_asuran_scan_time = 0
        self.asuran_scan_throttle = ThrottledTimer(10000)
        self.last_asuran_scan_target_id = None

    def call_a_priority_target(self, range_limit=Range.Spellcast.value):
        """Lock the nearest valid enemy within range_limit."""
        player_x, player_y = Player.GetXY()
        enemies_left = Routines.Agents.GetFilteredEnemyArray(player_x, player_y, range_limit)
        if enemies_left:
            yield from Routines.Yield.Keybinds.TargetNearestEnemy()
            target_id = Player.GetTargetID()
            yield from Routines.Yield.Keybinds.Interact()
            if target_id:
                Player.CallTarget(target_id)
            self.priority_target = target_id
            return
        else:
            if Routines.Checks.Agents.InDanger(Range.Spellcast):
                yield from Routines.Yield.Keybinds.TargetNearestEnemy()
                target_id = Player.GetTargetID()
                yield from Routines.Yield.Keybinds.Interact()
                if target_id:
                    Player.CallTarget(target_id)
                self.priority_target = target_id
                return
            self.priority_target = None
        return

    def update_priority_target_if_needed(self):
        """Reacquire when the locked target is gone, changed, or dead."""
        if not self.priority_target:
            yield from self.call_a_priority_target()
            return

        yield from Routines.Yield.Keybinds.TargetPriorityTarget()
        target_id = Player.GetTargetID()
        if target_id != self.priority_target:
            self.priority_target = None
            yield from self.call_a_priority_target()
            return

        if Agent.IsDead(self.priority_target):
            self.priority_target = None
            yield from self.call_a_priority_target()
            return

    def find_shadow_theft_target(self):
        """Shadow Theft target: model-ID priority first, then proximity, within
        half earshot."""
        player_x, player_y = Player.GetXY()
        enemy_agent_ids = Routines.Agents.GetFilteredEnemyArray(player_x, player_y, Range.Earshot.value * 0.5)

        best_agent_id = None
        best_priority = float("inf")
        best_dist_sq = float("inf")

        for agent_id in enemy_agent_ids:
            agent = Agent.GetAgentByID(agent_id)
            if agent is None or agent.agent_id == 0:
                continue
            model_id = Agent.GetModelID(agent.agent_id)
            dx, dy = agent.pos.x - player_x, agent.pos.y - player_y
            dist_sq = dx * dx + dy * dy

            rank = SHADOW_THEFT_PRIORITY.get(model_id, 999)

            if rank < best_priority or (rank == best_priority and dist_sq < best_dist_sq):
                best_priority = rank
                best_dist_sq = dist_sq
                best_agent_id = agent_id

        if best_agent_id:
            self.priority_target = best_agent_id
            Player.ChangeTarget(best_agent_id)
            Player.CallTarget(best_agent_id)
            Player.Interact(best_agent_id, True)
            yield from Routines.Yield.Keybinds.TargetPriorityTarget()
            return
        else:
            yield from Routines.Yield.Keybinds.TargetNearestEnemy()
            target_id = Player.GetTargetID()
            yield from Routines.Yield.Keybinds.Interact()
            if target_id:
                Player.CallTarget(target_id)
            self.priority_target = target_id
            return

    # Weapon-set swaps by raw keystroke. F1/F2/F3 are the character's weapon
    # sets — this build assumes daggers on set 1, spear+shield on 2, bow on 3.
    def swap_to_bow(self):
        if Agent.GetWeaponType(Player.GetAgentID())[0] != Weapon.Bow:
            Keystroke.PressAndRelease(Key.F3.value)

    def swap_to_shield_set(self):
        if Agent.GetWeaponType(Player.GetAgentID())[0] != Weapon.Spear:
            Keystroke.PressAndRelease(Key.F2.value)

    def swap_to_dagger(self):
        if Agent.GetWeaponType(Player.GetAgentID())[0] != Weapon.Daggers:
            Keystroke.PressAndRelease(Key.F1.value)

    def farm_routine(self):
        if not Routines.Checks.Map.IsExplorable():
            ActionQueueManager().ResetAllQueues()
            yield from Routines.Yield.wait(25)
            return

        if self.status == BuildStatus.Wait:
            self.swap_to_shield_set()
            yield from Routines.Yield.wait(100)
            self.priority_target = None
            return

        if self.status == BuildStatus.Pull:
            self.swap_to_bow()
            yield from self.update_priority_target_if_needed()

            elapsed = 0
            player_x, player_y = Player.GetXY()
            while not Routines.Agents.GetFilteredEnemyArray(player_x, player_y, Range.Area.value) and elapsed < 40:
                yield from Routines.Yield.wait(100)
                elapsed += 1

            self.status = BuildStatus.Kill
            return

        if self.status == BuildStatus.Kill:
            self.swap_to_dagger()
            player_agent_id = Player.GetAgentID()
            has_critical_eye = Routines.Checks.Effects.HasBuff(player_agent_id, self.critical_eye)
            has_i_am_unstoppable = Routines.Checks.Effects.HasBuff(player_agent_id, self.i_am_unstoppable)
            has_shadow_theft = Routines.Checks.Effects.HasBuff(player_agent_id, self.shadow_theft)

            if not (Routines.Checks.Player.CanAct() and Routines.Checks.Skills.CanCast()):
                if (yield from Routines.Yield.Skills.IsSkillIDUsable(self.critical_eye)) and not has_critical_eye:
                    critical_eye_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(self.critical_eye)
                    yield from Routines.Yield.Keybinds.UseSkill(critical_eye_slot)

                if (
                    yield from Routines.Yield.Skills.IsSkillIDUsable(self.i_am_unstoppable)
                ) and not has_i_am_unstoppable:
                    i_am_unstoppable_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(self.i_am_unstoppable)
                    yield from Routines.Yield.Keybinds.UseSkill(i_am_unstoppable_slot)
                yield from self.update_priority_target_if_needed()
                return

            yield from self.update_priority_target_if_needed()

            if not self.priority_target:
                yield from Routines.Yield.wait(25)
                return

            yield from Routines.Yield.Keybinds.TargetPriorityTarget()
            nearest_enemy_agent_id = Player.GetTargetID()
            nearest_enemy_agent = Agent.GetAgentByID(nearest_enemy_agent_id)
            if nearest_enemy_agent is None:
                yield
                return
            player_x, player_y = Player.GetXY()
            enemy_x, enemy_y = nearest_enemy_agent.pos.x, nearest_enemy_agent.pos.y

            dx = enemy_x - player_x
            dy = enemy_y - player_y
            dist_sq = dx * dx + dy * dy

            yield from Routines.Yield.Keybinds.Interact()

            if Routines.Checks.Player.CanAct() and Routines.Checks.Skills.CanCast():
                if (yield from Routines.Yield.Skills.IsSkillIDUsable(self.critical_eye)) and not has_critical_eye:
                    critical_eye_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(self.critical_eye)
                    yield from Routines.Yield.Keybinds.UseSkill(critical_eye_slot)

                if (
                    yield from Routines.Yield.Skills.IsSkillIDUsable(self.i_am_unstoppable)
                ) and not has_i_am_unstoppable:
                    i_am_unstoppable_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(self.i_am_unstoppable)
                    yield from Routines.Yield.Keybinds.UseSkill(i_am_unstoppable_slot)

                if (yield from Routines.Yield.Skills.IsSkillIDUsable(self.asuran_scan)) and nearest_enemy_agent_id:
                    if (
                        self.asuran_scan_throttle.IsExpired()
                        or not Agent.IsHexed(nearest_enemy_agent_id)
                        or self.last_asuran_scan_target_id != nearest_enemy_agent_id
                    ):
                        asura_scan_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(self.asuran_scan)
                        yield from Routines.Yield.Keybinds.UseSkill(asura_scan_slot)
                        yield from Routines.Yield.wait(200)

                        self.asuran_scan_throttle.Reset()
                        self.last_asuran_scan_target_id = nearest_enemy_agent_id
                        yield from Routines.Yield.Keybinds.Interact()

                # NOTE: legacy operator precedence preserved verbatim. This reads
                # as (A and B and not C) or (B and D), which is almost certainly
                # not what was intended, but changing it changes when Shadow
                # Theft fires. Left as-is.
                if (
                    nearest_enemy_agent_id
                    and (yield from Routines.Yield.Skills.IsSkillIDUsable(self.shadow_theft))
                    and not has_shadow_theft
                    or (yield from Routines.Yield.Skills.IsSkillIDUsable(self.shadow_theft))
                    and dist_sq <= Range.Area.value**2
                ):
                    yield from self.find_shadow_theft_target()

                    if self.priority_target:
                        nearest_enemy_agent_id = self.priority_target
                        nearest_enemy_agent = Agent.GetAgentByID(nearest_enemy_agent_id)

                        if nearest_enemy_agent and nearest_enemy_agent.is_living_type:
                            shadow_theft_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(self.shadow_theft)
                            yield from Routines.Yield.Keybinds.UseSkill(shadow_theft_slot)
                            yield from Routines.Yield.wait(350)

                if dist_sq <= Range.Adjacent.value**2:
                    player_current_energy = Agent.GetEnergy(player_agent_id) * Agent.GetMaxEnergy(player_agent_id)

                    # Fast chain: Jagged Strike -> Exhausting Assault
                    if (
                        yield from Routines.Yield.Skills.IsSkillIDUsable(self.exhausting_assault)
                    ) and player_current_energy >= 10:
                        nearest_enemy_agent = Agent.GetAgentByID(nearest_enemy_agent_id)
                        if not nearest_enemy_agent:
                            return

                        MAX_RANGE_SQ = Range.Adjacent.value**2

                        while not (yield from Routines.Yield.Skills.IsSkillIDUsable(self.jagged_strike)):
                            yield from Routines.Yield.wait(50)

                        player_x, player_y = Player.GetXY()
                        dx, dy = nearest_enemy_agent.pos.x - player_x, nearest_enemy_agent.pos.y - player_y
                        if dx * dx + dy * dy > MAX_RANGE_SQ:
                            yield from Routines.Yield.Keybinds.Interact()
                            return

                        jagged_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(self.jagged_strike)
                        exhausting_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(self.exhausting_assault)

                        yield from Routines.Yield.Keybinds.Interact()

                        yield from Routines.Yield.Keybinds.TargetPriorityTarget()
                        yield from Routines.Yield.Keybinds.UseSkill(jagged_slot)
                        yield from Routines.Yield.wait(200)

                        if (yield from Routines.Yield.Skills.IsSkillIDUsable(self.exhausting_assault)):
                            if Agent.IsDead(nearest_enemy_agent_id):
                                return
                            yield from Routines.Yield.Keybinds.UseSkill(exhausting_slot)
                            yield from Routines.Yield.wait(250)

                    # Full chain: Jagged Strike -> Fox Fangs -> Death Blossom
                    if (
                        yield from Routines.Yield.Skills.IsSkillIDUsable(self.death_blossom)
                    ) and player_current_energy >= 12:
                        player_x, player_y = Player.GetXY()
                        target_x, target_y = Agent.GetXY(nearest_enemy_agent_id)
                        dist_sq = (player_x - target_x) ** 2 + (player_y - target_y) ** 2
                        if dist_sq > Range.Adjacent.value**2:
                            yield from Routines.Yield.Keybinds.Interact()
                            return

                        yield from Routines.Yield.Keybinds.Interact()

                        skill_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(self.jagged_strike)
                        yield from Routines.Yield.Keybinds.TargetPriorityTarget()
                        yield from Routines.Yield.Keybinds.UseSkill(skill_slot)
                        yield from Routines.Yield.wait(200)

                        if (yield from Routines.Yield.Skills.IsSkillIDUsable(self.fox_fangs)):
                            if Agent.IsDead(nearest_enemy_agent_id):
                                return

                            skill_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(self.fox_fangs)
                            yield from Routines.Yield.Keybinds.UseSkill(skill_slot)
                            yield from Routines.Yield.wait(200)

                            if (yield from Routines.Yield.Skills.IsSkillIDUsable(self.death_blossom)):
                                if Agent.IsDead(nearest_enemy_agent_id):
                                    return

                                skill_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(self.death_blossom)
                                yield from Routines.Yield.Keybinds.UseSkill(skill_slot)
                                yield from Routines.Yield.wait(250)

                    # Slow chain, used while Exhausting Assault recharges.
                    # Same combo, 350 ms after Fox Fangs instead of 200.
                    if (
                        yield from Routines.Yield.Skills.IsSkillIDUsable(self.fox_fangs)
                    ) and player_current_energy >= 10:
                        player_x, player_y = Player.GetXY()
                        target_x, target_y = Agent.GetXY(nearest_enemy_agent_id)
                        dist_sq = (player_x - target_x) ** 2 + (player_y - target_y) ** 2
                        if dist_sq > Range.Adjacent.value**2:
                            yield from Routines.Yield.Keybinds.Interact()
                            return

                        yield from Routines.Yield.Keybinds.Interact()

                        skill_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(self.jagged_strike)
                        yield from Routines.Yield.Keybinds.TargetPriorityTarget()
                        yield from Routines.Yield.Keybinds.UseSkill(skill_slot)
                        yield from Routines.Yield.wait(200)

                        if (yield from Routines.Yield.Skills.IsSkillIDUsable(self.fox_fangs)):
                            if Agent.IsDead(nearest_enemy_agent_id):
                                return

                            skill_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(self.fox_fangs)
                            yield from Routines.Yield.Keybinds.UseSkill(skill_slot)
                            yield from Routines.Yield.wait(350)

                            if (yield from Routines.Yield.Skills.IsSkillIDUsable(self.death_blossom)):
                                if Agent.IsDead(nearest_enemy_agent_id):
                                    return

                                skill_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(self.death_blossom)
                                yield from Routines.Yield.Keybinds.UseSkill(skill_slot)
                                yield from Routines.Yield.wait(250)
            return

    def build_rotation_tree(self) -> BehaviorTree:
        """Single hosted node — see module header for why this is not a tree."""
        return rotation_tree(
            "ShadowTheftDaggerSpammer",
            [],
            [cast(self, "VoltaicSpearFarmRoutine", lambda: self.farm_routine())],
        )
