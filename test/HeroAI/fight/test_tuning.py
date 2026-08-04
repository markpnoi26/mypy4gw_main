"""Publisher-side behaviour: tuning sliders, overlay detail, enemy collection.

See .claude/skills/test-harness.md.
"""

from HeroAI.fight import health_retreat
from HeroAI.fight import publisher
from HeroAI.fight import zone

AUTHORED = publisher.AUTHORED_ZONE_CFG
TUNED_FIELDS = (
    "engagement_standoff",
    "advance_hold_ms",
    "recover_hold_tiers_ms",
    "reaim_commit_ms",
    "min_facing_recompute_ms",
)


class FakeCfg:
    """Only the two readers apply_tuning uses. A real Settings would write an INI
    into the account tree, which a test has no business doing."""

    def __init__(self, values=None):
        self.values = values or {}

    def get_float(self, section, key, default=0.0):
        return float(self.values.get(key, default))

    def get_int(self, section, key, default=0):
        return int(self.values.get(key, default))

    def get_bool(self, section, key, default=False):
        return bool(self.values.get(key, default))


def tune(values=None, times=1):
    """Apply and restore, so a slider under test cannot leak into test_zone."""
    saved = {name: getattr(zone.ZONE_CFG, name) for name in TUNED_FIELDS}
    try:
        for _ in range(times):
            publisher.apply_tuning(FakeCfg(values))
        return {name: getattr(zone.ZONE_CFG, name) for name in TUNED_FIELDS}
    finally:
        for name, value in saved.items():
            setattr(zone.ZONE_CFG, name, value)


HEALTH_AUTHORED = publisher.AUTHORED_HEALTH_CFG
HEALTH_FIELDS = ("enabled", "arm_fraction", "release_fraction", "max_steps")


def tune_health(values=None):
    """Apply and restore. HEALTH_CFG is the same object test_health_retreat reads,
    so a leak here would fail a suite that never touched a slider."""
    saved = {name: getattr(health_retreat.HEALTH_CFG, name) for name in HEALTH_FIELDS}
    try:
        publisher.apply_health_tuning(FakeCfg(values))
        return {name: getattr(health_retreat.HEALTH_CFG, name) for name in HEALTH_FIELDS}
    finally:
        for name, value in saved.items():
            setattr(health_retreat.HEALTH_CFG, name, value)


def test_an_empty_config_leaves_the_authored_numbers_alone():
    """Defaults come off AUTHORED_ZONE_CFG, so an unwritten INI is not an edit."""
    applied = tune()
    for name in TUNED_FIELDS:
        assert applied[name] == getattr(AUTHORED, name), name


def test_scales_do_not_compound_across_reloads():
    """The failure this guards is invisible for the first few seconds.

    reload_runtime fires once a second against a ZoneConfig it has already
    written to. A scale folded onto its own output instead of onto the authored
    baseline grows geometrically -- at 1.5x the recover dwell passes an hour
    inside twenty reloads -- and nothing about the tab would look wrong.
    """
    once = tune({"recover_dwell_scale": 1.5, "reaim_responsiveness": 0.5})
    many = tune({"recover_dwell_scale": 1.5, "reaim_responsiveness": 0.5}, times=20)
    assert once == many


def test_recover_scale_keeps_the_per_blob_size_tiering():
    applied = tune({"recover_dwell_scale": 1.5})
    assert applied["recover_hold_tiers_ms"] == tuple(t * 1.5 for t in AUTHORED.recover_hold_tiers_ms)
    assert len(set(applied["recover_hold_tiers_ms"])) == len(set(AUTHORED.recover_hold_tiers_ms))


def test_higher_responsiveness_shortens_both_windows():
    """The slider reads as 'snappier at higher values', so it divides."""
    snappy = tune({"reaim_responsiveness": 2.0})
    sluggish = tune({"reaim_responsiveness": 0.5})
    assert snappy["reaim_commit_ms"] < AUTHORED.reaim_commit_ms < sluggish["reaim_commit_ms"]
    assert snappy["min_facing_recompute_ms"] < AUTHORED.min_facing_recompute_ms
    assert sluggish["min_facing_recompute_ms"] > AUTHORED.min_facing_recompute_ms


def test_overlay_detail_defaults_to_full():
    assert publisher.read_overlay_detail(FakeCfg()) == publisher.OVERLAY_FULL


def test_a_saved_circles_only_setting_survives_the_new_key():
    """Migration, not a default. Someone running circles-only should not have the
    full overlay silently switched back on under them."""
    assert (
        publisher.read_overlay_detail(FakeCfg({"fight_zone_overlay_circles_only": True})) == publisher.OVERLAY_CIRCLES
    )


def test_the_detail_key_outranks_the_boolean_it_replaced():
    cfg = FakeCfg({"fight_zone_overlay_circles_only": True, "fight_zone_overlay_detail": publisher.OVERLAY_MINIMAL})
    assert publisher.read_overlay_detail(cfg) == publisher.OVERLAY_MINIMAL


def test_an_out_of_range_detail_is_clamped_to_a_real_mode():
    assert publisher.read_overlay_detail(FakeCfg({"fight_zone_overlay_detail": 99})) == publisher.OVERLAY_MINIMAL
    assert publisher.read_overlay_detail(FakeCfg({"fight_zone_overlay_detail": -5})) == publisher.OVERLAY_FULL


def test_out_of_range_values_are_clamped_not_honoured():
    """The INI is hand-editable, so the tab's range is not a guarantee."""
    applied = tune({"engagement_standoff_u": 5000.0, "advance_hold_ms": 10, "recover_dwell_scale": 99.0})
    assert applied["engagement_standoff"] == publisher.STANDOFF_MAX
    assert applied["advance_hold_ms"] == publisher.ADVANCE_HOLD_MIN
    assert applied["recover_hold_tiers_ms"] == tuple(t * publisher.SCALE_MAX for t in AUTHORED.recover_hold_tiers_ms)


class FakeAgentArray:
    def __init__(self, spirit_pets):
        self.spirit_pets = spirit_pets

    def GetSpiritPetArray(self):
        return list(self.spirit_pets)


class FakeAgent:
    def __init__(self, spawned):
        self.spawned = spawned

    def IsSpawned(self, agent_id):
        return bool(self.spawned.get(int(agent_id), False))


def with_agents(spirit_pets, spawned):
    """Swap the two native modules the collector reaches through."""
    saved = (publisher.AgentArray, publisher.Agent)
    publisher.AgentArray = FakeAgentArray(spirit_pets)
    publisher.Agent = FakeAgent(spawned)
    try:
        return publisher.collect_spirit_ids()
    finally:
        publisher.AgentArray, publisher.Agent = saved


def test_spirits_are_collected_and_pets_are_not():
    """Both share the SpiritPet allegiance; is_spawned is what separates them.

    Pets stay in the blob on purpose -- a hostile ranger's pet closes and hits,
    so it belongs there exactly as much as its owner does. A filter that took
    the whole SpiritPet array would quietly drop it.
    """
    assert with_agents([10, 11, 12], {10: True, 11: False, 12: True}) == {10, 12}


def test_no_spirits_is_an_empty_filter_not_a_crash():
    assert with_agents([], {}) == set()


def test_a_native_failure_degrades_to_filtering_nothing():
    """Better to form up on a spirit than to lose the whole enemy blob."""
    saved = publisher.AgentArray
    publisher.AgentArray = None
    try:
        assert publisher.collect_spirit_ids() == set()
    finally:
        publisher.AgentArray = saved


class DeathAwareAgent:
    """Agent stand-in reproducing the real IsDead/IsAlive asymmetry.

    Both return False when the living view cannot be resolved — that is the whole
    trap. IsValid only asks whether the agent exists, and a corpse exists.
    """

    def __init__(self, alive, corpses, unreadable):
        self.alive = set(alive)
        self.corpses = set(corpses)
        self.unreadable = set(unreadable)

    def IsValid(self, agent_id):
        return True

    def IsDead(self, agent_id):
        if agent_id in self.unreadable:
            return False
        return agent_id in self.corpses

    def IsAlive(self, agent_id):
        if agent_id in self.unreadable:
            return False
        return agent_id in self.alive


class PassThroughAgentArray:
    def __init__(self, ids):
        self.ids = list(ids)
        self.Filter = self

    def GetEnemyArray(self):
        return list(self.ids)

    def ByCondition(self, agent_array, filter_func):
        return [a for a in agent_array if filter_func(a)]

    def ByDistance(self, agent_array, pos, max_distance):
        return list(agent_array)


def collect_enemies(alive, corpses, unreadable):
    saved_agent, saved_array = publisher.Agent, publisher.AgentArray
    publisher.Agent = DeathAwareAgent(alive, corpses, unreadable)
    publisher.AgentArray = PassThroughAgentArray(list(alive) + list(corpses) + list(unreadable))
    try:
        return set(publisher.FightZonePublisher().collect_enemy_ids((0.0, 0.0)))
    finally:
        publisher.Agent, publisher.AgentArray = saved_agent, saved_array


def test_corpses_never_reach_the_blob():
    """A corpse in the enemy set holds the engagement open, re-forms the party on
    a pile of bodies and never lets the fight end."""
    assert collect_enemies(alive=[1, 2], corpses=[3, 4], unreadable=[]) == {1, 2}


def test_an_unreadable_agent_is_dropped_rather_than_kept():
    """The reason the test is IsAlive and not `not IsDead`.

    Both answer False when the living view cannot be resolved, so the NEGATION
    turns "cannot tell" into "keep it". IsValid does not save it — a corpse is a
    perfectly valid agent.
    """
    agent = DeathAwareAgent(alive=[1], corpses=[], unreadable=[9])
    assert agent.IsValid(9) and not agent.IsDead(9), "fixture must reproduce the trap"
    assert agent.IsAlive(9) is False

    assert collect_enemies(alive=[1], corpses=[], unreadable=[9]) == {1}


def test_engage_reach_defaults_to_the_authored_tip():
    assert publisher.read_engage_reach(FakeCfg()) == AUTHORED.frontline_ring_tip


def test_a_reach_saved_under_the_old_key_keeps_its_meaning():
    """The old key stored the ring's forward semi-axis measured from a fixed
    centre of 218, not the tip. Read straight across it would silently shorten
    everyone's reach by that 218."""
    assert publisher.read_engage_reach(FakeCfg({"engage_depth_u": 538.0})) == 756.0


def test_the_new_reach_key_outranks_the_old_one():
    cfg = FakeCfg({"engage_depth_u": 538.0, "engage_reach_u": 250.0})
    assert publisher.read_engage_reach(cfg) == 250.0


def test_reach_is_clamped_from_either_key():
    assert publisher.read_engage_reach(FakeCfg({"engage_reach_u": 5000.0})) == publisher.ENGAGE_REACH_MAX
    assert publisher.read_engage_reach(FakeCfg({"engage_depth_u": 4000.0})) == publisher.ENGAGE_REACH_MAX
    assert publisher.read_engage_reach(FakeCfg({"engage_reach_u": 10.0})) == publisher.ENGAGE_REACH_MIN


def test_an_unreadable_map_id_is_not_a_zone_change():
    """GetMapID returns 0 whenever the map is not ready, and IsMapReady can go
    false for a frame during ordinary play. Treating that as a change wipes the
    breadcrumb trail mid-fight and takes the long escape route with it."""
    assert publisher.map_changed(400, 0) is False
    assert publisher.map_changed(0, 0) is False


def test_a_real_zone_change_is_detected_in_both_directions():
    assert publisher.map_changed(400, 401) is True
    assert publisher.map_changed(0, 400) is True, "first sighting after a load must reset too"
    assert publisher.map_changed(400, 400) is False


def test_resetting_map_state_drops_every_map_local_reading():
    """Breadcrumbs and the safe spot are raw world coordinates. Carried across a
    zone they plot a route through geometry that belongs to the map we left —
    and the route moves the party now, so it is a walk into a wall."""
    pub = publisher.FightZonePublisher()
    pub.breadcrumbs.points.append((100.0, 200.0))
    pub.safe_spot.xy = (300.0, 400.0)
    pub.zone.retreat_steps.append((-250.0, 0.0))
    pub.zone.advance = 500.0
    pub.zone.state = zone.ZoneState.HOLDING
    pub.last_approach_xy = (1.0, 2.0)

    pub.reset_map_state()

    assert pub.breadcrumbs.points == []
    assert pub.safe_spot.xy is None
    assert pub.zone.retreat_steps == []
    assert pub.zone.advance == 0.0
    assert pub.zone.state == zone.ZoneState.TRAVELING
    assert pub.last_approach_xy is None


def test_health_retreat_is_off_until_it_is_switched_on():
    assert tune_health()["enabled"] is False
    assert tune_health({"health_retreat_enabled": True})["enabled"] is True


def test_an_empty_config_leaves_the_health_defaults_alone():
    applied = tune_health()
    for name in HEALTH_FIELDS:
        assert applied[name] == getattr(HEALTH_AUTHORED, name), name


def test_thresholds_are_stored_as_percentages_and_applied_as_fractions():
    """The INI is hand-edited, so it holds 60 rather than 0.6. Reading a
    percentage straight into the controller would arm it at 6000% and never."""
    applied = tune_health({"health_retreat_arm": 40.0, "health_retreat_release": 65.0})
    assert abs(applied["arm_fraction"] - 0.40) < 0.0001
    assert abs(applied["release_fraction"] - 0.65) < 0.0001


def test_the_release_threshold_cannot_be_dragged_onto_the_arm_threshold():
    """The gap IS the release condition. Collapsed, health sitting on the
    threshold refills the budget every dwell and the retreat ratchets again —
    which is the entire failure the budget exists to prevent."""
    applied = tune_health({"health_retreat_arm": 60.0, "health_retreat_release": 60.0})
    gap = (applied["release_fraction"] - applied["arm_fraction"]) * 100.0
    assert gap >= publisher.HEALTH_RELEASE_MIN_GAP - 0.0001, "gap collapsed to %.2f" % gap


def test_the_gap_is_measured_against_the_saved_arm_not_the_default():
    """An arm dragged above the default release is the case that breaks a floor
    computed from the authored numbers."""
    applied = tune_health({"health_retreat_arm": 85.0, "health_retreat_release": 70.0})
    gap = (applied["release_fraction"] - applied["arm_fraction"]) * 100.0
    assert gap >= publisher.HEALTH_RELEASE_MIN_GAP - 0.0001, "gap collapsed to %.2f" % gap


def test_the_thresholds_stay_inside_their_own_range():
    high = tune_health({"health_retreat_arm": 400.0, "health_retreat_release": 400.0})
    assert high["arm_fraction"] <= publisher.HEALTH_ARM_MAX / 100.0
    assert high["release_fraction"] <= publisher.HEALTH_RELEASE_MAX / 100.0
    low = tune_health({"health_retreat_arm": -50.0})
    assert abs(low["arm_fraction"] - (publisher.HEALTH_ARM_MIN / 100.0)) < 0.0001


def test_the_budget_is_clamped_to_at_least_one_step():
    """Zero would publish an armed retreat that can never step — a HOLD veto with
    no withdrawal behind it, which reads as the feature being broken."""
    assert tune_health({"health_retreat_steps": 0})["max_steps"] == publisher.HEALTH_STEPS_MIN
    assert tune_health({"health_retreat_steps": 99})["max_steps"] == publisher.HEALTH_STEPS_MAX
