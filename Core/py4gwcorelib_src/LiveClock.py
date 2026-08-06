"""Live millisecond clock, safe to import from anywhere."""

import ctypes

WIN_GET_TICK_COUNT64 = ctypes.windll.kernel32.GetTickCount64
WIN_GET_TICK_COUNT64.restype = ctypes.c_uint64
WIN_GET_TICK_COUNT64.argtypes = []


def GetLiveTimestamp() -> int:
    """Milliseconds since Windows booted.

    Same clock and epoch as `PySystem.get_tick_count64`, but read live instead of
    from the once-per-rendered-frame cache, so it keeps advancing while a client
    is minimised. Machine-wide, so every account agrees on the value, and free of
    the midnight rollover that `Utils.GetBaseTimestamp` has.

    Deliberately dependency-free: the shared-memory and whiteboard modules import
    it, and anything heavier would risk an import cycle.
    """
    return int(WIN_GET_TICK_COUNT64())
