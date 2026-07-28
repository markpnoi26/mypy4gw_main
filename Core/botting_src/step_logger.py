"""Per-run step logging for Botting FSMs.

Writes one log file per bot Start() at Logs/Levelers/<bot>/<char>_<ts>.log
so a crash, wipe, or manual stop leaves a clear trail of the last step
that was executing.
"""

from __future__ import annotations

import os
import re
import time
import traceback
from typing import TYPE_CHECKING
from typing import Optional

if TYPE_CHECKING:
    from ..Botting import BottingClass


_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _sanitize(name: str) -> str:
    if not name:
        return "unknown"
    return _SAFE.sub("_", name).strip("_") or "unknown"


def _project_root() -> str:
    try:
        import PySystem  # type: ignore

        root = str(PySystem.Console.get_projects_path() or "").strip()
        if root:
            return os.path.normpath(root)
    except Exception:
        pass
    return os.path.normpath(os.getcwd())


def _now_ms() -> int:
    return int(time.time() * 1000)


def _fmt_ts(ms: int) -> str:
    s, msec = divmod(ms, 1000)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s)) + f".{msec:03d}"


class StepLogger:
    """Attach once per BottingClass instance.

    Hooks:
      * fsm.update           — observes state changes each tick
      * fsm.on_transition    — composed to catch poll-based transitions
      * events.on_party_defeated / on_party_wipe / on_death (_fire_once)
      * Botting.Start / Stop / StartAtStep — session lifecycle
    """

    def __init__(self, bot: "BottingClass"):
        self.bot = bot
        self._attached = False
        self._file_path: Optional[str] = None
        self._run_started_ms: Optional[int] = None
        self._current_step: Optional[str] = None
        self._current_step_started_ms: Optional[int] = None
        self._last_observed_step: Optional[str] = None

    def attach(self) -> None:
        if self._attached:
            return
        try:
            self._wrap_fsm()
            self._wrap_events()
            self._wrap_bot_lifecycle()
            self._attached = True
        except Exception as e:
            self._safe_log_exception("attach failed", e)

    def _resolve_char_name(self) -> str:
        try:
            from ..Player import Player  # type: ignore

            name = Player.GetName() or ""
            return _sanitize(name)
        except Exception:
            return "unknown"

    def _ensure_file(self) -> Optional[str]:
        if self._file_path:
            return self._file_path
        try:
            char = self._resolve_char_name()
            bot_dir = _sanitize(self.bot.bot_name)
            ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
            log_dir = os.path.join(_project_root(), "Logs", "Levelers", bot_dir)
            os.makedirs(log_dir, exist_ok=True)
            self._file_path = os.path.join(log_dir, f"{char}_{ts}.log")
            return self._file_path
        except Exception:
            return None

    def _write(self, kind: str, detail: str = "") -> None:
        path = self._ensure_file()
        if not path:
            return
        line = f"{_fmt_ts(_now_ms())} | {kind:<15} | {detail}\n"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def _safe_log_exception(self, where: str, e: BaseException) -> None:
        try:
            tb = traceback.format_exc()
            self._write("LOGGER_ERROR", f"where={where} err={e!r}\n{tb}")
        except Exception:
            pass

    # ---- FSM hooks ----------------------------------------------------------
    def _wrap_fsm(self) -> None:
        fsm = self.bot.config.FSM

        orig_update = fsm.update

        def wrapped_update():
            orig_update()
            try:
                current = fsm.current_state.name if fsm.current_state else None
                if current != self._last_observed_step:
                    self._observe_state_change(current, done=(current is None and fsm.finished))
            except Exception as e:
                self._safe_log_exception("update-observe", e)

        fsm.update = wrapped_update

    def _observe_state_change(self, current: Optional[str], done: bool) -> None:
        now = _now_ms()
        prev = self._last_observed_step
        if prev is not None:
            dur = ""
            if self._current_step_started_ms is not None:
                dur = f" dur={now - self._current_step_started_ms}ms"
            self._write("EXIT", f"step={prev}{dur}")
        if current is not None:
            self._write("ENTER", f"step={current}")
            self._current_step = current
            self._current_step_started_ms = now
        else:
            self._current_step = None
            self._current_step_started_ms = None
        self._last_observed_step = current
        if done and prev is not None:
            self._write("DONE", f"last_step={prev}")

    # ---- Failure event hooks -----------------------------------------------
    def _wrap_events(self) -> None:
        events = self.bot.config.events
        self._wrap_fire(events.on_party_defeated, "PARTY_DEFEATED")
        self._wrap_fire(events.on_party_wipe, "PARTY_WIPE")
        self._wrap_fire(events.on_death, "PLAYER_DEATH")

    def _wrap_fire(self, event_obj, kind: str) -> None:
        try:
            orig_fire = event_obj._fire_once
        except AttributeError:
            return

        def wrapped_fire():
            try:
                step = self._current_step or "unknown"
                self._write(kind, f"step={step}")
            except Exception as e:
                self._safe_log_exception(f"fire-{kind}", e)
            orig_fire()

        event_obj._fire_once = wrapped_fire

    # ---- Bot lifecycle hooks -----------------------------------------------
    def _wrap_bot_lifecycle(self) -> None:
        bot = self.bot
        orig_start = bot.Start
        orig_stop = bot.Stop
        orig_start_at = bot.StartAtStep

        def wrapped_start():
            self._begin_run()
            orig_start()

        def wrapped_stop():
            step = self._current_step
            orig_stop()
            if step:
                self._write("STOP", f"last_step={step}")
            else:
                self._write("STOP", "last_step=<none>")

        def wrapped_start_at(step_name: str):
            # orig_start_at() internally calls Stop() then FSM.jump; run it first
            # so the old-run STOP lands in the old log file, then start a new file.
            orig_start_at(step_name)
            self._begin_run(resume_step=step_name)

        bot.Start = wrapped_start  # type: ignore[assignment]
        bot.Stop = wrapped_stop  # type: ignore[assignment]
        bot.StartAtStep = wrapped_start_at  # type: ignore[assignment]

    def _begin_run(self, resume_step: Optional[str] = None) -> None:
        # New run = new file.
        self._file_path = None
        self._run_started_ms = _now_ms()
        self._current_step = None
        self._current_step_started_ms = None
        self._last_observed_step = None
        char = self._resolve_char_name()
        if resume_step:
            self._write("START", f"bot={self.bot.bot_name} char={char} resume_at={resume_step}")
        else:
            self._write("START", f"bot={self.bot.bot_name} char={char}")
