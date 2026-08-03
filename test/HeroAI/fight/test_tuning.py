"""Publisher-side behaviour: tuning sliders, overlay detail, enemy collection.

See .claude/skills/test-harness.md.
"""

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
