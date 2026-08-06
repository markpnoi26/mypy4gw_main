"""Restores a minimized client for the duration of a map load, then re-minimizes."""

import ctypes

import PyImGui
import PySystem

from Core import GLOBAL_CACHE
from Core import Map
from Core import Routines
from Core import ThrottledTimer
from Core.py4gwcorelib_src.Utils import Utils

MODULE_NAME = "Load Assist"
MODULE_ICON = "Textures/Module_Icons/Frame Limiter.png"

SW_MINIMIZE = 6
SW_RESTORE = 9

# Fast poll: the restore must land before GW's loader reaches its non-pumping
# stretch, or the SW_RESTORE sits unread in the message queue while the loader
# waits for drawing that only a restored window can do — a deadlock that shows
# as "Not Responding" on map load.
POLL_INTERVAL_MS = 50

# Backstop so a check that never clears cannot strand the window on screen.
MAX_HOLD_MS = 30000

USER32 = ctypes.WinDLL("user32", use_last_error=True)

poll_timer = ThrottledTimer(POLL_INTERVAL_MS)
hold_timer = ThrottledTimer(MAX_HOLD_MS)
report_timer = ThrottledTimer(2000)

enabled = True
holding_window_up = False
restores = 0
status = "idle"
announced = False


def log(message: str, level=None) -> None:
    PySystem.Console.Log(MODULE_NAME, message, level or PySystem.Console.MessageType.Info)


def show_window(command: int) -> bool:
    try:
        hwnd = int(PySystem.Console.get_gw_window_handle() or 0)
        if not hwnd:
            log("no GW window handle; cannot move the window", PySystem.Console.MessageType.Warning)
            return False
        # Async: plain ShowWindow waits on the main thread's message pump, which
        # can already be inside the loader and not pumping — that blocked this
        # entire update loop along with it.
        USER32.ShowWindowAsync(ctypes.c_void_p(hwnd), command)
        return True
    except Exception as error:
        log("ShowWindow failed: %s" % error, PySystem.Console.MessageType.Error)
        return False


def minimize_now(reason: str) -> None:
    global holding_window_up, status

    show_window(SW_MINIMIZE)
    holding_window_up = False
    status = "idle"
    log("%s — minimizing" % reason)


def map_is_settling() -> bool:
    """True while the client cannot be trusted to act on this instance yet."""
    if Map.IsMapLoading():
        return True
    return not Routines.Checks.Map.MapValid()


def settle_reason() -> str:
    """Which check is keeping the window up. MapValid is several conditions, and
    knowing which one is stuck is the difference between a fix and a guess."""
    if Map.IsMapLoading():
        return "map loading"
    if not Map.IsMapReady():
        return "map not ready"
    if Map.IsInCinematic():
        return "cinematic"
    if not GLOBAL_CACHE.Party.IsPartyLoaded():
        return "party not loaded"
    return "settled"


def update():
    global holding_window_up, restores, status, announced

    if not announced:
        announced = True
        log("active — will restore a minimized client for map loads")

    if not enabled:
        return
    if not poll_timer.IsExpired():
        return
    poll_timer.Reset()

    if not holding_window_up:
        if not map_is_settling():
            return
        # Keys on the actual window state, NOT on whether frames are arriving — a
        # hidden client must be left alone rather than converted to a minimized one.
        was_minimized = Utils.IsWindowMinimized()
        holding_window_up = True
        hold_timer.Reset()
        report_timer.Reset()
        if was_minimized:
            # A minimized client cannot finish a load; lift it for the duration.
            show_window(SW_RESTORE)
            restores += 1
            status = "restored for map load"
            log("map load while minimized — restoring window")
        else:
            status = "map load — will minimize when valid"
        return

    if map_is_settling():
        # The posted restore can sit unread while the loader is between pumps;
        # keep re-posting until the window actually leaves the iconic state.
        if Utils.IsWindowMinimized():
            show_window(SW_RESTORE)
        if hold_timer.IsExpired():
            minimize_now("held %ds without settling (%s)" % (MAX_HOLD_MS // 1000, settle_reason()))
            return
        if report_timer.IsExpired():
            report_timer.Reset()
            status = "holding: %s" % settle_reason()
            log("still holding — %s" % settle_reason())
        return

    # Map is valid — go straight down, no settle delay. A half-initialized agent
    # read is survivable now: EnsureBuildContract guards the profession decode
    # rather than letting one bad sample abort the tree tick.
    minimize_now("map valid")


def draw():
    global enabled

    if not PyImGui.begin(MODULE_NAME):
        PyImGui.end()
        return

    enabled = PyImGui.checkbox("Restore during map loads", enabled)
    PyImGui.text("Status: %s" % status)
    PyImGui.text("Restores this session: %d" % restores)
    PyImGui.separator()
    PyImGui.text("A minimized client cannot finish loading a map — the textures")
    PyImGui.text("it needs are only uploaded while it is drawing. This lifts the")
    PyImGui.text("window for the load and drops it back afterwards.")

    PyImGui.end()


__all__ = ['update', 'draw']
