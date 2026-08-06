"""Narrow adapter over the widget runtime (``WidgetHandler``).

This is the ONLY launch-bar code that knows the handler's method names, so tiles/browser
depend on a small stable surface instead of handler internals. It consumes the neutral runtime
+ catalog metadata; it does NOT depend on the widget-manager UI we replace, and it does NOT do
discovery/bootstrap (a future "main toolbar" owns that). ``WidgetHandler`` is imported lazily so
importing this module never pulls in the heavy facade.

All enable/disable/configure calls are keyed by the full widget id (``folder_script_name``);
the handler's enable/disable take ``plain_name``, so we bridge via the resolved ``Widget``.
"""

import os
from dataclasses import dataclass
from typing import Optional

# Icon existence is validated once per path and cached: widgets don't relocate their icon
# files mid-session, and get() -> _meta() runs per tile per frame, so re-stat'ing every
# frame is pure waste. If a file is removed while running, that's the user's problem — we
# do not health-check per frame.
_ICON_VALIDATED: dict[str, str] = {}


def _validated_icon(raw: str) -> str:
    """Return `raw` if its icon file exists (checked once, cached), else ''."""
    if not raw:
        return ""
    cached = _ICON_VALIDATED.get(raw)
    if cached is None:
        cached = raw if os.path.isfile(raw) else ""
        _ICON_VALIDATED[raw] = cached
    return cached


# Static per-widget fields (name / icon / category / folder / configurable) do not
# change frame to frame, but _meta() runs ~180x/frame (once per tile). Compute them
# once per widget id and cache; each frame only the dynamic enabled/configuring are
# read. Keyed by the stable widget id -- a reloaded widget keeps its cached static
# fields until the process restarts, which is fine for a launch bar.
_STATIC_META: dict[str, tuple] = {}

# Whole-list cache: list_widgets() rebuilds only when the handler's widgets_revision
# changes (see WidgetHandler.widgets_revision), so nothing-changed frames cost O(1).
_LIST_CACHE: list = []
_LIST_REV: int = -1

# {id: WidgetMeta} index for O(1) get() by id, rebuilt from the cached list only
# when widgets_revision changes. Tiles resolve their meta every frame, so this
# avoids a get_widget_info + _meta per tile per frame.
_META_BY_ID: dict = {}
_META_BY_ID_REV: int = -1


def _static_meta(widget_id: str, w) -> tuple:
    """(name, icon, category, folder, configurable) for a widget, cached by id."""
    cached = _STATIC_META.get(widget_id)
    if cached is not None:
        return cached
    folder = str(getattr(w, "widget_path", "") or "")
    if not folder:  # derive from the id: "a/b/c/Widget.py" -> "a/b/c"
        rel = widget_id.replace("\\", "/").rsplit(".", 1)[0]
        folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
    folder = folder.replace("\\", "/").strip("/")
    category = str(getattr(w, "category", "") or "") or (folder.split("/")[0] if folder else "")
    static = (
        str(getattr(w, "name", "") or widget_id),
        _validated_icon(str(getattr(w, "image", "") or "")),
        category,
        folder,
        bool(getattr(w, "has_configure_property", False)),
    )
    _STATIC_META[widget_id] = static
    return static


@dataclass
class WidgetMeta:
    """Read-only view of one widget for the launch bar."""

    id: str  # full folder_script_name (stable key)
    name: str  # display name
    icon: str  # image path (may be a missing-texture path or empty)
    category: str  # top folder / MODULE_CATEGORY
    enabled: bool
    configurable: bool
    folder: str = ""  # normalized "/"-separated folder path (from widget_path) for the tree
    configuring: bool = False  # is its configure panel currently open


def _handler():
    """The WidgetHandler singleton, or None if the runtime is unavailable."""

    try:
        from Core.py4gwcorelib_src.WidgetManager import get_widget_handler

        return get_widget_handler()
    except Exception:
        return None


class WidgetRuntime:
    """Enumerate + toggle + configure widgets through the handler. Safe when it's unavailable."""

    def _meta(self, widget_id: str, w) -> WidgetMeta:
        # Static fields are cached (see _static_meta); only enabled/configuring are
        # read per frame -- this drops ~6 getattrs + all the string work per call,
        # and _meta runs ~180x/frame.
        name, icon, category, folder, configurable = _static_meta(widget_id, w)
        return WidgetMeta(
            id=widget_id,
            name=name,
            icon=icon,
            category=category,
            enabled=bool(getattr(w, "enabled", False)),
            configurable=configurable,
            folder=folder,
            configuring=bool(getattr(w, "configuring", False)),
        )

    def list_widgets(self) -> list[WidgetMeta]:
        h = _handler()
        if h is None:
            return []
        # Rebuild only when the handler signals a real change (widgets_revision);
        # otherwise return the cached list. list_widgets() runs several times/frame
        # (preset bars, browser), so this turns ~180 _meta() calls/frame into 0
        # while nothing changes. Falls back to per-call rebuild if the signal is
        # absent (older handler).
        global _LIST_CACHE, _LIST_REV
        rev = getattr(h, "widgets_revision", None)
        if rev is not None and rev == _LIST_REV:
            return _LIST_CACHE
        out = []
        for widget_id, w in getattr(h, "widgets", {}).items():
            try:
                out.append(self._meta(widget_id, w))
            except Exception:
                continue
        if rev is not None:
            _LIST_CACHE = out
            _LIST_REV = rev
        return out

    def revision(self) -> int:
        """The handler's widget-set revision (bumps on enable/disable/discover).

        Callers cache per-widget derived data (labels, tooltips, enabled state) against
        this so they rebuild only when the widget set actually changes. Returns 0 when
        the handler or the signal is unavailable (older handler) -- a constant, so such
        callers should treat 0 as "no change signal" if they need per-frame freshness.
        """
        h = _handler()
        return int(getattr(h, "widgets_revision", 0)) if h is not None else 0

    def _widget(self, widget_id: str):
        h = _handler()
        if h is None or not widget_id:
            return None
        try:
            return h.get_widget_info(widget_id)
        except Exception:
            return None

    def get(self, widget_id: str) -> Optional[WidgetMeta]:
        # Tiles call this every frame; resolve from a revision-cached {id: meta}
        # index (built off the already-cached list_widgets) instead of re-running
        # get_widget_info + _meta per tile per frame.
        h = _handler()
        if h is None:
            return None
        rev = getattr(h, "widgets_revision", None)
        if rev is None:  # no change signal -> uncached fallback
            w = self._widget(widget_id)
            return self._meta(widget_id, w) if w is not None else None
        global _META_BY_ID, _META_BY_ID_REV
        if rev != _META_BY_ID_REV:
            _META_BY_ID = {m.id: m for m in self.list_widgets()}
            _META_BY_ID_REV = rev
        return _META_BY_ID.get(widget_id)

    def tooltip_text(self, widget_id: str) -> str:
        m = self.get(widget_id)
        if m is None:
            return widget_id or ""
        return "Enable / disable %s" % m.name

    def draw_tooltip(self, widget_id: str) -> bool:
        """Render the widget's OWN tooltip if it defines one, exactly as the Widget Manager does.

        A widget may expose ``has_tooltip_property`` + a ``tooltip`` callable that draws its own
        tooltip window (begin_tooltip/…/end_tooltip). Call this only while the row is hovered.
        Returns True if a custom tooltip was drawn, False otherwise (caller shows a fallback).
        """
        w = self._widget(widget_id)
        if w is None or not bool(getattr(w, "has_tooltip_property", False)):
            return False
        fn = getattr(w, "tooltip", None)
        if not callable(fn):
            return False
        try:
            fn()
            return True
        except Exception:
            return False

    def is_enabled(self, widget_id: str) -> bool:
        w = self._widget(widget_id)
        return bool(getattr(w, "enabled", False)) if w is not None else False

    def toggle(self, widget_id: str) -> None:
        h = _handler()
        w = self._widget(widget_id)
        if h is None or w is None:
            return
        if bool(getattr(w, "enabled", False)):
            # System-safe disable path (defers System widgets to their confirmation modal)
            try:
                h._request_disable_widget(w)
            except Exception:
                try:
                    h.disable_widget(w.plain_name)
                except Exception:
                    pass
        else:
            try:
                h.enable_widget(w.plain_name)
            except Exception:
                pass

    # ---- global widget-manager actions (browser toolbar) ---------------------------
    def is_optional_paused(self) -> bool:
        """True if optional (non-System) widgets are currently paused."""

        h = _handler()
        return bool(getattr(h, "optional_widgets_paused", False)) if h is not None else False

    def toggle_optional_paused(self) -> None:
        """Pause/resume all optional widgets, broadcasting to other accounts (multibox)."""

        h = _handler()
        if h is None:
            return
        try:
            h.toggle_optional_widgets_paused()  # flips state + ShMem PauseWidgets/ResumeWidgets broadcast
        except Exception:
            pass

    def reload_all(self) -> None:
        """Re-discover and reload all widgets (the old WM 'Reload' button)."""

        h = _handler()
        if h is None:
            return
        try:
            h.reload_widgets()
        except Exception:
            pass

    def is_all_paused(self) -> bool:
        """True if every widget on this client is paused."""

        h = _handler()
        return bool(getattr(h, "paused", False)) if h is not None else False

    def toggle_pause_all(self) -> None:
        """Pause/resume every widget on this client (does not broadcast)."""

        h = _handler()
        if h is None:
            return
        try:
            if bool(getattr(h, "paused", False)):
                h.ResumeAllWidgets()
            else:
                h.PauseAllWidgets()
        except Exception:
            pass

    # ---- per-frame widget-manager lifecycle (owned by the launchpad now) ------------
    def draw_configuring(self) -> None:
        """Render each configuring widget's configure() panel (was the WM entry's job)."""

        h = _handler()
        if h is None:
            return
        try:
            h.execute_configuring_widgets()
        except Exception:
            pass

    def draw_disable_confirmation(self) -> None:
        """Render the System-widget disable confirmation modal (was the WM entry's job).

        WidgetRuntime.toggle/disable defer System widgets to a pending flag; this must be drawn
        each frame or disabling a System widget silently stalls.
        """

        h = _handler()
        if h is None:
            return
        try:
            h._draw_pending_disable_confirmation()
        except Exception:
            pass

    def set_configuring(self, widget_id: str, value: bool = True) -> None:
        # _widget() resolves by the FULL unique id; set the flag on that exact widget
        # rather than round-tripping through the handler's non-unique plain_name lookup
        # (which could toggle configure on a different widget that shares a plain_name).
        w = self._widget(widget_id)
        if w is None:
            return
        try:
            w.set_configuring(value)
        except Exception:
            pass

    # ---- favorites (shared with the Widget Manager) ---------------------------------
    # The WM persists favorites in its account settings under [Favorites] favorites as a
    # comma-separated list of widget ids. The handler exposes that settings key as
    # MANAGER_INI_KEY, so we read/write the SAME store instead of keeping a private set.
    def _fav_cfg(self):
        h = _handler()
        if h is None:
            return None
        key = str(getattr(h, "MANAGER_INI_KEY", "") or "")
        if not key:
            return None
        try:
            from Core.py4gwcorelib_src.Settings import Settings

            return Settings(key, "account")
        except Exception:
            return None

    def list_favorites(self) -> set:
        cfg = self._fav_cfg()
        if cfg is None:
            return set()
        try:
            raw = cfg.get_str("Favorites", "favorites", "") or ""
        except Exception:
            return set()
        return {p.strip() for p in raw.split(",") if p.strip()}

    def is_favorite(self, widget_id: str) -> bool:
        return bool(widget_id) and widget_id in self.list_favorites()

    def set_favorite(self, widget_id: str, value: bool) -> None:
        cfg = self._fav_cfg()
        if cfg is None or not widget_id:
            return
        favs = self.list_favorites()
        if value:
            favs.add(widget_id)
        else:
            favs.discard(widget_id)
        try:
            cfg.set("Favorites", "favorites", ",".join(sorted(favs)))
        except Exception:
            pass

    def toggle_favorite(self, widget_id: str) -> None:
        self.set_favorite(widget_id, not self.is_favorite(widget_id))
