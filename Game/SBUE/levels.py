"""Browse the install's levels the way the engine ships them, and import one.

One tab with two halves, because World Partition splits a build's levels in two --
and the decoder says which by the world's own partitioned flag, never by the shape
of a name:

``Scene``  the self-contained levels: a room, a test map, a level small enough to
           hold whole. Nothing to window, so nothing to choose -- pick it, import it.
``World``  the partitioned worlds. Far too big to hold at once, and the running game
           never holds one either: it streams a window of cells around the player. So
           one is imported a window at a time, stated as a share of the ground the
           decoder measured the world to cover, and shown at the size that share
           really is.

Both lists filter through the same C# engine every other list here uses, and both
hand what they picked to the host's ONE import entry -- as a single statement, not
one call per package: a window is twenty cells of one world, and reading it as one
thing is what lets a mesh two cells share be decoded once and lets a host whose
project IS one file get a window rather than twenty projects.

Nothing is cut or matched on this side: the window and the hierarchical level go to
the decoder as dataset arguments, so the cells are cut where they are read, and the
metres a size is shown in come from the unit scale the decoder states for the engine.

Nothing here imports a host.
"""

from __future__ import annotations

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import command, filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...RuriRipperPyBridge.session import cabmap_state
from . import datasets, read

TAB_KEY = "UnrealEngine:scene"
STATE = "ruri_unreal_levels"
WORLD_STATE = "ruri_unreal_world"

LEVEL = "LEVEL"
WORLD = "WORLD"
ALL_LEVELS = -1

KINDS = (
    (LEVEL, "Scene", "The self-contained levels -- small enough to import whole"),
    (WORLD, "World",
     "The partitioned worlds -- import a window of one at a time, the way the game "
     "streams it"),
)

#: What the decoder last said this install ships. Module state, like every other
#: tab's caches here: a draw never crosses the CLR boundary, a command fills this
#: and the rows redraw.
_WORLDS = {"rows": [], "unit_scale": 0.0}
_CELLS = {"world": "", "rows": [], "error": ""}
#: handle -> the row list last published under it, so a keystroke re-searches an
#: already-open table instead of rebuilding one that has not changed.
_published = {}

#: What the list is searched and ruled over. ``kind`` is the decoder's own word for
#: which of the build's tables claimed a level -- empty for a build that names none of
#: them, which is the family answer -- so a rule on it costs nothing where it is absent.
_LEVEL_FIELDS = (("name", "Name"), ("kind", "Kind"), ("world", "Package"))
_CELL_FIELDS = (("name", "Cell"), ("level", "Package"), ("grid", "Grid"))


def state_of(context):
    return host_port.current().panel_state(context, STATE)


def world_state_of(context):
    return host_port.current().panel_state(context, WORLD_STATE)


def _active_state(context):
    """The half of the tab currently on screen -- which is also whose rows the
    shared rule editor is editing."""
    state = state_of(context)
    return state if state.kind == LEVEL else world_state_of(context)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
LEVEL_ENTRY = Schema("UnrealLevelEntry", """One self-contained level.""", (
    Field("key", app_state.STRING, ""),
    Field("name", app_state.STRING, ""),
    Field("kind", app_state.STRING, ""),
))

CELL_ENTRY = Schema("UnrealCellEntry", """One streaming cell of a partitioned
world.""", (
    Field("key", app_state.STRING, ""),
    Field("name", app_state.STRING, ""),
    Field("hlevel", app_state.INT, 0),
    Field("always_loaded", app_state.BOOL, False),
    Field("present", app_state.BOOL, False),
))

LEVELS = Schema("UnrealLevels", """The self-contained half, plus which half of the
tab is on screen.""", (
    Field("kind", app_state.ENUM, LEVEL, "Kind",
          "Which of the engine's two kinds of level to browse", items=KINDS),
    Field("search", app_state.STRING, "", "Filter",
          "Filter by level name or package", update="on_filter_edit", live=True),
    Field("entries", app_state.COLLECTION, element=LEVEL_ENTRY),
    Field("active_index", app_state.INT, 0),
    Field("status", app_state.STRING, "Refresh to read the levels this install ships."),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE))

WORLDS = Schema("UnrealWorld", """One partitioned world and the window of it to
read.""", (
    Field("search", app_state.STRING, "", "Filter",
          "Filter by cell name, package or grid", update="on_filter_edit", live=True),
    Field("entries", app_state.COLLECTION, element=CELL_ENTRY),
    Field("active_index", app_state.INT, 0),
    Field("world", app_state.ENUM, None, "World",
          "The partitioned world to stream a window of", items="world_choices",
          update="on_world_pick"),
    Field("size", app_state.FLOAT, 0.25, "Size",
          "How much of the world to read, as a share of the ground its cells cover, "
          "taken about the world's centre",
          subtype=app_state.FACTOR, minimum=0.01, maximum=1.0),
    Field("level", app_state.INT, 0, "Level",
          "The hierarchical level to read (0 is the leaf cells, -1 every level)",
          minimum=ALL_LEVELS),
    Field("use_always_loaded", app_state.BOOL, True, "Always loaded",
          "Take the always-loaded cells too, whose actors the cook folded into the "
          "world's own package -- the persistent level every window of this world sits "
          "on. A small partitioned world is often nothing but one of these",
          update="on_filter_edit"),
    Field("status", app_state.STRING, ""),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE))


def _cell_count(row):
    return int(float(row.get("cells", 0) or 0))


def _world_choices(state, context):
    return [(row.get("world", ""), row.get("name", "") or row.get("world", ""),
             "{0} streaming cell(s) -- {1}".format(_cell_count(row), row.get("world", "")))
            for row in _WORLDS["rows"] if str(row.get("partitioned", "0")) == "1"]


def _on_filter_edit(state, context):
    _rebuild(state)


def _on_world_pick(state, context):
    """Picking a world drops the cells read for the previous one: they are that
    world's."""
    _CELLS["world"] = ""
    _CELLS["rows"] = []
    _CELLS["error"] = ""
    _rebuild(state)


LEVEL_HANDLERS = app_state.Handlers(
    "SBUE.levels", base=filtering.HANDLERS, on_filter_edit=_on_filter_edit)
WORLD_HANDLERS = app_state.Handlers(
    "SBUE.world", base=filtering.HANDLERS, on_filter_edit=_on_filter_edit,
    on_world_pick=_on_world_pick, world_choices=_world_choices)


def _filter_fields(context=None):
    """The vocabulary of whichever half is on screen -- one tab, one rule editor,
    and the fields it offers are the ones the visible list actually has."""
    try:
        state = state_of(None)
    except (KeyError, RuntimeError):
        return _LEVEL_FIELDS
    return _LEVEL_FIELDS if state.kind == LEVEL else _CELL_FIELDS


FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=TAB_KEY, fields=_filter_fields,
    state_for=_active_state,
    apply=lambda context: _rebuild(_active_state(context))))


# ---------------------------------------------------------------------------
# The lists
# ---------------------------------------------------------------------------
def _matching(handle, rows, columns, values_of, state):
    """The rows passing the search box and every enabled rule, matched by the same
    C# engine every other list here uses. Falls back to the unfiltered rows only
    when there is no bridge to ask, which is also the only state in which there is
    nothing to show."""
    if cabmap_state.BRIDGE is None or not rows:
        return list(rows)
    if _published.get(handle) is not rows:
        cabmap_state.BRIDGE.open_host_table(handle, columns, [values_of(row) for row in rows])
        _published[handle] = rows
    ids = cabmap_state.BRIDGE.search_data_table(handle, state.search.strip(),
                                                state.filter_rules)
    return [rows[index] for index in ids if 0 <= index < len(rows)]


def _level_rows():
    return [row for row in _WORLDS["rows"] if str(row.get("partitioned", "0")) != "1"]


def _cell_rows(state):
    return [row for row in _CELLS["rows"]
            if state.use_always_loaded or str(row.get("alwaysLoaded", "0")) != "1"]


def _rebuild(state):
    with filtering.rebuilding():
        _fill_levels(state) if _is_levels(state) else _fill_cells(state)


def _is_levels(state):
    """Which half this record is. Asked of the record rather than of the context:
    a handler is called with the state that changed, and the two schemas differ by
    exactly the field the switch lives on."""
    return hasattr(state, "kind")


def _fill_levels(state):
    chosen = filtering.selected_key(state)
    state.entries.clear()
    rows = _matching("ruri.unreal.level", _level_rows(),
                     tuple(key for key, _label in _LEVEL_FIELDS),
                     lambda row: (row.get("name", ""), row.get("kind", ""),
                                  row.get("world", "")), state)
    for row in rows:
        entry = state.entries.add()
        entry.key = row.get("world", "")
        entry.name = row.get("name", "") or row.get("world", "")
        entry.kind = row.get("kind", "")
    state.status = "{0} of {1} self-contained level(s)".format(
        len(state.entries), len(_level_rows()))
    filtering.restore_selection(state, chosen)


def _fill_cells(state):
    chosen = filtering.selected_key(state)
    state.entries.clear()
    rows = _matching("ruri.unreal.cell\x1f" + _CELLS["world"], _cell_rows(state),
                     tuple(key for key, _label in _CELL_FIELDS),
                     lambda row: (row.get("cell", ""), row.get("level", ""),
                                  row.get("grid", "")), state)
    for row in rows:
        entry = state.entries.add()
        entry.key = row.get("level", "")
        entry.name = row.get("cell", "")
        entry.hlevel = int(float(row.get("hlevel", 0) or 0))
        entry.always_loaded = str(row.get("alwaysLoaded", "0")) == "1"
        entry.present = str(row.get("present", "0")) == "1"
    state.status = "{0} cell(s) in the window".format(len(state.entries))
    filtering.restore_selection(state, chosen)


def selected(state):
    if 0 <= state.active_index < len(state.entries):
        return state.entries[state.active_index]
    return None


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------
def _world_row(state):
    for row in _WORLDS["rows"]:
        if row.get("world", "") == state.world:
            return row
    return None


def _world_rect(state):
    """The ground the picked world's cells cover, in the decoder's own units, or
    None when it states none."""
    row = _world_row(state)
    if row is None:
        return None
    min_x, min_y = float(row.get("minX", 0) or 0), float(row.get("minY", 0) or 0)
    max_x, max_y = float(row.get("maxX", 0) or 0), float(row.get("maxY", 0) or 0)
    return (min_x, min_y, max_x, max_y) if max_x > min_x and max_y > min_y else None


def _window(state):
    """The rect to cut the cells to: the world's own ground shrunk to ``size``
    about its centre."""
    rect = _world_rect(state)
    if rect is None or state.size >= 1.0:
        return None
    min_x, min_y, max_x, max_y = rect
    center_x, center_y = (min_x + max_x) * 0.5, (min_y + max_y) * 0.5
    half_x, half_y = (max_x - min_x) * 0.5 * state.size, (max_y - min_y) * 0.5 * state.size
    return (center_x - half_x, center_y - half_y, center_x + half_x, center_y + half_y)


def _metres(units):
    """``units`` of the engine's own length in metres, by the scale the decoder
    states."""
    scale = _WORLDS["unit_scale"]
    return units * scale if scale else 0.0


# ---------------------------------------------------------------------------
# What the buttons do
# ---------------------------------------------------------------------------
def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_level(context):
    return _loaded(context) and selected(state_of(context)) is not None


def _has_world(context):
    return _loaded(context) and bool(world_state_of(context).world)


def _has_cells(context):
    return _loaded(context) and bool(len(world_state_of(context).entries))


def _refresh(context, arguments):
    """Re-read the worlds the install ships off the decoder."""
    state = state_of(context)
    world = world_state_of(context)
    try:
        _WORLDS["rows"] = datasets.worlds()
        session = datasets.session() or {}
        _WORLDS["unit_scale"] = float(session.get("unitScale", 0) or 0)
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    _published.clear()
    _rebuild(state)
    _rebuild(world)
    return None


def _read_cells(context, arguments):
    """Read the picked world's streaming cells, cut to the size and level stated."""
    state = world_state_of(context)
    args = {}
    window = _window(state)
    if window is not None:
        args.update(minX=window[0], minY=window[1], maxX=window[2], maxY=window[3])
    if state.level != ALL_LEVELS:
        args["level"] = state.level
    try:
        _CELLS["rows"] = datasets.world_cells(state.world, **args)
        _CELLS["world"] = state.world
        _CELLS["error"] = ""
    except Exception as exc:
        _CELLS["rows"] = []
        _CELLS["error"] = "{0}: {1}".format(type(exc).__name__, exc)
        state.status = _CELLS["error"]
        return {"CANCELLED"}
    _published.clear()
    _rebuild(state)
    return None


def _import(context, state, packages, what):
    """Read every picked package as ONE placement statement and hand it to the
    host's one import entry -- the same road the browser's own rows take."""
    browser = app_browser.state_of(context)
    blocked = app_browser._blocking_required_options(
        app_browser._ensure_active_config(browser))
    if blocked:
        state.status = blocked
        return
    packages = list(dict.fromkeys(name for name in packages if name))
    if not packages:
        state.status = "Nothing selected."
        return
    options = app_browser.as_options(browser, scene=True)
    label = packages[0] if len(packages) == 1 else "{0} ({1} {2}s)".format(
        _CELLS["world"] or packages[0], len(packages), what)
    stated = yield command.Read(lambda: read.packages(packages, label, options), 0.7)
    if stated is None:
        state.status = "The {0} {1} package(s) place nothing this install carries.".format(
            len(packages), what)
        return
    yield command.Mark(0.8)
    built = host_port.current().import_packages(context, stated, options)
    state.status = "{0}: {1} placement(s) from {2} package(s). {3}".format(
        label, built.imported, len(packages), "  ".join(built.warnings[:2]))


def _import_level(context, arguments):
    state = state_of(context)
    entry = selected(state)
    for step in _import(context, state, [entry.key if entry else ""], "level"):
        yield step


def _import_window(context, arguments):
    state = world_state_of(context)
    for step in _import(context, state, [entry.key for entry in state.entries], "cell"):
        yield step


def _import_world(context, arguments):
    state = world_state_of(context)
    for step in _import(context, state, [state.world], "world"):
        yield step


REFRESH = command.COMMANDS.define(
    "ruri.unreal_worlds_refresh", "Refresh", _refresh,
    description="Re-read the worlds this install ships off the decoder",
    icon="FILE_REFRESH", internal=True, poll=_loaded)
READ_CELLS = command.COMMANDS.define(
    "ruri.unreal_cells_read", "Read Cells", _read_cells,
    description="Read the picked world's streaming cells, cut to the size and level stated",
    icon="VIEWZOOM", internal=True, poll=_has_world)
IMPORT_LEVEL = command.COMMANDS.define(
    "ruri.unreal_level_import", "Import Level", _import_level,
    description="Import this level whole: its actors at their places, as the browser would",
    icon="IMPORT", poll=_has_level, steps=True, status_state=STATE,
    failure="Unreal level import failed")
IMPORT_WINDOW = command.COMMANDS.define(
    "ruri.unreal_window_import", "Import Window", _import_window,
    description="Import every cell listed below, as one window with its actors at their "
                "world places",
    icon="IMPORT", poll=_has_cells, steps=True, status_state=WORLD_STATE,
    failure="Unreal window import failed")
IMPORT_WORLD = command.COMMANDS.define(
    "ruri.unreal_world_import", "Import World Package", _import_world,
    description="Import the world's own package: the persistent level the cook folded its "
                "always-loaded actors into",
    icon="IMPORT", poll=_has_world, steps=True, status_state=WORLD_STATE,
    failure="Unreal world import failed")


# ---------------------------------------------------------------------------
# What it looks like
# ---------------------------------------------------------------------------
_LEVEL_COLUMNS = (
    app_layout.ListColumn("name", width=0.6, icon="FILE_3D"),
    app_layout.ListColumn("kind", width=0.2, enabled=False),
    app_layout.ListColumn("key", align=app_layout.RIGHT, enabled=False),
)
#: A cell the cook folded into the world package is pinned; one the window names
#: but this install does not carry is dimmed rather than hidden -- it is real
#: partition data with nothing behind it here.
_CELL_COLUMNS = (
    app_layout.ListColumn("name", width=0.75,
                          icon=lambda row: ("PINNED" if row.always_loaded else
                                            ("MESH_GRID" if row.present
                                             else "GHOST_DISABLED")),
                          active=lambda row: row.present or row.always_loaded),
    app_layout.ListColumn(lambda row: "L{0}".format(row.hlevel),
                          align=app_layout.RIGHT, enabled=False),
)


def _draw_self_contained(layout, context):
    """The levels that hold whole: nothing to window, so nothing to choose."""
    state = state_of(context)
    command.draw_progress(layout, state)
    filtering.draw_search_row(layout, state,
                              extra_operator=(REFRESH.id, "FILE_REFRESH"))
    if not _WORLDS["rows"]:
        layout.label(text="Refresh to read the levels this install ships.", icon="INFO")
        return
    layout.list(state, "entries", "active_index", _LEVEL_COLUMNS, rows=10,
                identifier="unreal_levels")
    layout.label(text=state.status, icon="INFO")
    app_browser.draw_import_options(layout, context)
    tail = layout.column(align=True)
    tail.enabled = selected(state) is not None
    tail.operator(IMPORT_LEVEL.id, icon="IMPORT")


def _draw_streaming(layout, context):
    """The partitioned worlds: pick one, take a share of it, read its cells, import
    them."""
    state = world_state_of(context)
    command.draw_progress(layout, state)
    if not _WORLDS["rows"]:
        layout.label(text="Refresh to read the worlds this install ships.", icon="INFO")
        layout.operator(REFRESH.id, icon="FILE_REFRESH")
        return
    head = layout.row(align=True)
    head.prop(state, "world", text="")
    head.operator(REFRESH.id, text="", icon="FILE_REFRESH")
    if not state.world:
        layout.label(text="This install ships no partitioned world.", icon="INFO")
        return

    row = _world_row(state)
    rect = _world_rect(state)
    box = layout.box()
    box.label(text="{0} cell(s) in this world".format(_cell_count(row) if row else 0),
              icon="WORLD")
    if rect is None:
        box.label(text="It states no ground of its own -- import its package whole.",
                  icon="INFO")
        box.operator(IMPORT_WORLD.id, icon="IMPORT")
        return
    box.label(text="{0:.0f} x {1:.0f} m whole".format(
        _metres(rect[2] - rect[0]), _metres(rect[3] - rect[1])))

    size = layout.column(align=True)
    size.prop(state, "size", slider=True)
    window = _window(state)
    if window is not None:
        size.label(text="{0:.0f} x {1:.0f} m of it".format(
            _metres(window[2] - window[0]), _metres(window[3] - window[1])))
    knobs = size.row(align=True)
    knobs.prop(state, "level")
    knobs.prop(state, "use_always_loaded")
    size.operator(READ_CELLS.id, icon="VIEWZOOM")

    if _CELLS["error"]:
        alert = layout.row()
        alert.alert = True
        alert.label(text=_CELLS["error"], icon="ERROR")
    if _CELLS["world"] != state.world:
        layout.label(text="Read the cells of this world to pick a window.", icon="INFO")
        return
    filtering.draw_search_row(layout, state)
    layout.list(state, "entries", "active_index", _CELL_COLUMNS, rows=10,
                identifier="unreal_cells")
    layout.label(text=state.status, icon="INFO")
    if not len(state.entries):
        layout.label(text="Nothing in this window: widen Size, or turn Always loaded on.",
                     icon="INFO")
    app_browser.draw_import_options(layout, context)
    tail = layout.column(align=True)
    tail.enabled = bool(len(state.entries))
    tail.operator(IMPORT_WINDOW.id, icon="IMPORT")
    layout.operator(IMPORT_WORLD.id, icon="IMPORT")


_KIND_DRAW = {LEVEL: _draw_self_contained, WORLD: _draw_streaming}


def draw_scene_tab(layout, context):
    """Pick which of the engine's two kinds of level to browse, then browse it."""
    state = state_of(context)
    layout.row(align=True).prop(state, "kind", expand=True)
    _KIND_DRAW[state.kind](layout, context)


def register():
    filtering.register_spec(FILTER_SPEC)
    host = host_port.current()
    host.register_state(STATE, LEVELS, LEVEL_HANDLERS, extra={"FILTER_SPEC_KEY": TAB_KEY})
    host.register_state(WORLD_STATE, WORLDS, WORLD_HANDLERS,
                        extra={"FILTER_SPEC_KEY": TAB_KEY})


def unregister():
    host = host_port.current()
    host.unregister_state(WORLD_STATE)
    host.unregister_state(STATE)
    _published.clear()
    _WORLDS["rows"] = []
    _WORLDS["unit_scale"] = 0.0
    _CELLS["rows"] = []
    _CELLS["world"] = ""
    _CELLS["error"] = ""
