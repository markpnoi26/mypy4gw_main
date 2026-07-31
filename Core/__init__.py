import traceback
import math
from enum import Enum
import time
from time import sleep
import inspect
import sys
from dataclasses import dataclass, field
import os
import subprocess
import builtins as builtins_module


# *******************************************************************************
# ********* Start of manual import of external libraries  ***********************
# *******************************************************************************
def find_system_python():
    # Cached on builtins, not in a module global: a library reload re-executes this file,
    # and spawning a shell to run `where python` on every reload is a visible hitch.
    cached = getattr(builtins_module, "py4gw_system_python_path", None)
    if cached is not None:
        return cached or None
    try:
        python_path = subprocess.check_output("where python", shell=True).decode().split("\n")[0].strip()
        if python_path and os.path.exists(python_path):
            resolved = os.path.dirname(python_path)
            builtins_module.py4gw_system_python_path = resolved
            return resolved
    except Exception:
        pass
    resolved = sys.prefix if sys.prefix and os.path.exists(sys.prefix) else None
    builtins_module.py4gw_system_python_path = resolved or ""
    return resolved


system_python_path = find_system_python()
if system_python_path:
    site_packages_path = os.path.join(system_python_path, "Lib", "site-packages")
    if site_packages_path not in sys.path:
        sys.path.append(site_packages_path)
    # Unguarded this prepends on every import; a reload loop would grow the environment
    # block until Windows refuses to spawn anything.
    if not os.environ.get("PATH", "").startswith(site_packages_path + os.pathsep):
        os.environ["PATH"] = site_packages_path + os.pathsep + os.environ["PATH"]


# *******************************************************************************
# ********* End of manual import of external libraries  ***********************
# *******************************************************************************


import Py4GW
import PySystem
import PyGameThread
import PyPing
import PyScanner
import PyImGui
import PyCallback

# ── Legacy Console: inject into builtins so all legacy code (100+ bare
#    `Console.xxx()` and `ConsoleLog()` calls) works without per-file migration. ──
import builtins
from .py4gwcorelib_src.Console import ConsoleLog

builtins.Console = PySystem.Console
builtins.ConsoleLog = ConsoleLog


import PyAgent
import PyPlayer
import PyParty
import PyItem
import PyInventory

# ── Reforged: PyInventory.Bag.GetItems() returns dicts.
#    Convert each dict to a SimpleNamespace so all legacy attribute-access
#    callers (item.model_id, item.item_id, item.quantity, item.slot, …)
#    work retroactively. ──
import types as _types

_Bag = PyInventory.Bag

# Guarded because PyInventory.Bag is a native class that survives a library reload: without
# this, re-importing Core captures the already-wrapped function and wraps it again, stacking
# one layer per reload.
if not getattr(_Bag.GetItems, "py4gw_wrapped", False):
    _orig_Bag_GetItems = _Bag.GetItems

    def _wrapped_Bag_GetItems(self):
        items = _orig_Bag_GetItems(self)
        return [_types.SimpleNamespace(**item) if isinstance(item, dict) else item for item in (items or [])]

    _wrapped_Bag_GetItems.py4gw_wrapped = True
    _Bag.GetItems = _wrapped_Bag_GetItems

import PySkill
import PySkillbar
import PyMerchant
import PyEffects
import PyKeystroke
import PyOverlay
import PyQuest
import PyPathing
import PyUIManager
import PyCamera
import PyDXOverlay
import PyAgentEvents
import PyListeners

# Inject PySystem, PyPing, PyGameThread into builtins so all widget modules
# (loaded dynamically via importlib) can access them without explicit import.
import builtins

builtins.PySystem = PySystem
builtins.PyPing = PyPing
builtins.PyGameThread = PyGameThread
builtins.PyDXOverlay = PyDXOverlay
builtins.PyAgentEvents = PyAgentEvents

from .enums import *
from .ImGui_src.IconsFontAwesome5 import IconsFontAwesome5
from .Map import *
from .ImGui import *
from .model_data import *
from .Agent import *
from .Player import *
from .AgentArray import *
from .Party import *
from .Item import *
from .ItemArray import *
from .Inventory import *
from .Skill import *
from .Skillbar import *
from .Effect import *
from .Merchant import *
from .Listeners import *
from .Quest import *
from .Camera import *
from .Scanner import *

from .Py4GWcorelib import *
from .Overlay import *
from .DXOverlay import *
from .UIManager import *
from .Routines import *
from .SkillManager import *
from .GlobalCache import GLOBAL_CACHE
from .Pathing import AutoPathing
from .BldMgrBT import BldMgrBT
from .BldMgrBT import BTBuildMgr
from .Botting import BottingClass as Botting
from .Context import GWContext

# from .CombatEvents import CombatEventQueue, CombatEvents, COMBAT_EVENTS
from .Database import Database
from .GWUI import GWUI

from .py4gwcorelib_src.Profiling import ProfilingRegistry, SimpleProfiler
from .py4gwcorelib_src.FrameCache import FRAME_CACHE, frame_cache
from .py4gwcorelib_src.WidgetManager import WidgetHandler, Widget
from .py4gwcorelib_src.FileDialog import FileDialog
from .py4gwcorelib_src.JsonFactory import JsonFactory

from .native_src.internals.types import Vec2f, Vec3f, GamePos

traceback = traceback
math = math
Enum = Enum
time = time
sleep = sleep
inspect = inspect
dataclass = dataclass
field = field

Vec2f = Vec2f
Vec3f = Vec3f
GamePos = GamePos

Py4Gw = Py4GW
Py4GW = Py4GW
PySystem = PySystem
PyGameThread = PyGameThread
PyPing = PyPing
PyScanner = PyScanner
PyImGui = PyImGui

PyAgent = PyAgent
PyPlayer = PyPlayer
PyParty = PyParty
PyItem = PyItem
PyInventory = PyInventory
PySkill = PySkill
PySkillbar = PySkillbar
PyMerchant = PyMerchant
PyEffects = PyEffects
PyPathing = PyPathing
PyOverlay = PyOverlay
PyQuest = PyQuest
PyUIManager = PyUIManager
PyCamera = PyCamera
PyDXOverlay = PyDXOverlay
PyAgentEvents = PyAgentEvents
GLOBAL_CACHE = GLOBAL_CACHE
AutoPathing = AutoPathing
IconsFontAwesome5 = IconsFontAwesome5
Database = Database
# CombatEvents = CombatEvents
# COMBAT_EVENTS = COMBAT_EVENTS
ProfilingRegistry = ProfilingRegistry
SimpleProfiler = SimpleProfiler
FRAME_CACHE = FRAME_CACHE
frame_cache = frame_cache
WidgetHandler = WidgetHandler
Widget = Widget
GWUI = GWUI


# redirect print output to Py4GW Console
class Py4GWLogger:
    def write(self, message):
        if message.strip():  # Avoid logging empty lines
            PySystem.Console.Log("print:", f"{message.strip()}", PySystem.Console.MessageType.Info)

    def flush(self):
        pass  # Required for sys.stdout but does nothing


class Py4GWLoggerError:
    def write(self, message):
        if message.strip():  # Avoid logging empty lines
            PySystem.Console.Log("print:", f"{message.strip()}", PySystem.Console.MessageType.Error)

    def flush(self):
        pass  # Required for sys.stdout but does nothing


# Redirect Python's print output to Py4GW Console
sys.stdout = Py4GWLogger()
sys.stderr = Py4GWLoggerError()
