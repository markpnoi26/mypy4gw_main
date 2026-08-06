import PyGameThread
import PyImGui
from Core import *
from Core.HotkeyManager import HOTKEY_MANAGER

# do not ever disable this module, it is the main module for everything
MODULE_ALIASES = ['System/Environment Upkeeper.py']
MODULE_NAME = "Environment Upkeeper"
MODULE_ICON = "Textures/Module_Icons/Environment Upkeeper.png"
OPTIONAL = False

__widget__ = {
    "name": "Environment Upkeeper",
    "enabled": True,
    "category": "Coding",
    "subcategory": "Environment",
    "icon": "ICON_TREE",
    "quickdock": False,
    "hidden": True,  ##special category for Environment Upkeeper (do not use)
}


class WidgetConfig:
    def __init__(self):
        self.action_queue_manager = ActionQueueManager()
        # The loot singleton is kept alive by holding it.
        from Core.py4gwcorelib_src.loot_filters import LootFilters

        self.loot_config = LootFilters()

        self.overlay = Overlay()

        self.throttle_action_queue = ThrottledTimer(50)
        self.throttle_transition_queue = ThrottledTimer(50)
        self.throttle_loot_queue = ThrottledTimer(1250)
        self.throttle_merchant_queue = ThrottledTimer(750)
        self.throttle_salvage_queue = ThrottledTimer(325)
        self.throttle_identify_queue = ThrottledTimer(250)
        self.throttle_fast_queue = ThrottledTimer(20)


widget_config = WidgetConfig()


def reset_on_load():
    global widget_config

    widget_config.throttle_action_queue.Reset()
    widget_config.throttle_transition_queue.Reset()
    widget_config.throttle_loot_queue.Reset()
    widget_config.throttle_merchant_queue.Reset()
    widget_config.throttle_salvage_queue.Reset()
    widget_config.throttle_identify_queue.Reset()
    widget_config.throttle_fast_queue.Reset()

    # Resetting all queues
    widget_config.action_queue_manager.ResetAllQueues()


def tooltip():
    PyImGui.begin_tooltip()

    # Title
    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("Environment Upkeeper", title_color.to_tuple_normalized())
    ImGui.pop_font()
    red = ColorPalette.GetColor("red")
    PyImGui.text_colored("This is a system Widget, deactivating it will cause issues.", red.to_tuple_normalized())
    PyImGui.separator()

    # Description
    PyImGui.text("This widget is responsible for managing the environment upkeep tasks")
    PyImGui.text("such as processing action queues for various activities like looting,")
    PyImGui.text("merchant interactions, salvaging, and identifying items. It ensures")
    PyImGui.text("that these tasks are performed efficiently and in a timely manner,")
    PyImGui.text("enhancing the overall experience.")

    PyImGui.spacing()

    # Features
    PyImGui.text_colored("Features:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Manages multiple action queues for different activities.")
    PyImGui.bullet_text("Throttles queue processing to optimize performance.")
    PyImGui.bullet_text("Integrates with Loot Filters for item management.")
    PyImGui.bullet_text("Upkeeps Singletons")

    PyImGui.spacing()

    # Credits
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Developed by Apo")

    PyImGui.end_tooltip()


# The update loop runs faster than the draw loop did; this keeps the cache
# refresh and coroutine drain at roughly their previous cadence.
PUMP_INTERVAL_MS = 16

# PyGameThread.enqueue silently discards the callable while a map is loading, so
# an in-flight pump is not guaranteed to ever release the flag. Re-dispatch after
# this long rather than waiting for a release that may never come.
PUMP_DISPATCH_ESCAPE_MS = 1000

pump_timer = ThrottledTimer(PUMP_INTERVAL_MS)
pump_dispatch_escape = ThrottledTimer(PUMP_DISPATCH_ESCAPE_MS)
pump_in_flight = False


def pump():
    """Drains every ActionQueue and the coroutine list.

    HeroAI's moves, casts and looting depend on this, so a minimised client has to
    keep doing it. Driven from exactly one loop at a time - see update()/draw().
    """
    global widget_config

    if not pump_timer.IsExpired():
        return
    pump_timer.Reset()

    if Routines.Checks.Map.MapValid():
        GLOBAL_CACHE._update_cache()
    else:
        # A map change makes every agent/item id meaningless, so the loot class clears its own
        # session ids. Driving it from here as well keeps the old behaviour when the map is invalid.
        from Core.py4gwcorelib_src.loot_filters import LootFilters

        LootFilters().on_map_change()

    for routine in GLOBAL_CACHE.Coroutines[:]:
        try:
            next(routine)
        except StopIteration:
            GLOBAL_CACHE.Coroutines.remove(routine)

    if Map.IsMapLoading() or Map.IsInCinematic():
        widget_config.action_queue_manager.ResetNonTransitionQueues()

        if widget_config.throttle_transition_queue.IsExpired():
            widget_config.action_queue_manager.ProcessQueue("TRANSITION")
            widget_config.throttle_transition_queue.Reset()
        return

    if not Routines.Checks.Map.MapValid():
        return

    if widget_config.throttle_action_queue.IsExpired():
        widget_config.action_queue_manager.ProcessQueue("ACTION")
        widget_config.throttle_action_queue.Reset()

    if widget_config.throttle_loot_queue.IsExpired():
        widget_config.action_queue_manager.ProcessQueue("LOOT")
        widget_config.throttle_loot_queue.Reset()

    if widget_config.throttle_merchant_queue.IsExpired():
        widget_config.action_queue_manager.ProcessQueue("MERCHANT")
        widget_config.throttle_merchant_queue.Reset()

    if widget_config.throttle_salvage_queue.IsExpired():
        widget_config.action_queue_manager.ProcessQueue("SALVAGE")
        widget_config.throttle_salvage_queue.Reset()

    if widget_config.throttle_identify_queue.IsExpired():
        widget_config.action_queue_manager.ProcessQueue("IDENTIFY")
        widget_config.throttle_identify_queue.Reset()

    if widget_config.throttle_fast_queue.IsExpired():
        widget_config.action_queue_manager.ProcessQueue("FAST")
        widget_config.throttle_fast_queue.Reset()


def pump_on_game_thread():
    global pump_in_flight

    try:
        pump()
    finally:
        pump_in_flight = False


def update():
    """Drives the pump while the draw loop is stalled, via the game thread.

    The queues execute arbitrary game calls - Map.Travel sends a raw UI message -
    and this callback runs on Py4GW's own update thread, where those calls fault.
    Hopping to the game thread puts them back where draw() used to run them.
    """
    global pump_in_flight

    if not Utils.IsDrawLoopStalled():
        return

    if not pump_timer.IsExpired():
        return

    if pump_in_flight and not pump_dispatch_escape.IsExpired():
        return

    pump_in_flight = True
    pump_dispatch_escape.Reset()
    PyGameThread.enqueue(pump_on_game_thread)


def draw():
    global widget_config

    if not Utils.IsDrawLoopStalled():
        pump()

    # Both need a live frame: hotkeys read current input state, texture upkeep
    # needs the device.
    HOTKEY_MANAGER.update()
    widget_config.overlay.UpkeepTextures()
