from __future__ import annotations

from dataclasses import dataclass, field

from Core import ActionQueueManager, Agent, GLOBAL_CACHE, Range, SharedCommandType, Utils, Weapon
from Core.Map import Map
from Core.Player import Player
from Core.enums_src.UI_enums import ControlAction
from Core.UIManager import UIManager
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

from ..cache_data import CacheData
from .smart_unstuck import (
    SMART_UNSTUCK_CFG,
    SmartUnstuckState,
    force_front_detour,
    reset_smart_unstuck,
    update_smart_unstuck,
)
from .steering import (
    STEERING_CFG,
    LeaderSteeringState,
    compute_aim_point,
    compute_slot_point,
    get_live_leader_xy,
    is_leader_moving,
    mark_move_issued,
    reset_steering,
    sample_leader_motion,
    should_reissue_move,
)


@dataclass(slots=True)
class FollowExecutionState:
    last_follow_move_point: tuple[float, float] | None = None
    last_follow_assigned_point: tuple[float, float, int] | None = None
    follow_map_entry_signature: tuple[int, int, int, int, int] | None = None
    last_leader_publish_signature: tuple[int, int, int, int, int] | None = None
    recovery_active: bool = False
    last_recovery_follow_command_ms: int = 0
    recovery_detour_attempted: bool = False
    pet_recovery_notified: bool = False
    relocating_to_flag: bool = False
    plane_disagree_samples: int = 0
    plane_agree_samples: int = 0
    planes_disagree: bool = False
    native_follow_active: bool = False
    native_follow_started_ms: int = 0
    stuck: SmartUnstuckState = field(default_factory=SmartUnstuckState)
    steer: LeaderSteeringState = field(default_factory=LeaderSteeringState)


FOLLOW_RECOVERY_DISTANCE = Range.Spirit.value
FOLLOW_RECOVERY_START_DISTANCE = FOLLOW_RECOVERY_DISTANCE
FOLLOW_RECOVERY_RELEASE_DISTANCE = Range.Earshot.value

# How far past its tolerance a flagged follower may drift before returning to
# station outranks fighting where it stands. Wide enough that a follower already
# on its spot never jitters, tight enough that a backliner pulled into the melee
# walks back out rather than tanking from the healer slot.
FLAG_RETURN_MARGIN = Range.Nearby.value

# Plane readings flap where two surfaces overlap in plan view - the middle of a
# bridge reads as both. Acting on one sample is what sends a follower hunting for
# a destination on the level it is not on, so require agreement to persist in
# either direction before believing it.
PLANE_DEBOUNCE_SAMPLES = 3

# Native follow is GW's own pathing, borrowed as transport when ours has provably
# failed. Never load-bearing: expiry hands control back to normal follow rather
# than deciding anything (runtime-behaviour.md).
NATIVE_FOLLOW_TIMEOUT_MS = 15000


def update_plane_agreement(state: FollowExecutionState, own_plane: int, destination_plane: int) -> bool:
    """Debounced 'is the destination on my level'. True while they agree."""
    if own_plane == destination_plane:
        state.plane_agree_samples += 1
        state.plane_disagree_samples = 0
        if state.plane_agree_samples >= PLANE_DEBOUNCE_SAMPLES:
            state.planes_disagree = False
    else:
        state.plane_disagree_samples += 1
        state.plane_agree_samples = 0
        if state.plane_disagree_samples >= PLANE_DEBOUNCE_SAMPLES:
            state.planes_disagree = True
    return not state.planes_disagree


def get_follow_destination_distance(cached_data: CacheData) -> float:
    destination = get_follow_destination_xy(cached_data)
    if destination is None:
        return 0.0
    return float(Utils.Distance(destination, Agent.GetXY(Player.GetAgentID())))


def get_follow_destination_xy(cached_data: CacheData) -> tuple[float, float] | None:
    options = GLOBAL_CACHE.ShMem.GetHeroAIOptionsFromEmail(cached_data.account_email)

    if not options:
        return None

    def _is_nonzero_xy(x: float, y: float) -> bool:
        return abs(float(x)) > 0.001 or abs(float(y)) > 0.001

    published_follow_xy = (float(options.FollowPos.x), float(options.FollowPos.y))
    flag_xy = (float(options.FlagPos.x), float(options.FlagPos.y))
    is_flagged = bool(getattr(options, "IsFlagged", False))
    if _is_nonzero_xy(*published_follow_xy):
        return published_follow_xy

    if is_flagged and _is_nonzero_xy(*flag_xy):
        return flag_xy

    return None


def _notify_recovery_console_message(message_text: str) -> None:
    sender_email = str(Player.GetAccountEmail() or "").strip()
    leader_account = GLOBAL_CACHE.ShMem.GetAccountDataFromPartyNumber(0)
    leader_email = str(getattr(leader_account, "AccountEmail", "") or "").strip() if leader_account else ""
    if sender_email and leader_email and sender_email != leader_email:
        GLOBAL_CACHE.ShMem.SendMessage(
            sender_email,
            leader_email,
            SharedCommandType.ConsoleMessage,
            (0, 0, 0, 0),
            (message_text,),
        )


def _maybe_notify_pet_recovery(cached_data: CacheData, state: FollowExecutionState) -> None:
    player_agent_id = int(Player.GetAgentID())
    pet_id = int(GLOBAL_CACHE.Party.Pets.GetPetID(player_agent_id) or 0)
    if pet_id <= 0 or not Agent.IsValid(pet_id):
        state.pet_recovery_notified = False
        return

    destination = get_follow_destination_xy(cached_data)
    if destination is None:
        state.pet_recovery_notified = False
        return

    pet_x, pet_y = Agent.GetXY(pet_id)
    pet_distance = float(Utils.Distance(destination, (pet_x, pet_y)))
    if pet_distance < float(FOLLOW_RECOVERY_START_DISTANCE):
        state.pet_recovery_notified = False
        return

    if state.pet_recovery_notified:
        return

    _notify_recovery_console_message(f"pet lagged behind at x={pet_x:.0f}, y={pet_y:.0f}")
    state.pet_recovery_notified = True


def is_follow_recovery_active(cached_data: CacheData, state: FollowExecutionState) -> bool:
    options = cached_data.account_options
    player_agent_id = int(Player.GetAgentID())

    if (
        not options
        or not bool(getattr(options, "Following", False))
        or player_agent_id <= 0
        or player_agent_id == int(GLOBAL_CACHE.Party.GetPartyLeaderID())
    ):
        state.recovery_active = False
        state.pet_recovery_notified = False
        return False

    _maybe_notify_pet_recovery(cached_data, state)

    distance_to_destination = get_follow_destination_distance(cached_data)
    if state.recovery_active:
        state.recovery_active = distance_to_destination >= FOLLOW_RECOVERY_RELEASE_DISTANCE
        if not state.recovery_active:
            state.recovery_detour_attempted = False
        return state.recovery_active

    if distance_to_destination < FOLLOW_RECOVERY_START_DISTANCE:
        return False

    state.recovery_active = True
    state.recovery_detour_attempted = False
    try:
        _notify_recovery_console_message("Hey, Wait for me!")
    except Exception:
        pass
    return True


def execute_follower_follow(
    cached_data: CacheData,
    state: FollowExecutionState,
) -> BehaviorTree.NodeState:
    follow_active_state = BehaviorTree.NodeState.SUCCESS

    def _is_nonzero_xy(x: float, y: float) -> bool:
        return abs(float(x)) > 0.001 or abs(float(y)) > 0.001

    def _reset_follow_runtime() -> None:
        state.last_follow_move_point = None
        state.last_follow_assigned_point = None
        state.last_recovery_follow_command_ms = 0
        state.recovery_detour_attempted = False
        state.relocating_to_flag = False
        state.native_follow_active = False
        state.native_follow_started_ms = 0
        state.planes_disagree = False
        state.plane_agree_samples = 0
        state.plane_disagree_samples = 0
        reset_smart_unstuck(state.stuck)
        reset_steering(state.steer)

    def _account_map_signature(account) -> tuple[int, int, int, int, int] | None:
        if account is None or not bool(getattr(account, "IsSlotActive", False)):
            return None
        return (
            int(account.AgentData.Map.MapID),
            int(account.AgentData.Map.Region),
            int(account.AgentData.Map.District),
            int(account.AgentData.Map.Language),
            int(account.AgentPartyData.PartyID),
        )

    def _assigned_point_changed(
        previous: tuple[float, float, int] | None,
        current: tuple[float, float, int],
        refresh_distance: float,
    ) -> bool:
        if previous is None:
            return True
        previous_x, previous_y, previous_z = previous
        current_x, current_y, current_z = current
        if previous_z != current_z:
            return True
        return Utils.Distance((previous_x, previous_y), (current_x, current_y)) > refresh_distance

    options = cached_data.account_options
    if not options or not options.Following:
        state.recovery_active = False
        return BehaviorTree.NodeState.FAILURE

    # During an active stuck-avoidance detour, BT.Move needs to tick at the
    # full HeroAI BT rate (~33ms) so it can detect "almost there" mid-walk and
    # switch the engine target BEFORE the follower physically arrives at a
    # waypoint. Apo's "constantly steer" — at the previous 100ms throttle the
    # follower covered an entire 89u waypoint between BT ticks, so BT only
    # ever sampled the player at arrival moments and tolerance had no effect.
    # Idle mode keeps the 250ms throttle since smoothness doesn't matter there.
    if state.stuck.mode != "idle":
        cached_data.follow_throttle_timer.SetThrottleTime(0)
    elif is_leader_moving(state.steer, STEERING_CFG):
        cached_data.follow_throttle_timer.SetThrottleTime(STEERING_CFG.moving_throttle_ms)
    else:
        cached_data.follow_throttle_timer.SetThrottleTime(STEERING_CFG.idle_throttle_ms)

    if not cached_data.follow_throttle_timer.IsExpired():
        return BehaviorTree.NodeState.FAILURE

    leader_agent_id = int(GLOBAL_CACHE.Party.GetPartyLeaderID())
    player_agent_id = int(Player.GetAgentID())
    if player_agent_id == leader_agent_id:
        state.recovery_active = False
        cached_data.follow_throttle_timer.Reset()
        return BehaviorTree.NodeState.FAILURE

    recovery_active = is_follow_recovery_active(cached_data, state)

    # IsCasting decodes model_state, which GW barely advances while not rendering —
    # the MC probe read attacking/casting/moving/idle as all-zero through entire
    # minimised fights. The skillbar casting field is game-logic driven and the
    # GLOBAL_CACHE pump refreshes it on both loops, so it is the signal that stops
    # follow moves from cancelling this client's own casts while minimised.
    if Agent.IsCasting(player_agent_id) or (GLOBAL_CACHE.SkillBar.GetCasting() or 0) != 0:
        return BehaviorTree.NodeState.FAILURE

    map_sig = (
        int(Map.GetMapID()),
        int(Map.GetRegion()[0]),
        int(Map.GetDistrict()),
        int(Map.GetLanguage()[0]),
        int(cached_data.account_data.AgentPartyData.PartyID),
    )
    if state.follow_map_entry_signature != map_sig:
        state.follow_map_entry_signature = map_sig
        state.last_leader_publish_signature = None
        _reset_follow_runtime()

    own_flag_active = bool(getattr(options, "IsFlagged", False)) and _is_nonzero_xy(
        float(options.FlagPos.x),
        float(options.FlagPos.y),
    )
    leader_account = GLOBAL_CACHE.ShMem.GetAccountDataFromPartyNumber(0)
    leader_publish_signature = _account_map_signature(leader_account)
    leader_signature_matches_local = leader_publish_signature == map_sig
    if state.last_leader_publish_signature != leader_publish_signature:
        state.last_leader_publish_signature = leader_publish_signature
        _reset_follow_runtime()

    leader_options = GLOBAL_CACHE.ShMem.GetHeroAIOptionsByPartyNumber(0)
    all_flag_active = (
        leader_signature_matches_local
        and leader_options is not None
        and bool(getattr(leader_options, "IsFlagged", False))
        and _is_nonzero_xy(float(leader_options.AllFlag.x), float(leader_options.AllFlag.y))
    )

    follow_threshold_raw = float(options.FollowMoveThreshold)
    combat_threshold_raw = float(options.FollowMoveThresholdCombat)

    published_follow_xy = (float(options.FollowPos.x), float(options.FollowPos.y))
    published_follow_z = int(float(options.FollowPos.z))

    if own_flag_active:
        if _is_nonzero_xy(*published_follow_xy):
            follow_x, follow_y = published_follow_xy
            follow_z = published_follow_z
        else:
            follow_x = float(options.FlagPos.x)
            follow_y = float(options.FlagPos.y)
            follow_z = 0
    else:
        if not bool(getattr(options, "LeaderFollowReady", False)):
            _reset_follow_runtime()
            return BehaviorTree.NodeState.FAILURE
        if not leader_signature_matches_local:
            _reset_follow_runtime()
            return BehaviorTree.NodeState.FAILURE
        if follow_threshold_raw < 0.0 and combat_threshold_raw < 0.0:
            _reset_follow_runtime()
            return BehaviorTree.NodeState.FAILURE
        follow_x, follow_y = published_follow_xy
        follow_z = published_follow_z
        if (not _is_nonzero_xy(follow_x, follow_y)) and follow_z == 0:
            _reset_follow_runtime()
            return BehaviorTree.NodeState.FAILURE

    # Rebuild the formation slot from the leader's LIVE position and travel
    # heading instead of consuming the published FollowPos, which is stale by
    # the publish interval plus this follower's throttle (1000ms of it once the
    # leader is in combat). Only while the leader is actually moving: standing
    # still there is no heading to derive, and the published point — already
    # navmesh-validated leader-side — is the better answer.
    tick_ms = int(Utils.GetBaseTimestamp())
    live_leader_xy = get_live_leader_xy(leader_agent_id)
    if live_leader_xy is None:
        reset_steering(state.steer)
    else:
        sample_leader_motion(state.steer, STEERING_CFG, live_leader_xy, tick_ms)

    combat_active = bool(cached_data.IsHeadlessCombatPauseActive())

    # Combat tolerance is Range.Adjacent (166u), so station-keeping in a fight
    # would make followers chase a repositioning leader instead of standing and
    # fighting. Fight-zone positioning owns movement there instead.
    steering_active = (
        live_leader_xy is not None
        and follow_z == 0
        and not own_flag_active
        and not all_flag_active
        and not combat_active
        and is_leader_moving(state.steer, STEERING_CFG)
    )
    if steering_active and live_leader_xy is not None:
        local_slot = compute_slot_point(
            state.steer,
            float(options.FollowOffset.x),
            float(options.FollowOffset.y),
            live_leader_xy,
        )
        if local_slot is None:
            steering_active = False
        else:
            follow_x, follow_y = local_slot

    is_melee = cached_data.data.weapon_type in {
        Weapon.Axe.value,
        Weapon.Hammer.value,
        Weapon.Daggers.value,
        Weapon.Scythe.value,
        Weapon.Sword.value,
    }

    if combat_active:
        if combat_threshold_raw >= 0.0:
            follow_distance = max(0.0, combat_threshold_raw)
        else:
            follow_distance = max(0.0, follow_threshold_raw)
    else:
        follow_distance = max(0.0, follow_threshold_raw)

    if combat_active and is_melee and not own_flag_active and not all_flag_active:
        melee_leash_distance = max(follow_distance, float(Range.Spellcast.value))
        if Utils.Distance((follow_x, follow_y), Player.GetXY()) <= melee_leash_distance:
            cached_data.follow_throttle_timer.Reset()
            return BehaviorTree.NodeState.FAILURE

    planes_agree = update_plane_agreement(state, int(Agent.GetZPlane(player_agent_id)), follow_z)

    assigned_point = (follow_x, follow_y, follow_z)
    destination_refresh_distance = max(25.0, min(150.0, follow_distance * 0.25))
    assigned_changed = _assigned_point_changed(
        state.last_follow_assigned_point,
        assigned_point,
        destination_refresh_distance,
    )
    if assigned_changed:
        state.last_follow_move_point = None
    state.last_follow_assigned_point = assigned_point

    # A flag re-position (assigned point moved) takes priority over combat: the
    # follower must walk to the new flag even mid-fight. Latch a relocation
    # state that is cleared once the follower arrives, so it keeps moving across
    # ticks instead of re-yielding to local aggro after the one-tick
    # assigned_changed pulse.
    if (own_flag_active or all_flag_active) and assigned_changed:
        state.relocating_to_flag = True

    # Upstream "follow recovery": when the follower is far from its destination,
    # tighten the tolerance to FOLLOW_RECOVERY_RELEASE_DISTANCE so it keeps
    # closing the gap instead of stopping at the normal slot threshold.
    effective_follow_distance = (
        min(follow_distance, FOLLOW_RECOVERY_RELEASE_DISTANCE) if recovery_active else follow_distance
    )
    # When flagged the published threshold is 0.0 (hold position exactly), which
    # makes the arrival check below impossible to satisfy and permanently blocks
    # HandleCombat in the headless tree selector.  Enforce a minimum arrival
    # radius of Adjacent so followers that have reached the flag can fight.
    if (own_flag_active or all_flag_active) and effective_follow_distance < float(Range.Adjacent.value):
        effective_follow_distance = float(Range.Adjacent.value)
    # Station-keeping: arriving inside the threshold is only a reason to stop
    # when the leader has also stopped. While the leader runs, a follower that
    # halts on arrival re-acquires only once the slot has drifted a full
    # threshold away (322u by default), so its duty cycle sits below the
    # leader's and the gap grows every cycle. That deadband is the straggle.
    dist_to_follow = Utils.Distance((follow_x, follow_y), Player.GetXY())
    if dist_to_follow <= effective_follow_distance and not steering_active:
        state.last_recovery_follow_command_ms = 0
        state.recovery_detour_attempted = False
        state.relocating_to_flag = False
        reset_smart_unstuck(state.stuck)
        return BehaviorTree.NodeState.FAILURE

    # Flagged followers: yield to HandleCombat only while there are enemies in
    # range of THIS follower (local aggro), not party-wide aggro. Holding
    # position makes Follow win the selector, which both blocks combat AND
    # leaves the follower idle once the engine move is interrupted by aggro.
    # Gating on local aggro (instead of the party-driven `in_aggro`) lets a
    # follower with no nearby enemies walk back to its flag even while the rest
    # of the party fights elsewhere. While relocating to a freshly moved flag,
    # do NOT yield — the flag move must win over combat. Recovery (dist >=
    # Spirit) is handled below and also takes priority over fighting.
    # ...but only while the follower is still roughly ON its spot. A backliner
    # that got dragged into the melee is exactly the case that must walk back
    # while the fight is still going: standing where it drifted to is how a monk
    # ends up in the mob it is meant to be healing from range. Beyond the return
    # margin, position wins over fighting in place.
    drifted_off_station = dist_to_follow > (effective_follow_distance + FLAG_RETURN_MARGIN)
    if (
        (own_flag_active or all_flag_active)
        and bool(cached_data.data.local_in_aggro)
        and not recovery_active
        and not state.relocating_to_flag
        and not drifted_off_station
    ):
        # Drop the cached move point so that once combat ends the arrival/move
        # path below re-issues Player.Move toward the flag instead of skipping
        # it via the "already moved here" dedup — otherwise a follower that
        # chased an enemy away stays put after combat instead of returning.
        state.last_follow_move_point = None
        cached_data.follow_throttle_timer.Reset()
        return BehaviorTree.NodeState.FAILURE

    # A station on another level cannot be walked to directly: the move resolves
    # at this follower's plane, which is how a bridge crossing ends with the
    # backline on the far bank. Fighting a few paces off station beats leaving
    # the fight, so suspend the station until the planes agree again.
    if bool(cached_data.data.local_in_aggro) and not planes_agree:
        state.last_follow_move_point = None
        cached_data.follow_throttle_timer.Reset()
        return BehaviorTree.NodeState.FAILURE

    if follow_z == 0 and not own_flag_active:
        update_smart_unstuck(
            state.stuck,
            SMART_UNSTUCK_CFG,
            current_xy=Player.GetXY(),
            follow_xy=(follow_x, follow_y),
            assigned_changed=assigned_changed,
        )
    else:
        reset_smart_unstuck(state.stuck)

    # During an active detour, BT.Move has already issued Player.Move with its
    # own stall-aware pacing. Skip our Player.Move below — otherwise we clobber
    # the in-flight pathing and reintroduce inter-waypoint stutter.
    if state.stuck.mode != "idle":
        state.last_follow_move_point = None
        cached_data.follow_throttle_timer.Reset()
        return follow_active_state

    if recovery_active:
        if own_flag_active or all_flag_active:
            # Flagged followers have a fixed world destination (the flag), so
            # recover by walking straight to it. The detour/engine-follow
            # recovery below is meant for leader-following; for the flag case it
            # never issues a move command, leaving a far-flagged follower
            # standing still even when the leader is far away.
            if ActionQueueManager().IsEmpty("ACTION"):
                Player.Move(follow_x, follow_y)
                state.last_follow_move_point = (follow_x, follow_y)
            cached_data.follow_throttle_timer.Reset()
            return follow_active_state
        if not state.recovery_detour_attempted:
            force_front_detour(
                state.stuck,
                SMART_UNSTUCK_CFG,
                current_xy=Player.GetXY(),
                follow_xy=(follow_x, follow_y),
            )
            state.recovery_detour_attempted = True
            state.last_recovery_follow_command_ms = 0
            cached_data.follow_throttle_timer.Reset()
            return follow_active_state
        now_ms = int(Utils.GetBaseTimestamp())
        if now_ms - int(state.last_recovery_follow_command_ms) < 1000:
            cached_data.follow_throttle_timer.Reset()
            return follow_active_state
        if ActionQueueManager().IsEmpty("ACTION"):
            ActionQueueManager().AddAction(
                "ACTION", UIManager.Keypress, ControlAction.ControlAction_TargetPartyMember1.value, 0
            )
            ActionQueueManager().AddAction("ACTION", UIManager.Keypress, ControlAction.ControlAction_Follow.value, 0)
            state.last_recovery_follow_command_ms = now_ms
        cached_data.follow_throttle_timer.Reset()
        return follow_active_state

    xx = follow_x
    yy = follow_y

    if steering_active:
        xx, yy = compute_aim_point(state.steer, STEERING_CFG, (follow_x, follow_y), Player.GetXY())
        if not should_reissue_move(state.steer, STEERING_CFG, Player.GetXY(), (xx, yy), tick_ms):
            return BehaviorTree.NodeState.FAILURE
    elif not assigned_changed and state.last_follow_move_point is not None:
        last_x, last_y = state.last_follow_move_point
        if Utils.Distance((last_x, last_y), (xx, yy)) <= 10.0:
            return BehaviorTree.NodeState.FAILURE

    if not ActionQueueManager().IsEmpty("ACTION"):
        return BehaviorTree.NodeState.FAILURE

    # Borrow GW's own pathing when ours has provably failed. Triggered by the
    # failure itself, not by plane: the plane reading is what misleads us at a
    # bridge, while "issued moves are not closing the gap" is true of bridges,
    # corridors and doorways alike. Transport only - it steers to the leader with
    # no formation offset, so combat and flagged followers never hand over.
    native_follow_wanted = (
        not own_flag_active
        and not all_flag_active
        and not bool(cached_data.data.local_in_aggro)
        and (not planes_agree or state.stuck.no_progress_samples > 0)
    )

    if state.native_follow_active:
        if native_follow_wanted and (tick_ms - state.native_follow_started_ms) < NATIVE_FOLLOW_TIMEOUT_MS:
            cached_data.follow_throttle_timer.Reset()
            return BehaviorTree.NodeState.FAILURE
        state.native_follow_active = False
        state.last_follow_move_point = None
        ActionQueueManager().AddAction(
            "ACTION", UIManager.Keypress, ControlAction.ControlAction_CancelAction.value, 0
        )
    elif native_follow_wanted:
        state.native_follow_active = True
        state.native_follow_started_ms = tick_ms
        reset_smart_unstuck(state.stuck)
        ActionQueueManager().AddAction(
            "ACTION", UIManager.Keypress, ControlAction.ControlAction_TargetPartyMember1.value, 0
        )
        ActionQueueManager().AddAction("ACTION", UIManager.Keypress, ControlAction.ControlAction_Follow.value, 0)
        cached_data.follow_throttle_timer.Reset()
        return BehaviorTree.NodeState.FAILURE

    Player.Move(xx, yy)
    if steering_active:
        mark_move_issued(state.steer, Player.GetXY(), (xx, yy), tick_ms)

    state.last_follow_move_point = (xx, yy)

    cached_data.follow_throttle_timer.Reset()
    return BehaviorTree.NodeState.FAILURE
