from __future__ import annotations

from typing import TYPE_CHECKING

from Core.build_src.combat_services import BuildCoroutine
import PySystem

from Core import AgentArray, GLOBAL_CACHE, Profession, Range, Routines, ThrottledTimer, Utils
from Core.Agent import Agent
from Core.Player import Player
from Core.Skill import Skill
from Core.enums_src.GameData_enums import Attribute
from HeroAI.targeting import TargetMinionNonEnchanted

if TYPE_CHECKING:
    from Core.build_src.combat_services import CombatServices

__all__ = ["DeathMagic"]

HEAL_OBSERVE_GRACE_MS = 750


class DeathMagic:
    def __init__(self, build: CombatServices) -> None:
        self.build: CombatServices = build
        self.heal_latch_until_ms: int = 0
        self.heal_latch_snapshot: dict[int, float] = {}
        self.heal_backoff_until_ms: int = 0
        self.health_recovering: bool = False
        self.order_assumed_until_ms: int = 0
        self.blood_of_the_master_throttle: ThrottledTimer = ThrottledTimer(5000)
        self.blood_of_the_master_throttle.Stop()
        self.heal_diagnostics_logged: set[str] = set()

    # region minion herd
    def own_minions(
        self,
        max_distance: float = Range.Earshot.value,
        *,
        exclude_enchanted: bool = False,
    ) -> list[int]:
        """Live minions this character controls.

        GetFilteredMinionArray returns every minion on the field including other
        necromancers', and a sacrifice paid to heal someone else's army is pure
        loss, so the owner filter is not optional.

        `exclude_enchanted` drops minions already carrying an enchantment. On a
        bar with Death Nova that means the minion has been claimed as a bomb —
        rescuing it undoes the trade. Enchantment presence is the readable
        signal here for the same reason Death_Nova targets on it: per-skill
        effect reads on minions are not dependable.
        """
        player_agent_id = Player.GetAgentID()
        player_x, player_y = Player.GetXY()
        minion_array = Routines.Agents.GetFilteredMinionArray(player_x, player_y, max_distance)
        minions = [agent_id for agent_id in (minion_array or []) if Agent.GetOwnerID(agent_id) == player_agent_id]
        if exclude_enchanted:
            minions = [agent_id for agent_id in minions if not Agent.IsEnchanted(agent_id)]
        return minions

    def minion_herd(
        self,
        max_distance: float = Range.Earshot.value,
        *,
        hurt_health: float = 0.75,
        exclude_enchanted: bool = False,
    ) -> tuple[int, float, int]:
        """`(count, worst_health, hurt_count)` over the controlled minions."""
        minions = self.own_minions(max_distance, exclude_enchanted=exclude_enchanted)
        if not minions:
            return 0, 1.0, 0
        healths = [float(Agent.GetHealth(agent_id)) for agent_id in minions]
        return len(minions), min(healths), sum(1 for health in healths if health < hurt_health)

    def minion_outlook(self, agent_id: int, heal_amount: float) -> tuple[float, float] | None:
        """`(seconds_to_live, seconds_bought_by_a_heal)`, or None if unreadable.

        Minion degeneration gets worse the longer the minion has been alive, so
        a health bar on its own says nothing about urgency: a fresh minion and
        an old one sitting at the same 40% are minutes and seconds from death
        respectively. The degeneration rate is readable per agent, so forecast
        from it instead of from the bar.

        None means the rate could not be read — callers fall back to health
        fractions rather than treating the minion as healthy.
        """
        max_health = int(Agent.GetMaxHealth(agent_id))
        if max_health <= 0:
            return None
        loss_per_second = -float(Agent.GetHealthRegen(agent_id)) * max_health
        if loss_per_second <= 0.0:
            return None
        current_health = float(Agent.GetHealth(agent_id)) * max_health
        return current_health / loss_per_second, heal_amount / loss_per_second

    def death_magic_rank(self) -> int:
        for attribute in Agent.GetAttributes(Player.GetAgentID()):
            if int(attribute.attribute_id) == int(Attribute.DeathMagic):
                return int(attribute.level)
        return 0

    def skill_scaled_value(self, skill_id: int, rank: int) -> float:
        scale_0pts, scale_15pts = GLOBAL_CACHE.Skill.Data.GetScale(skill_id)
        return float(scale_0pts) + (float(scale_15pts) - float(scale_0pts)) * (rank / 15.0)

    # endregion

    # region health budget
    def update_health_recovery(self, panic_health: float, resume_health: float) -> bool:
        """Latch the caster out of health spending until they have really recovered.

        A single threshold makes the caster oscillate on the floor: one
        sacrifice puts them under it, a tick of regeneration lifts them a hair
        over, the next sacrifice fires. The latch sets on the way down and only
        clears at the higher mark.
        """
        health = float(Agent.GetHealth(Player.GetAgentID()))
        if health < panic_health:
            self.health_recovering = True
        elif self.health_recovering and health >= resume_health:
            self.health_recovering = False
        return self.health_recovering

    def survives_sacrifice(
        self,
        sacrifice_pct: float,
        min_health_after: float,
        min_health_after_abs: int,
    ) -> bool:
        player_agent_id = Player.GetAgentID()
        max_health = int(Agent.GetMaxHealth(player_agent_id))
        if max_health <= 0:
            return False
        health_after_pct = float(Agent.GetHealth(player_agent_id)) - sacrifice_pct
        if min_health_after_abs > 0 and (health_after_pct * max_health) <= min_health_after_abs:
            return False
        return health_after_pct > min_health_after

    def log_heal_diagnostic_once(self, key: str, message: str) -> None:
        """One line per distinct condition, per build instance.

        These fire on the paths where a bad observable would otherwise wedge the
        healing rules silently, so they have to be visible — but a rotation runs
        every frame, so they must never repeat.
        """
        if key in self.heal_diagnostics_logged:
            return
        self.heal_diagnostics_logged.add(key)
        PySystem.Console.Log("BloodOfTheMaster", message, PySystem.Console.MessageType.Warning)

    def herd_health_snapshot(self, minions: list[int]) -> dict[int, float]:
        return {agent_id: float(Agent.GetHealth(agent_id)) * int(Agent.GetMaxHealth(agent_id)) for agent_id in minions}

    def heal_latch_blocked(self, minions: list[int], min_gain: float, backoff_ms: int) -> bool:
        """Hold off a second heal until the first one has been seen to land.

        The caster's own health is the wrong thing to confirm on: it drops
        whether or not the heal did anything, so a latch watching it releases
        immediately and permits an endless chain of sacrifices. What the skill
        changes is minion health, so that is what gets snapshotted and compared.

        Expiry without an observed gain does not mean the cast is still in
        flight — it means the heal cannot keep ahead of what is happening to
        this herd. Paying again at the same rate is how a necromancer spends a
        whole fight healing and contributes nothing else, so that outcome
        starts a backoff instead of a retry. Minions that died in the meantime
        drop out of the comparison rather than masking a real gain.
        """
        now_ms = int(Utils.GetBaseTimestamp())
        if now_ms < self.heal_backoff_until_ms:
            return True
        if not self.heal_latch_until_ms:
            return False

        current = self.herd_health_snapshot(minions)
        observed_gain = sum(
            current[agent_id] - health for agent_id, health in self.heal_latch_snapshot.items() if agent_id in current
        )
        if observed_gain >= min_gain:
            self.heal_latch_until_ms = 0
            self.heal_latch_snapshot = {}
            return False
        if now_ms < self.heal_latch_until_ms:
            return True

        self.log_heal_diagnostic_once(
            "ineffective",
            f"a cast produced no observed minion healing (gain={observed_gain:.0f}, wanted={min_gain:.0f}); "
            "backing off — the herd is past what this heal can hold",
        )
        self.heal_latch_until_ms = 0
        self.heal_latch_snapshot = {}
        self.heal_backoff_until_ms = now_ms + max(0, int(backoff_ms))
        return True

    def arm_heal_latch(self, skill_id: int, snapshot: dict[int, float]) -> None:
        activation_ms = int((GLOBAL_CACHE.Skill.Data.GetActivation(skill_id) or 0.0) * 1000)
        aftercast_ms = int((GLOBAL_CACHE.Skill.Data.GetAftercast(skill_id) or 0.0) * 1000)
        self.heal_latch_snapshot = snapshot
        self.heal_latch_until_ms = int(Utils.GetBaseTimestamp()) + activation_ms + aftercast_ms + HEAL_OBSERVE_GRACE_MS

    # endregion

    # region B
    def Blood_of_the_Master(
        self,
        *,
        scan_range: float = Range.Earshot.value,
        hurt_health: float = 0.75,
        critical_health: float = 0.40,
        rescue_seconds: float = 4.0,
        min_seconds_bought: float = 6.0,
        min_interval_ms: int = 5000,
        ineffective_backoff_ms: int = 5000,
        min_hurt_minions: int = 3,
        rescue_only: bool = False,
        min_health_after: float = 0.55,
        emergency_min_health_after: float = 0.35,
        min_health_after_abs: int = 100,
        order_active_margin: float = 0.10,
        panic_health: float = 0.30,
        resume_health: float = 0.55,
        sacrifice_pct: float = 0.17,
    ) -> BuildCoroutine:
        """Heal the controlled minions, but never at a price that kills the caster.

        Triage first. Because degeneration worsens with age, a heal of a fixed
        size buys less and less time as a minion gets older, until the minion is
        terminal and the sacrifice buys nothing — the health is gone, the minion
        dies anyway, and the caster has spent the fight healing corpses-to-be
        instead of keeping up. `min_seconds_bought` is where that line sits:
        minions the heal cannot keep up with are ignored by both triggers and
        left to die, which is what re-animating is for.

        Of the minions still worth healing, two triggers. `rescue_seconds` is
        the urgent one — a minion is that close to death, and minions leave no
        exploitable corpse, so what dies is gone. `min_hurt_minions` is the
        value one — enough of the herd is down that a flat heal across all of
        them pays for 17% of maximum health. `rescue_only` drops the value
        trigger so the two can sit at different heights in a rotation behind
        different health floors.

        `min_interval_ms` is a flat floor between casts that both triggers obey,
        rescues included. The skill's own 2 second recharge is far too fast to
        be the limit — at that rate the caster does nothing but sacrifice — and
        a rescue that repeats every recharge was never a rescue, just a minion
        the heal cannot save. `ineffective_backoff_ms` extends the wait further
        once a cast has been observed not to help.

        No aggro gate: minions degenerate whether or not anything is being
        fought, and a herd left to rot between fights arrives at the next one
        already dying.
        """
        blood_of_the_master_id: int = Skill.GetID("Blood_of_the_Master")
        blood_of_the_master = self.build.GetCustomSkill(blood_of_the_master_id)

        if not self.build.IsSkillEquipped(blood_of_the_master_id):
            return False

        minions = self.own_minions(
            scan_range,
            exclude_enchanted=self.build.IsSkillEquipped(Skill.GetID("Death_Nova")),
        )
        if not minions:
            return False

        heal_amount = self.skill_scaled_value(blood_of_the_master_id, self.death_magic_rank())
        rescue = False
        hurt_count = 0
        for minion_agent_id in minions:
            health = float(Agent.GetHealth(minion_agent_id))
            outlook = self.minion_outlook(minion_agent_id, heal_amount)
            if outlook is None:
                # Degeneration unreadable — fall back to the health bar rather
                # than reading the minion as fine and never healing it. This
                # path has no triage, so it is the one to suspect if healing
                # stays too eager.
                self.log_heal_diagnostic_once(
                    "outlook",
                    f"degeneration unreadable on minion {minion_agent_id} "
                    f"(regen={Agent.GetHealthRegen(minion_agent_id)}); "
                    "forecasting disabled, falling back to health thresholds",
                )
                rescue = rescue or health < critical_health
                hurt_count += 1 if health < hurt_health else 0
                continue
            seconds_to_live, seconds_bought = outlook
            if seconds_bought < min_seconds_bought:
                continue
            rescue = rescue or seconds_to_live < rescue_seconds
            hurt_count += 1 if health < hurt_health else 0

        if not rescue and (rescue_only or hurt_count < min_hurt_minions):
            return False

        self.blood_of_the_master_throttle.SetThrottleTime(min_interval_ms)
        throttle_ready = self.blood_of_the_master_throttle.IsStopped() or self.blood_of_the_master_throttle.IsExpired()
        if not throttle_ready:
            return False

        if self.update_health_recovery(panic_health, resume_health):
            return False
        if self.heal_latch_blocked(minions, heal_amount * 0.5, ineffective_backoff_ms):
            return False

        if blood_of_the_master is not None:
            declared_sacrifice = float(blood_of_the_master.Conditions.SacrificePercent or 0.0)
            if declared_sacrifice > 0.0:
                sacrifice_pct = declared_sacrifice

        floor = emergency_min_health_after if rescue else min_health_after
        # Order of Undeath keeps draining after this cast resolves, so the
        # sacrifice has to leave headroom for the drain on top of whatever hurt
        # the minions in the first place.
        if Routines.Checks.Agents.HasEffect(Player.GetAgentID(), Skill.GetID("Order_of_Undeath")):
            floor += order_active_margin

        if not self.survives_sacrifice(sacrifice_pct, floor, min_health_after_abs):
            return False

        herd_before = self.herd_health_snapshot(minions)
        if (
            yield from self.build.CastSkillID(
                skill_id=blood_of_the_master_id,
                log=False,
                aftercast_delay=250,
            )
        ):
            self.arm_heal_latch(blood_of_the_master_id, herd_before)
            self.blood_of_the_master_throttle.Reset()
            return True

        return False

    # endregion

    # region C
    def Contagion(
        self,
        *,
        prefer_after_skill_id: int | None = None,
        refresh_window_ms: int = 2000,
        assume_active_ms: int = 5000,
    ) -> BuildCoroutine:
        """Maintain Contagion (elite enchantment) on the caster.

        Unlike most combat skills this has no aggro gate: Contagion is kept up
        even out of combat so its condition-mirroring is ready the moment a
        condition lands on the caster.

        ``prefer_after_skill_id`` (Masochism by default) encodes a soft ordering
        preference: while in or close to aggro, if that skill is equipped but not
        yet active on the caster, Contagion is deferred for this tick so the
        other skill can go up first. Out of aggro the preference is ignored
        (the other skill would not cast there anyway), so upkeep is never
        blocked.
        """
        contagion_id: int = Skill.GetID("Contagion")

        if not self.build.IsSkillEquipped(contagion_id):
            return False

        player_agent_id = Player.GetAgentID()

        # Soft ordering preference: hold Contagion until the preferred skill
        # (Masochism) is active, but only while near aggro where that skill can
        # actually cast. Out of aggro we maintain Contagion regardless.
        if prefer_after_skill_id is None:
            prefer_after_skill_id = Skill.GetID("Masochism")
        if (
            (self.build.IsInAggro() or self.build.IsCloseToAggro())
            and self.build.IsSkillEquipped(prefer_after_skill_id)
            and not Routines.Checks.Agents.HasEffect(player_agent_id, prefer_after_skill_id)
        ):
            return False

        now_ms = int(Utils.GetBaseTimestamp())
        assumed_effects = getattr(self.build, "_self_effect_assumed_until", {})

        # Anti-spam debounce: skip while a recent cast is still assumed active
        # (covers the gap before the effect registers in the cache).
        if int(assumed_effects.get(contagion_id, 0) or 0) > now_ms:
            return False

        # Refresh window: skip when Contagion is already up with more than the
        # refresh window remaining. Cast otherwise — initial application when
        # the effect is gone, or refresh inside the last window.
        if Routines.Checks.Agents.HasEffect(player_agent_id, contagion_id):
            remaining_ms = int(
                GLOBAL_CACHE.Effects.GetEffectTimeRemaining(
                    player_agent_id,
                    contagion_id,
                )
                or 0
            )
            if remaining_ms > refresh_window_ms:
                assumed_effects.pop(contagion_id, None)
                return False

        cast_result = yield from self.build.CastSkillID(
            skill_id=contagion_id,
            log=False,
            aftercast_delay=250,
        )
        if cast_result:
            assumed_effects[contagion_id] = now_ms + max(0, int(assume_active_ms))
            setattr(self.build, "_self_effect_assumed_until", assumed_effects)
            return True

        return False

    # endregion

    # region D
    def Death_Nova(self) -> BuildCoroutine:
        death_nova_id: int = Skill.GetID("Death_Nova")
        death_nova = self.build.GetCustomSkill(death_nova_id)

        if not self.build.IsSkillEquipped(death_nova_id):
            return False

        target_agent_id = TargetMinionNonEnchanted(distance=Range.Spellcast.value)
        if not target_agent_id:
            return False

        max_health_threshold = float(death_nova.Conditions.LessLife or 1.0) if death_nova is not None else 1.0
        if Agent.GetHealth(target_agent_id) > max_health_threshold:
            return False

        return (
            yield from self.build.CastSkillIDAndRestoreTarget(
                skill_id=death_nova_id,
                target_agent_id=target_agent_id,
                log=False,
                aftercast_delay=250,
            )
        )

    def _animate_minion(self, skill_name: str, *, aftercast_delay: int = 250) -> BuildCoroutine:
        skill_id: int = Skill.GetID(skill_name)

        if not self.build.IsSkillEquipped(skill_id):
            return False

        target_corpse_id = Routines.Agents.GetNearestExploitableCorpse(
            Range.Spellcast.value,
            reserve=True,
            skill_id=skill_id,
            aftercast_delay=aftercast_delay,
        )
        if not target_corpse_id:
            return False

        return (
            yield from self.build.CastSkillIDAndRestoreTarget(
                skill_id=skill_id,
                target_agent_id=target_corpse_id,
                log=False,
                aftercast_delay=aftercast_delay,
            )
        )

    def Animate_Bone_Fiend(self) -> BuildCoroutine:
        return (yield from self._animate_minion("Animate_Bone_Fiend"))

    def Animate_Bone_Horror(self) -> BuildCoroutine:
        return (yield from self._animate_minion("Animate_Bone_Horror"))

    def Animate_Bone_Minions(self) -> BuildCoroutine:
        return (yield from self._animate_minion("Animate_Bone_Minions"))

    def Animate_Flesh_Golem(self) -> BuildCoroutine:
        return (yield from self._animate_minion("Animate_Flesh_Golem"))

    def Animate_Shambling_Horror(self) -> BuildCoroutine:
        return (yield from self._animate_minion("Animate_Shambling_Horror"))

    def Animate_Vampiric_Horror(self) -> BuildCoroutine:
        return (yield from self._animate_minion("Animate_Vampiric_Horror"))

    def Dark_Aura(
        self,
        *,
        required_profession: Profession = Profession.Necromancer,
        required_skill_id: int | None = None,
        other_ally: bool = False,
        assume_active_ms: int = 25000,
    ) -> BuildCoroutine:
        dark_aura_id: int = Skill.GetID("Dark_Aura")
        if required_skill_id is None:
            required_skill_id = Skill.GetID("Soul_Taker")

        if not self.build.IsSkillEquipped(dark_aura_id):
            return False
        if not (self.build.IsInAggro() or self.build.IsCloseToAggro()):
            return False

        target_agent_id = Routines.Targeting.TargetAllyByProfession(
            required_profession,
            required_skill_id=required_skill_id,
            other_ally=other_ally,
            filter_skill_id=dark_aura_id,
            distance=Range.Spellcast.value,
        )

        if not target_agent_id and not other_ally:
            player_agent_id = Player.GetAgentID()
            primary_profession, _ = Agent.GetProfessions(player_agent_id)
            if (
                int(primary_profession or 0) == int(required_profession)
                and self.build.IsSkillEquipped(required_skill_id)
                and not Routines.Checks.Agents.HasEffect(player_agent_id, dark_aura_id)
            ):
                target_agent_id = player_agent_id

        if not target_agent_id:
            return False

        now_ms = int(Utils.GetBaseTimestamp())
        assumed_targets = getattr(self.build, "_dark_aura_assumed_targets", {})
        if int(assumed_targets.get(target_agent_id, 0) or 0) > now_ms:
            return False

        cast_result = yield from self.build.CastSkillIDAndRestoreTarget(
            skill_id=dark_aura_id,
            target_agent_id=target_agent_id,
            log=False,
            aftercast_delay=250,
        )
        if cast_result:
            assumed_targets[target_agent_id] = now_ms + max(0, int(assume_active_ms))
            setattr(self.build, "_dark_aura_assumed_targets", assumed_targets)
            return True

        return False

    # endregion

    # region O
    def Order_of_Undeath(
        self,
        *,
        scan_range: float = Range.Earshot.value,
        min_minions: int = 3,
        min_health: float = 0.65,
        refresh_window_ms: int = 2000,
        assume_active_ms: int = 3000,
        panic_health: float = 0.30,
        resume_health: float = 0.55,
    ) -> BuildCoroutine:
        """Keep the elite up only while the caster can afford to bleed for it.

        The drain is paid per minion hit, so its cost scales with exactly the
        thing that makes the build good — a full army in aggro. Nothing in the
        game turns it off early, which makes the refresh decision the only
        control available: `min_health` is what breaks the cycle, because a
        caster who declines to refresh gets the enchantment's remaining
        duration to regenerate in.

        `min_minions` keeps it off the bar when the drain would buy nothing.
        """
        order_of_undeath_id: int = Skill.GetID("Order_of_Undeath")

        if not self.build.IsSkillEquipped(order_of_undeath_id):
            return False
        if not (self.build.IsInAggro() or self.build.IsCloseToAggro()):
            return False

        minion_count, _, _ = self.minion_herd(scan_range)
        if minion_count < min_minions:
            return False

        if self.update_health_recovery(panic_health, resume_health):
            return False

        player_agent_id = Player.GetAgentID()
        if float(Agent.GetHealth(player_agent_id)) < min_health:
            return False

        now_ms = int(Utils.GetBaseTimestamp())
        if self.order_assumed_until_ms > now_ms:
            return False

        if Routines.Checks.Agents.HasEffect(player_agent_id, order_of_undeath_id):
            remaining_ms = int(
                GLOBAL_CACHE.Effects.GetEffectTimeRemaining(
                    player_agent_id,
                    order_of_undeath_id,
                )
                or 0
            )
            if remaining_ms > refresh_window_ms:
                self.order_assumed_until_ms = 0
                return False

        if (
            yield from self.build.CastSkillID(
                skill_id=order_of_undeath_id,
                log=False,
                aftercast_delay=250,
            )
        ):
            self.order_assumed_until_ms = now_ms + max(0, int(assume_active_ms))
            return True

        return False

    # endregion

    # region P
    def Putrid_Bile(self) -> BuildCoroutine:
        putrid_bile_id: int = Skill.GetID("Putrid_Bile")
        assassins_promise_id: int = Skill.GetID("Assassins_Promise")

        if not self.build.IsSkillEquipped(putrid_bile_id):
            return False
        if not self.build.IsInAggro():
            return False

        # Snapshot alive enemies in spellcast range — used by the Assasins promise-focus
        # search and the single-target fallback.
        player_pos = Player.GetXY()
        enemy_array = AgentArray.GetEnemyArray()
        enemy_array = AgentArray.Filter.ByDistance(enemy_array, player_pos, Range.Spellcast.value)
        enemy_array = AgentArray.Filter.ByCondition(enemy_array, lambda agent_id: Agent.IsAlive(agent_id))
        if not enemy_array:
            return False

        def _has_putrid_bile(agent_id: int) -> bool:
            return putrid_bile_id in self.build.GetEffectAndBuffIds(agent_id)

        # Tier 1: live Assassins Promise-hexed enemy without Putrid Bile already up. Piggybacks
        # on the Assassins Promise focus so both hexes detonate when the target dies.
        target_agent_id = 0
        for enemy_id in enemy_array:
            effect_ids = self.build.GetEffectAndBuffIds(enemy_id)
            if assassins_promise_id in effect_ids and putrid_bile_id not in effect_ids:
                target_agent_id = enemy_id
                break

        # Tier 2: best cluster with 2+ neighbors in Range.Nearby. Anchor must
        # be < 25% HP (about to die) so the detonation pays off.
        if not target_agent_id:
            target_agent_id = Routines.Targeting.PickClusteredTarget(
                cluster_radius=Range.Nearby.value,
                preferred_condition=lambda agent_id: (
                    Routines.Targeting.CountNearbyEnemies(agent_id, Range.Nearby.value) >= 2
                    and Agent.GetHealth(agent_id) < 0.25
                    and not _has_putrid_bile(agent_id)
                ),
                filter_radius=Range.Spellcast.value,
            )

        # Tier 3: best cluster with 1+ neighbor in Range.Nearby. Anchor must
        # be < 35% HP.
        if not target_agent_id:
            target_agent_id = Routines.Targeting.PickClusteredTarget(
                cluster_radius=Range.Nearby.value,
                preferred_condition=lambda agent_id: (
                    Routines.Targeting.CountNearbyEnemies(agent_id, Range.Nearby.value) >= 1
                    and Agent.GetHealth(agent_id) < 0.35
                    and not _has_putrid_bile(agent_id)
                ),
                filter_radius=Range.Spellcast.value,
            )

        # Tier 4: any enemy < 35% HP without Putrid Bile (no cluster
        # requirement). Closest first so the cast is least likely to whiff.
        if not target_agent_id:
            candidates = [aid for aid in enemy_array if Agent.GetHealth(aid) < 0.35 and not _has_putrid_bile(aid)]
            if candidates:
                target_agent_id = sorted(
                    candidates,
                    key=lambda aid: Utils.Distance(player_pos, Agent.GetXY(aid)),
                )[0]

        if not target_agent_id:
            return False

        return (
            yield from self.build.CastSkillIDAndRestoreTarget(
                skill_id=putrid_bile_id,
                target_agent_id=target_agent_id,
                log=False,
                aftercast_delay=250,
            )
        )

    def Putrid_Explosion(self) -> BuildCoroutine:
        putrid_explosion_id: int = Skill.GetID("Putrid_Explosion")

        if not self.build.IsSkillEquipped(putrid_explosion_id):
            return False
        if not self.build.IsInAggro():
            return False

        # Tiered fallback: prefer corpses with the largest enemy-target cluster
        # around them. If no corpse has 4+ enemy targets in Range.Nearby, fall
        # through to 3+, 2+, 1+. Each tier returns the highest-scoring corpse
        # meeting its floor.
        target_corpse_id = (
            Routines.Targeting.PickClusteredEnemiesAroundCorpse(
                cluster_radius=Range.Nearby.value,
                filter_radius=Range.Spellcast.value,
                min_enemy_targets=4,
            )
            or Routines.Targeting.PickClusteredEnemiesAroundCorpse(
                cluster_radius=Range.Nearby.value,
                filter_radius=Range.Spellcast.value,
                min_enemy_targets=3,
            )
            or Routines.Targeting.PickClusteredEnemiesAroundCorpse(
                cluster_radius=Range.Nearby.value,
                filter_radius=Range.Spellcast.value,
                min_enemy_targets=2,
            )
            or Routines.Targeting.PickClusteredEnemiesAroundCorpse(
                cluster_radius=Range.Nearby.value,
                filter_radius=Range.Spellcast.value,
                min_enemy_targets=1,
            )
        )
        if not target_corpse_id:
            return False

        return (
            yield from self.build.CastSkillIDAndRestoreTarget(
                skill_id=putrid_explosion_id,
                target_agent_id=target_corpse_id,
                log=False,
                aftercast_delay=250,
            )
        )

    # endregion

    # region R
    def Rising_Bile(self) -> BuildCoroutine:
        rising_bile_id: int = Skill.GetID("Rising_Bile")

        if not self.build.IsSkillEquipped(rising_bile_id):
            return False
        if not self.build.IsInAggro():
            return False

        # Pure cluster pick: anchor with the most alive enemies in Range.Area.
        # Hard floor of 2+ neighbors (3+ total foes damaged) — Rising Bile only
        # pays off when the on-end AoE hits a real cluster. Cast as the opening
        # hex so the 20s timer accumulates maximum per-second damage.
        target_agent_id = Routines.Targeting.PickClusteredTarget(
            cluster_radius=Range.Area.value,
            preferred_condition=lambda agent_id: (
                Routines.Targeting.CountNearbyEnemies(agent_id, Range.Area.value) >= 2
                and rising_bile_id not in self.build.GetEffectAndBuffIds(agent_id)
            ),
            filter_radius=Range.Spellcast.value,
        )

        if not target_agent_id:
            return False

        return (
            yield from self.build.CastSkillIDAndRestoreTarget(
                skill_id=rising_bile_id,
                target_agent_id=target_agent_id,
                log=False,
                aftercast_delay=250,
            )
        )

    # endregion
