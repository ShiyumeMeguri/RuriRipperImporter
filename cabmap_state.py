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

import re
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
_active_rules = ()  # whatever was last passed to apply_filter()'s `rules` arg

# --- Process-Monitor-style Include/Exclude rule engine, ported from the
# WinForms browser's MainForm.Filter.cs (FilterRule record + RowPasses /
# RelationMatches / CompareValues / TryRegex). A "rule" is anything exposing
# .field / .relation / .value / .action / .enabled attributes -- both a
# bpy PropertyGroup instance (the real UI storage, see cabmap_panel.py) and
# SimpleRule below (headless/test use) satisfy this duck type.
#
# Every enabled rule is a required constraint: Include(X) means the row MUST
# match X, Exclude(X) means the row must NOT match X. A row passes only if
# ALL enabled rules hold simultaneously (empty/all-disabled rule set => show
# everything). MainForm.Filter.cs carries the matching fix.

FILTER_FIELDS = ("name", "container", "type_names", "source", "deps")
FIELD_LABELS = {"name": "Name", "container": "Container", "type_names": "Type",
                 "source": "Source", "deps": "Deps"}

RELATIONS = ("is", "is_not", "contains", "excludes", "begins_with", "ends_with",
             "less_than", "more_than", "matches_regex", "not_matches_regex")
RELATION_LABELS = {
    "is": "is", "is_not": "is not", "contains": "contains", "excludes": "excludes",
    "begins_with": "begins with", "ends_with": "ends with",
    "less_than": "less than", "more_than": "more than",
    "matches_regex": "matches regex", "not_matches_regex": "not matches regex",
}

ACTIONS = ("include", "exclude")


class SimpleRule:
    """Plain-Python rule for headless/test use -- same attribute shape a
    RURI_PG_filter_rule PropertyGroup instance has."""
    __slots__ = ("field", "relation", "value", "action", "enabled")

    def __init__(self, field, relation, value, action, enabled=True):
        self.field = field
        self.relation = relation
        self.value = value
        self.action = action
        self.enabled = enabled


def _relation_matches(relation, cell_value, rule_value):
    """Mirrors RelationMatches/CompareValues/TryRegex."""
    if relation in ("less_than", "more_than"):
        try:
            lhs, rhs = float(cell_value), float(rule_value)
        except (TypeError, ValueError):
            return False
        return lhs < rhs if relation == "less_than" else lhs > rhs

    text = str(cell_value).lower()
    needle = str(rule_value).lower()
    if relation == "is":
        return text == needle
    if relation == "is_not":
        return text != needle
    if relation == "contains":
        return needle in text
    if relation == "excludes":
        return needle not in text
    if relation == "begins_with":
        return text.startswith(needle)
    if relation == "ends_with":
        return text.endswith(needle)
    if relation in ("matches_regex", "not_matches_regex"):
        try:
            found = re.search(str(rule_value), str(cell_value), re.IGNORECASE) is not None
        except re.error:
            return False
        return found if relation == "matches_regex" else not found
    return False


def _rule_matches(rule, row):
    return _relation_matches(rule.relation, row.get(rule.field, ""), rule.value)


def row_passes_rules(row, rules):
    """Mirrors RowPasses: every ENABLED rule is a required constraint --
    Include(X) requires a match, Exclude(X) requires a non-match. A row
    passes only if it satisfies every enabled rule (no rules => show all)."""
    for rule in rules:
        if not rule.enabled:
            continue
        matched = _rule_matches(rule, row)
        if rule.action == "exclude":
            if matched:
                return False
        else:
            if not matched:
                return False
    return True


def reset():
    global ROWS, VISIBLE, BRIDGE, _sort_dir, _ROWS_BY_CAB
    ROWS = []
    VISIBLE = []
    BRIDGE = None
    _sort_dir = 0
    _ROWS_BY_CAB = None
    clear_animation_build_state()


def ensure_bridge(hook_ids):
    global BRIDGE
    if BRIDGE is None:
        BRIDGE = pythonnet_bridge.RipperBridge(hook_ids)
    return BRIDGE


# --- Animation browser build context ----------------------------------------
# The animation browser (cabmap_panel.RURI_UL_animation_clips) only DISCOVERS
# which CABs in the selected row's dependency closure are AnimationClip-
# classed (cabmap_panel.RURI_OT_discover_animations) -- pure in-memory cabmap
# metadata (ResolveClosureCabNames + each CAB's already-loaded TypeNames), no
# VFS decrypt, no AssetRipper export, no db. Actually building an action is
# deferred until the user checks specific clips and clicks "Import Checked
# Animations". That later build needs a real Blender armature to attach onto
# AND a real db (guid-keyed resolved closure) to translate a checked clip's
# CAB name into its actual guid; two paths lead there:
#   - the character was already fully imported (Import (Append)/(Reset
#     Scene)) -- set_animation_build_state records the REAL post-build
#     fields immediately (db/arm_name/maps/path_to_meshobjects all present).
#   - only "Discover Animations" (cheap, no import_cabs call at all) has run
#     so far -- the state has no db/armature yet, just enough to resolve
#     them lazily (seed_cab + options) the first time the user actually
#     asks to attach a clip (mark_animation_build_done fills in the real
#     fields once that happens). Nothing here is bpy-serializable, so
#     (matching this module's existing BRIDGE/ROWS convention) it lives here
#     as plain Python state rather than on the bpy PropertyGroup. Keyed to a
#     single character at a time: discovering/importing a different one
#     replaces this outright.

ANIMATION_BUILD_STATE = None
# dict(db=..., arm_name=..., maps=..., path_to_meshobjects=..., seed_cab=..., options=...) | None


def set_animation_build_state(db, arm_name, maps, path_to_meshobjects):
    """A character was just FULLY imported (mesh/skeleton/materials already
    built) -- record its real post-build fields immediately."""
    global ANIMATION_BUILD_STATE
    ANIMATION_BUILD_STATE = {
        "db": db,
        "arm_name": arm_name,
        "maps": maps,
        "path_to_meshobjects": path_to_meshobjects,
        "seed_cab": None,
        "options": None,
    }


def set_animation_discovery_state(seed_cab, options):
    """Only a cheap CAB-level clip discovery has happened -- no import_cabs
    call yet, no db, nothing built into the scene. arm_name/maps/
    path_to_meshobjects/db stay unset until mark_animation_build_done runs
    the lazy full import (which resolves seed_cab's closure for the first
    time)."""
    global ANIMATION_BUILD_STATE
    ANIMATION_BUILD_STATE = {
        "db": None,
        "arm_name": None,
        "maps": None,
        "path_to_meshobjects": None,
        "seed_cab": seed_cab,
        "options": options,
    }


def mark_animation_build_done(db, arm_name, maps, path_to_meshobjects):
    """Fill in the real post-build fields once the lazy full import (see
    RURI_OT_import_selected_animations) has actually happened, so a second
    click attaches more clips to the SAME armature instead of re-importing."""
    if ANIMATION_BUILD_STATE is not None:
        ANIMATION_BUILD_STATE["db"] = db
        ANIMATION_BUILD_STATE["arm_name"] = arm_name
        ANIMATION_BUILD_STATE["maps"] = maps
        ANIMATION_BUILD_STATE["path_to_meshobjects"] = path_to_meshobjects


def clear_animation_build_state():
    global ANIMATION_BUILD_STATE
    ANIMATION_BUILD_STATE = None


def load_rows():
    """Pull every row from the currently-loaded cabmap into ROWS (plain Python
    list, not bpy data) and reset the filter to show everything."""
    global ROWS, VISIBLE, _ROWS_BY_CAB
    if BRIDGE is None:
        raise RuntimeError("No bridge session -- call ensure_bridge() first.")
    ROWS = BRIDGE.enumerate_rows()
    VISIBLE = list(range(len(ROWS)))
    _ROWS_BY_CAB = None  # rebuilt lazily on first rows_by_cab() call


_ROWS_BY_CAB = None  # dict[str, dict] -- lazily built cab -> row index, see rows_by_cab()


def rows_by_cab():
    """{cab -> row dict}, built once per load_rows() and cached -- used to
    look up TypeNames/Name for a batch of dependency-closure CAB names
    (see resolve_closure_cab_names) without an O(closure_size * len(ROWS))
    linear scan."""
    global _ROWS_BY_CAB
    if _ROWS_BY_CAB is None:
        _ROWS_BY_CAB = {row["cab"]: row for row in ROWS}
    return _ROWS_BY_CAB


def _row_matches(row, query_lower):
    return (query_lower in row["name"].lower()
            or query_lower in row["container"].lower()
            or query_lower in row["source"].lower()
            or query_lower in row["type_names"].lower())


def apply_filter(query, rules=()):
    """Row shows if it matches the quick search across Name/Container/Source/
    Type AND passes the Include/Exclude rule set (row_passes_rules) --
    mirrors the WinForms browser's RowPasses exactly (quick search AND rules,
    both must pass)."""
    global VISIBLE, _active_rules
    query = (query or "").strip().lower()
    rules = tuple(rules)
    _active_rules = rules
    VISIBLE = [i for i, row in enumerate(ROWS)
               if (not query or _row_matches(row, query)) and row_passes_rules(row, rules)]
    _apply_sort()


def reapply_filter(query):
    """Re-run apply_filter with whatever rules were last active -- for when
    only the rule set changed, not the search text."""
    apply_filter(query, _active_rules)


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
        reapply_filter(_pending_query)  # keeps whatever Include/Exclude rules are currently active
        on_ready()
        _timer_registered = False
        return None  # unregister this timer

    bpy.app.timers.register(_tick, first_interval=0.05)
