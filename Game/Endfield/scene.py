"""Browse the game's scenes the way the game itself lists them, and import one.

One tab with two halves, because the game itself ships two kinds of scene (see
``scene_state`` for how they are told apart -- by the game's own ``isSingleLevel``
flag, never by the shape of a name):

``Scene``   the self-contained ones -- a dungeon, a station interior. Small enough
            to hold whole, so there is nothing to choose: pick it, import it.
``World``   the open-world maps, map01 and map02. Far too big to hold at once
            (map02 whole resolves to 26811 CABs), and the running game never holds
            one either -- it streams a window around the player. So these are
            imported one named PLACE at a time, at the size the game itself gives
            that place (供能高地 is x[-640..384] z[-128..896], its own published
            rect), with a scale in case you want more or less of it.

A third half, ``UI``, browses the lit little stages an interface stands a model on
and loads one AROUND a character already in the scene -- which is the one thing
here that needs a scene to put it around, so it appears only where there is one.

What a window IS gets stated here (``scene_state.packages``) and BUILT by the
host: one real object per placement where there is a scene to hold them, one glTF
whose nodes share their meshes where the project is a file. Neither is a lesser
version of the other, and this module contains no branch for either.

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
from .. import section
from . import SECTIONS, datasets, scene_state

TAB_KEY = "Endfield:streamingscene"
STATE = "ruri_endfield_scene"

SELF_CONTAINED = scene_state.SELF_CONTAINED
STREAMING = scene_state.STREAMING
UI_STAGE = "ui"

#: What a scene row can be filtered by -- the same three values the list draws,
#: which is also exactly what gets published to the search engine.
_FILTER_FIELDS = (("name", "Name"), ("id", "Id"), ("group", "Group"))
_FILTER_COLUMNS = tuple(key for key, _label in _FILTER_FIELDS)

#: handle -> the row list last published under it, so a keystroke re-searches an
#: already-open table instead of rebuilding one that has not changed.
_published = {}

#: Read once: a redraw runs this, and the machine's RAM does not change.
_machine_memory_gb = []


def state_of(context):
    return host_port.current().panel_state(context, STATE)


def _kinds():
    """The halves this host can offer. A display stage is loaded AROUND what is
    already in the scene, so where there is no scene there is nothing for it to be
    loaded around -- and a half that cannot work is absent, not disabled."""
    kinds = [(SELF_CONTAINED, "Scene",
              "The self-contained scenes -- small enough to import whole"),
             (STREAMING, "World",
              "The open-world maps -- import one named place of map01/map02 at a time")]
    if section(SECTIONS, "ui_scene").available:
        kinds.append((UI_STAGE, "UI",
                      "The lit little stages an interface puts a model on -- CharInfo, "
                      "CharFormation, WeaponInfo. Load one around a character already "
                      "in the scene"))
    return kinds


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
SCENE_ENTRY = Schema("EndfieldSceneEntry", """One drawn line: a family header or a
scene/place.""", (
    Field("label", app_state.STRING, ""),
    Field("key", app_state.STRING, ""),
    Field("is_group", app_state.BOOL, False),
))

SCENE = Schema("EndfieldScene", """The scene browser's whole state: which half is on
screen, what each half has selected, and how much of a place to take.""", (
    Field("kind", app_state.ENUM, SELF_CONTAINED, "Kind",
          "Which of the game's kinds of scene to browse", items="kind_items",
          update="on_kind"),
    Field("search", app_state.STRING, "", "Filter",
          "Filter by displayed name or id", update="on_filter_edit", live=True),
    Field("entries", app_state.COLLECTION, element=SCENE_ENTRY),
    Field("active_index", app_state.INT, 0, update="on_selection"),
    Field("world_map", app_state.ENUM, None, "Map",
          "Which open-world map's places to list", items="map_items",
          update="on_map_change"),
    Field("scale", app_state.FLOAT, 1.0, "Size",
          "How much of the place to take, against the size the game itself gives it. "
          "1.0 is exactly that", minimum=0.1, soft_maximum=3.0),
    Field("scene_state_id", app_state.ENUM, None, "Scene State",
          "Which dressing of this world to read. States are alternates of the same "
          "place, so one at a time", items="state_items"),
    Field("reset_scene", app_state.BOOL, True, "Reset Scene",
          "Empty the document before importing, so a re-imported window does not stack "
          "on the previous one"),
    Field("status", app_state.STRING, "Refresh to read the game's scene list."),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE))


def _rows(state):
    """What this half lists: whole scenes, or one streaming map's named places."""
    if state.kind == SELF_CONTAINED:
        return scene_state.SCENES[SELF_CONTAINED]
    return scene_state.LANDMARKS.get(state.world_map, [])


def _selected(state):
    if 0 <= state.active_index < len(state.entries):
        entry = state.entries[state.active_index]
        if not entry.is_group:
            return entry
    return None


def _map_name(state):
    """The map a load reads chunks from: the scene itself, or the streaming map
    the selected place belongs to."""
    if state.kind == STREAMING:
        return state.world_map
    entry = _selected(state)
    return entry.key if entry else ""


def _rect(state):
    """The world rect to read. A self-contained scene is loaded whole; a place
    inside a streaming map is loaded at the rect the game gives it, scaled."""
    if state.kind == SELF_CONTAINED:
        return scene_state.WHOLE
    entry = _selected(state)
    if entry is None:
        return None
    for row in _rows(state):
        if row["id"] == entry.key:
            return scene_state.scaled(row["rect"], state.scale)
    return None


def _summary(state):
    """The relevant map's chunk summary, or None when it has not been read yet.
    Never reads it itself: a redraw runs this, and a redraw must not touch the
    VFS."""
    name = _map_name(state)
    return scene_state.SUMMARIES.get(name) if name else None


# ---------------------------------------------------------------------------
# The handlers the schema names
# ---------------------------------------------------------------------------
def _kind_items(state, context):
    return _kinds()


def _map_items(state, context):
    return [(row["id"], row["label"], row["id"])
            for row in scene_state.SCENES[STREAMING]]


def _state_items(state, context):
    summary = _summary(state)
    return [(str(one), str(one), "Scene state {0}".format(one))
            for one in (summary["scene_state_ids"] if summary else [])] or [("0", "0", "")]


def _on_kind(state, context):
    rebuild(state)


def _on_filter_edit(state, context):
    rebuild(state)


def _on_selection(state, context):
    """Selecting a whole scene IS the intent to look at it, so its inventory is
    read then. A place inside a map needs nothing: its map's inventory was read
    when the map was picked."""
    if filtering.is_rebuilding():
        return
    if state.kind == SELF_CONTAINED:
        entry = _selected(state)
        if entry is not None:
            _read_summary(state, entry.key)


def _on_map_change(state, context):
    _read_summary(state, state.world_map)
    rebuild(state)


HANDLERS = app_state.Handlers(
    "Endfield.scene", base=filtering.HANDLERS,
    kind_items=_kind_items, map_items=_map_items, state_items=_state_items,
    on_kind=_on_kind, on_filter_edit=_on_filter_edit, on_selection=_on_selection,
    on_map_change=_on_map_change)

FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=TAB_KEY, fields=_FILTER_FIELDS, state_for=state_of,
    apply=lambda context: rebuild(state_of(context))))


def _read_summary(state, map_name):
    """Read one map's chunk summary (manifest-only, cached per map) and aim the
    scene-state selector at the lowest state it ships."""
    if not map_name or cabmap_state.BRIDGE is None:
        return
    try:
        summary = scene_state.load_summary(map_name)
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return
    if summary["scene_state_ids"]:
        state.scene_state_id = str(summary["scene_state_ids"][0])


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------
def _filter_handle(state):
    """One open table per list. The streaming half's places change with the map,
    so its map is part of the handle -- switching maps opens a different table
    rather than searching the previous one."""
    return ("ruri.endfield.scene" if state.kind == SELF_CONTAINED
            else "ruri.endfield.world\x1f" + state.world_map)


def _matching_rows(state):
    """The rows passing the search box AND every enabled rule -- matched by the
    SAME C# engine the asset-bundle browser uses, over this list published as a
    table. No matching happens on this side."""
    rows = _rows(state)
    if cabmap_state.BRIDGE is None or not rows:
        return list(rows)
    handle = _filter_handle(state)
    if _published.get(handle) is not rows:
        cabmap_state.BRIDGE.open_host_table(
            handle, _FILTER_COLUMNS,
            [(row["label"], row["id"], row.get("group", "")) for row in rows])
        _published[handle] = rows
    ids = cabmap_state.BRIDGE.search_data_table(handle, state.search.strip(),
                                                state.filter_rules)
    return [rows[index] for index in ids if 0 <= index < len(rows)]


def rebuild(state):
    """Rebuild the drawn line list. Whole scenes are grouped by the id family the
    game files them under; a map's places are not -- there are a handful and they
    are all siblings."""
    with filtering.rebuilding():
        _fill(state)


def _fill(state):
    chosen = filtering.selected_key(state)
    state.entries.clear()
    if state.kind == UI_STAGE:
        filtering.restore_selection(state, chosen)
        return
    rows = _matching_rows(state)
    grouped = state.kind == SELF_CONTAINED
    rows.sort(key=lambda row: (row["group"] if grouped else "", row["id"]))

    counts = {}
    for row in rows:
        counts[row.get("group", "")] = counts.get(row.get("group", ""), 0) + 1

    current_group = None
    for row in rows:
        if grouped and row["group"] != current_group:
            current_group = row["group"]
            header = state.entries.add()
            header.label = "{0}  ({1})".format(current_group, counts[current_group])
            header.is_group = True
        entry = state.entries.add()
        entry.label = row["label"]
        entry.key = row["id"]
    filtering.restore_selection(state, chosen)


# ---------------------------------------------------------------------------
# What the buttons do
# ---------------------------------------------------------------------------
def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_selection(context):
    state = state_of(context)
    return _loaded(context) and _rect(state) is not None and bool(_map_name(state))


def _language():
    """The game language these lists are shown in -- the host application's own
    locale, mapped onto the languages the game ships."""
    return datasets.language_for_locale(host_port.current().locale())


def _refresh(context, arguments):
    """Read the game's own scene list, with the names it shows for them."""
    state = state_of(context)
    try:
        scene_state.load_scenes(_language())
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    maps = scene_state.SCENES[STREAMING]
    if not scene_state.SCENES[SELF_CONTAINED] and not maps:
        state.status = "No scenes with streaming data under this game root's VFS."
        return {"CANCELLED"}
    if maps:
        # Assigning an enum its CURRENT value fires no update callback -- read the
        # inventory explicitly so the first map is ready either way.
        state.world_map = maps[0]["id"]
        _read_summary(state, state.world_map)
    _published.clear()
    rebuild(state)
    state.status = scene_state.STATUS
    return None


def _discover(context, arguments):
    """Read the selection's placements and price it -- what it resolves to in CABs
    is the number that says whether the import fits in memory."""
    state = state_of(context)
    options = app_browser.as_options(app_browser.state_of(context), scene=True)
    rect, map_name = _rect(state), _map_name(state)
    if rect is None or not map_name:
        state.status = "Nothing selected."
        return {"CANCELLED"}
    detail = int(options.get("detail_level", 0) or 0)
    state_id = int(state.scene_state_id or 0)
    yield command.Read(
        lambda: scene_state.discover_placements(map_name, rect, state_id, detail), 0.6)
    yield command.Read(lambda: scene_state.resolve_cabs(cabmap_state.BRIDGE), 0.9)
    estimate = scene_state.estimate()
    state.status = "{0} placeable, {1} distinct assets -> {2} CAB(s) in closure.".format(
        estimate["placeable"], estimate["distinct_assets"], estimate["closure_cabs"])


def _import(context, arguments):
    """Materialise whatever is currently discovered, re-reading first when the
    selection has moved on since."""
    state = state_of(context)
    browser = app_browser.state_of(context)
    options = app_browser.as_options(browser, scene=True)
    rect, map_name = _rect(state), _map_name(state)
    if rect is None or not map_name:
        state.status = "Nothing selected."
        return
    # Staleness guard: whatever path led here, NEVER import something other than
    # what is currently selected -- re-read in place if they disagree.
    detail = int(options.get("detail_level", 0) or 0)
    state_id = int(state.scene_state_id or 0)
    window = rect + (state_id, detail)
    if scene_state.CURRENT_MAP != map_name or scene_state.CURRENT_WINDOW != window:
        for step in _discover(context, arguments):
            yield step
    packages = scene_state.packages(_label(state))
    if packages is None:
        state.status = "This selection resolves to nothing importable."
        return
    host = host_port.current()
    if state.reset_scene and host_port.SCENE_GRAPH in host.capabilities:
        host.clear_scene(context)
    yield command.Mark(0.15)
    built = host.import_packages(context, packages, options)
    state.status = "{0}: {1} object(s). {2}".format(
        packages.label, built.imported, "  ".join(built.warnings[:2]))


def _label(state):
    entry = _selected(state)
    return (entry.label if entry else "") or _map_name(state)


REFRESH = command.COMMANDS.define(
    "ruri.endfield_scene_refresh", "Refresh Scenes", _refresh,
    description="Read every scene the game ships streaming data for, under its own name",
    icon="FILE_REFRESH", internal=True, poll=_loaded)
DISCOVER = command.COMMANDS.define(
    "ruri.endfield_scene_discover", "Read", _discover,
    description="Decode this selection's chunks and estimate what importing it would cost",
    icon="VIEWZOOM", poll=_has_selection, steps=True, status_state=STATE,
    failure="Reading this selection failed")
IMPORT = command.COMMANDS.define(
    "ruri.endfield_scene_import", "Import", _import,
    description="Resolve the selection's dependency closure and import it",
    icon="IMPORT", poll=_has_selection, steps=True, status_state=STATE,
    failure="Scene import failed")


# ---------------------------------------------------------------------------
# What it looks like
# ---------------------------------------------------------------------------
#: Filtering already happened in rebuild, against the game's own fields rather
#: than the drawn string, so no row is hidden here.
_COLUMNS = (
    app_layout.ListColumn("label", width=0.72, icon="WORLD"),
    # The game's own id, dimmed -- blank when the name already IS the id, so a
    # scene the game ships no name for is not printed twice.
    app_layout.ListColumn(lambda row: "" if row.key == row.label else row.key,
                          align=app_layout.RIGHT, enabled=False),
)
_GROUP_COLUMN = app_layout.ListColumn("label", icon="OUTLINER_COLLECTION")


def _draw_list(layout, state):
    filtering.draw_search_row(layout, state,
                              extra_operator=(REFRESH.id, "FILE_REFRESH"))
    layout.list(state, "entries", "active_index", _COLUMNS, rows=10,
                identifier="endfield_scenes", group_key="is_group",
                group_column=_GROUP_COLUMN)
    layout.label(text=state.status, icon="INFO")


def _system_memory_gb():
    if not _machine_memory_gb:
        _machine_memory_gb.append(scene_state.system_memory_gb())
    return _machine_memory_gb[0]


def _draw_estimate(layout, state):
    if not scene_state.placeable_count():
        return
    estimate = scene_state.estimate()
    box = layout.box()
    min_x, min_z, max_x, max_z, state_id, _detail = scene_state.CURRENT_WINDOW
    box.label(text="{0} state {1}".format(scene_state.CURRENT_MAP, state_id)
              if min_x == float("-inf") else
              "{0} x[{1:.0f}..{2:.0f}] z[{3:.0f}..{4:.0f}] state {5}".format(
                  scene_state.CURRENT_MAP, min_x, max_x, min_z, max_z, state_id))
    box.label(text="{0} renderer(s), {1} distinct asset(s)".format(
        estimate["total_renderers"], estimate["distinct_assets"]))
    box.label(text="{0} placeable".format(estimate["placeable"])
        + (", {0} at other detail levels".format(estimate["detail_filtered"])
           if estimate["detail_filtered"] else "")
        + (", {0} distant stand-in(s)".format(estimate["stand_in_filtered"])
           if estimate["stand_in_filtered"] else "")
        + (", {0} with no transform".format(estimate["no_transform"])
           if estimate["no_transform"] else "")
        + (", !! {0} with no renderer".format(estimate["no_renderers"])
           if estimate["no_renderers"] else ""))
    box.label(text="{0} seed CAB(s) -> {1} in closure".format(
        estimate["resolved_cabs"], estimate["closure_cabs"]))
    projected = scene_state.projected_peak_gb(estimate["closure_cabs"])
    machine = _system_memory_gb()
    over = machine is not None and projected > machine * 0.9
    line = box.row()
    line.alert = bool(over)
    line.label(text="~{0:.0f} GB peak".format(projected) if machine is None else
                    "~{0:.0f} GB peak, this machine has {1:.0f} GB".format(projected, machine),
               icon="ERROR" if over else "NONE")
    if over and state.kind == STREAMING:
        box.label(text="Turn Size down, or expect it to swap.", icon="INFO")


def _draw_actions(layout, context, state, enabled):
    command.draw_progress(layout, state)
    options = layout.column(align=True)
    options.enabled = enabled
    options.prop(state, "scene_state_id")
    # 与浏览器同一份导入选项 —— 场景导入读的也是它。一个场景窗口是几百上千张材质,
    # 所以第二个记忆值(Game Shaders)画在按 Import 的地方,别让人跑去另一个 tab 找。
    app_browser.draw_import_options(options, context)
    if host_port.NODE_MATERIALS in host_port.current().capabilities:
        options.prop(app_browser.state_of(context), "scene_shaders")
    options.operator(DISCOVER.id, icon="VIEWZOOM")
    _draw_estimate(layout, state)
    tail = layout.column(align=True)
    tail.enabled = enabled
    if host_port.SCENE_GRAPH in host_port.current().capabilities:
        tail.prop(state, "reset_scene")
    tail.operator(IMPORT.id, icon="IMPORT")


def _draw_self_contained(layout, context, state):
    """The self-contained scenes: nothing to window, so nothing to choose."""
    _draw_list(layout, state)
    _draw_actions(layout, context, state, _selected(state) is not None)


def _draw_streaming(layout, context, state):
    """The open-world maps: pick a map, then one of the places the game itself
    names in it, at the size the game itself gives that place."""
    if not scene_state.SCENES[STREAMING]:
        layout.label(text="Refresh to read the game's scene list.", icon="INFO")
        layout.operator(REFRESH.id, icon="FILE_REFRESH")
        return
    layout.prop(state, "world_map")
    _draw_list(layout, state)

    entry = _selected(state)
    summary = _summary(state)
    if summary is not None:
        box = layout.box()
        box.label(text="{0}: {1} cell chunk(s), {2:.0f} MB whole".format(
            state.world_map, summary["anchored_files"],
            summary["anchored_bytes"] / 1048576.0))
        box.label(text="+ {0} map-wide/dynamic chunk(s), {1:.0f} MB, bounded to the "
                       "selection".format(summary["floating_files"],
                                          summary["floating_bytes"] / 1048576.0))

    size = layout.row(align=True)
    size.enabled = entry is not None
    size.prop(state, "scale")
    rect = _rect(state) if entry is not None else None
    if rect is not None:
        size.label(text="{0:.0f} x {1:.0f} m".format(rect[2] - rect[0], rect[3] - rect[1]))
    _draw_actions(layout, context, state, entry is not None)


def _draw_ui_stage(layout, context, state):
    from . import ui_scene
    ui_scene.draw_ui_scene_tab(layout, context)


_KIND_DRAW = {SELF_CONTAINED: _draw_self_contained, STREAMING: _draw_streaming,
              UI_STAGE: _draw_ui_stage}


def draw_streaming_scene_tab(layout, context):
    """Pick which of the game's kinds of scene to browse, then browse it."""
    state = state_of(context)
    layout.row(align=True).prop(state, "kind", expand=True)
    _KIND_DRAW[state.kind](layout, context, state)


def register():
    filtering.register_spec(FILTER_SPEC)
    host_port.current().register_state(STATE, SCENE, HANDLERS,
                                       extra={"FILTER_SPEC_KEY": TAB_KEY})


def unregister():
    host_port.current().unregister_state(STATE)
    _published.clear()
    scene_state.reset()
