# HeroAI's policy half of the combat-layer filter: settings gate, player reference, filtering.
# Imports the real module (nativestub serves the natives) and monkeypatches the
# Core surfaces it reads, per test/Core/FrameTree/test_child_of_guard.py.

import HeroAI.layer_targeting as layer_targeting
from Core.routines_src import CombatLayer


class FakeSettings:
    def __init__(self, enabled=True, tolerance=250.0):
        self.LayerAwareTargeting = enabled
        self.CombatLayerZTolerance = tolerance


def stub_world(monkeypatch, settings, player_id=1, valid=True, heights=None):
    heights = heights or {}
    monkeypatch.setattr(layer_targeting, 'Settings', lambda: settings)
    monkeypatch.setattr(layer_targeting.Player, 'GetAgentID', staticmethod(lambda: player_id))
    monkeypatch.setattr(layer_targeting.Agent, 'IsValid', staticmethod(lambda agent_id: valid))
    monkeypatch.setattr(layer_targeting.Agent, 'GetZPlane', staticmethod(lambda agent_id: 0))
    monkeypatch.setattr(
        layer_targeting.Agent, 'GetXYZ', staticmethod(lambda agent_id: (0.0, 0.0, heights.get(agent_id, 0.0)))
    )


def test_disabled_toggle_passes_the_pool_through(monkeypatch):
    stub_world(monkeypatch, FakeSettings(enabled=False), heights={2: 5000.0})
    assert layer_targeting.enemy_pool_filter([2]) == [2]


def test_invalid_player_passes_the_pool_through(monkeypatch):
    stub_world(monkeypatch, FakeSettings(), valid=False, heights={2: 5000.0})
    assert layer_targeting.enemy_pool_filter([2]) == [2]


def test_missing_player_id_passes_the_pool_through(monkeypatch):
    stub_world(monkeypatch, FakeSettings(), player_id=0, heights={2: 5000.0})
    assert layer_targeting.enemy_pool_filter([2]) == [2]


def test_enemies_beyond_tolerance_are_dropped(monkeypatch):
    stub_world(monkeypatch, FakeSettings(tolerance=250.0), heights={1: 0.0, 2: 100.0, 3: 880.0, 4: -900.0})
    assert layer_targeting.enemy_pool_filter([2, 3, 4]) == [2]


def test_install_registers_with_the_core_mechanism(monkeypatch):
    stub_world(monkeypatch, FakeSettings(tolerance=10.0), heights={1: 0.0, 2: 5.0, 3: 500.0})
    try:
        layer_targeting.InstallLayerFilter()
        assert CombatLayer.ApplyEnemyPoolFilter([2, 3]) == [2]
    finally:
        CombatLayer.SetEnemyPoolFilter(None)
