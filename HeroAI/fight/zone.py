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
    # How far the retreat axis may pull the blob axis. Past this the two
    # disagree enough that something is wrong with the reading — being ganked
    # from behind is the obvious case — and the enemies win outright.
    max_approach_blend_rad: float = math.radians(60.0)
    # Weight of the retreat axis inside that cone. Small: the blob decides where
    # "in front" is, the retreat only biases which way the backline sits.
    approach_blend: float = 0.35
    # A blob centroid closer than this to the party centroid gives no usable
    # direction. It sits INSIDE the formation, so the bearing to it is decided
    # by which enemy happens to have shuffled, and one step flips it by a
    # half-turn. The formation then rotates about its own middle and the
    # backline — a full 620u out along -facing — is thrown bodily through
    # whatever is standing there.
    #
    # Sized against the default formation: the party centroid sits ~310u behind
    # the front line, so enemies in honest melee contact are right at this
    # distance. Holding the last good axis through contact is correct anyway —
    # the fight is where it already is, and re-deriving a heading from a
    # centroid standing on top of you is how the spin starts.
    min_facing_baseline: float = float(Range.Area.value)
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
    # Measured from the contact point, which is at the FRONT. The known cost is
    # that an enemy which walked around onto the backline reads as a new threat
    # elsewhere and drives a full re-aim, interrupting casts.
    #
    # The standing candidate fix — measure from the party centroid instead — is
    # REJECTED. Measuring from the front is what gives the formation its fallback
    # under a rush: enemies that close onto the party stay outside this radius,
    # so they still count as approaching, the engagement point follows them onto
    # the party, and engagement_standoff then plants the pin behind it. The party
    # gives ground and the backline gets its range back. Measuring from the party
    # centroid filters exactly those enemies out and the fallback collapses (413u
    # of ground given vs 173u, ending in front of the party rather than behind
    # it). Bounded by construction at max_anchor_offset_from_leader +
    # engagement_standoff from the leader, so it cannot rout.
    contact_radius: float = float(Range.Area.value)

    # Small blobs are unstable by construction: removing one of N shifts the
    # centroid by (centroid - dead) / (N - 1), so at N=3 a mob 300u out from the
    # centre moves it 150u on death, against 43u at N=8. Movement scales the same
    # way, so the tail of every fight is its twitchiest phase.
    #
    # Refusing to re-aim below a size threshold traded that twitch for a worse
    # failure: the last one or two enemies pull away, the pin is frozen where the
    # pack died, and the party fights the rest of it out of formation. Instead
    # the timing gates stretch as the blob shrinks — the formation keeps
    # re-forming all the way to the last kill, just progressively more slowly.
    #
    # The two gates are tiered separately because they charge for jitter in
    # different currencies. The floor is nearly free: it costs nothing on the
    # first move after a quiet spell and only caps how often a twitchy tail can
    # drag the formation, so it carries most of the slowdown. The commit window
    # delays every genuine relocation by its full length, so it stays close to
    # flat — a lone enemy that really walked off is answered in a few seconds,
    # not ten. Index 0 is a lone enemy; the last entry covers every size at or
    # above its own.
    reaim_commit_slowdown_tiers: tuple[float, ...] = (3.0, 2.5, 2.0, 1.0)
    reaim_floor_slowdown_tiers: tuple[float, ...] = (6.0, 4.0, 2.5, 1.0)
    # Distance still catches the one case angle cannot see — a mob retreating
    # straight down the axis, where the bearing never changes but the fight has
    # walked away. Deliberately large: this is for relocation, not jitter.
    facing_rehome_distance: float = 700.0
    # The deviation must persist this long before it counts. Kills reaction to
    # a mob shuffling through the threshold and back.
    reaim_commit_ms: int = 1500
    # Hard floor on how often the formation may be re-aimed at all.
    min_facing_recompute_ms: int = 4000
    # Blob sizes that force a re-aim the instant the fight shrinks past them,
    # ahead of every gate above.
    #
    # The tiers make the tail of a fight the SLOWEST phase by construction — one
    # enemy buys a 4.5s confirm and a 24s floor — and it is also the phase where
    # the survivors are most likely to be somewhere else entirely. Damping is
    # the right answer while a pack is being fought and the wrong one once it is
    # down to stragglers: the party holds formation while two casters plink at
    # it from across the room. Crossing DOWN through a size is the trigger, so
    # this fires twice a fight, not every tick, and a fresh wave re-arms it.
    force_reaim_at_sizes: tuple[int, ...] = (3, 1)

    # Average party health that says the fight is going badly enough to trade
    # ground for it.
    give_ground_health: float = 0.60
    # ...and the mark it must recover to before the party stops giving more. A
    # single threshold oscillates: a heal lands, the formation steps up, the
    # same spike lands again. The gap is what makes it a decision rather than a
    # tremor. Between the two marks the party keeps what it has already given
    # and takes no more.
    hold_ground_health: float = 0.80
    # One step per interval, so the party walks back and the mob follows in a
    # column. Giving the whole budget at once abandons a fight that was being
    # won and outruns the enemies instead of gathering them.
    give_ground_step: float = 150.0
    give_ground_interval_ms: int = 1500
    # Ceiling on top of engagement_standoff. The pin is separately bounded at
    # max_anchor_offset_from_leader from the leader, so this cannot rout.
    max_extra_standoff: float = 500.0


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
    # Size of the blob the last re-aim test ran against, and the two windows that
    # size bought it. Reported, not used — the overlay has no other way to say
    # why a stale-looking pin is behaving correctly.
    reaim_blob_size: int = 0
    reaim_commit_window_ms: float = 0.0
    reaim_floor_ms: float = 0.0
    # Blob size at the previous measurement, so a threshold can be detected as a
    # crossing. A level test ("size <= 3") would re-aim on every tick of the
    # endgame and never let a cast finish.
    last_blob_size: int = 0
    forced_reaim_count: int = 0
    # Current distance the pin sits behind the contact point. Starts at
    # engagement_standoff and ratchets outward while the party is losing.
    standoff: float = 0.0
    last_give_ground_ms: int = 0
    giving_ground: bool = False

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
    retreat_axis: float | None = None,
) -> float:
    """Blob axis, nudged toward the way out, inside the cone.

    Shared with should_reaim on purpose: comparing a blended current facing
    against an unblended proposal would leave a permanent offset the size of the
    blend, and the gate would either never fire or fire constantly.
    """
    if math.hypot(blob_centre[0] - party_xy[0], blob_centre[1] - party_xy[1]) < cfg.min_facing_baseline:
        # Nothing to re-derive from. Hold the last good heading — unless there
        # is none, on which fresh zones depend, where a noisy axis still beats
        # whichever direction the previous fight happened to end on.
        if zone.last_facing_target is not None:
            return zone.facing

    blob_axis = math.atan2(blob_centre[1] - party_xy[1], blob_centre[0] - party_xy[0])

    # The rear of the formation should sit on the ground we intend to give, so
    # the escape route -- reversed, because it points outward -- is the better
    # reference. It falls back to the walked-in approach when no route is
    # plotted (no navmesh, or boxed in), which is what this always used.
    if retreat_axis is not None:
        advance_axis = retreat_axis + math.pi
    elif approach_xy is not None:
        advance_axis = math.atan2(engagement_xy[1] - approach_xy[1], engagement_xy[0] - approach_xy[0])
    else:
        return blob_axis

    if angle_difference(advance_axis, blob_axis) <= cfg.max_approach_blend_rad:
        return blend_angle(blob_axis, advance_axis, cfg.approach_blend)
    return blob_axis


def anchor_and_facing(
    zone: FightZone,
    cfg: ZoneConfig,
    leader_xy: tuple[float, float],
    party_xy: tuple[float, float],
    approach_xy: tuple[float, float] | None,
    enemy_positions: list[tuple[float, float]],
    now_ms: int,
    retreat_axis: float | None = None,
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
        zone.facing = compute_axis(zone, cfg, party_xy, approach_xy, blob_centre, engagement, retreat_axis)
        zone.last_facing_target = blob_centre
        zone.last_facing_ms = now_ms
    else:
        latch_facing(zone, engagement, approach_xy, leader_xy, enemy_positions, now_ms)

    zone.reaim_pending_since_ms = 0
    zone.engagement_x, zone.engagement_y = engagement
    # Ground already given is KEPT across a re-aim while the party is still
    # hurt. Resetting to base here would march it back into the mob every time
    # the fight re-forms — and the forced re-aims at three and one enemies land
    # in exactly the stretch where it is most likely to still be losing.
    if not zone.giving_ground:
        zone.standoff = cfg.engagement_standoff
    return apply_standoff(engagement, zone.facing, zone.standoff)


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
    from. Falls back to leader->enemies when the last safe spot sits too close
    to describe an approach (fresh zone-in, or ambushed while standing still).
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


def tier_for(tiers: tuple[float, ...], blob_size: int) -> float:
    if not tiers:
        return 1.0
    return tiers[min(max(blob_size, 1), len(tiers)) - 1]


def crossed_force_threshold(cfg: ZoneConfig, previous: int, current: int) -> bool:
    """Did the blob just shrink DOWN through one of the forced sizes?

    `previous <= 0` never counts: that is the first measurement of a fight, and
    arriving at a size is not the same as falling to it.
    """
    if previous <= 0:
        return False
    return any((previous > size) and (current <= size) for size in cfg.force_reaim_at_sizes)


def reaim_windows(cfg: ZoneConfig, blob_size: int) -> tuple[float, float]:
    """Commit window and recompute floor, in ms, for a blob of this size."""
    return (
        cfg.reaim_commit_ms * tier_for(cfg.reaim_commit_slowdown_tiers, blob_size),
        cfg.min_facing_recompute_ms * tier_for(cfg.reaim_floor_slowdown_tiers, blob_size),
    )


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
        zone.reaim_blob_size = 0
        return False

    # Counted on the still-approaching set rather than every enemy alive: that
    # set is what the re-aim maths is measured against, so it is the one whose
    # instability matters.
    blob = resolve_engagement_blob(cfg, inputs.party_xy, approaching)
    zone.reaim_blob_size = len(blob)
    commit_window, recompute_floor = reaim_windows(cfg, len(blob))
    zone.reaim_commit_window_ms = commit_window
    zone.reaim_floor_ms = recompute_floor

    # Measured before the geometry test, and deliberately not updated in the
    # no-approaching-enemies branch above: an empty approach set means the mob
    # closed to contact, not that it died, and treating that as a shrink would
    # spend the force on a pack that is already on top of the front line.
    forced = crossed_force_threshold(cfg, zone.last_blob_size, len(blob))
    zone.last_blob_size = len(blob)

    blob_centre = centroid(blob)
    if blob_centre is None:
        zone.reaim_pending_since_ms = 0
        return False

    # Ahead of the swing/drift test, not just the timers. Killing one of three
    # moves the blob centroid ~150u, which is real movement the lateral gate is
    # built to ignore — so waiting for geometry to argue for it defeats the
    # point of forcing.
    if forced:
        zone.reaim_pending_since_ms = 0
        zone.forced_reaim_count += 1
        return True

    engagement = clamp_toward(blob_centre, inputs.leader_xy, cfg.max_anchor_offset_from_leader)
    proposed = compute_axis(
        zone, cfg, inputs.party_xy, inputs.approach_xy, blob_centre, engagement, inputs.retreat_axis
    )
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
    if (now_ms - zone.reaim_pending_since_ms) < commit_window:
        return False
    # A floor, not a rejection: pending is deliberately left standing so the
    # re-aim fires the moment the floor expires rather than restarting the
    # confirmation from scratch.
    if (now_ms - zone.last_facing_ms) < recompute_floor:
        return False

    zone.reaim_pending_since_ms = 0
    return True


def give_ground(zone: FightZone, cfg: ZoneConfig, inputs: "ZoneInputs") -> None:
    """Trade ground for survival, one step at a time, while the party is losing.

    Straight back along -facing, and that is the whole point rather than an
    approximation: retreating down the axis the party already faces drags the
    mob into a column in front of it. Enemies working the flanks have to come
    round to follow, so being overwhelmed collapses back into one fight with a
    front. Sliding sideways along the escape bearing would spill them around
    the edges instead. The rear already leans toward the escape route through
    compute_axis, so -facing IS the way out, inside the blend cone.

    Recomputed from the STORED engagement point, not a fresh one: the contact
    point is where the fight was when the pin was placed, and holding it is
    what makes each step a retreat from that line rather than a fresh reading
    of a mob that is busy chasing us.
    """
    if inputs.party_health_avg >= cfg.hold_ground_health:
        zone.giving_ground = False
        return
    # Between the marks: keep what has been given, take no more. Pushing back up
    # the moment a heal lands is how the formation ends up yo-yoing on a spike.
    if inputs.party_health_avg > cfg.give_ground_health:
        return
    if (inputs.now_ms - zone.last_give_ground_ms) < cfg.give_ground_interval_ms:
        return

    ceiling = cfg.engagement_standoff + cfg.max_extra_standoff
    if zone.standoff >= ceiling:
        zone.giving_ground = True
        return

    zone.standoff = min(ceiling, zone.standoff + cfg.give_ground_step)
    zone.last_give_ground_ms = inputs.now_ms
    zone.giving_ground = True
    zone.anchor_x, zone.anchor_y = apply_standoff((zone.engagement_x, zone.engagement_y), zone.facing, zone.standoff)


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
    # Where the party walked in from. None when it sits too close to say.
    approach_xy: tuple[float, float] | None = None
    # Bearing of the plotted escape route, pointing OUTWARD from the party.
    # The formation's rear is aimed along it so backing up follows the way out.
    retreat_axis: float | None = None
    # Mean health fraction across the party. 1.0 when nothing is known, so a
    # missing reading never argues for retreating.
    party_health_avg: float = 1.0


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
            zone.last_blob_size = 0
            # A new fight starts at full standoff. The abandon-and-redrop below
            # deliberately does NOT reset this: that path is the same fight
            # continuing somewhere else, and ground given to it still counts.
            zone.giving_ground = False
            zone.anchor_x, zone.anchor_y = anchor_and_facing(
                zone,
                cfg,
                inputs.leader_xy,
                inputs.party_xy,
                inputs.approach_xy,
                inputs.enemy_positions,
                now,
                inputs.retreat_axis,
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
            zone.last_blob_size = 0
            zone.anchor_x, zone.anchor_y = anchor_and_facing(
                zone,
                cfg,
                inputs.leader_xy,
                inputs.party_xy,
                inputs.approach_xy,
                inputs.enemy_positions,
                now,
                inputs.retreat_axis,
            )
            enter_state(zone, ZoneState.ENGAGING, now)
            return zone
        enter_state(zone, ZoneState.TRAVELING, now)
        return zone

    if zone.state == ZoneState.ENGAGING:
        if should_reaim(zone, cfg, inputs):
            zone.anchor_x, zone.anchor_y = anchor_and_facing(
                zone,
                cfg,
                inputs.leader_xy,
                inputs.party_xy,
                inputs.approach_xy,
                inputs.enemy_positions,
                now,
                inputs.retreat_axis,
            )
        give_ground(zone, cfg, inputs)
        if not inputs.party_in_aggro:
            enter_state(zone, ZoneState.CLEARING, now)
            return zone
        if inputs.members_in_position or (now - zone.entered_state_ms) >= cfg.engage_timeout_ms:
            enter_state(zone, ZoneState.HOLDING, now)
        return zone

    if zone.state == ZoneState.HOLDING:
        if should_reaim(zone, cfg, inputs):
            zone.anchor_x, zone.anchor_y = anchor_and_facing(
                zone,
                cfg,
                inputs.leader_xy,
                inputs.party_xy,
                inputs.approach_xy,
                inputs.enemy_positions,
                now,
                inputs.retreat_axis,
            )
        give_ground(zone, cfg, inputs)
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
