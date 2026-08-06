"""Measures which Py4GW loops survive a minimised client."""

import ctypes
import time
from dataclasses import dataclass

import PyGameThread
import PyImGui
import PySystem

from Core import Map
from Core import Routines
from Core import ThrottledTimer
from Core import Utils
from Core.native_src.methods.PlayerMethods import PlayerMethods

MODULE_NAME = "Loop Census"
MODULE_ICON = "Textures/Module_Icons/Frame Limiter.png"

SAMPLE_INTERVAL_MS = 1000
CHAT_INTERVAL_MS = 5000
RING_SIZE = 180

# The strongest cross-account signal: an emote is visible on every other client in
# the instance, so it needs no chat channel, party or district to line up.
EMOTE_COMMAND = "wave"

# GW's send-side channel prefixes. PlayerMethods.SendChat rejects anything outside
# ! @ # $ % " / — cycle with the panel button if the line lands in the wrong tab.
CHAT_CHANNELS = ('"', '!', '#', '$', '%')


@dataclass
class Sample:
    update_hz: float
    main_hz: float
    draw_hz: float
    enqueued: int
    executed: int
    frame_tick_delta: int
    minimized: bool
    background: bool
    map_valid: bool


update_ticks = 0
main_ticks = 0
draw_ticks = 0
enqueued = 0
executed = 0

sample_timer = ThrottledTimer(SAMPLE_INTERVAL_MS)
chat_timer = ThrottledTimer(CHAT_INTERVAL_MS)
last_sample_time = time.perf_counter()
last_frame_tick = 0

samples = []
chat_enabled = False
emote_enabled = False
channel_index = 0

SW_HIDE = 0
SW_SHOW = 5
HIDE_TEST_MS = 15000

USER32 = ctypes.WinDLL("user32", use_last_error=True)

hide_timer = ThrottledTimer(HIDE_TEST_MS)
hiding = False

# Hang watchdog. IsHungAppWindow is the exact test Windows uses before it ghosts a
# window as "Not Responding" (message pump silent > 5s), so recording its
# transitions tells us when the GW main thread starved and for how long. gt_lag is
# the age of the last executed heartbeat — it separates "game thread stalled" from
# "only the message pump stalled".
HANG_DOCUMENT = "HANG.json"
HANG_EVENTS_MAX = 50
GT_STALL_ALERT_MS = 3000

hang_events: list[str] = []
window_hung = False
hung_since_ms = 0
last_gt_ran_ms = 0
map_valid_since_ms = 0
gt_stall_alerted = False


def note_game_thread_ran():
    global executed, last_gt_ran_ms
    executed += 1
    last_gt_ran_ms = int(Utils.GetLiveTimestamp())


def is_window_hung() -> bool:
    try:
        hwnd = int(PySystem.Console.get_gw_window_handle() or 0)
        if not hwnd:
            return False
        return bool(USER32.IsHungAppWindow(ctypes.c_void_p(hwnd)))
    except Exception:
        return False


def record_hang_event(line: str) -> None:
    from Core.py4gwcorelib_src.JsonFactory import JsonFactory

    hang_events.append(line)
    if len(hang_events) > HANG_EVENTS_MAX:
        del hang_events[0 : len(hang_events) - HANG_EVENTS_MAX]

    document = JsonFactory(HANG_DOCUMENT)
    document.set_json("events", list(hang_events))
    document.save()

    PySystem.Console.Log(MODULE_NAME, line, PySystem.Console.MessageType.Warning)


def take_sample(elapsed):
    global update_ticks, main_ticks, draw_ticks, last_frame_tick

    frame_tick = PySystem.get_tick_count64()
    sample = Sample(
        update_hz=update_ticks / elapsed,
        main_hz=main_ticks / elapsed,
        draw_hz=draw_ticks / elapsed,
        enqueued=enqueued,
        executed=executed,
        frame_tick_delta=frame_tick - last_frame_tick,
        minimized=PySystem.window.is_window_minimized(),
        background=PySystem.window.is_window_in_background(),
        map_valid=Routines.Checks.Map.MapValid(),
    )
    last_frame_tick = frame_tick
    update_ticks = 0
    main_ticks = 0
    draw_ticks = 0
    return sample


def format_line(sample):
    tick_state = "frozen" if sample.frame_tick_delta == 0 else "+%d" % sample.frame_tick_delta
    return "[census] upd=%.0f/s main=%.0f/s gt=%d/%d tick=%s min=%d map=%s" % (
        sample.update_hz,
        sample.main_hz,
        sample.executed,
        sample.enqueued,
        tick_state,
        int(sample.minimized),
        "ok" if sample.map_valid else "no",
    )


def send_chat_report(line):
    channel = CHAT_CHANNELS[channel_index]
    if PlayerMethods.SendChat(channel, line):
        return True
    PySystem.Console.Log(
        MODULE_NAME,
        "SendChat rejected the line (channel=%s len=%d)" % (channel, len(line)),
        PySystem.Console.MessageType.Warning,
    )
    return False


def set_window_state(command: int) -> None:
    try:
        hwnd = int(PySystem.Console.get_gw_window_handle() or 0)
        if hwnd:
            # Async so a busy main thread can never block this update loop.
            USER32.ShowWindowAsync(ctypes.c_void_p(hwnd), command)
    except Exception as error:
        PySystem.Console.Log(MODULE_NAME, "ShowWindow failed: %s" % error, PySystem.Console.MessageType.Error)


def start_hide_test() -> None:
    """Hide the window for a fixed spell, then bring it back.

    SW_HIDE is not SW_MINIMIZE: a hidden window is not iconic, so GW's own
    minimised check does not see it and may keep rendering. The census rows
    recorded while hidden answer that. Timed and driven from update() so it always
    restores itself — a hidden window has no panel to click.
    """
    global hiding

    hiding = True
    hide_timer.Reset()
    set_window_state(SW_HIDE)


def watch_for_hangs(sample):
    global window_hung, hung_since_ms, map_valid_since_ms, last_gt_ran_ms, gt_stall_alerted

    now_ms = int(Utils.GetLiveTimestamp())

    if sample.map_valid:
        if map_valid_since_ms == 0:
            # Fresh map: seed the heartbeat stamp so lag doesn't count load time.
            map_valid_since_ms = now_ms
            last_gt_ran_ms = now_ms
    else:
        map_valid_since_ms = 0
        gt_stall_alerted = False

    gt_lag_ms = 0
    if map_valid_since_ms and (now_ms - map_valid_since_ms) > GT_STALL_ALERT_MS:
        gt_lag_ms = now_ms - last_gt_ran_ms

    hung_now = is_window_hung()
    if hung_now and not window_hung:
        hung_since_ms = now_ms
        record_hang_event(
            "%s HUNG start | min=%d map=%s loading=%d gt_lag=%dms upd=%.0f/s"
            % (
                time.strftime("%H:%M:%S"),
                int(sample.minimized),
                "ok" if sample.map_valid else "no",
                int(Map.IsMapLoading()),
                gt_lag_ms,
                sample.update_hz,
            )
        )
    elif window_hung and not hung_now:
        record_hang_event("%s HUNG end | lasted=%dms" % (time.strftime("%H:%M:%S"), now_ms - hung_since_ms))
    window_hung = hung_now

    if gt_lag_ms > GT_STALL_ALERT_MS:
        if not gt_stall_alerted:
            gt_stall_alerted = True
            record_hang_event(
                "%s GT-STALL | heartbeat %dms old, min=%d loading=%d hung=%d"
                % (
                    time.strftime("%H:%M:%S"),
                    gt_lag_ms,
                    int(sample.minimized),
                    int(Map.IsMapLoading()),
                    int(hung_now),
                )
            )
    elif gt_stall_alerted and gt_lag_ms < 1500:
        gt_stall_alerted = False
        record_hang_event("%s GT-STALL recovered" % time.strftime("%H:%M:%S"))


def update():
    global update_ticks, last_sample_time, enqueued, hiding

    update_ticks += 1

    if hiding and hide_timer.IsExpired():
        hiding = False
        set_window_state(SW_SHOW)

    if not sample_timer.IsExpired():
        return
    sample_timer.Reset()

    now = time.perf_counter()
    elapsed = now - last_sample_time
    last_sample_time = now
    if elapsed <= 0:
        return

    sample = take_sample(elapsed)
    samples.append(sample)
    if len(samples) > RING_SIZE:
        del samples[0]

    line = format_line(sample)
    PySystem.Console.Log(MODULE_NAME, line, PySystem.Console.MessageType.Info)

    watch_for_hangs(sample)

    # enqueue is a silent no-op while the map is not ready, which would read as a
    # stalled game thread; skipping keeps enqueued/executed comparable.
    if not sample.map_valid:
        return

    enqueued += 1
    PyGameThread.enqueue(note_game_thread_ran)

    if not chat_timer.IsExpired():
        return
    chat_timer.Reset()

    if emote_enabled:
        PlayerMethods.SendChatCommand(EMOTE_COMMAND)
    if chat_enabled:
        send_chat_report(line)


def render_panel():
    global chat_enabled, emote_enabled, channel_index

    if not PyImGui.begin(MODULE_NAME):
        PyImGui.end()
        return

    emote_enabled = PyImGui.checkbox("Emote every 5s (watch from another client)", emote_enabled)
    chat_enabled = PyImGui.checkbox("Also report to chat", chat_enabled)
    PyImGui.text("Enable these before minimising — the panel stops drawing once you do.")

    if PyImGui.button("channel: %s" % CHAT_CHANNELS[channel_index]):
        channel_index = (channel_index + 1) % len(CHAT_CHANNELS)
    PyImGui.same_line()
    if PyImGui.button("Send test line"):
        sample = samples[-1] if samples else None
        send_chat_report(format_line(sample) if sample else "[census] test")
    PyImGui.same_line()
    if PyImGui.button("Test emote"):
        PlayerMethods.SendChatCommand(EMOTE_COMMAND)
    PyImGui.same_line()
    if PyImGui.button("Hide %ds" % (HIDE_TEST_MS // 1000)):
        start_hide_test()

    PyImGui.text("Samples: %d   Game thread: %d/%d" % (len(samples), executed, enqueued))

    if hang_events:
        PyImGui.separator()
        PyImGui.text("Hang events (also in json/<account>/%s):" % HANG_DOCUMENT)
        for event_line in reversed(hang_events[-10:]):
            PyImGui.text(event_line)

    PyImGui.separator()

    if PyImGui.begin_table("LoopCensusRows", 7):
        for header in ("upd/s", "main/s", "draw/s", "gt", "tick", "min", "map"):
            PyImGui.table_setup_column(header)
        PyImGui.table_headers_row()

        for sample in reversed(samples):
            PyImGui.table_next_row()
            PyImGui.table_next_column()
            PyImGui.text("%.0f" % sample.update_hz)
            PyImGui.table_next_column()
            PyImGui.text("%.0f" % sample.main_hz)
            PyImGui.table_next_column()
            PyImGui.text("%.0f" % sample.draw_hz)
            PyImGui.table_next_column()
            PyImGui.text("%d/%d" % (sample.executed, sample.enqueued))
            PyImGui.table_next_column()
            PyImGui.text("frozen" if sample.frame_tick_delta == 0 else "+%d" % sample.frame_tick_delta)
            PyImGui.table_next_column()
            PyImGui.text("yes" if sample.minimized else "-")
            PyImGui.table_next_column()
            PyImGui.text("ok" if sample.map_valid else "-")

        PyImGui.end_table()

    PyImGui.end()


def main():
    global main_ticks
    main_ticks += 1


def draw():
    global draw_ticks
    draw_ticks += 1
    render_panel()


__all__ = ['update', 'main', 'draw']
