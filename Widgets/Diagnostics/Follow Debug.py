"""Overlay and sample log for the follow pipeline."""

import time

import PyImGui

from Core import Agent
from Core import GLOBAL_CACHE
from Core import Overlay
from Core import Player
from Core import ThrottledTimer
from Core import Utils
from HeroAI.follow.follower_runtime import FOLLOW_RECOVERY_RELEASE_DISTANCE
from HeroAI.follow.follower_runtime import FOLLOW_RECOVERY_START_DISTANCE

MODULE_NAME = "Follow Debug"
MODULE_ICON = "Textures/Module_Icons/Frame Limiter.png"

SAMPLE_INTERVAL_MS = 250
LOG_INTERVAL_MS = 1000
LOG_SAMPLES_MAX = 400
LOG_DOCUMENT = "FOLLOW.json"

# One row per account per sample, so this fills eight times faster than the
# single-client log and needs the headroom to still cover a useful window.
FLEET_SAMPLES_MAX = 2400
FLEET_DOCUMENT = "FLEET.json"

WHITE = 0xFFFFFFFF
CYAN = 0xFF00E5FF
RED = 0xFFFF3B30
AMBER = 0xFFFFCC00
GREEN = 0xFF34C759
FAINT = 0x50FFFFFF

sample_timer = ThrottledTimer(SAMPLE_INTERVAL_MS)
log_timer = ThrottledTimer(LOG_INTERVAL_MS)

samples = []
fleet_samples = []
latest = None
last_error = ""
overlay_enabled = True
fleet_enabled = True
logging_enabled = True
show_rings = True
follow_state_holder = None


def resolve_follow_state():
    """The live FollowExecutionState, owned by whichever module drives following.

    Located by scanning loaded modules rather than imported: the instance lives in
    the HeroAI widget, which is loaded by path and is not importable by name.
    """
    global follow_state_holder

    if follow_state_holder is not None:
        state = getattr(follow_state_holder, "follow_execution_state", None)
        if state is not None:
            return state
        follow_state_holder = None

    import sys

    for module in list(sys.modules.values()):
        if module is None:
            continue
        state = getattr(module, "follow_execution_state", None)
        if state is not None and hasattr(state, "recovery_active"):
            follow_state_holder = module
            return state
    return None


def collect():
    me = Player.GetAgentID()
    if not me:
        return None

    email = Player.GetAccountEmail() or ""
    options = GLOBAL_CACHE.ShMem.GetHeroAIOptionsFromEmail(email) if email else None

    my_x, my_y = Agent.GetXY(me)
    sample = {
        "t": time.strftime("%H:%M:%S"),
        "me": (round(my_x, 1), round(my_y, 1)),
        "my_plane": int(Agent.GetZPlane(me)),
        "moving": bool(Agent.IsMoving(me)),
        "destination": None,
        # FollowPos.z carries the leader's z-PLANE, not a world height - see
        # leader_publish. A mismatch against ours is the bridge/tunnel signal.
        "dest_plane": None,
        "distance": None,
        "following": None,
        "combat": None,
        "flagged": None,
        "recovery": None,
        "relocating": None,
        "stuck": None,
        "waypoint": None,
        "assigned": None,
        "assigned_plane": None,
        "move_point": None,
    }

    if options is not None:
        follow_x = float(options.FollowPos.x)
        follow_y = float(options.FollowPos.y)
        if abs(follow_x) > 0.001 or abs(follow_y) > 0.001:
            sample["destination"] = (round(follow_x, 1), round(follow_y, 1))
            sample["dest_plane"] = int(float(options.FollowPos.z))
            sample["distance"] = round(float(Utils.Distance((follow_x, follow_y), (my_x, my_y))), 1)
        sample["following"] = bool(getattr(options, "Following", False))
        sample["combat"] = bool(getattr(options, "Combat", False))
        sample["flagged"] = bool(getattr(options, "IsFlagged", False))

    state = resolve_follow_state()
    if state is not None:
        sample["recovery"] = bool(state.recovery_active)
        sample["relocating"] = bool(state.relocating_to_flag)
        sample["stuck"] = str(state.stuck.mode)
        sample["waypoint"] = "%d/%d" % (state.stuck.waypoint_idx, len(state.stuck.waypoints))
        sample["move_point"] = state.last_follow_move_point
        assigned = state.last_follow_assigned_point
        if assigned:
            sample["assigned"] = (round(assigned[0], 1), round(assigned[1], 1))
            sample["assigned_plane"] = int(assigned[2])

    return sample


def plane_mismatch(sample) -> bool:
    return sample["dest_plane"] is not None and sample["dest_plane"] != sample["my_plane"]


def record(sample) -> None:
    from Core.py4gwcorelib_src.JsonFactory import JsonFactory

    line = (
        "%s me=%s plane=%d moving=%d | dest=%s destplane=%s dist=%s mismatch=%d"
        " | following=%s combat=%s flagged=%s recovery=%s reloc=%s stuck=%s wp=%s"
        " | assigned=%s aplane=%s move=%s"
        % (
            sample["t"],
            sample["me"],
            sample["my_plane"],
            int(sample["moving"]),
            sample["destination"],
            sample["dest_plane"],
            sample["distance"],
            int(plane_mismatch(sample)),
            sample["following"],
            sample["combat"],
            sample["flagged"],
            sample["recovery"],
            sample["relocating"],
            sample["stuck"],
            sample["waypoint"],
            sample["assigned"],
            sample["assigned_plane"],
            sample["move_point"],
        )
    )

    samples.append(line)
    if len(samples) > LOG_SAMPLES_MAX:
        del samples[0 : len(samples) - LOG_SAMPLES_MAX]

    document = JsonFactory(LOG_DOCUMENT)
    document.set_json("samples", list(samples))
    document.save()


def draw_world(sample) -> None:
    my_x, my_y = sample["me"]
    Overlay().BeginDraw()
    try:
        my_z = Overlay().FindZ(my_x, my_y, 0)
        Overlay().DrawPoly3D(my_x, my_y, my_z, 60.0, WHITE, 16, 2.0, False)

        destination = sample["destination"]
        if destination is not None:
            dest_x, dest_y = destination
            dest_z = Overlay().FindZ(dest_x, dest_y, 0)
            mismatched = plane_mismatch(sample)
            colour = RED if mismatched else CYAN

            Overlay().DrawLine3D(my_x, my_y, my_z, dest_x, dest_y, dest_z, colour, 2.0)
            Overlay().DrawPoly3D(dest_x, dest_y, dest_z, 90.0, colour, 24, 2.0, False)
            Overlay().DrawText3D(
                dest_x,
                dest_y,
                dest_z,
                "dist %s  plane %s->%s%s"
                % (
                    sample["distance"],
                    sample["my_plane"],
                    sample["dest_plane"],
                    "  PLANE MISMATCH" if mismatched else "",
                ),
                colour,
            )

            if show_rings:
                Overlay().DrawPoly3D(
                    dest_x, dest_y, dest_z, FOLLOW_RECOVERY_RELEASE_DISTANCE, GREEN, 48, 1.0, False
                )
                Overlay().DrawPoly3D(dest_x, dest_y, dest_z, FOLLOW_RECOVERY_START_DISTANCE, AMBER, 48, 1.0, False)

        assigned = sample["assigned"]
        if assigned is not None:
            ax, ay = assigned
            az = Overlay().FindZ(ax, ay, 0)
            Overlay().DrawPoly3D(ax, ay, az, 45.0, AMBER, 12, 2.0, False)
            Overlay().DrawLine3D(my_x, my_y, my_z, ax, ay, az, FAINT, 1.0)

        state = resolve_follow_state()
        if state is not None and state.stuck.waypoints:
            previous = (my_x, my_y)
            for index, waypoint in enumerate(state.stuck.waypoints):
                wx, wy = waypoint
                wz = Overlay().FindZ(wx, wy, 0)
                reached = index < state.stuck.waypoint_idx
                Overlay().DrawPoly3D(wx, wy, wz, 40.0, FAINT if reached else GREEN, 12, 2.0, False)
                Overlay().DrawLine3D(
                    previous[0], previous[1], Overlay().FindZ(previous[0], previous[1], 0), wx, wy, wz, GREEN, 1.0
                )
                previous = waypoint
    finally:
        Overlay().EndDraw()


def collect_fleet():
    """Every account's published position and plane, read from shared memory.

    This is the only view that works while the followers are minimised: their own
    overlay needs frames they are not rendering, but the shared-memory writer runs
    on the update loop, so what they publish stays current.
    """
    fleet = []
    shmem = GLOBAL_CACHE.ShMem
    accounts = shmem.GetAllAccounts()
    for index in range(shmem.max_num_players):
        account = shmem.GetAccountData(index)
        if not account or not account.IsSlotActive or not account.AccountEmail:
            continue

        agent = account.AgentData
        options = accounts.HeroAIOptions[index]
        follow_x = float(options.FollowPos.x)
        follow_y = float(options.FollowPos.y)
        has_destination = abs(follow_x) > 0.001 or abs(follow_y) > 0.001

        fleet.append(
            {
                "email": str(account.AccountEmail),
                "name": str(agent.CharacterName),
                "xy": (float(agent.Pos.x), float(agent.Pos.y)),
                "plane": int(agent.ZPlane),
                "speed": float(Utils.Distance((0.0, 0.0), (float(agent.Velocity.x), float(agent.Velocity.y)))),
                "destination": (follow_x, follow_y) if has_destination else None,
                "dest_plane": int(float(options.FollowPos.z)) if has_destination else None,
                "distance": (
                    round(float(Utils.Distance((follow_x, follow_y), (float(agent.Pos.x), float(agent.Pos.y)))), 1)
                    if has_destination
                    else None
                ),
                "combat": bool(options.Combat),
                "following": bool(options.Following),
            }
        )
    return fleet


def record_fleet(fleet) -> None:
    """Every account's row, written from whichever client runs this widget.

    Deliberately ungated by aggro: the failures worth catching - a follower that
    stops advancing on a correct destination - happen out of combat, which is
    exactly when the MC probe records nothing.
    """
    from Core.py4gwcorelib_src.JsonFactory import JsonFactory

    stamp = time.strftime("%H:%M:%S")
    for member in fleet:
        fleet_samples.append(
            "%s %-14s xy=(%.0f,%.0f) plane=%d v=%.0f | dest=%s destplane=%s dist=%s mismatch=%d"
            " | following=%s combat=%s"
            % (
                stamp,
                (member["name"] or member["email"])[:14],
                member["xy"][0],
                member["xy"][1],
                member["plane"],
                member["speed"],
                (
                    "(%.0f,%.0f)" % (member["destination"][0], member["destination"][1])
                    if member["destination"]
                    else None
                ),
                member["dest_plane"],
                member["distance"],
                int(member["dest_plane"] is not None and member["dest_plane"] != member["plane"]),
                member["following"],
                member["combat"],
            )
        )

    if len(fleet_samples) > FLEET_SAMPLES_MAX:
        del fleet_samples[0 : len(fleet_samples) - FLEET_SAMPLES_MAX]

    document = JsonFactory(FLEET_DOCUMENT)
    document.set_json("samples", list(fleet_samples))
    document.save()


def draw_fleet(fleet) -> None:
    Overlay().BeginDraw()
    try:
        for member in fleet:
            x, y = member["xy"]
            if abs(x) < 0.001 and abs(y) < 0.001:
                continue
            z = Overlay().FindZ(x, y, 0)
            mismatched = member["dest_plane"] is not None and member["dest_plane"] != member["plane"]
            colour = RED if mismatched else (GREEN if member["combat"] else AMBER)

            Overlay().DrawPoly3D(x, y, z, 70.0, colour, 16, 2.0, False)
            Overlay().DrawText3D(
                x,
                y,
                z,
                "%s  p%d  d%s  v%.0f%s"
                % (
                    member["name"] or member["email"],
                    member["plane"],
                    member["distance"],
                    member["speed"],
                    "  MISMATCH" if mismatched else "",
                ),
                colour,
            )

            if member["destination"] is not None:
                dest_x, dest_y = member["destination"]
                dest_z = Overlay().FindZ(dest_x, dest_y, 0)
                Overlay().DrawLine3D(x, y, z, dest_x, dest_y, dest_z, colour, 1.0)
    finally:
        Overlay().EndDraw()


def render_panel() -> None:
    global overlay_enabled, logging_enabled, show_rings, fleet_enabled

    if not PyImGui.begin(MODULE_NAME):
        PyImGui.end()
        return

    overlay_enabled = PyImGui.checkbox("World overlay (this client)", overlay_enabled)
    fleet_enabled = PyImGui.checkbox("Fleet view (every account, works minimised)", fleet_enabled)
    logging_enabled = PyImGui.checkbox("Log to json/<account>/%s" % LOG_DOCUMENT, logging_enabled)
    show_rings = PyImGui.checkbox("Recovery rings", show_rings)
    PyImGui.separator()

    if fleet_enabled:
        try:
            for member in collect_fleet():
                mismatched = member["dest_plane"] is not None and member["dest_plane"] != member["plane"]
                text = "%-14s p%-3d dest_p%-4s dist %-7s v%-5.0f combat %s" % (
                    (member["name"] or member["email"])[:14],
                    member["plane"],
                    member["dest_plane"],
                    member["distance"],
                    member["speed"],
                    member["combat"],
                )
                if mismatched:
                    PyImGui.text_colored(text + "  PLANE MISMATCH", (1.0, 0.3, 0.2, 1.0))
                else:
                    PyImGui.text(text)
        except Exception as error:
            PyImGui.text_colored("fleet read failed: %s" % error, (1.0, 0.3, 0.2, 1.0))
        PyImGui.separator()

    if last_error:
        PyImGui.text_colored("error: %s" % last_error, (1.0, 0.3, 0.2, 1.0))

    if latest is None:
        PyImGui.text("no sample yet")
        PyImGui.end()
        return

    if plane_mismatch(latest):
        PyImGui.text_colored("PLANE MISMATCH - destination is on another level", (1.0, 0.3, 0.2, 1.0))

    PyImGui.text("me %s plane %d moving %d" % (latest["me"], latest["my_plane"], int(latest["moving"])))
    PyImGui.text("destination %s plane %s" % (latest["destination"], latest["dest_plane"]))
    PyImGui.text("distance %s" % latest["distance"])
    PyImGui.text(
        "following %s  combat %s  flagged %s" % (latest["following"], latest["combat"], latest["flagged"])
    )
    PyImGui.text(
        "recovery %s  relocating %s  stuck %s  wp %s"
        % (latest["recovery"], latest["relocating"], latest["stuck"], latest["waypoint"])
    )
    PyImGui.text("assigned %s plane %s" % (latest["assigned"], latest["assigned_plane"]))
    PyImGui.text("move point %s" % (latest["move_point"],))

    PyImGui.separator()
    PyImGui.text("samples (%d)" % len(samples))
    for line in samples[-12:]:
        PyImGui.text(line)

    PyImGui.end()


def tick() -> None:
    global latest, last_error

    if not sample_timer.IsExpired():
        return
    sample_timer.Reset()

    try:
        sample = collect()
        if sample is None:
            return
        latest = sample
        last_error = ""
        if logging_enabled and log_timer.IsExpired():
            log_timer.Reset()
            record(sample)
            if fleet_enabled:
                record_fleet(collect_fleet())
    except Exception as error:
        last_error = str(error)


def update():
    if Utils.IsDrawLoopStalled():
        tick()


def draw():
    global last_error

    if not Utils.IsDrawLoopStalled():
        tick()

    render_panel()

    if overlay_enabled and latest is not None:
        try:
            draw_world(latest)
        except Exception as error:
            last_error = str(error)

    if fleet_enabled:
        try:
            draw_fleet(collect_fleet())
        except Exception as error:
            last_error = str(error)
