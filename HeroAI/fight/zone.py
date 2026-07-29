"""Fight zone lifecycle: an auto-dropped party pin with a state machine."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum

from Core import Range


class ZoneState(IntEnum):
    TRAVELING = 0
    ENGAGING = 1
    HOLDING = 2
    CLEARING = 3


@dataclass(slots=True)
class ZoneConfig:
    # How far from the pin still counts as "in the zone".
    engage_radius: float = float(Range.Spellcast.value)
    # Leader beyond this from the pin means the pin is stale. Matches
    # FOLLOW_RECOVERY_DISTANCE so zone abandon and follow recovery agree.
    abandon_distance: float = float(Range.Spirit.value)
    # The pin is placed at the enemy centroid but never dragged further than
    # this from the leader — a stray far-off enemy must not yank the party.
    max_anchor_offset_from_leader: float = 600.0
    # How far BACK along the approach the pin sits from the engagement point.
    # Anchoring on the enemy centroid itself plants the front line inside the
    # mob and drags every line forward with it, so the backline ends up doing
    # its work at midline range. Standing off keeps the whole formation behind
    # the contact point, where it was authored to be.
    engagement_standoff: float = 400.0
    # Enemies within this of each other are one blob. A mob group is what you
    # fight; the centroid of two separate groups points at empty ground between
    # them.
    blob_weld_distance: float = 500.0
    # How far the approach axis may pull the blob axis. Past this the two
    # disagree enough that something is wrong with the approach reading — being
    # ganked from behind is the obvious case — and the enemies win outright.
    max_approach_blend_rad: float = math.radians(60.0)
    # Weight of the approach axis inside that cone. Small: the blob decides
    # where "in front" is, the approach only biases which way the backline sits.
    approach_blend: float = 0.35
    # Enemies further than this from the leader are not part of this engagement.
    engagement_scan_radius: float = float(Range.Spellcast.value)
    # ENGAGING gives up and holds anyway; a blocked member must not wedge the party.
    engage_timeout_ms: int = 6000
    # Re-aim gate. Angle rather than distance is the primary test because
    # distance gets scale backwards: a blob 2000u away sliding 400u barely moves
    # the axis, while the same 400u at close range flips it.
    reaim_angle_threshold: float = math.radians(28.0)
    # Angle alone is scale-invariant, which is backwards up close: 100u of
    # shuffle is 27deg at 200u range but 5deg at 1200u, so melee fights re-aim
    # far more than ranged ones. Requiring a real lateral displacement too makes
    # the two gates cover each other's blind spot — near-field jitter fails the
    # lateral test, far-field drift fails the angle test.
    reaim_min_lateral: float = 250.0
    # Enemies this close to the contact point have ARRIVED. A melee mob closing
    # on the front line is not the fight moving somewhere else — it is the fight
    # working exactly as expected — so it must not drag the formation around.
    #
    # OPEN — measured from the contact point, which is at the FRONT. An enemy
    # that walked around onto the backline reads as a new threat elsewhere and
    # drives a full re-aim; the melee then follow, and the loop repeats without
    # converging. Costly because movement interrupts casting, so the backline
    # stops healing exactly when it is being focused. Candidate fix: measure
    # from the party centroid instead. Under observation.
    contact_radius: float = float(Range.Area.value)

    # Small blobs are unstable by construction: removing one of N shifts the
    # centroid by (centroid - dead) / (N - 1), so at N=3 a mob 300u out from the
    # centre moves it 150u on death, against 43u at N=8. Movement scales the same
    # way. The tail of every fight is therefore its twitchiest phase, and
    # re-aiming there buys nothing — the fight is already won. Fewer than this
    # many still-approaching enemies and the formation just holds what it has.
    reaim_min_blob_size: int = 3
    # Distance still catches the one case angle cannot see — a mob retreating
    # straight down the axis, where the bearing never changes but the fight has
    # walked away. Deliberately large: this is for relocation, not jitter.
    facing_rehome_distance: float = 700.0
    # The deviation must persist this long before it counts. Kills reaction to
    # a mob shuffling through the threshold and back.
    reaim_commit_ms: int = 1500
    # Hard floor on how often the formation may be re-aimed at all.
    min_facing_recompute_ms: int = 4000


ZONE_CFG = ZoneConfig()


@dataclass(slots=True)
class FightZone:
    state: ZoneState = ZoneState.TRAVELING
    anchor_x: float = 0.0
    anchor_y: float = 0.0
    facing: float = 0.0
    radius: float = 0.0
    entered_state_ms: int = 0
    last_facing_target: tuple[float, float] | None = None
    last_facing_ms: int = 0
    # Point of contact. The pin sits engagement_standoff behind this.
    engagement_x: float = 0.0
    engagement_y: float = 0.0
    # When the re-aim deviation first went over threshold. 0 = not pending.
    reaim_pending_since_ms: int = 0

    def is_active(self) -> bool:
        return self.state != ZoneState.TRAVELING

    def anchor(self) -> tuple[float, float]:
        return (self.anchor_x, self.anchor_y)


def centroid(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    if not points:
        return None
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )


def clamp_toward(
    point: tuple[float, float],
    origin: tuple[float, float],
    max_distance: float,
) -> tuple[float, float]:
    dx = point[0] - origin[0]
    dy = point[1] - origin[1]
    distance = math.hypot(dx, dy)
    if distance <= max_distance or distance <= 0.001:
        return (float(point[0]), float(point[1]))
    scale = max_distance / distance
    return (origin[0] + (dx * scale), origin[1] + (dy * scale))


def resolve_anchor(
    cfg: ZoneConfig,
    leader_xy: tuple[float, float],
    enemy_positions: list[tuple[float, float]],
) -> tuple[float, float]:
    """Pin goes where the fight is, not where the leader is — but never so far
    that one distant enemy drags the whole party off the leader."""
    enemy_centroid = centroid(enemy_positions)
    if enemy_centroid is None:
        return (float(leader_xy[0]), float(leader_xy[1]))
    return clamp_toward(enemy_centroid, leader_xy, cfg.max_anchor_offset_from_leader)


def blend_angle(previous: float, target: float, alpha: float) -> float:
    """Shortest-arc blend. Averaging raw angles wraps at +-pi."""
    sin_part = (math.sin(previous) * (1.0 - alpha)) + (math.sin(target) * alpha)
    cos_part = (math.cos(previous) * (1.0 - alpha)) + (math.cos(target) * alpha)
    if abs(sin_part) < 1e-9 and abs(cos_part) < 1e-9:
        return target
    return math.atan2(sin_part, cos_part)


def angle_difference(left: float, right: float) -> float:
    return abs(math.atan2(math.sin(left - right), math.cos(left - right)))


def cluster_enemies(
    positions: list[tuple[float, float]],
    weld_distance: float,
) -> list[list[tuple[float, float]]]:
    """Group enemies whose weld radii overlap into blobs (union-find)."""
    count = len(positions)
    if count == 0:
        return []
    parent = list(range(count))

    def find(index: int) -> int:
        root = index
        while parent[root] != root:
            root = parent[root]
        while parent[index] != root:
            parent[index], index = root, parent[index]
        return root

    threshold = weld_distance * weld_distance
    for i in range(count):
        for j in range(i + 1, count):
            dx = positions[i][0] - positions[j][0]
            dy = positions[i][1] - positions[j][1]
            if (dx * dx) + (dy * dy) < threshold:
                root_i, root_j = find(i), find(j)
                if root_i != root_j:
                    parent[root_i] = root_j

    groups: dict[int, list[tuple[float, float]]] = {}
    for i in range(count):
        groups.setdefault(find(i), []).append(positions[i])
    return list(groups.values())


def select_engagement_blob(
    clusters: list[list[tuple[float, float]]],
    origin: tuple[float, float],
) -> list[tuple[float, float]] | None:
    """The blob we are actually fighting: the one with the nearest member."""
    best: list[tuple[float, float]] | None = None
    best_distance = float("inf")
    for cluster in clusters:
        for x, y in cluster:
            distance = math.hypot(x - origin[0], y - origin[1])
            if distance < best_distance:
                best_distance = distance
                best = cluster
    return best


def apply_standoff(
    engagement_xy: tuple[float, float],
    facing: float,
    standoff: float,
) -> tuple[float, float]:
    """Pull the pin back down the approach axis from the point of contact."""
    return (
        engagement_xy[0] - (math.cos(facing) * standoff),
        engagement_xy[1] - (math.sin(facing) * standoff),
    )


def resolve_engagement_blob(
    cfg: ZoneConfig,
    party_xy: tuple[float, float],
    enemy_positions: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    blob = select_engagement_blob(cluster_enemies(enemy_positions, cfg.blob_weld_distance), party_xy)
    return list(blob) if blob else list(enemy_positions)


def compute_axis(
    zone: FightZone,
    cfg: ZoneConfig,
    party_xy: tuple[float, float],
    approach_xy: tuple[float, float] | None,
    blob_centre: tuple[float, float],
    engagement_xy: tuple[float, float],
) -> float:
    """Blob axis, nudged by the approach inside the cone.

    Shared with should_reaim on purpose: comparing a blended current facing
    against an unblended proposal would leave a permanent offset the size of the
    blend, and the gate would either never fire or fire constantly.
    """
    if math.hypot(blob_centre[0] - party_xy[0], blob_centre[1] - party_xy[1]) < 0.001:
        return zone.facing
    blob_axis = math.atan2(blob_centre[1] - party_xy[1], blob_centre[0] - party_xy[0])
    if approach_xy is not None:
        approach_axis = math.atan2(engagement_xy[1] - approach_xy[1], engagement_xy[0] - approach_xy[0])
        if angle_difference(approach_axis, blob_axis) <= cfg.max_approach_blend_rad:
            return blend_angle(blob_axis, approach_axis, cfg.approach_blend)
    return blob_axis


def anchor_and_facing(
    zone: FightZone,
    cfg: ZoneConfig,
    leader_xy: tuple[float, float],
    party_xy: tuple[float, float],
    approach_xy: tuple[float, float] | None,
    enemy_positions: list[tuple[float, float]],
    now_ms: int,
) -> tuple[float, float]:
    """Blob -> axis -> pin, computed only when the flag is (re)placed.

    The axis is taken from the party toward the nearest enemy blob, so that blob
    is in front of the pin by construction and front/mid/back project back from
    it. The approach path then nudges that axis inside a cone — it decides which
    way the backline leans, not where the front is. Outside the cone the two
    disagree too much to reconcile (ganked from behind is the obvious case) and
    the enemies win outright: facing the wrong way is worse than an odd retreat
    direction.
    """
    blob_centre = centroid(resolve_engagement_blob(cfg, party_xy, enemy_positions))

    engagement = (
        clamp_toward(blob_centre, leader_xy, cfg.max_anchor_offset_from_leader)
        if blob_centre is not None
        else (float(leader_xy[0]), float(leader_xy[1]))
    )

    if blob_centre is not None:
        zone.facing = compute_axis(zone, cfg, party_xy, approach_xy, blob_centre, engagement)
        zone.last_facing_target = blob_centre
        zone.last_facing_ms = now_ms
    else:
        latch_facing(zone, engagement, approach_xy, leader_xy, enemy_positions, now_ms)

    zone.reaim_pending_since_ms = 0
    zone.engagement_x, zone.engagement_y = engagement
    return apply_standoff(engagement, zone.facing, cfg.engagement_standoff)


def compute_zone_facing(
    zone: FightZone,
    engagement_xy: tuple[float, float],
    approach_xy: tuple[float, float] | None,
    leader_xy: tuple[float, float],
    enemy_positions: list[tuple[float, float]],
) -> float:
    """The axis of advance: from where the party walked in, toward the fight.

    That is what puts the backline behind the party rather than wherever the
    leader happens to be facing — the retreat direction is the one they came
    from. Falls back to leader->enemies when the trail is too short to describe
    an approach (fresh zone-in, or the leader was standing still).
    """
    if approach_xy is not None:
        dx = engagement_xy[0] - approach_xy[0]
        dy = engagement_xy[1] - approach_xy[1]
        if math.hypot(dx, dy) >= 0.001:
            return math.atan2(dy, dx)

    enemy_centroid = centroid(enemy_positions)
    if enemy_centroid is not None:
        dx = enemy_centroid[0] - leader_xy[0]
        dy = enemy_centroid[1] - leader_xy[1]
        if math.hypot(dx, dy) >= 0.001:
            return math.atan2(dy, dx)

    return zone.facing


def should_reaim(zone: FightZone, cfg: ZoneConfig, inputs: "ZoneInputs") -> bool:
    """Has the fight moved enough, for long enough, to be worth re-forming?

    Measured against the SELECTED BLOB, recomputed exactly as anchoring does.
    Comparing an all-enemy centroid against a stored blob centroid compares two
    different quantities: with two mob groups they sit permanently hundreds of
    units apart, so the test fires forever and every firing re-clamps the anchor
    to wherever the leader is standing.
    """
    now_ms = inputs.now_ms
    if zone.last_facing_target is None:
        return False

    # Enemies that have already reached the contact point tell us nothing about
    # where to stand — they are where we expected them. Judging on the ones
    # still out there is what stops a charging melee mob from walking the
    # formation backwards as it closes.
    approaching = [
        position
        for position in inputs.enemy_positions
        if math.hypot(position[0] - zone.engagement_x, position[1] - zone.engagement_y) > cfg.contact_radius
    ]
    if not approaching:
        zone.reaim_pending_since_ms = 0
        return False

    # Counted on the still-approaching set rather than every enemy alive: that
    # set is what the re-aim maths is measured against, so it is the one whose
    # instability matters.
    blob = resolve_engagement_blob(cfg, inputs.party_xy, approaching)
    if len(blob) < cfg.reaim_min_blob_size:
        zone.reaim_pending_since_ms = 0
        return False

    blob_centre = centroid(blob)
    if blob_centre is None:
        zone.reaim_pending_since_ms = 0
        return False

    engagement = clamp_toward(blob_centre, inputs.leader_xy, cfg.max_anchor_offset_from_leader)
    proposed = compute_axis(zone, cfg, inputs.party_xy, inputs.approach_xy, blob_centre, engagement)
    swing = angle_difference(proposed, zone.facing)
    drift = math.hypot(
        blob_centre[0] - zone.last_facing_target[0],
        blob_centre[1] - zone.last_facing_target[1],
    )

    # Lateral component of the drift — how far the blob moved ACROSS the axis
    # rather than along it. Movement straight down the axis is a mob closing or
    # retreating, which never justifies turning the formation.
    lateral = drift * math.sin(min(swing, math.pi / 2.0))
    swung = swing >= cfg.reaim_angle_threshold and lateral >= cfg.reaim_min_lateral
    if not swung and drift < cfg.facing_rehome_distance:
        zone.reaim_pending_since_ms = 0
        return False

    if zone.reaim_pending_since_ms == 0:
        zone.reaim_pending_since_ms = now_ms
        return False
    if (now_ms - zone.reaim_pending_since_ms) < cfg.reaim_commit_ms:
        return False
    if (now_ms - zone.last_facing_ms) < cfg.min_facing_recompute_ms:
        return False

    zone.reaim_pending_since_ms = 0
    return True


def latch_facing(
    zone: FightZone,
    engagement_xy: tuple[float, float],
    approach_xy: tuple[float, float] | None,
    leader_xy: tuple[float, float],
    enemy_positions: list[tuple[float, float]],
    now_ms: int,
) -> None:
    zone.facing = compute_zone_facing(zone, engagement_xy, approach_xy, leader_xy, enemy_positions)
    zone.last_facing_target = centroid(enemy_positions)
    zone.last_facing_ms = now_ms


@dataclass(slots=True)
class ZoneInputs:
    leader_xy: tuple[float, float]
    enemy_positions: list[tuple[float, float]]
    party_in_aggro: bool
    leader_local_aggro: bool
    loot_pending: bool
    members_in_position: bool
    now_ms: int
    # Centre of mass of the party. The blob axis is measured from here.
    party_xy: tuple[float, float] = (0.0, 0.0)
    # Where the party walked in from. None when the trail is too short to say.
    approach_xy: tuple[float, float] | None = None


def enter_state(zone: FightZone, state: ZoneState, now_ms: int) -> None:
    if zone.state == state:
        return
    zone.state = state
    zone.entered_state_ms = now_ms


def tick_zone(zone: FightZone, cfg: ZoneConfig, inputs: ZoneInputs) -> FightZone:
    now = inputs.now_ms

    if zone.state == ZoneState.TRAVELING:
        if inputs.party_in_aggro:
            zone.radius = cfg.engage_radius
            zone.last_facing_target = None
            zone.anchor_x, zone.anchor_y = anchor_and_facing(
                zone, cfg, inputs.leader_xy, inputs.party_xy, inputs.approach_xy, inputs.enemy_positions, now
            )
            enter_state(zone, ZoneState.ENGAGING, now)
        return zone

    leader_distance = math.hypot(
        inputs.leader_xy[0] - zone.anchor_x,
        inputs.leader_xy[1] - zone.anchor_y,
    )

    # Leader has left the pin behind. Still fighting means the engagement moved
    # with them, so re-drop the pin rather than falling back to plain follow —
    # breaking off mid-fight drags an aggro train.
    if leader_distance > cfg.abandon_distance:
        if inputs.party_in_aggro and inputs.leader_local_aggro:
            zone.last_facing_target = None
            zone.anchor_x, zone.anchor_y = anchor_and_facing(
                zone, cfg, inputs.leader_xy, inputs.party_xy, inputs.approach_xy, inputs.enemy_positions, now
            )
            enter_state(zone, ZoneState.ENGAGING, now)
            return zone
        enter_state(zone, ZoneState.TRAVELING, now)
        return zone

    if zone.state == ZoneState.ENGAGING:
        if should_reaim(zone, cfg, inputs):
            zone.anchor_x, zone.anchor_y = anchor_and_facing(
                zone, cfg, inputs.leader_xy, inputs.party_xy, inputs.approach_xy, inputs.enemy_positions, now
            )
        if not inputs.party_in_aggro:
            enter_state(zone, ZoneState.CLEARING, now)
            return zone
        if inputs.members_in_position or (now - zone.entered_state_ms) >= cfg.engage_timeout_ms:
            enter_state(zone, ZoneState.HOLDING, now)
        return zone

    if zone.state == ZoneState.HOLDING:
        if should_reaim(zone, cfg, inputs):
            zone.anchor_x, zone.anchor_y = anchor_and_facing(
                zone, cfg, inputs.leader_xy, inputs.party_xy, inputs.approach_xy, inputs.enemy_positions, now
            )
        if not inputs.party_in_aggro:
            enter_state(zone, ZoneState.CLEARING, now)
        return zone

    # CLEARING: hold the pin through looting so nobody wanders off mid-pickup.
    if inputs.party_in_aggro:
        enter_state(zone, ZoneState.ENGAGING, now)
        return zone
    if not inputs.loot_pending:
        enter_state(zone, ZoneState.TRAVELING, now)
    return zone
