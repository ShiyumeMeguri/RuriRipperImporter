"""Plain-Python (non-bpy) backing store for the cabmap browser: the full row
list, the pythonnet bridge session, the virtual-folder-tree navigation state,
and search/sort/debounce bookkeeping.

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

import numpy as np

try:
    from . import pythonnet_bridge
except ImportError:  # standalone (non-package) testing
    import pythonnet_bridge

DISPLAY_CAP = 500  # max rows ever materialized into the UI collection at once
SEARCH_DEBOUNCE_SECONDS = 0.25

ROWS = []       # list[dict] -- the full cabmap, set by load_rows()
VISIBLE = []    # list[int] -- indices into ROWS after the current filter+sort
BRIDGE = None   # pythonnet_bridge.RipperBridge | None -- the active session

# Folder-browser navigation (see "Virtual folder tree" below). Plain Python for the
# same reason VISIBLE/SELECTED_CABS are: rebuilt/mutated far too often for bpy
# property overhead, and nothing here needs to survive a file save.
CURRENT_DIR = ()          # tuple[str, ...] -- () is the virtual root; segments of the browsed folder
CURRENT_SUBFOLDERS = []   # list[(name, recursive_file_count)] -- CURRENT_DIR's child folders, alpha-sorted

# Multi-selection lives HERE (plain Python), not on the windowed
# CollectionProperty: the window is torn down and rebuilt on every filter/
# sort/search change, so any per-item bpy state would be wiped by the very
# next keystroke. Keyed by cab (the row identity), which also means a
# selection survives re-sorting and stays attached to the same assets when
# the visible window scrolls/narrows.
SELECTED_CABS = set()   # cab keys of every selected row
SELECT_ANCHOR = None    # ROWS index of the last plainly-clicked row (Shift range anchor)


def clear_selection():
    global SELECT_ANCHOR
    SELECTED_CABS.clear()
    SELECT_ANCHOR = None


def selected_row_dicts():
    """Selected rows in master ROWS order -- the deterministic order batch
    imports run in (not click order, which nobody can reproduce)."""
    if not SELECTED_CABS:
        return []
    index_of = ROWS.cab_to_index()
    ids = sorted(index_of[cab] for cab in SELECTED_CABS if cab in index_of)
    return [ROWS[i] for i in ids]

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
    global CURRENT_DIR, CURRENT_SUBFOLDERS, _ROOT
    ROWS = []
    VISIBLE = []
    BRIDGE = None
    _sort_dir = 0
    _ROWS_BY_CAB = None
    CURRENT_DIR = ()
    CURRENT_SUBFOLDERS = []
    _ROOT = _Node()
    clear_selection()
    clear_animation_build_state()


def ensure_bridge(hook_ids):
    """Get (or lazily create) the session's one active bridge, with its hook selection kept in
    sync with hook_ids on every call -- NOT just on first construction.

    Root-cause fix (2026-07-18): this used to construct RipperBridge(hook_ids) once and return
    the SAME instance forever after, ignoring hook_ids on every later call. A cabmap "Build"/
    "Load" done with zero (or a since-corrected) hook selection -- e.g. before a hook the user
    wants was even listable, or before they'd ticked its checkbox -- permanently poisoned BRIDGE
    with no VFS game hook wired up. Re-ticking the checkbox and rebuilding/reloading the cabmap
    never fixed it: this function kept handing back the same broken session, and downstream
    scene-tab operators (RURI_OT_scene_discover_maps et al.) read cabmap_state.BRIDGE directly
    rather than calling ensure_bridge themselves, so they inherited the poisoning with no path to
    recover short of restarting Blender. Symptom: "Discover maps failed: InvalidOperationException:
    No VFS game hook active" even with the right hook checked.
    Fix: re-apply the current hook_ids via RipperBridge.reinitialize() whenever they differ from
    what the existing session was last (re)Initialize()d with -- see reinitialize()'s doc comment
    for why this is safe (RuriHook.ApplyHooks is a diff against the currently active hook set, so
    re-Initialize only enables/disables the delta) and preserves the already-loaded cabmap/db
    instead of dropping it the way constructing a fresh RipperBridge would."""
    global BRIDGE
    hook_ids = tuple(hook_ids)
    if BRIDGE is None:
        BRIDGE = pythonnet_bridge.RipperBridge(hook_ids)
    elif BRIDGE.hook_ids != hook_ids:
        BRIDGE.reinitialize(hook_ids)
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
# dict(db=..., arm_name=..., maps=..., path_to_meshobjects=..., seed_cabs=[...], options=...) | None


def set_animation_build_state(db, arm_name, maps, path_to_meshobjects):
    """A character was just FULLY imported (mesh/skeleton/materials already
    built) -- record its real post-build fields immediately."""
    global ANIMATION_BUILD_STATE
    ANIMATION_BUILD_STATE = {
        "db": db,
        "arm_name": arm_name,
        "maps": maps,
        "path_to_meshobjects": path_to_meshobjects,
        "seed_cabs": [],
        "options": None,
    }


def set_animation_discovery_state(seed_cabs, options):
    """Only a cheap CAB-level clip discovery has happened -- no import_cabs
    call yet, no db, nothing built into the scene. arm_name/maps/
    path_to_meshobjects/db stay unset until mark_animation_build_done runs
    the lazy full import (which resolves the seed closure for the first
    time). seed_cabs is a LIST -- discovery runs over the whole multi-
    selection, and the lazy build must co-seed every one of them or clips
    discovered from the extra rows would never resolve in clips_by_cab."""
    global ANIMATION_BUILD_STATE
    ANIMATION_BUILD_STATE = {
        "db": None,
        "arm_name": None,
        "maps": None,
        "path_to_meshobjects": None,
        "seed_cabs": list(seed_cabs),
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
    """Pull every row from the currently-loaded cabmap into ROWS and reset the
    browser to the virtual root folder. ROWS is a columnar row_table.RowTable
    -- indexing/iteration yield dict-compatible row views, so per-row
    consumers are unchanged while the hot paths (search/sort/window) run
    columnar."""
    global ROWS, _ROWS_BY_CAB
    if BRIDGE is None:
        raise RuntimeError("No bridge session -- call ensure_bridge() first.")
    ROWS = BRIDGE.enumerate_table()
    _ROWS_BY_CAB = None  # rebuilt lazily on first rows_by_cab() call
    clear_selection()    # cab keys from a previous map mean nothing in this one
    _build_tree()        # also resets CURRENT_DIR/VISIBLE/CURRENT_SUBFOLDERS to the root listing


_ROWS_BY_CAB = None  # dict[str, dict] -- lazily built cab -> row index, see rows_by_cab()


class _RowsByCab:
    """cab -> row-view mapping over a columnar RowTable: the cab->index dict
    is the table's own lazy index; row views materialize per lookup only."""

    __slots__ = ("_table",)

    def __init__(self, table):
        self._table = table

    def get(self, cab, default=None):
        index = self._table.cab_to_index().get(cab)
        return self._table[index] if index is not None else default

    def __getitem__(self, cab):
        return self._table[self._table.cab_to_index()[cab]]

    def __contains__(self, cab):
        return cab in self._table.cab_to_index()


def rows_by_cab():
    """cab -> row-view mapping, built once per load_rows() and cached -- used
    to look up TypeNames/Name for a batch of dependency-closure CAB names
    (see resolve_closure_cab_names) without an O(closure_size * len(ROWS))
    linear scan."""
    global _ROWS_BY_CAB
    if _ROWS_BY_CAB is None:
        _ROWS_BY_CAB = _RowsByCab(ROWS)
    return _ROWS_BY_CAB


# --- Virtual folder tree ------------------------------------------------------
# The browser's default view: a real file-browser-style drill-down over each
# row's container path(s) (Unity's own AssetBundle.Container addressable keys,
# see CabMap.Entry.ContainerPaths -- already lowercase/"/"-separated, exactly a
# virtual filesystem path) instead of dumping all ~260k rows flat. Built once
# per load_rows() in O(total path segments); browse_dir() then reads it in
# O(children of that folder), so opening a folder never rescans ROWS the way
# an ad-hoc per-click scan would.

_NO_PATH_BUCKET = "(no virtual path)"  # synthetic root folder for the rare row with zero container paths


class _Node:
    """One folder-tree node, keyed into by its parent's `children` dict under
    its own path segment -- that segment IS the node's leaf name, so nothing
    here stores its own name. A node with `children` is browsable as a
    folder; a node with `files` means at least one row's container path ends
    exactly here (both can be true at once: some other row's path continues
    past this one -- rare, but shown as both a folder and a file rather than
    picking one)."""

    __slots__ = ("children", "files", "file_count")

    def __init__(self):
        self.children = {}   # str segment -> _Node
        self.files = []      # list[int] -- ROWS indices whose container path ends exactly here
        self.file_count = 0  # recursive count of files at or below this node


_ROOT = _Node()


def _add_leaf(segments, row_index):
    node = _ROOT
    for seg in segments:
        child = node.children.get(seg)
        if child is None:
            child = _Node()
            node.children[seg] = child
        node = child
        node.file_count += 1
    node.files.append(row_index)


def _build_tree():
    """Rebuild the folder tree from ROWS and reset browsing to the root. A row
    exported under more than one container path (rare) appears as its own
    leaf under EVERY one of its paths -- the same asset reachable from more
    than one virtual name, same as the real game would resolve it. A row that
    lands nowhere (zero container paths, or every one of them is blank/all-
    separators) falls back to a child of _NO_PATH_BUCKET keyed by its own cab
    id, so it stays reachable (as a FOLDER you can open, not a same-named
    leaf sitting directly on the bucket node -- a childless node reads as a
    file, not a folder, see _Node) instead of silently vanishing.

    Local-bound methods + a plain list instead of a genexpr/tuple() (segments
    is only ever iterated here, never used as a dict key) -- ~45% faster at
    real cabmap scale (260k rows, confirmed by measurement), worth it since
    this runs synchronously inside the Build/Load operator."""
    global _ROOT
    _ROOT = _Node()
    path_count_of = ROWS.container_path_count
    path_of = ROWS.container_path
    cab_of = ROWS.cab
    for index in range(len(ROWS)):
        placed = False
        for p in range(path_count_of(index)):
            segments = [s for s in path_of(index, p).split("/") if s]
            if segments:
                _add_leaf(segments, index)
                placed = True
        if not placed:
            _add_leaf((_NO_PATH_BUCKET, cab_of(index)), index)
    browse_dir(())


def _node_at(path):
    node = _ROOT
    for seg in path:
        node = node.children.get(seg)
        if node is None:
            return None
    return node


def browse_dir(path):
    """Point the browser at a virtual folder (a tuple of path segments, ()
    for root) and recompute VISIBLE/CURRENT_SUBFOLDERS for exactly that
    folder's own children -- O(children), never O(len(ROWS)). An unreachable
    path (e.g. CURRENT_DIR from a since-replaced cabmap) falls back to root
    rather than showing a dead end."""
    global CURRENT_DIR, VISIBLE, CURRENT_SUBFOLDERS
    node = _node_at(path)
    if node is None:
        path, node = (), _ROOT
    CURRENT_DIR = tuple(path)
    subfolders = []
    files = []
    for name, child in node.children.items():
        if child.children:  # has descendants beyond itself -> browsable folder
            subfolders.append((name, child.file_count))
        if child.files:     # a row's container path ends exactly here -> also a file entry
            files.extend(child.files)
    subfolders.sort(key=lambda pair: pair[0].lower())
    CURRENT_SUBFOLDERS = subfolders
    VISIBLE = files
    _apply_sort()


def has_active_query(query, rules):
    """True when the flat global-search view should replace the folder
    browser -- non-blank quick-search text, or any ENABLED Include/Exclude
    rule (a disabled rule is inert, same as apply_filter treats it)."""
    if (query or "").strip():
        return True
    return any(r.enabled for r in rules)


def refresh_visible(query, rules):
    """The single dispatch point between the two views: flat global search/
    rule results (apply_filter) or the folder listing for CURRENT_DIR
    (browse_dir). Always refreshes _active_rules -- even when the folder-tree
    branch runs and skips apply_filter entirely -- so a later debounced
    search (reapply_filter, which only has the cached rules to go on) never
    fires against a stale or since-removed rule set."""
    global _active_rules
    _active_rules = tuple(rules)
    if has_active_query(query, rules):
        apply_filter(query, rules)
    else:
        browse_dir(CURRENT_DIR)


def leaf_name_in_current_dir(index):
    """The display name for ROWS[index] AS BROWSED under CURRENT_DIR
    specifically. Matters only for the rare row with more than one container
    path: its default name (RowTable.name(), always path[0]'s leaf) can
    belong to a completely different folder than the one it's actually being
    shown in here. Falls back to that default if, somehow, none of the row's
    paths match (shouldn't happen for anything browse_dir actually placed in
    VISIBLE)."""
    depth = len(CURRENT_DIR)
    for p in range(ROWS.container_path_count(index)):
        segments = tuple(s for s in ROWS.container_path(index, p).split("/") if s)
        if len(segments) == depth + 1 and segments[:depth] == CURRENT_DIR:
            return segments[-1]
    return ROWS.name(index)


def apply_filter(query, rules=()):
    """Row shows if it matches the quick search across Name/Container/Source/
    Type AND passes the Include/Exclude rule set (row_passes_rules) --
    mirrors the WinForms browser's RowPasses exactly (quick search AND rules,
    both must pass).

    The quick search runs vectorized over the RowTable's column blobs; the
    rule engine, when rules exist, evaluates per-row over the already-
    search-narrowed candidates only. Always a FLAT result set over the whole
    cabmap, never scoped to CURRENT_DIR -- see refresh_visible for how this
    and the folder browser (browse_dir) dispatch between each other."""
    global VISIBLE, _active_rules, CURRENT_SUBFOLDERS
    query = (query or "").strip().lower()
    rules = tuple(rules)
    _active_rules = rules
    CURRENT_SUBFOLDERS = []
    enabled_rules = [r for r in rules if r.enabled]
    candidates = np.flatnonzero(ROWS.search_mask(query))
    if enabled_rules:
        VISIBLE = [int(i) for i in candidates
                   if row_passes_rules(ROWS[int(i)], enabled_rules)]
    else:
        VISIBLE = candidates.tolist()
    _apply_sort()


def reapply_filter(query):
    """Re-run refresh_visible with whatever rules were last active -- for
    when only the debounced search text changed, not the rule set (see
    schedule_filter). Routes through refresh_visible, not apply_filter
    directly, so an empty query clearing back to no-rules-either correctly
    lands back in the folder browser instead of a stale flat view."""
    refresh_visible(query, _active_rules)


def _apply_sort():
    global VISIBLE
    if _sort_dir == 0:
        VISIBLE.sort()  # back to load order
        return
    # Columnar: one cached key-materialization pass per column instead of
    # deriving display strings inside every comparison.
    values = ROWS.sort_values(_sort_column)
    VISIBLE.sort(key=values.__getitem__, reverse=(_sort_dir == 2))


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
