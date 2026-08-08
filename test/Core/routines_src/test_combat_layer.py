# Combat-layer filter mechanics. Pure logic — no natives touched.
# Loaded by path so the eager Core facade stays out of the test.

import pathload

combat_layer = pathload.load('Core/routines_src/CombatLayer.py')


def teardown_function():
    combat_layer.SetEnemyPoolFilter(None)


def test_within_tolerance_is_same_layer():
    assert combat_layer.IsSameCombatLayer(0, 100.0, 0, 300.0, 250.0) is True


def test_boundary_is_inclusive():
    assert combat_layer.IsSameCombatLayer(0, 0.0, 0, 250.0, 250.0) is True
    assert combat_layer.IsSameCombatLayer(0, 0.0, 0, 250.001, 250.0) is False


def test_zplane_match_does_not_bypass_tolerance():
    # both on plane 0, 880 apart — the Tunnels-of-the-Forsaken case
    assert combat_layer.IsSameCombatLayer(0, 0.0, 0, 880.0, 250.0) is False


def test_zplane_mismatch_does_not_force_a_split():
    assert combat_layer.IsSameCombatLayer(0, 10.0, 3, 12.0, 250.0) is True


def test_non_finite_samples_fail_open():
    assert combat_layer.IsSameCombatLayer(0, float('nan'), 0, 900.0, 10.0) is True
    assert combat_layer.IsSameCombatLayer(0, 0.0, 0, float('inf'), 10.0) is True


def test_negative_tolerance_clamps_to_zero():
    assert combat_layer.IsSameCombatLayer(0, 5.0, 0, 5.0, -100.0) is True
    assert combat_layer.IsSameCombatLayer(0, 5.0, 0, 6.0, -100.0) is False


def test_filter_runs_before_ranking():
    # an ineligible nearest candidate must not mask a valid farther one
    kept = combat_layer.FilterByCombatLayer([1, 2, 3], lambda agent_id: agent_id != 1)
    assert kept == [2, 3]


def test_pool_filter_is_identity_when_unregistered():
    assert combat_layer.ApplyEnemyPoolFilter([1, 2]) == [1, 2]


def test_pool_filter_applies_provider():
    combat_layer.SetEnemyPoolFilter(lambda ids: [i for i in ids if i > 1])
    assert combat_layer.ApplyEnemyPoolFilter([1, 2, 3]) == [2, 3]


def test_pool_filter_fails_open_when_provider_raises():
    def boom(ids):
        raise RuntimeError('provider exploded')

    combat_layer.SetEnemyPoolFilter(boom)
    assert combat_layer.ApplyEnemyPoolFilter([1, 2]) == [1, 2]
