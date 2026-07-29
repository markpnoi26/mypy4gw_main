"""Velocity-aware follow steering: lead the formation slot instead of chasing it."""

from __future__ import annotations

import math
from dataclasses import dataclass

from Core import Agent


@dataclass(slots=True)
class SteeringConfig:
    # Below this the leader counts as stationary: heading is held rather than
    # recomputed, because atan2 over sub-unit deltas is pure noise.
    moving_speed_threshold: float = 40.0
    min_sample_ms: int = 40
    max_sample_ms: int = 700
    # A jump larger than this inside one sample is a zone or rubber-band, not
    # running — discard it instead of inferring a 5000u/s heading from it.
    max_sample_distance: float = 600.0
    heading_smoothing: float = 0.35
    speed_smoothing: float = 0.3
    # Aim this far ahead of the slot, in seconds of leader travel. Covers the
    # reissue interval, ACTION queue throttle and server round trip. Never
    # load-bearing: a wrong value costs smoothness, it cannot decide whether the
    # follower moves at all.
    lead_seconds: float = 0.45
    # Extra lead proportional to how far behind the slot we are, so closing the
    # gap is a converging diagonal rather than a stern chase.
    catchup_gain: float = 0.5
    max_catchup_lead: float = 400.0
    max_lead_distance: float = 600.0
    moving_throttle_ms: int = 50
    idle_throttle_ms: int = 250
    min_reissue_interval_ms: int = 100
    reissue_interval_ms: int = 400
    reissue_bearing_delta: float = math.radians(6.0)


STEERING_CFG = SteeringConfig()


@dataclass(slots=True)
class LeaderSteeringState:
    last_leader_xy: tuple[float, float] | None = None
    last_sample_ms: int = 0
    heading: float = 0.0
    has_heading: bool = False
    speed: float = 0.0
    last_issue_ms: int = 0
    last_issue_bearing: float = 0.0
    has_issued: bool = False


def reset_steering(state: LeaderSteeringState) -> None:
    state.last_leader_xy = None
    state.last_sample_ms = 0
    state.heading = 0.0
    state.has_heading = False
    state.speed = 0.0
    state.last_issue_ms = 0
    state.last_issue_bearing = 0.0
    state.has_issued = False


def blend_angle(previous: float, target: float, alpha: float) -> float:
    """Shortest-arc EMA. Blending the raw angles wraps catastrophically at +-pi."""
    sin_part = (math.sin(previous) * (1.0 - alpha)) + (math.sin(target) * alpha)
    cos_part = (math.cos(previous) * (1.0 - alpha)) + (math.cos(target) * alpha)
    if abs(sin_part) < 1e-9 and abs(cos_part) < 1e-9:
        return target
    return math.atan2(sin_part, cos_part)


def angle_difference(left: float, right: float) -> float:
    return abs(math.atan2(math.sin(left - right), math.cos(left - right)))


# Must stay identical to FollowFormationPublisher._rotate_local_to_world — the
# follower reproduces the leader's slot placement locally, so a divergence here
# silently moves every follower off its published formation position. Duplicated
# rather than imported: leader_publish is on the startup-sensitive import path
# that Core/GlobalCache/SharedMemory.py reaches directly.
def rotate_local_to_world(local_x: float, local_y: float, angle: float) -> tuple[float, float]:
    rotated = angle - (math.pi / 2.0)
    c = -math.cos(rotated)
    s = -math.sin(rotated)
    return ((local_x * c) - (local_y * s), (local_x * s) + (local_y * c))


def get_live_leader_xy(leader_agent_id: int) -> tuple[float, float] | None:
    """Leader position from this client's own agent array — no shared-memory lag."""
    if leader_agent_id <= 0 or not Agent.IsValid(leader_agent_id):
        return None
    x, y = Agent.GetXY(leader_agent_id)
    if abs(float(x)) < 0.001 and abs(float(y)) < 0.001:
        return None
    return (float(x), float(y))


def sample_leader_motion(
    state: LeaderSteeringState,
    cfg: SteeringConfig,
    leader_xy: tuple[float, float],
    now_ms: int,
) -> None:
    """Derive heading and speed from position deltas over measured wall time.

    Position deltas rather than Agent.GetVelocityXY: the delta is correct under
    any velocity unit convention and stays meaningful when the field is stale.
    """
    x = float(leader_xy[0])
    y = float(leader_xy[1])

    if state.last_leader_xy is None:
        state.last_leader_xy = (x, y)
        state.last_sample_ms = now_ms
        return

    # Negative dt is the GetBaseTimestamp midnight rollover, not time travel.
    dt_ms = now_ms - state.last_sample_ms
    if 0 <= dt_ms < cfg.min_sample_ms:
        return
    if dt_ms < 0:
        state.last_leader_xy = (x, y)
        state.last_sample_ms = now_ms
        state.speed = 0.0
        return

    delta_x = x - state.last_leader_xy[0]
    delta_y = y - state.last_leader_xy[1]
    distance = math.hypot(delta_x, delta_y)
    if dt_ms > cfg.max_sample_ms or distance > cfg.max_sample_distance:
        state.last_leader_xy = (x, y)
        state.last_sample_ms = now_ms
        state.speed = 0.0
        return

    state.last_leader_xy = (x, y)
    state.last_sample_ms = now_ms

    sampled_speed = distance / (float(dt_ms) / 1000.0)
    state.speed = (state.speed * (1.0 - cfg.speed_smoothing)) + (sampled_speed * cfg.speed_smoothing)
    if sampled_speed < cfg.moving_speed_threshold:
        return

    sampled_heading = math.atan2(delta_y, delta_x)
    if state.has_heading:
        state.heading = blend_angle(state.heading, sampled_heading, cfg.heading_smoothing)
    else:
        state.heading = sampled_heading
        state.has_heading = True


def is_leader_moving(state: LeaderSteeringState, cfg: SteeringConfig) -> bool:
    return state.has_heading and state.speed >= cfg.moving_speed_threshold


def compute_slot_point(
    state: LeaderSteeringState,
    offset_x: float,
    offset_y: float,
    leader_xy: tuple[float, float],
) -> tuple[float, float] | None:
    if not state.has_heading:
        return None
    rotated_x, rotated_y = rotate_local_to_world(offset_x, offset_y, state.heading)
    return (leader_xy[0] + rotated_x, leader_xy[1] + rotated_y)


def compute_aim_point(
    state: LeaderSteeringState,
    cfg: SteeringConfig,
    slot_xy: tuple[float, float],
    follower_xy: tuple[float, float],
) -> tuple[float, float]:
    slot_x, slot_y = slot_xy
    if not is_leader_moving(state, cfg):
        return (slot_x, slot_y)

    gap = math.hypot(slot_x - follower_xy[0], slot_y - follower_xy[1])
    lead = (state.speed * cfg.lead_seconds) + min(gap * cfg.catchup_gain, cfg.max_catchup_lead)
    lead = min(lead, cfg.max_lead_distance)
    return (slot_x + (math.cos(state.heading) * lead), slot_y + (math.sin(state.heading) * lead))


def should_reissue_move(
    state: LeaderSteeringState,
    cfg: SteeringConfig,
    follower_xy: tuple[float, float],
    aim_xy: tuple[float, float],
    now_ms: int,
) -> bool:
    """Steer on bearing change, not on target displacement.

    The aim point moves continuously while the leader runs, so a distance-based
    dedup re-issues every tick and saturates the ACTION queue. Bearing is what
    actually has to change for the follower to alter course.
    """
    if not state.has_issued:
        return True
    elapsed_ms = now_ms - state.last_issue_ms
    if elapsed_ms < cfg.min_reissue_interval_ms:
        return False
    if elapsed_ms >= cfg.reissue_interval_ms:
        return True
    bearing = math.atan2(aim_xy[1] - follower_xy[1], aim_xy[0] - follower_xy[0])
    return angle_difference(bearing, state.last_issue_bearing) >= cfg.reissue_bearing_delta


def mark_move_issued(
    state: LeaderSteeringState,
    follower_xy: tuple[float, float],
    aim_xy: tuple[float, float],
    now_ms: int,
) -> None:
    state.last_issue_bearing = math.atan2(aim_xy[1] - follower_xy[1], aim_xy[0] - follower_xy[0])
    state.last_issue_ms = now_ms
    state.has_issued = True
