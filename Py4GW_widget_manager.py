"""Py4GW widget host — the always-on script the C++ DLL runs (`g_widget_host`).

The DLL calls this module's ``main()`` **every draw frame** (via ExecuteDraw, which runs both
``draw()`` and ``main()``). It has three jobs now:

  1. **Bootstrap once** — resolve the manager settings key, discover widgets, apply the saved
     enabled-state (forcing System widgets on). Widgets then run via their C++ PyCallbacks.
  2. **Render the launchpad every frame** — the launchpad (LaunchBar) is the widget-manager UI,
     and this always-on host is where it must be drawn (HEAD drew the old WM UI here). Without
     this the launchpad has no host and nothing appears on screen.
  3. **Serve library reloads** — drop the whole project module tree and rebuild it in place, so
     code edits land without restarting the client.

Both bootstrap and launchpad are made bulletproof: a missing/broken settings file must never stop
the launchpad — the cornerstone UI — from rendering.

**Nothing from the reloadable tree may be bound at module level here.** This module is the anchor
a reload re-enters through; a name captured at import would still point into the dropped modules
afterwards. Every ``Core`` import lives inside a function, and ``get_widget_handler`` below is a
re-resolving wrapper rather than a re-export, because ~40 bot scripts import it from this module.
Editing this file (or ``py4gw_library_reload.py``) still needs a client restart.
"""

import os

import py4gw_library_reload

MODULE_NAME = "Widget Manager"

INI_KEY = ""
INI_PATH = "Widgets/WidgetManager"
INI_FILENAME = "WidgetManager.ini"

widget_manager = None
launchpad_register = None

# Widgets boot some callbacks from their first draw(), which has not happened when the reload
# returns. Auditing that same frame reports those as lost when they are only late.
AUDIT_DELAY_FRAMES = 30
pending_audit_before = None
pending_audit_frames = 0


def _log(msg: str) -> None:
    try:
        import PySystem

        PySystem.Console.Log(MODULE_NAME, msg, PySystem.Console.MessageType.Warning)
    except Exception:
        pass


def get_widget_handler():
    """Resolve the current WidgetHandler singleton.

    Deliberately not a re-export: a library reload replaces the WidgetManager module and its
    singleton, and the ~40 scripts that do ``from Py4GW_widget_manager import get_widget_handler``
    bind this function object once. Looking the handler up per call keeps them correct across a
    reload; a re-export would hand them the dead one.
    """
    from Core.py4gwcorelib_src.WidgetManager import get_widget_handler as resolve

    return resolve()


def _handler():
    global widget_manager
    if widget_manager is None:
        try:
            widget_manager = get_widget_handler()
        except Exception as exc:
            _log("widget handler unavailable (will retry): %s" % exc)
            return None
    return widget_manager


def _bootstrap_once() -> None:
    """Resolve settings + discover widgets + apply saved state. Retries each frame until done."""

    global INI_KEY
    if INI_KEY:
        return
    handler = _handler()
    if handler is None:
        return
    try:
        from Core.py4gwcorelib_src.Settings import Settings

        if not os.path.exists(INI_PATH):
            os.makedirs(INI_PATH, exist_ok=True)

        cfg = Settings(f"{INI_PATH}/{INI_FILENAME}", "account")
        key = cfg.name
        if not key:
            return  # settings not ready yet — retry next frame (launchpad still renders below)

        # Order is load-bearing: MANAGER_INI_KEY must be set before discovery (it reads each
        # widget's saved-enabled state during load), then _apply_ini_configuration re-applies
        # and force-enables System widgets.
        INI_KEY = key
        handler.MANAGER_INI_KEY = INI_KEY
        handler.discover()
        handler.enable_all = bool(cfg.get_bool("Configuration", "enable_all", True))
        handler._apply_ini_configuration()
    except Exception as exc:
        _log("bootstrap error (will retry): %s" % exc)


def _register_launchpad() -> None:
    global launchpad_register
    try:
        if launchpad_register is None:
            from Core.py4gwcorelib_src.launch_bar.launchpad import register_launchpad_once

            launchpad_register = register_launchpad_once
        launchpad_register()
    except Exception as exc:
        launchpad_register = None
        _log("launchpad registration error (will retry): %s" % exc)


def perform_pending_reload() -> bool:
    """Rebuild the whole project module tree in place. Returns True if a reload ran.

    Runs from the host rather than from a widget because the widgets are part of what gets
    dropped -- a widget cannot survive re-executing itself from inside its own frame callback.
    """
    global INI_KEY, widget_manager, launchpad_register
    global pending_audit_before, pending_audit_frames

    handler = widget_manager
    if handler is None or not getattr(handler, "reload_requested", False):
        return False
    handler.reload_requested = False

    try:
        before = py4gw_library_reload.callback_names()
        py4gw_library_reload.drain_game_thread()
        py4gw_library_reload.teardown_callbacks()
        # Must precede the purge: it needs the live modules to reach the mapping.
        py4gw_library_reload.release_shared_memory()
        dropped = py4gw_library_reload.purge()

        widget_manager = None
        launchpad_register = None
        INI_KEY = ""
        if _handler() is None:
            _log("reload could not rebuild the widget handler; a client restart is needed")
            return False

        _bootstrap_once()
        _register_launchpad()
        pending_audit_before = before
        pending_audit_frames = AUDIT_DELAY_FRAMES
        py4gw_library_reload.log("reloaded %d modules" % len(dropped))
        return True
    except Exception as exc:
        _log("library reload failed: %s" % exc)
        return False


def run_pending_audit() -> None:
    global pending_audit_before, pending_audit_frames

    if pending_audit_before is None:
        return
    pending_audit_frames -= 1
    if pending_audit_frames > 0:
        return
    before = pending_audit_before
    pending_audit_before = None
    try:
        py4gw_library_reload.audit_callbacks(before, py4gw_library_reload.callback_names())
    except Exception as exc:
        _log("callback audit failed: %s" % exc)


def update():
    return  # widgets run via C++ callbacks; nothing on the update loop here


def draw():
    return  # nothing here; the launchpad renders via its own registered Draw callback


def main():
    """Called every draw frame by the widget host: lifecycle only. Registers the launchpad
    callback once, runs the settings/discovery bootstrap once, and serves a pending library
    reload. The launchpad itself renders through its own Draw callback, so this host's
    steady-state per-frame cost is ~nil."""

    _register_launchpad()
    _bootstrap_once()
    perform_pending_reload()
    run_pending_audit()


if __name__ == "__main__":
    main()
