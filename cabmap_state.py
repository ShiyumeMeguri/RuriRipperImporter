"""Plain-Python (non-bpy) backing store for the cabmap browser: the full row
list, the pythonnet bridge session, and search/sort/debounce bookkeeping.

Deliberately NOT a bpy CollectionProperty. At real-world cabmap scale (~260k
rows for Endfield 1.3.3, confirmed against the real game) a CollectionProperty
of that many PropertyGroup structs is itself the bottleneck: RNA allocation
per element, .blend/undo bloat, O(n) mutation cost -- exactly why the WinForms
original this addon's browser mirrors used VirtualMode + a plain backing list
instead of eagerly materializing every row into a widget. cabmap_panel
materializes only a capped, already-filtered window into a small
CollectionProperty for display.
"""

from __future__ import annotations

import time

try:
    from . import pythonnet_bridge
except ImportError:  # standalone (non-package) testing
    import pythonnet_bridge

DISPLAY_CAP = 500  # max rows ever materialized into the UI collection at once
SEARCH_DEBOUNCE_SECONDS = 0.25

ROWS = []       # list[dict] -- the full cabmap, set by load_rows()
VISIBLE = []    # list[int] -- indices into ROWS after the current filter+sort
BRIDGE = None   # pythonnet_bridge.RipperBridge | None -- the active session

_pending_query = None
_last_edit_time = 0.0
_timer_registered = False
_sort_column = "name"
_sort_dir = 0  # 0 = unsorted (load order), 1 = ascending, 2 = descending


def reset():
    global ROWS, VISIBLE, BRIDGE, _sort_dir
    ROWS = []
    VISIBLE = []
    BRIDGE = None
    _sort_dir = 0


def ensure_bridge(hook_ids):
    global BRIDGE
    if BRIDGE is None:
        BRIDGE = pythonnet_bridge.RipperBridge(hook_ids)
    return BRIDGE


def load_rows():
    """Pull every row from the currently-loaded cabmap into ROWS (plain Python
    list, not bpy data) and reset the filter to show everything."""
    global ROWS, VISIBLE
    if BRIDGE is None:
        raise RuntimeError("No bridge session -- call ensure_bridge() first.")
    ROWS = BRIDGE.enumerate_rows()
    VISIBLE = list(range(len(ROWS)))


def _row_matches(row, query_lower):
    return (query_lower in row["name"].lower()
            or query_lower in row["container"].lower()
            or query_lower in row["source"].lower()
            or query_lower in row["type_names"].lower())


def apply_filter(query):
    """Case-insensitive substring match across Name/Container/Source/Type,
    mirroring the WinForms browser's quick-search semantics (RowPasses)."""
    global VISIBLE
    query = (query or "").strip().lower()
    VISIBLE = (list(range(len(ROWS))) if not query
               else [i for i, row in enumerate(ROWS) if _row_matches(row, query)])
    _apply_sort()


def _apply_sort():
    global VISIBLE
    if _sort_dir == 0:
        VISIBLE.sort()  # back to load order
        return
    key = _sort_column
    VISIBLE.sort(key=lambda i: ROWS[i].get(key, ""), reverse=(_sort_dir == 2))


def cycle_sort(column):
    """Tri-state per column: ascending -> descending -> unsorted, mirroring the
    WinForms browser's column-header click behaviour."""
    global _sort_column, _sort_dir
    if _sort_column != column:
        _sort_column, _sort_dir = column, 1
    else:
        _sort_dir = (_sort_dir + 1) % 3
    _apply_sort()


def sort_state():
    return _sort_column, _sort_dir


def display_window():
    """Up to DISPLAY_CAP filtered/sorted rows, ready to materialize into the UI
    CollectionProperty. Returns (total_visible_count, [(rows_index, row_dict), ...])."""
    capped = VISIBLE[:DISPLAY_CAP]
    return len(VISIBLE), [(i, ROWS[i]) for i in capped]


def schedule_filter(query, on_ready):
    """Debounce a search-box edit: only actually filter ~SEARCH_DEBOUNCE_SECONDS
    after the user stops typing, not on every keystroke -- a synchronous
    substring match across 4 columns over 260k rows per keystroke would
    visibly stutter the text field (the WinForms original hit the same wall
    and used a 250ms Timer for the same reason)."""
    global _pending_query, _last_edit_time, _timer_registered
    import bpy

    _pending_query = query
    _last_edit_time = time.monotonic()
    if _timer_registered:
        return
    _timer_registered = True

    def _tick():
        global _timer_registered
        if time.monotonic() - _last_edit_time < SEARCH_DEBOUNCE_SECONDS:
            return 0.05
        apply_filter(_pending_query)
        on_ready()
        _timer_registered = False
        return None  # unregister this timer

    bpy.app.timers.register(_tick, first_interval=0.05)
