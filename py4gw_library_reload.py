"""In-process reload of the project module tree, without restarting the client.

Lives at the repo root on purpose. Everything under ``LIBRARY_ROOTS`` gets dropped from
``sys.modules`` during a reload, so the code performing the drop must sit outside those
roots or it would delete itself mid-call. This file and ``Py4GW_widget_manager.py`` are
therefore the two modules a reload cannot refresh -- editing either still needs a
client restart.

Native pybind11 modules (``Py4GW``, ``PySystem``, ``PyImGui``, ...) are never dropped:
re-importing one re-runs its init, which re-registers its C++ types, and pybind11 raises
on the duplicate. They are safe by construction here because the purge is opt-in by root
rather than opt-out by exclusion list.
"""

import sys

import PyCallback
import PySystem

LIBRARY_ROOTS = ("Core", "HeroAI", "Widgets", "Sources", "Bots", "bot_factory")

DYNAMIC_PREFIXES = ("py4gw_widget_", "py4gw_script_")

ANCHOR_MODULES = ("Py4GW_widget_manager", "py4gw_library_reload")

# Frame callbacks the C++ side owns rather than Python. Anything named here survives
# teardown. Populated from what audit_callbacks() reports after a real reload -- a name
# that vanishes and never comes back was not ours to remove.
KEEP_CALLBACK_NAMES: set[str] = set()

MODULE_NAME = "LibraryReload"


def log(message: str, error: bool = False) -> None:
    try:
        level = PySystem.Console.MessageType.Error if error else PySystem.Console.MessageType.Info
        PySystem.Console.Log(MODULE_NAME, message, level)
    except Exception:
        pass


def purgeable(roots=LIBRARY_ROOTS) -> list[str]:
    out = []
    for name in list(sys.modules):
        if name in ANCHOR_MODULES:
            continue
        if name.split(".")[0] in roots or name.startswith(DYNAMIC_PREFIXES):
            out.append(name)
    return sorted(out)


def purge(roots=LIBRARY_ROOTS) -> list[str]:
    dropped = purgeable(roots)
    for name in dropped:
        sys.modules.pop(name, None)
    return dropped


def callback_names() -> list[str]:
    try:
        return [str(entry[1]) for entry in PyCallback.PyCallback.GetCallbackInfo()]
    except Exception:
        return []


def teardown_callbacks() -> list[str]:
    """Drop every frame callback so re-imported modules can register cleanly.

    Purging a module does not stop its functions running: the C++ scheduler holds the
    function object, and that object still carries the dead module's globals. Without
    this the old code keeps ticking alongside the new one forever.
    """
    removed = []
    try:
        inventory = PyCallback.PyCallback.GetCallbackInfo()
    except Exception as exc:
        log("could not read the callback registry: %s" % exc, error=True)
        return removed

    for entry in inventory:
        try:
            callback_id, name = entry[0], str(entry[1])
        except Exception:
            continue
        if name in KEEP_CALLBACK_NAMES:
            continue
        try:
            if PyCallback.PyCallback.RemoveById(callback_id):
                removed.append(name)
        except Exception as exc:
            log("could not remove callback '%s': %s" % (name, exc), error=True)
    return removed


def audit_callbacks(before: list[str], after: list[str]) -> list[str]:
    """Report callbacks that were torn down and never came back.

    Run this several frames after the reload, not in the same frame: callbacks booted from a
    widget's first draw() (loot_filters, recolor_beacons and friends, via System Settings) have
    not been re-registered yet when the reload returns, and auditing immediately reports them
    as lost when they are merely late.

    A name that survives the delay is either owned by the C++ side -- add it to
    KEEP_CALLBACK_NAMES -- or registered by Python code whose trigger no longer runs, in which
    case the trigger is the bug and the name does NOT belong in that list. Keeping a Python
    callback would pin a function from a purged module and defeat the reload.
    """
    missing = sorted(set(before) - set(after))
    for name in missing:
        log(
            "callback '%s' did not re-register after reload -- if C++ owns it add it to "
            "KEEP_CALLBACK_NAMES in py4gw_library_reload.py, otherwise find what used to "
            "register it" % name,
            error=True,
        )
    return missing


def drain_game_thread() -> None:
    """Discard queued game-thread actions belonging to modules about to be dropped."""
    try:
        import PyGameThread

        PyGameThread.clear_calls()
    except Exception:
        pass


# Mappings that refused to close on a previous reload. Held deliberately: a strong reference
# keeps SharedMemory.__del__ from firing at some random later frame and printing a BufferError
# traceback nobody can act on. Retried on each reload, so this drains rather than grows.
SHARED_MEMORY_GRAVEYARD: list = []


def detach_shared_memory() -> list:
    """Collect every live shared-memory mapping so they can be closed after the purge.

    Swept generically rather than by name: there are at least three independent mappings
    (multibox in Core/GlobalCache/SharedMemory.py, system in native_src/ShMem/SysShaMem.py,
    team viewer in HeroAI/team_viewer_broadcast.py), and hardcoding them means every new one
    silently leaks. Leaves each owner's ``shm`` attribute alone -- the multibox manager
    re-attaches whenever it finds that slot empty (SharedMemory.py:104), which would just spawn
    a replacement mapping nothing ever closes.
    """
    try:
        from Core.GlobalCache import GLOBAL_CACHE

        try:
            GLOBAL_CACHE.Coroutines.clear()
        except Exception:
            pass
        try:
            from Core.py4gwcorelib_src.FrameCache import FRAME_CACHE

            FRAME_CACHE.clear()
        except Exception:
            pass
    except Exception as exc:
        log("could not clear caches before reload: %s" % exc, error=True)

    try:
        import gc
        from multiprocessing import shared_memory

        return [obj for obj in gc.get_objects() if isinstance(obj, shared_memory.SharedMemory)]
    except Exception as exc:
        log("could not enumerate shared memory (mappings will leak): %s" % exc, error=True)
        return []


def close_shared_memory(holders: list) -> None:
    """Close the old mappings. Must run AFTER the purge.

    Readers hand out ctypes views over the mapping (``AllAccounts.from_buffer(shm.buf)``,
    ``HeroAITeamViewerStruct.from_buffer``), and those views outlive the call -- HeroAI's
    PartyCache keeps a dict of AccountStruct harvested from them. They only become unreachable
    once the purge drops the modules holding them, so closing any earlier raises
    ``BufferError: cannot close exported pointers exist``.

    Anything that still will not close is parked in the graveyard and retried next reload, by
    which point its holders are a full generation gone. The regions themselves are owned by the
    C++ side, so closing this process's view is safe; the rebuilt readers re-attach.
    """
    import gc

    SHARED_MEMORY_GRAVEYARD.extend(h for h in holders if h is not None)
    gc.collect()

    pinned = []
    for holder in SHARED_MEMORY_GRAVEYARD:
        try:
            holder.close()
        except BufferError:
            pinned.append(holder)
        except Exception:
            pass

    SHARED_MEMORY_GRAVEYARD[:] = pinned
    if pinned:
        log("%d shared memory mapping(s) still pinned; retrying next reload" % len(pinned))
