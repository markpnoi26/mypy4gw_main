"""Leader-side fight zone driver.

Rides the existing follow channel rather than new shared-memory fields: the
region is allocated and owned by the C++ multibox module, so HeroAIOptionStruct
cannot grow without a matching native change. During a fight the leader swaps
the anchor, the formation and the per-slot tolerance; followers are unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import field

import HeroAI.globals as hero_globals
from Core import Agent
from Core import AgentArray
from Core import Range
from Core import ThrottledTimer
from Core.py4gwcorelib_src.Settings import Settings

from .assignment import AssignmentLatch
from .assignment import MemberLine
from .breadcrumbs import BREADCRUMB_CFG
from .breadcrumbs import Breadcrumbs
from .breadcrumbs import sample as sample_breadcrumbs
from .engagement import ENGAGEMENT_CFG
from .engagement import EngagementState
from .engagement import update_engagement
from .escape import ESCAPE_CFG
from .escape import EscapeState
from .escape import TerrainProbe
from .escape import plot_escape
from .formation import CAST_RANGE
from .formation import FightFormationLoader
from .formation import rotate_fight_local_to_world
from .lines import CombatLine
from .lines import LineSource
from .lines import ResolvedLine
from .lines import get_manual_line
from .lines import infer_line_from_profession
from .safespot import SAFE_CFG
from .safespot import SafeSpot
from .safespot import approach_from
from .safespot import update_safe_spot
from .zone import ZONE_CFG
from .zone import FightZone
from .zone import ZoneConfig
from .zone import ZoneInputs
from .zone import ZoneState
from .zone import backline_ring
from .zone import blob_depth
from .zone import centroid
from .zone import frontline_reached
from .zone import frontline_ring
from .zone import given_ground
from .zone import midline_ring
from .zone import overrun
from .zone import resolve_engagement_blob
from .zone import tick_zone

RUNTIME_INI_PATH = "HeroAI"
RUNTIME_INI_NAME = "FightRuntime.ini"
RUNTIME_SECTION = "FightRuntime"
ENABLED_KEY = "fight_zone_enabled"
OVERLAY_KEY = "show_fight_zone_overlay"
CIRCLES_ONLY_KEY = "fight_zone_overlay_circles_only"
OVERLAY_DETAIL_KEY = "fight_zone_overlay_detail"
# FULL diagnoses, MINIMAL is for actually fighting: the planted flag, the three
# trigger rings, and the escape path. Nothing per-character, nothing labelled —
# the standing circle and tolerance ring around every member is the bulk of what
# makes the overlay unreadable in a fight, and it says nothing the party's own
# bodies do not already show.
OVERLAY_FULL = 0
OVERLAY_CIRCLES = 1
OVERLAY_MINIMAL = 2
OVERLAY_DETAIL_NAMES = ("Full", "Armed circles", "Minimal")
# Drives the frontline ring's TIP: how far ahead the party will still walk to
# find a fight. A new key because the meaning changed — the old one stored the
# ring's forward semi-axis measured from a fixed centre of 218, so reading a
# saved value straight into the tip would quietly shorten everyone's reach by
# that 218. LEGACY_RING_CENTRE converts instead, and is frozen at the historical
# value on purpose: it must not follow the config.
ENGAGE_DEPTH_KEY = "engage_depth_u"
ENGAGE_REACH_KEY = "engage_reach_u"
LEGACY_RING_CENTRE = 218.0
# Floor is below the authored 300 so the reach can be tuned down as well as up.
# Ceiling keeps the ring inside the scan radius.
ENGAGE_REACH_MIN = 150.0
ENGAGE_REACH_MAX = 900.0
RUNTIME_RELOAD_MS = 1000

# --- Fight Lines tuning sliders ---------------------------------------------
# Ceiling is the standoff this replaced, so the old behaviour is still reachable
# from the tab rather than needing a code change.
STANDOFF_KEY = "engagement_standoff_u"
STANDOFF_MIN = 0.0
STANDOFF_MAX = 400.0
# Floor is roughly run speed at a 250u step; anything quicker is a slide, not a
# sequence of deliberate moves, and the whole ground controller assumes moves.
ADVANCE_HOLD_KEY = "advance_hold_ms"
ADVANCE_HOLD_MIN = 750
ADVANCE_HOLD_MAX = 4000
RECOVER_DWELL_KEY = "recover_dwell_scale"
REAIM_RESPONSE_KEY = "reaim_responsiveness"
SCALE_MIN = 0.5
SCALE_MAX = 2.0

# The authored numbers, captured before anything writes over them. Sliders scale
# THESE and never ZONE_CFG's current contents: reload_runtime runs once a second
# against a config it has already written to, so a scale folded back onto its own
# output compounds — a 1.5x recover dwell reaches an hour inside twenty minutes.
AUTHORED_ZONE_CFG = ZoneConfig()


def clamp(value, low, high):
    return min(high, max(low, value))


def read_engage_reach(cfg: Settings) -> float:
    """Frontline reach, converting a value saved under the old key."""
    reach = float(cfg.get_float(RUNTIME_SECTION, ENGAGE_REACH_KEY, 0.0))
    if reach <= 0.0:
        legacy = float(cfg.get_float(RUNTIME_SECTION, ENGAGE_DEPTH_KEY, 0.0))
        reach = (LEGACY_RING_CENTRE + legacy) if legacy > 0.0 else AUTHORED_ZONE_CFG.frontline_ring_tip
    return clamp(reach, ENGAGE_REACH_MIN, ENGAGE_REACH_MAX)


def read_overlay_detail(cfg: Settings) -> int:
    """Detail level, falling back to the boolean this replaced.

    A saved circles-only setting keeps meaning something rather than silently
    reverting to the full overlay the first time the tab is opened.
    """
    legacy = OVERLAY_CIRCLES if cfg.get_bool(RUNTIME_SECTION, CIRCLES_ONLY_KEY, False) else OVERLAY_FULL
    return int(clamp(int(cfg.get_int(RUNTIME_SECTION, OVERLAY_DETAIL_KEY, legacy)), OVERLAY_FULL, OVERLAY_MINIMAL))


@dataclass(slots=True)
class FightRuntimeConfig:
    # Default off: the whole feature is opt-in so travel behaviour is untouched
    # until it has been watched through the overlay.
    enabled: bool = False
    show_overlay: bool = False
    # One of OVERLAY_FULL / OVERLAY_CIRCLES / OVERLAY_MINIMAL. Drives what the
    # snapshot even builds, not just what gets drawn: the per-slot list is the
    # expensive half, so a mode that cannot show it does not pay for it.
    overlay_detail: int = OVERLAY_FULL


def apply_tuning(cfg: Settings) -> None:
    """Fold the Fight Lines sliders onto the shared ZoneConfig.

    Everything here is derived from AUTHORED_ZONE_CFG, never read back off
    ZONE_CFG — see the note on that constant.
    """
    standoff = float(cfg.get_float(RUNTIME_SECTION, STANDOFF_KEY, AUTHORED_ZONE_CFG.engagement_standoff))
    ZONE_CFG.engagement_standoff = clamp(standoff, STANDOFF_MIN, STANDOFF_MAX)

    # Applied onto the shared config so the whole controller — triggers,
    # snapshot, drawn ring — reads the tuned value with no plumbing.
    ZONE_CFG.frontline_ring_tip = read_engage_reach(cfg)

    advance = int(cfg.get_int(RUNTIME_SECTION, ADVANCE_HOLD_KEY, AUTHORED_ZONE_CFG.advance_hold_ms))
    ZONE_CFG.advance_hold_ms = int(clamp(advance, ADVANCE_HOLD_MIN, ADVANCE_HOLD_MAX))

    # Scales the whole per-blob-size table rather than replacing it: the tail of
    # a fight giving ground most rarely is authored behaviour, and a flat dwell
    # would throw it away.
    recover = clamp(float(cfg.get_float(RUNTIME_SECTION, RECOVER_DWELL_KEY, 1.0)), SCALE_MIN, SCALE_MAX)
    ZONE_CFG.recover_hold_tiers_ms = tuple(tier * recover for tier in AUTHORED_ZONE_CFG.recover_hold_tiers_ms)

    # Reads as "snappier at higher values", so it DIVIDES the two windows. Both
    # move together because they are one feel — a short confirm behind a long
    # floor just waits in a different place.
    response = clamp(float(cfg.get_float(RUNTIME_SECTION, REAIM_RESPONSE_KEY, 1.0)), SCALE_MIN, SCALE_MAX)
    ZONE_CFG.reaim_commit_ms = int(AUTHORED_ZONE_CFG.reaim_commit_ms / response)
    ZONE_CFG.min_facing_recompute_ms = int(AUTHORED_ZONE_CFG.min_facing_recompute_ms / response)


def collect_spirit_ids() -> set[int]:
    """Spirits, which are not part of the fight blob.

    A spirit does not move, so a centroid that counts one is anchored to a
    stationary object: the formation gets planted on ground the mob has already
    left, and every re-aim drags it back there. The pack is what the party is
    fighting, and the pack walks.

    Read off the SpiritPet array rather than by testing every enemy: spirits and
    pets share that allegiance and are separated by `is_spawned`, so this costs
    one native call plus a check per spirit instead of a check per enemy. Pets
    are deliberately left in — a hostile ranger's pet closes and hits, so it
    belongs in the blob exactly as much as its owner does.

    Likely already a no-op: allegiance buckets an agent into exactly one array,
    so a spirit should never appear in the enemy array to begin with. Those
    arrays are filled by Py4GW.dll, which cannot be inspected from this tree, so
    the filter stands as the thing that makes it true rather than assuming it.
    """
    try:
        return {int(agent_id) for agent_id in AgentArray.GetSpiritPetArray() if Agent.IsSpawned(int(agent_id))}
    except Exception:
        return set()


def mean_party_health(party_health: dict[int, float]) -> float:
    """1.0 when nothing is known: an absent reading must never argue for retreat."""
    if not party_health:
        return 1.0
    return sum(party_health.values()) / len(party_health)


def party_centroid(
    leader_xy: tuple[float, float],
    member_positions: list[tuple[int, tuple[float, float]]],
) -> tuple[float, float]:
    points = [xy for _, xy in member_positions]
    points.append((float(leader_xy[0]), float(leader_xy[1])))
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )


@dataclass(slots=True)
class SlotPlan:
    party_position: int
    character_name: str
    # `line` is the pin's line, which drives tolerance. `requested_line` is the
    # line the member resolved to. They differ when overflow spilled someone —
    # which is exactly the "why is my monk standing in the midline" question.
    line: CombatLine
    requested_line: CombatLine
    source: LineSource
    pin_index: int
    world_x: float
    world_y: float
    tolerance: float


@dataclass(slots=True)
class FightPlan:
    zone: FightZone
    slots: dict[int, SlotPlan] = field(default_factory=dict)
    depth_clamped: bool = False

    def is_active(self) -> bool:
        return self.zone.is_active()


class FightZonePublisher:
    def __init__(self) -> None:
        self.zone = FightZone()
        self.safe_spot = SafeSpot()
        self.breadcrumbs = Breadcrumbs()
        self.engagement = EngagementState()
        self.escape = EscapeState()
        self.last_approach_xy: tuple[float, float] | None = None
        self.formation_loader = FightFormationLoader()
        self.assignment = AssignmentLatch()
        self.runtime = FightRuntimeConfig()
        self.runtime_timer = ThrottledTimer(RUNTIME_RELOAD_MS)
        self.plan = FightPlan(zone=self.zone)
        self.build_lines_by_character: dict[str, CombatLine] = {}
        self.resolved_by_character: dict[str, ResolvedLine] = {}
        self.last_party_health: dict[int, float] = {}
        self.last_zone_inputs: ZoneInputs | None = None

    def runtime_cfg(self) -> Settings:
        return Settings(f"{RUNTIME_INI_PATH}/{RUNTIME_INI_NAME}", "global")

    def reload_runtime(self) -> None:
        if not self.runtime_timer.IsExpired():
            return
        self.runtime_timer.Reset()
        try:
            cfg = self.runtime_cfg()
            try:
                cfg.reload()
            except Exception:
                pass
            self.runtime.enabled = bool(cfg.get_bool(RUNTIME_SECTION, ENABLED_KEY, False))
            self.runtime.show_overlay = bool(cfg.get_bool(RUNTIME_SECTION, OVERLAY_KEY, False))
            self.runtime.overlay_detail = read_overlay_detail(cfg)
            apply_tuning(cfg)
            hero_globals.show_fight_zone_overlay = self.runtime.show_overlay
            hero_globals.fight_zone_overlay_detail = self.runtime.overlay_detail
        except Exception:
            pass

    def report_build_line(self, character_name: str, line: CombatLine) -> None:
        """Followers report their build-declared line; only their own client knows
        which build matched. Cached until the character reports something else."""
        key = str(character_name or "").strip()
        if key:
            self.build_lines_by_character[key] = line

    def resolve_member_line(self, character_name: str, primary_profession: int) -> ResolvedLine:
        key = str(character_name or "").strip()
        manual = get_manual_line(character_name)
        if manual != CombatLine.AUTO:
            resolved = ResolvedLine(manual, LineSource.MANUAL)
        else:
            declared = self.build_lines_by_character.get(key, CombatLine.AUTO)
            if declared != CombatLine.AUTO:
                resolved = ResolvedLine(declared, LineSource.BUILD)
            else:
                resolved = ResolvedLine(infer_line_from_profession(primary_profession), LineSource.INFERRED)
        # Cached so the UI reports the same answer the assigner used. Recomputing
        # it there would miss build declarations, which only live in this cache.
        self.resolved_by_character[key] = resolved
        return resolved

    def collect_enemy_ids(self, leader_xy: tuple[float, float]) -> list[int]:
        try:
            ids = AgentArray.GetEnemyArray()
        except Exception:
            return []
        ids = AgentArray.Filter.ByCondition(
            ids,
            lambda aid: Agent.IsValid(int(aid)) and not Agent.IsDead(int(aid)),
        )
        ids = AgentArray.Filter.ByDistance(ids, leader_xy, float(ZONE_CFG.engagement_scan_radius))
        return [int(agent_id) for agent_id in ids]

    def collect_enemy_positions(self, enemy_ids: list[int]) -> list[tuple[float, float]]:
        positions: list[tuple[float, float]] = []
        for agent_id in enemy_ids:
            try:
                x, y = Agent.GetXY(int(agent_id))
            except Exception:
                continue
            if abs(float(x)) < 0.001 and abs(float(y)) < 0.001:
                continue
            positions.append((float(x), float(y)))
        return positions

    def members_in_position(self, members: list[tuple[int, tuple[float, float]]]) -> bool:
        if not self.plan.slots:
            return False
        for party_position, member_xy in members:
            slot = self.plan.slots.get(int(party_position))
            if slot is None:
                continue
            if math.hypot(member_xy[0] - slot.world_x, member_xy[1] - slot.world_y) > slot.tolerance:
                return False
        return True

    def tick(
        self,
        leader_xy: tuple[float, float],
        member_lines: list[MemberLine],
        member_positions: list[tuple[int, tuple[float, float]]],
        party_in_aggro: bool,
        leader_local_aggro: bool,
        loot_pending: bool,
        now_ms: int,
        party_health: dict[int, float] | None = None,
        party_target_ids: dict[int, int] | None = None,
        terrain_probe: TerrainProbe | None = None,
    ) -> FightPlan:
        party_health = party_health or {}
        party_target_ids = party_target_ids or {}
        self.last_party_health = party_health
        self.reload_runtime()

        # Track the PARTY centroid, not the leader. A leader jinks back and
        # forth while pulling and repositioning; the blob as a whole travels in
        # one coherent direction, which is the axis the formation wants.
        party_centre = party_centroid(leader_xy, member_positions)
        # Runs before the dry-run bail so the latch keeps following the party
        # while the feature is off. Otherwise the first fight after switching it
        # on would remember wherever the party happened to be at that moment.
        update_safe_spot(self.safe_spot, SAFE_CFG, party_centre, party_in_aggro)
        # NOT recorded during a fight. Crumbs dropped while withdrawing sit
        # between the party and the enemies, and a route walked back through
        # them leads into the mob before it leads out. Same gate as the safe
        # spot, for the same reason: only quiet ground describes a way out.
        if not party_in_aggro:
            sample_breadcrumbs(self.breadcrumbs, BREADCRUMB_CFG, party_centre)

        # Dry run: with the feature off but the overlay on, the zone is still
        # computed and drawn, it just never becomes an active plan. That is the
        # whole point of watching it before switching it on.
        if not (self.runtime.enabled or self.runtime.show_overlay):
            self.zone.state = ZoneState.TRAVELING
            self.assignment.clear()
            self.escape.clear()
            self.plan = FightPlan(zone=self.zone)
            hero_globals.fight_zone_debug_snapshot = None
            return self.plan

        enemy_ids = self.collect_enemy_ids(leader_xy)
        # Spirits are dropped from the BLOB but left in `enemy_ids`, which is
        # what the engagement detector reads. A spirit hitting the party is
        # still a fight — it just is not a thing to form up on.
        spirits = collect_spirit_ids()
        enemy_positions = self.collect_enemy_positions([i for i in enemy_ids if i not in spirits])
        self.last_approach_xy = approach_from(self.safe_spot, SAFE_CFG, party_centre)
        was_active = self.zone.is_active()

        # Proximity is not a fight, and it must not be able to end one either:
        # AND-ing party_in_aggro here would let a momentary loss of scan range
        # tear the zone down instantly, defeating the disengage hold. The
        # detector applies its own, tighter distance gate.
        engaged = update_engagement(
            self.engagement,
            ENGAGEMENT_CFG,
            leader_xy,
            enemy_ids,
            party_health,
            party_target_ids,
            now_ms,
        )

        # Plotted from the party centre, not the pin, and BEFORE the zone ticks:
        # the route still places nobody, but the formation's rear is aimed along
        # it, so the pin has to be able to read this tick's route rather than
        # last tick's. Nothing here depends on the zone, so there is no cycle.
        if engaged or self.zone.is_active():
            plot_escape(
                self.escape,
                ESCAPE_CFG,
                party_centre,
                enemy_positions,
                self.safe_spot.xy,
                now_ms,
                probe=terrain_probe,
                trail=self.breadcrumbs,
            )
        else:
            self.escape.clear()

        formation = self.formation_loader.get()

        zone_inputs = ZoneInputs(
            leader_xy=leader_xy,
            enemy_positions=enemy_positions,
            party_in_aggro=engaged,
            leader_local_aggro=leader_local_aggro,
            loot_pending=loot_pending,
            members_in_position=self.members_in_position(member_positions),
            now_ms=now_ms,
            party_xy=party_centre,
            approach_xy=self.last_approach_xy,
            retreat_axis=self.escape.route.axis if self.escape.route is not None else None,
            retreat_path=list(self.escape.route.path) if self.escape.route is not None else [],
            retreat_distance=self.escape.route.distance if self.escape.route is not None else 0.0,
            midline_depth=formation.midline_depth(),
            backline_depth=formation.backline_depth(),
        )
        tick_zone(self.zone, ZONE_CFG, zone_inputs)
        self.last_zone_inputs = zone_inputs

        if not self.zone.is_active():
            if was_active:
                self.assignment.clear()
            self.escape.clear()
            self.plan = FightPlan(zone=self.zone)
            hero_globals.fight_zone_debug_snapshot = None
            return self.plan

        assignment = self.assignment.get(formation, member_lines)

        slots: dict[int, SlotPlan] = {}
        for member in member_lines:
            pin_index = assignment.pin_for(member.party_position)
            if pin_index is None or pin_index >= len(formation.pins):
                continue
            pin = formation.pins[pin_index]
            world_x, world_y = rotate_fight_local_to_world(pin.x, pin.y, self.zone.facing)
            resolved = self.resolved_by_character.get(
                str(member.character_name or "").strip(),
                ResolvedLine(member.line, LineSource.INFERRED),
            )
            slots[int(member.party_position)] = SlotPlan(
                party_position=int(member.party_position),
                character_name=member.character_name,
                line=pin.line,
                requested_line=member.line,
                source=resolved.source,
                pin_index=pin_index,
                world_x=self.zone.anchor_x + world_x,
                world_y=self.zone.anchor_y + world_y,
                tolerance=max(float(Range.Adjacent.value), formation.tolerances.get(pin.line)),
            )

        self.plan = FightPlan(
            zone=self.zone,
            slots=slots,
            depth_clamped=self.formation_loader.depth_was_clamped,
        )
        self.publish_debug_snapshot(formation.depth(), formation.worst_case_separation())
        if not self.runtime.enabled:
            return FightPlan(zone=FightZone())
        return self.plan

    def publish_debug_snapshot(self, depth: float, worst_case: float = 0.0) -> None:
        if not self.plan.is_active():
            hero_globals.fight_zone_debug_snapshot = None
            return
        # The Fight Lines tab still wants the scalars with the overlay off, but
        # only the 3D draw needs the per-slot list, which is what costs.
        drawing = self.runtime.show_overlay
        detail = self.runtime.overlay_detail
        # Full is the only mode that labels anything or shows where the party
        # came from; the other two are for watching a fight, not reading one.
        labelled = drawing and detail == OVERLAY_FULL
        # Minimal drops every per-character marker — the clutter it exists to
        # remove — and so never pays to build the list.
        slotted = drawing and detail != OVERLAY_MINIMAL
        # Armed-circles keeps the escape WAYPOINT without the dogleg to it;
        # minimal wants the path itself, which is one of its three things.
        routed = drawing and detail != OVERLAY_CIRCLES
        route = self.escape.route
        # The two readings the ground controller acts on, recomputed against the
        # post-tick pin so the drawn blob and midline are the ones the NEXT
        # decision will be judged by.
        inputs = self.last_zone_inputs
        blob_centre = None
        blob_front_depth = None
        past_midline = False
        closing_armed = False
        # The rings the triggers actually enforce, not the raw rank depths —
        # what is drawn must be what the blob is judged against.
        rings: dict[str, tuple[float, float, float]] = {}
        if inputs is not None:
            blob_centre = centroid(resolve_engagement_blob(ZONE_CFG, inputs.party_xy, inputs.enemy_positions))
            blob_front_depth = blob_depth(self.zone, ZONE_CFG, inputs)
            past_midline = overrun(self.zone, ZONE_CFG, inputs)
            closing_armed = not frontline_reached(self.zone, ZONE_CFG, inputs)
            rings = {
                name: (ring.centre, ring.fwd, ring.lat)
                for name, ring in (
                    ("backline", backline_ring(ZONE_CFG, inputs)),
                    ("midline", midline_ring(ZONE_CFG, inputs)),
                    ("frontline", frontline_ring(ZONE_CFG)),
                )
            }
        hero_globals.fight_zone_debug_snapshot = {
            "state": self.zone.state.name,
            "enabled": self.runtime.enabled,
            "driving": self.runtime.enabled and self.plan.is_active(),
            "anchor": (self.zone.anchor_x, self.zone.anchor_y),
            "facing": self.zone.facing,
            "approach": self.last_approach_xy if labelled else None,
            "detail": detail,
            "radius": self.zone.radius,
            "reaim_blob_size": self.zone.reaim_blob_size,
            "reaim_commit_ms": self.zone.reaim_commit_window_ms,
            "reaim_floor_ms": self.zone.reaim_floor_ms,
            "forced_reaims": self.zone.forced_reaim_count,
            "giving_ground": self.zone.giving_ground,
            "closing": self.zone.closing,
            "given_ground": given_ground(self.zone),
            "advance": self.zone.advance,
            "party_health": mean_party_health(self.last_party_health),
            "blob": blob_centre,
            "blob_depth": blob_front_depth,
            "rings": rings,
            "overrun": past_midline,
            "breached": self.zone.breached,
            "closing_armed": closing_armed,
            "escape": (
                None
                if route is None
                else {
                    "from": route.origin,
                    "waypoint": route.waypoint,
                    "distance": route.distance,
                    "source": route.source.name,
                    "path": list(route.path) if routed else (),
                }
            ),
            "escape_boxed_in": self.escape.boxed_in,
            "escape_terrain_known": self.escape.terrain_known,
            "depth": depth,
            "worst_case": worst_case,
            "cast_range": CAST_RANGE,
            "depth_clamped": self.plan.depth_clamped,
            "slots": [
                {
                    "name": slot.character_name,
                    "line": slot.line.name,
                    "requested_line": slot.requested_line.name,
                    "spilled": slot.line != slot.requested_line,
                    "pos": (slot.world_x, slot.world_y),
                    "tolerance": slot.tolerance,
                }
                for slot in (self.plan.slots.values() if slotted else ())
            ],
            "slot_count": len(self.plan.slots),
        }
