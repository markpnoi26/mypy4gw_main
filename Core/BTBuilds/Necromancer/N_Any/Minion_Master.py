"""Order of Undeath minion master.

Every rule here is about one resource: the caster's health. Order of Undeath
bleeds it on every minion hit and Blood of the Master — the only way to reverse
minion degeneration — costs 17% of maximum health a cast, on a 2 second
recharge. Ungoverned, the pair kills the necromancer long before it kills
anything else.

So the ladder spends health in a fixed order of desperation: rescue a minion
that is about to be lost, then keep the elite up, then grow the army (free),
then top the herd up only while comfortable. Each health-spending rung sits
behind a floor, the floors tighten while Order of Undeath is active, and a
caster who drops through the panic mark stops spending entirely until they have
climbed back to the resume mark.

Minion degeneration gets worse the longer a minion has been alive, which makes
the health bar a poor guide on its own — an old minion and a fresh one at the
same 40% are seconds and minutes from death. Every healing decision here reads
the degeneration rate instead and forecasts from it, which is also what lets the
build give up on minions it can no longer keep ahead of.
"""

from Core import BldMgrBT
from Core import Profession
from Core import Range
from Core import Routines
from Core.Builds.Skills import SkillsTemplate
from Core.Player import Player
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ...nodes import cast, cond, guarded_cast, rotation_tree

ORDER_OF_UNDEATH_ID = Skill.GetID("Order_of_Undeath")
ANIMATE_BONE_FIEND_ID = Skill.GetID("Animate_Bone_Fiend")
BLOOD_OF_THE_MASTER_ID = Skill.GetID("Blood_of_the_Master")

ANIMATE_BONE_MINIONS_ID = Skill.GetID("Animate_Bone_Minions")
ANIMATE_BONE_HORROR_ID = Skill.GetID("Animate_Bone_Horror")
ANIMATE_SHAMBLING_HORROR_ID = Skill.GetID("Animate_Shambling_Horror")
ANIMATE_VAMPIRIC_HORROR_ID = Skill.GetID("Animate_Vampiric_Horror")
ANIMATE_FLESH_GOLEM_ID = Skill.GetID("Animate_Flesh_Golem")
DEATH_NOVA_ID = Skill.GetID("Death_Nova")
MASOCHISM_ID = Skill.GetID("Masochism")
PUTRID_BILE_ID = Skill.GetID("Putrid_Bile")
PUTRID_EXPLOSION_ID = Skill.GetID("Putrid_Explosion")
SIGNET_OF_LOST_SOULS_ID = Skill.GetID("Signet_of_Lost_Souls")
EBON_BATTLE_STANDARD_OF_HONOR_ID = Skill.GetID("Ebon_Battle_Standard_of_Honor")

MINION_SCAN_RANGE = Range.Earshot.value
MINION_HURT_HEALTH = 0.75

# Hard floor between Blood of the Master casts, rescues included. The skill
# recharges in 2 seconds, which as a limit means the caster spends the fight
# sacrificing; this is the real limit.
BOTM_INTERVAL_MS = 5000

# How much of the fight goes into minion healing. Raise MIN_SECONDS_BOUGHT to
# write off dying minions sooner. INEFFECTIVE_BACKOFF_MS extends the wait past
# the floor once a cast has been seen not to help, so a minion the heal cannot
# save stops pulling casts for the rest of its life.
MIN_SECONDS_BOUGHT = 6.0
MIN_HURT_MINIONS_FOR_TOP_UP = 3
INEFFECTIVE_BACKOFF_MS = 5000

# Cast plus aftercast plus a server round trip, with room to spare — a minion
# forecast to die inside this window is dropped everything else for.
RESCUE_SECONDS = 4.0

# Only used for minions whose degeneration rate cannot be read.
MINION_CRITICAL_HEALTH = 0.40

RESCUE_MIN_HEALTH_AFTER = 0.35
TOP_UP_MIN_HEALTH_AFTER = 0.55
MIN_HEALTH_AFTER_ABSOLUTE = 100
ORDER_ACTIVE_MARGIN = 0.10

PANIC_HEALTH = 0.30
RESUME_HEALTH = 0.55

ORDER_MIN_MINIONS = 3
ORDER_MIN_HEALTH = 0.65

# Policy, not a game limit: every extra minion is another source of Order of
# Undeath hits, and the drain for those is paid out of the caster. Death Magic
# rank is a reasonable army size to bound it at.
MINION_CAP_HEADROOM = 1
FALLBACK_MINION_CAP = 8


class Minion_Master(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Minion Master",
            required_primary=Profession.Necromancer,
            required_secondary=Profession(0),
            template_code="AAAAAAAAAAAAAAAA",
            required_skills=[
                ORDER_OF_UNDEATH_ID,
                ANIMATE_BONE_FIEND_ID,
                BLOOD_OF_THE_MASTER_ID,
            ],
            optional_skills=[
                ANIMATE_BONE_MINIONS_ID,
                ANIMATE_BONE_HORROR_ID,
                ANIMATE_SHAMBLING_HORROR_ID,
                ANIMATE_VAMPIRIC_HORROR_ID,
                ANIMATE_FLESH_GOLEM_ID,
                DEATH_NOVA_ID,
                MASOCHISM_ID,
                PUTRID_BILE_ID,
                PUTRID_EXPLOSION_ID,
                SIGNET_OF_LOST_SOULS_ID,
                EBON_BATTLE_STANDARD_OF_HONOR_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def minion_cap(self) -> int:
        rank = self.skills.Necromancer.DeathMagic.death_magic_rank()
        if rank <= 0:
            return FALLBACK_MINION_CAP
        return rank + MINION_CAP_HEADROOM

    def controlled_minion_count(self) -> int:
        """Authoritative count, not a range scan.

        The cap applies to every minion bound to this necromancer, and minions
        wander well past the range the healing rules read at, so counting the
        nearby ones would keep summoning past the cap.
        """
        player_agent_id = Player.GetAgentID()
        for owner_agent_id, minion_count in Player.GetControlledMinions():
            if owner_agent_id == player_agent_id:
                return int(minion_count)
        # An empty world-context array must not read as "no minions", or the cap
        # stops capping. Fall back to counting what is in scan range.
        return len(self.skills.Necromancer.DeathMagic.own_minions(Range.Compass.value))

    def has_room_for_minion(self) -> bool:
        return self.controlled_minion_count() < self.minion_cap()

    def blood_of_the_master(self, *, rescue_only: bool):
        """Rescue pass and top-up pass share one implementation.

        The rescue pass is allowed to dig into health the top-up pass may not
        touch, because a minion that dies is gone for good — minions leave no
        exploitable corpse, so the army only ever shrinks between animates.
        """
        death_magic = self.skills.Necromancer.DeathMagic
        return (
            yield from death_magic.Blood_of_the_Master(
                scan_range=MINION_SCAN_RANGE,
                hurt_health=MINION_HURT_HEALTH,
                critical_health=MINION_CRITICAL_HEALTH,
                rescue_seconds=RESCUE_SECONDS,
                min_seconds_bought=MIN_SECONDS_BOUGHT,
                min_interval_ms=BOTM_INTERVAL_MS,
                ineffective_backoff_ms=INEFFECTIVE_BACKOFF_MS,
                min_hurt_minions=MIN_HURT_MINIONS_FOR_TOP_UP,
                rescue_only=rescue_only,
                min_health_after=TOP_UP_MIN_HEALTH_AFTER,
                emergency_min_health_after=RESCUE_MIN_HEALTH_AFTER,
                min_health_after_abs=MIN_HEALTH_AFTER_ABSOLUTE,
                order_active_margin=ORDER_ACTIVE_MARGIN,
                panic_health=PANIC_HEALTH,
                resume_health=RESUME_HEALTH,
            )
        )

    def build_rotation_tree(self) -> BehaviorTree:
        necro = lambda: self.skills.Necromancer
        anyskills = lambda: self.skills.Any
        equipped = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id))
        animate = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id) and self.has_room_for_minion())
        return rotation_tree(
            "MinionMaster",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                cast(self, "BloodOfTheMasterRescue", lambda: self.blood_of_the_master(rescue_only=True)),
                guarded_cast(
                    self,
                    "DeathNova",
                    equipped(DEATH_NOVA_ID),
                    lambda: necro().DeathMagic.Death_Nova(),
                ),
                guarded_cast(self, "Masochism", equipped(MASOCHISM_ID), lambda: necro().SoulReaping.Masochism()),
                cast(
                    self,
                    "OrderOfUndeath",
                    lambda: necro().DeathMagic.Order_of_Undeath(
                        scan_range=MINION_SCAN_RANGE,
                        min_minions=ORDER_MIN_MINIONS,
                        min_health=ORDER_MIN_HEALTH,
                        panic_health=PANIC_HEALTH,
                        resume_health=RESUME_HEALTH,
                    ),
                ),
                guarded_cast(
                    self,
                    "EbonBattleStandardOfHonor",
                    equipped(EBON_BATTLE_STANDARD_OF_HONOR_ID),
                    lambda: anyskills().NoAttribute.Ebon_Battle_Standard_of_Honor(),
                ),
                guarded_cast(
                    self,
                    "AnimateFleshGolem",
                    animate(ANIMATE_FLESH_GOLEM_ID),
                    lambda: necro().DeathMagic.Animate_Flesh_Golem(),
                ),
                guarded_cast(
                    self,
                    "AnimateBoneFiend",
                    animate(ANIMATE_BONE_FIEND_ID),
                    lambda: necro().DeathMagic.Animate_Bone_Fiend(),
                ),
                guarded_cast(
                    self,
                    "AnimateBoneMinions",
                    animate(ANIMATE_BONE_MINIONS_ID),
                    lambda: necro().DeathMagic.Animate_Bone_Minions(),
                ),
                guarded_cast(
                    self,
                    "AnimateShamblingHorror",
                    animate(ANIMATE_SHAMBLING_HORROR_ID),
                    lambda: necro().DeathMagic.Animate_Shambling_Horror(),
                ),
                guarded_cast(
                    self,
                    "AnimateVampiricHorror",
                    animate(ANIMATE_VAMPIRIC_HORROR_ID),
                    lambda: necro().DeathMagic.Animate_Vampiric_Horror(),
                ),
                guarded_cast(
                    self,
                    "AnimateBoneHorror",
                    animate(ANIMATE_BONE_HORROR_ID),
                    lambda: necro().DeathMagic.Animate_Bone_Horror(),
                ),
                cast(self, "BloodOfTheMasterTopUp", lambda: self.blood_of_the_master(rescue_only=False)),
                guarded_cast(
                    self,
                    "PutridBile",
                    equipped(PUTRID_BILE_ID),
                    lambda: necro().DeathMagic.Putrid_Bile(),
                ),
                guarded_cast(
                    self,
                    "PutridExplosion",
                    equipped(PUTRID_EXPLOSION_ID),
                    lambda: necro().DeathMagic.Putrid_Explosion(),
                ),
                guarded_cast(
                    self,
                    "SignetOfLostSouls",
                    equipped(SIGNET_OF_LOST_SOULS_ID),
                    lambda: necro().SoulReaping.Signet_of_Lost_Souls(),
                ),
                cast(self, "AutoAttack", lambda: self.AutoAttack(target_type="EnemyClustered")),
            ],
        )
