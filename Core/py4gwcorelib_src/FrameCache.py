from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Hashable, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


@dataclass(frozen=True)
class FrameCacheKey:
    category: str
    source_lib: str
    function_name: str
    key: Hashable = ""


class FrameCache:
    # Per-thread storage. The draw loop and the update loop both read through this
    # cache and both invalidate it; a single shared dict lets one thread clear
    # entries the other is mid-read of, and those entries include ctypes views into
    # shared memory, so the dangling view faults the client rather than raising.
    _instance: "FrameCache | None" = None
    _local: threading.local
    _callback_name: str
    _update_callback_name: str
    _callback_registered: bool

    def __new__(cls) -> "FrameCache":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_local"):
            self._local = threading.local()
        if not hasattr(self, "_callback_name"):
            self._callback_name = "FrameCache.ResetCache"
        if not hasattr(self, "_update_callback_name"):
            self._update_callback_name = "FrameCache.ResetCache.Update"
        if not hasattr(self, "_callback_registered"):
            self._callback_registered = False

    def values(self) -> dict[FrameCacheKey, Any]:
        store = getattr(self._local, "values", None)
        if store is None:
            store = {}
            self._local.values = store
        return store

    def get_or_create(
        self,
        category: str,
        function_name: str,
        factory: Callable[[], T],
        source_lib: str = "",
        key: Any = "",
    ) -> T:
        cache_key = FrameCacheKey(
            category=str(category),
            source_lib=str(source_lib),
            function_name=str(function_name),
            key=self._normalize_key(key),
        )
        store = self.values()
        if cache_key not in store:
            store[cache_key] = factory()
        return store[cache_key]

    def reset_cache(self) -> None:
        self.values().clear()

    def clear(self) -> None:
        self.reset_cache()

    def items(self) -> list[tuple[FrameCacheKey, Any]]:
        return list(self.values().items())

    @staticmethod
    def _normalize_key(key: Any) -> Hashable:
        if key is None:
            return None

        # Fast path: most cache keys are already hashable (int, str, tuple, etc.)
        try:
            hash(key)
            return key
        except TypeError:
            pass

        if isinstance(key, list):
            return tuple(part if _is_hashable(part) else FrameCache._normalize_key(part) for part in key)
        if isinstance(key, set):
            return frozenset(part if _is_hashable(part) else FrameCache._normalize_key(part) for part in key)
        if isinstance(key, dict):
            return tuple(
                (
                    name if _is_hashable(name) else FrameCache._normalize_key(name),
                    value if _is_hashable(value) else FrameCache._normalize_key(value),
                )
                for name, value in key.items()
            )
        return id(key)

    def enable(self) -> None:
        if self._callback_registered:
            return
        import PyCallback

        # Registered on both loops. Draw alone leaves every cached read frozen on
        # a minimised client, because the draw loop is what stops; Update alone
        # would clear mid-frame. Clearing twice only costs a repeated native read.
        PyCallback.PyCallback.Register(
            self._callback_name,
            PyCallback.Phase.PreUpdate,
            self.reset_cache,
            priority=7,
            context=PyCallback.Context.Draw,
        )
        PyCallback.PyCallback.Register(
            self._update_callback_name,
            PyCallback.Phase.PreUpdate,
            self.reset_cache,
            priority=7,
            context=PyCallback.Context.Update,
        )
        self._callback_registered = True

    def disable(self) -> None:
        if not self._callback_registered:
            return
        import PyCallback

        PyCallback.PyCallback.RemoveByName(self._callback_name)
        PyCallback.PyCallback.RemoveByName(self._update_callback_name)
        self._callback_registered = False


def _is_hashable(value: Any) -> bool:
    try:
        hash(value)
        return True
    except TypeError:
        return False


FRAME_CACHE = FrameCache()
FRAME_CACHE.enable()


def frame_cache(
    category: str,
    source_lib: str = "",
    key: Any = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            if callable(key):
                resolved_key = key(*args, **kwargs)
            elif key is not None:
                resolved_key = key
            elif not args and not kwargs:
                resolved_key = "global"
            elif kwargs:
                resolved_key = {"args": args, "kwargs": kwargs}
            else:
                resolved_key = args

            return FRAME_CACHE.get_or_create(
                category=category,
                function_name=func.__name__,
                factory=lambda: func(*args, **kwargs),
                source_lib=source_lib,
                key=resolved_key,
            )

        return wrapper

    return decorator
