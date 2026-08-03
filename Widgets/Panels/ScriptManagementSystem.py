MODULE_NAME = "Script Management System"
MODULE_ICON = "Textures/Module_Icons/Template.png"
OPTIONAL = True

import os
import traceback

import PyImGui
import PySystem

from Core import Color
from Core import ImGui
from Core.py4gwcorelib_src.script_manager import ScriptRegistry

SCRIPTS_PATH = "Scripts"
RELOAD_DELAY_MS = 350

registry = ScriptRegistry(SCRIPTS_PATH)
loaded = False
search = ""
function_filter = 0
launched_id = ""
last_error = ""
# Discovery is MANUAL. Nothing in the draw path touches the disk — only the
# Rescan button does. It used to call changed_on_disk() every frame to badge
# "changed on disk", which walks seven pack roots and stats all 173 scripts:
# 11ms per frame, two thirds of the frame budget at 60fps, for a badge.
last_scan = ""
# The filtered list, rebuilt only when something it depends on moves. The
# registry's own revision counter is the invalidation key, so a rescan that
# finds nothing new costs one tuple compare.
view_key = None
view_functions = ["(all)"]
view_scripts = []


def log(message, level=None):
    try:
        PySystem.Console.Log(MODULE_NAME, message, level if level is not None else PySystem.Console.MessageType.Info)
    except Exception:
        pass


def script_status():
    try:
        return str(PySystem.script_control.status())
    except Exception as exc:
        return "unavailable (%s)" % exc


def resolve(path):
    if os.path.isabs(path):
        return path
    return os.path.join(PySystem.Console.get_projects_path(), path)


def launch(meta):
    global launched_id, last_error
    full = resolve(meta.path)
    if not os.path.exists(full):
        last_error = "missing: %s" % full
        log(last_error, PySystem.Console.MessageType.Error)
        return
    try:
        PySystem.script_control.defer_stop_load_and_run(full, RELOAD_DELAY_MS)
        launched_id = meta.id
        last_error = ""
        log("launching %s" % meta.name)
    except Exception as exc:
        last_error = str(exc)
        log("launch failed: %s" % traceback.format_exc(), PySystem.Console.MessageType.Error)


def stop():
    global launched_id, last_error
    try:
        PySystem.script_control.stop()
        launched_id = ""
        last_error = ""
        log("stopped")
    except Exception as exc:
        last_error = str(exc)
        log("stop failed: %s" % exc, PySystem.Console.MessageType.Error)


def scan():
    """The only thing in this widget that reads the disk, and it is on a button.

    Reports what the scan actually did, because with no automatic probe there is
    nothing else to tell you whether pressing it was worth it.
    """
    global last_scan
    before = len(registry.scripts)
    try:
        changed = registry.refresh()
    except Exception as exc:
        last_scan = "scan failed: %s" % exc
        log(last_scan, PySystem.Console.MessageType.Error)
        return
    delta = len(registry.scripts) - before
    if not changed:
        last_scan = "no change"
    elif delta:
        last_scan = "%+d script(s)" % delta
    else:
        last_scan = "metadata updated"


def rebuild_view():
    global view_key, view_functions, view_scripts
    key = (registry.revision, search, function_filter)
    if key == view_key:
        return
    view_key = key
    view_functions = ["(all)"] + registry.functions()
    chosen = view_functions[function_filter] if 0 <= function_filter < len(view_functions) else "(all)"
    view_scripts = registry.query(function="" if chosen == "(all)" else chosen, text=search)


def draw_toolbar():
    global search, function_filter

    if PyImGui.button("Rescan##sr"):
        scan()
    PyImGui.same_line(0.0, 6.0)
    PyImGui.text("%d script(s)" % len(registry.scripts))
    if last_scan:
        PyImGui.same_line(0.0, 6.0)
        PyImGui.text_disabled(last_scan)

    search = PyImGui.input_text("Search##sr", search)
    function_filter = PyImGui.combo("Function##sr", function_filter, view_functions)


def draw_row(meta):
    running = meta.id == launched_id
    label = ("> " if running else "  ") + meta.name
    if PyImGui.selectable(label + "##sr_" + meta.id, running):
        launch(meta)
    if PyImGui.is_item_hovered():
        PyImGui.begin_tooltip()
        PyImGui.text(meta.name)
        PyImGui.separator()
        PyImGui.text("function: %s" % (meta.function or "-"))
        PyImGui.text("tags:     %s" % (" ".join(meta.tags) or "-"))
        PyImGui.text("claims:   %s" % (" ".join(meta.claims) or "none"))
        PyImGui.text("path:     %s" % meta.path)
        if meta.error:
            PyImGui.separator()
            PyImGui.text_colored("error: %s" % meta.error, Color(255, 120, 120, 255).to_tuple_normalized())
        PyImGui.end_tooltip()


def draw_widget():
    if not ImGui.Begin("", MODULE_NAME, flags=PyImGui.WindowFlags.AlwaysAutoResize):
        ImGui.End("")
        return

    PyImGui.text("Console: %s" % script_status())
    if launched_id:
        PyImGui.text("Launched: %s" % launched_id)
    if last_error:
        PyImGui.text_colored(last_error, Color(255, 120, 120, 255).to_tuple_normalized())

    if PyImGui.button("Stop##sr"):
        stop()
    PyImGui.separator()

    rebuild_view()
    draw_toolbar()
    PyImGui.separator()

    scripts = view_scripts
    if not scripts:
        PyImGui.text("<no scripts>")
    if PyImGui.begin_child("sr_list", (420.0, 260.0), 1, 0):
        for meta in scripts:
            draw_row(meta)
    PyImGui.end_child()

    errors = registry.errors()
    if errors:
        PyImGui.separator()
        PyImGui.text_colored(
            "%d script(s) with bad metadata" % len(errors), Color(255, 120, 120, 255).to_tuple_normalized()
        )

    ImGui.End("")


def tooltip():
    PyImGui.begin_tooltip()
    PyImGui.text_colored(MODULE_NAME, Color(255, 200, 100, 255).to_tuple_normalized())
    PyImGui.separator()
    PyImGui.text_wrapped("Browse and launch scripts from Scripts/ by their declared metadata.")
    PyImGui.bullet_text("Filter by function, search by name.")
    PyImGui.bullet_text("Launches through PySystem.script_control (one script at a time).")
    PyImGui.bullet_text("Discovery is manual: only Rescan reads the disk, and it imports nothing.")
    PyImGui.end_tooltip()


def main():
    global loaded
    if not loaded:
        registry.reload()
        loaded = True
        log("discovered %d script(s) in %s" % (len(registry.scripts), SCRIPTS_PATH))
    draw_widget()


if __name__ == "__main__":
    main()
