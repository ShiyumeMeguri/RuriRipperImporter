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
            rect), with a scale in case you want more or less of it. The place's map, as the
            game's own map UI draws it, stands in the panel with the window outlined on it,
            and a rectangle dragged over it narrows the window to that part -- which is how a
            place larger than the machine holds is taken a piece at a time.

A third half, ``UI``, browses the lit little stages an interface stands a model on
and loads one AROUND a character already in the scene -- which is the one thing
here that needs a scene to put it around, so it appears only where there is one.

What a window IS is the hook's statement of its seed (``scene_state.seed``), loaded
through the kernel's one load and BUILT by the host: one real object per placement
where there is a scene to hold them, one glTF whose nodes share their meshes where
the project is a file. This module contains no branch for either.

Nothing here imports a host.
"""

from __future__ import annotations

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import command, filtering, loading
from ...Kernel.app import layout as app_layout
from ...Kernel.app import picture as app_picture
from ...Kernel.app import schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...Kernel.app import view as app_view
from ...Kernel.bridge import cabmap_state
from .. import section
from . import SECTIONS, datasets, scene_state

TAB_KEY = "Endfield:streamingscene"
STATE = "ruri_endfield_scene"

SELF_CONTAINED = scene_state.SELF_CONTAINED
STREAMING = scene_state.STREAMING
UI_STAGE = "ui"

#: This half's live view and the seats that draw it. WHICH half is on screen is a
#: standing rule over the hook's own two tables, not a second list shape: whole
#: scenes are the map table, a world's places are the landmark table.
BOUND = app_view.Bound(TAB_KEY)

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
SCENE = Schema("EndfieldScene", """The scene browser's whole state: which half is on
screen, what each half has selected, and how much of a place to take.""", (
    Field("kind", app_state.ENUM, SELF_CONTAINED, "Kind",
          "Which of the game's kinds of scene to browse", items="kind_items",
          update="on_kind"),
    Field("search", app_state.STRING, "", "Filter",
          "Filter by displayed name or id", update="on_filter_edit", live=True),
    Field("rows", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("active_index", app_state.INT, 0, update="on_selection"),
    Field("world_map", app_state.ENUM, None, "Map",
          "Which open-world map's places to list", items="map_items",
          update="on_map_change"),
    Field("scale", app_state.FLOAT, 1.0, "Size",
          "How much of the place to take, against the size the game itself gives it. "
          "1.0 is exactly that. Changing it takes the whole place again, at this size",
          minimum=0.1, soft_maximum=3.0, update="on_scale"),
    Field("window_for", app_state.STRING, "",
          description="The place the window below was picked for; empty when none was"),
    Field("window_min_x", app_state.FLOAT, 0.0),
    Field("window_min_z", app_state.FLOAT, 0.0),
    Field("window_max_x", app_state.FLOAT, 0.0),
    Field("window_max_z", app_state.FLOAT, 0.0),
    Field("scene_state_id", app_state.ENUM, None, "Scene State",
          "Which dressing of this world to read. States are alternates of the same "
          "place, so one at a time", items="state_items"),
    Field("reset_scene", app_state.BOOL, True, "Reset Scene",
          "Empty the document before importing, so a re-imported window does not stack "
          "on the previous one"),
    Field("status", app_state.STRING, "Refresh to read the game's scene list."),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE))


def _table(state):
    """WHICH of the hook's tables this half lists: the maps, or their places."""
    return scene_state.TABLES.get(
        scene_state.MAPS if state.kind == SELF_CONTAINED else scene_state.PLACES)


def _standing(state):
    """This half's own constraint, in the same rule vocabulary the user's are in."""
    if state.kind == SELF_CONTAINED:
        return [cabmap_state.Rule("streaming", "is", "0", "include")]
    return [cabmap_state.Rule("scene", "is", state.world_map or "", "include")]


def _selected(state):
    return BOUND.picked(state)


def _map_name(state):
    """The map a load reads chunks from: the scene itself, or the streaming map
    the selected place belongs to."""
    if state.kind == STREAMING:
        return state.world_map
    entry = _selected(state)
    return entry.key if entry else ""


def _rect(state):
    """The world rect to read. A self-contained scene is loaded whole; a place
    inside a streaming map is loaded at the rect the game gives it, scaled -- or at
    the rectangle picked on its map, which is one place's and goes with that place."""
    if state.kind == SELF_CONTAINED:
        return scene_state.WHOLE
    entry = _selected(state)
    if entry is None:
        return None
    if state.window_for == entry.key:
        return app_picture.snapped(
            (state.window_min_x, state.window_min_z, state.window_max_x, state.window_max_z), _cell(state))
    # The line the user is on IS the row: the rect is four of its own cells, read
    # off the view. Scanning a second copy of the list to find it again would be a
    # second statement of which row is selected.
    stated = entry.values()
    try:
        rect = (float(stated["minX"]), float(stated["minZ"]),
                float(stated["maxX"]), float(stated["maxZ"]))
    except (KeyError, TypeError, ValueError):
        return None
    return app_picture.snapped(scene_state.scaled(rect, state.scale), _cell(state))


def _summary(state):
    """The relevant map's chunk summary, or None when it has not been read yet.
    Never reads it itself: a redraw runs this, and a redraw must not touch the
    VFS."""
    name = _map_name(state)
    return scene_state.SUMMARIES.get(name) if name else None


def _cell(state):
    """The side of the cells the map streams in, in world units: what a window can only be taken in whole
    multiples of. 0 while the map's summary is not read."""
    summary = _summary(state)
    return summary["cell_size"] if summary is not None else 0


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
    when the map was picked -- except that the picker, if one is up, follows the selection."""
    if filtering.is_rebuilding():
        return
    if state.kind == SELF_CONTAINED:
        entry = _selected(state)
        if entry is not None:
            _read_summary(state, entry.key)
    _sync_picker(state)


def _on_map_change(state, context):
    _read_summary(state, state.world_map)
    rebuild(state)


def _on_scale(state, context):
    state.window_for = ""
    _sync_picker(state)


HANDLERS = app_state.Handlers(
    "Endfield.scene", base=filtering.HANDLERS,
    kind_items=_kind_items, map_items=_map_items, state_items=_state_items,
    on_kind=_on_kind, on_filter_edit=_on_filter_edit, on_selection=_on_selection,
    on_map_change=_on_map_change, on_scale=_on_scale)

FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=TAB_KEY, fields=BOUND.fields, state_for=state_of,
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
def rebuild(state):
    """Ask the kernel for the drawn list as it is now stated. Nothing is matched,
    sorted, grouped or counted here: the search text, the user's rules and this
    half's own standing rule all go to the one engine, over the very buffers the
    hook built its tables from."""
    with filtering.rebuilding():
        if state.kind == UI_STAGE:
            BOUND.open(None, state)
        else:
            BOUND.open(_table(state), state, standing=_standing(state))
    _sync_picker(state)


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
    return datasets.language_for_locale("")


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
    rebuild(state)
    state.status = scene_state.STATUS
    return None


def _discover(context, arguments):
    """Read the selection's placements and price it -- what it resolves to in CABs
    is the number that says whether the import fits in memory."""
    state = state_of(context)
    options = app_browser.as_options(app_browser.state_of(context))
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
    options = app_browser.as_options(browser)
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
        yield from _discover(context, arguments)
    seed = scene_state.seed()
    if not seed:
        state.status = "This selection resolves to nothing importable."
        return
    host = host_port.current()
    if state.reset_scene and host_port.SceneGraph in host.capabilities:
        host.clear_scene(context)
    states = [state_id]
    notes = []
    anchor = None
    if host_port.SceneGraph in host.capabilities:
        # The renderer the stacks are verified under, not whatever the startup scene was saved with.
        host.apply_renderer(context)
        viewer = host.source_view_position(context)
        anchor, from_view = datasets.scene_anchor(map_name, *rect, [str(state_id)], detail, viewer)
        notes.append("lit from the view" if from_view else
                     "lit from the middle of the level (the view stands outside it; Light From Here re-lights at the view)")
        # The level's own sky and main light go up before anything that samples them is built.
        host.apply_environment(context, datasets.scene_environment(map_name, anchor, states))
    yield command.Mark(0.15)
    built = loading.load(context, [seed], options)
    notes.extend(built.warnings[:2])
    if anchor is not None:
        notes.extend(_stand_up_lighting(context, host, map_name, rect, anchor, states))
    state.status = "{0}: {1} object(s). {2}".format(
        _label(state), built.imported, "  ".join(notes))


def _stand_up_lighting(context, host, map_name, rect, anchor, states):
    """Everything the level lights with around ``anchor`` beyond its sky and main light. Returns the lines
    to report."""
    notes = []
    # Everything the level states for every material, as one state the stacks read live: its
    # globals (fog, the default sky SH), its baked irradiance rebuilt as the game's camera
    # clipmaps, the reflection probes the game's camera would use, the cloud shadow texture, the
    # shadow ramp, the water state, the deferred decals, the water proxies its wetness pass draws and what
    # the render pipeline itself binds for every material.
    _written, unread = host.apply_level_resources(
        context, datasets.scene_globals(map_name, anchor, states),
        [datasets.scene_irradiance(map_name, anchor),
         datasets.scene_reflection(map_name, anchor, states),
         datasets.scene_cookies(map_name, states),
         datasets.scene_cloud_shadow(map_name, anchor, states),
         datasets.scene_shadow_ramp(map_name, anchor, states),
         datasets.scene_water(map_name, rect, anchor, states),
         datasets.render_pipeline(),
         datasets.scene_decals(map_name, rect, states),
         datasets.scene_water_wetness(map_name, rect, states)])
    if unread:
        notes.append("{0} level resource(s) no shading stack reads".format(len(unread)))
    # Its deferred decals, handed to the objects their boxes reach (the stacks apply them in
    # the material, where the game projects them into its GBuffer).
    notes.extend(host.apply_box_lists(context, datasets.scene_decal_boxes(map_name, rect, states),
                                      datasets.DECAL_RANGE, datasets.DECAL_LISTS))
    # Its reflection probes, each object walking only the slots whose boxes can reach it (the game
    # bins them per pixel before shading).
    notes.extend(host.apply_box_lists(context, datasets.scene_reflection_boxes(map_name, anchor, states),
                                      datasets.PROBE_RANGE, datasets.PROBE_LISTS))
    # Its water proxies' wetness, each object walking only the proxy triangles whose wetness band
    # can reach it (the game rasterises the proxies into a screen mask before lighting).
    notes.extend(host.apply_box_lists(context, datasets.scene_water_wetness_boxes(map_name, rect, states),
                                      datasets.WETNESS_RANGE, datasets.WETNESS_LISTS))
    # The level's volumetric fog, integrated by the host the way the game integrates it.
    medium = datasets.scene_medium(map_name, anchor, states)
    if medium is not None:
        notes.extend(host.apply_medium(context, medium))
    # The level states its own colour grading; a host with a display chain takes it.
    grading = datasets.scene_grading(map_name, anchor, states)
    if grading is not None and host_port.Compositor in host.capabilities:
        host.apply_post_inputs(context, grading["inputs"])
    return notes


def _relight(context, arguments):
    """Resolve the imported level's camera-centred state again around where the document is looked at from
    now -- the game re-resolves it as its camera moves; here the user moves the view and asks."""
    state = state_of(context)
    rect, map_name = _rect(state), _map_name(state)
    host = host_port.current()
    anchor = host.source_view_position(context)
    if rect is None or not map_name or anchor is None:
        state.status = "Nothing to re-light: import a level and look at it first."
        return
    states = [int(state.scene_state_id or 0)]
    yield command.Mark(0.1)
    host.apply_environment(context, datasets.scene_environment(map_name, anchor, states))
    notes = _stand_up_lighting(context, host, map_name, rect, anchor, states)
    state.status = "{0}: lit from the view. {1}".format(_label(state), "  ".join(notes))


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
RELIGHT = command.COMMANDS.define(
    "ruri.endfield_scene_relight", "Light From Here", _relight,
    description="Resolve the level's sky, irradiance, probes, fog and grading again around the current view",
    icon="LIGHT_SUN", requires=host_port.SceneGraph, poll=_has_selection, steps=True, status_state=STATE,
    failure="Re-lighting the level failed")


def _has_picture(context):
    state = state_of(context)
    entry = _selected(state) if state.kind == STREAMING else None
    return entry is not None and scene_state.picture_of(entry.key) is not None


def _offer(context, state, entry, picture):
    """Hand the host's one picker this place's map, with the window it starts from. What it picks goes to this place."""
    key = entry.key

    def accepted(where, rectangle):
        chosen = state_of(where)
        chosen.window_min_x, chosen.window_min_z, chosen.window_max_x, chosen.window_max_z = rectangle
        chosen.window_for = key
        chosen.status = "{0}: {1}, {2}. Read it to price it.".format(
            picture.label, *app_picture.describe(rectangle, _cell(chosen)))

    host_port.current().pick_rectangle(context, picture, _rect(state), accepted, _cell(state))


def _pick(context, arguments):
    """Let the user drag a rectangle over the selected place's map: that rectangle becomes the window."""
    state = state_of(context)
    entry = _selected(state)
    picture = scene_state.picture_of(entry.key) if entry is not None else None
    if picture is None:
        state.status = "The map has not been read yet."
        return {"CANCELLED"}
    _offer(context, state, entry, picture)
    return None


def _sync_picker(state):
    """Keep the one picker, if there is one, on the place that is selected: its map, and the window that place
    starts from. A picker with nothing to pick on -- the selection left the places, or the map would not read --
    ends. Nothing here starts one."""
    host = host_port.current()
    if host_port.RegionPicking not in host.capabilities or not host.picking():
        return
    entry = _selected(state) if state.kind == STREAMING else None
    if entry is None:
        host.stop_picking()
        return
    picture = scene_state.picture_of(entry.key)
    if picture is None and not state.loading:
        picture = scene_state.read_picture(entry.key, entry.label)
    if picture is not None:
        _offer(None, state, entry, picture)
    elif scene_state.PICTURE_FOR == entry.key:
        host.stop_picking()
    else:
        _want_picture(entry)


def _whole(context, arguments):
    state = state_of(context)
    state.window_for = ""
    _sync_picker(state)


PICK = command.COMMANDS.define(
    "ruri.endfield_scene_pick", "Pick On Map", _pick,
    description="Drag a rectangle over the place's map to take only that part of it",
    icon="BORDERMOVE", requires=host_port.RegionPicking, poll=_has_picture, internal=True)
WHOLE = command.COMMANDS.define(
    "ruri.endfield_scene_whole", "Whole Place", _whole,
    description="Take the whole place again, at the size above",
    icon="ZOOM_ALL", requires=host_port.RegionPicking, poll=_has_picture, internal=True)


# ---------------------------------------------------------------------------
# What it looks like
# ---------------------------------------------------------------------------
#: Filtering already happened in rebuild, against the game's own fields rather
#: than the drawn string, so no row is hidden here.
_COLUMNS = (
    BOUND.column("", width=0.72, icon="WORLD"),
    # The game's own id, dimmed and blank when the name already IS the id. The two
    # halves are two tables that key themselves differently (a map by its own name,
    # a place by its level id), so the cell asks for the KEY rather than a column.
    BOUND.key_column(align=app_layout.RIGHT, enabled=False),
)
_GROUP_COLUMN = BOUND.column("", icon="OUTLINER_COLLECTION")


def _draw_list(layout, state):
    filtering.draw_search_row(layout, state,
                              extra_operator=(REFRESH.id, "FILE_REFRESH"))
    app_view.draw_list(BOUND, layout, state, _COLUMNS, "endfield_scenes",
                       group_column=_GROUP_COLUMN)
    if state.status:
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
    # 与浏览器同一份导入选项 —— 场景导入读的也是它。
    app_browser.draw_import_options(options, context)
    options.operator(DISCOVER.id, icon="VIEWZOOM")
    _draw_estimate(layout, state)
    tail = layout.column(align=True)
    tail.enabled = enabled
    if host_port.SceneGraph in host_port.current().capabilities:
        tail.prop(state, "reset_scene")
    tail.operator(IMPORT.id, icon="IMPORT")
    tail.operator(RELIGHT.id, icon="LIGHT_SUN")


def _draw_self_contained(layout, context, state):
    """The self-contained scenes: nothing to window, so nothing to choose."""
    _draw_list(layout, state)
    _draw_actions(layout, context, state, _selected(state) is not None)


def _want_picture(entry):
    """Ask for the map of the place on screen to be read once the redraw is over: a redraw must not touch the
    VFS, and what it can do instead is say what it is waiting for."""
    key = entry.key
    if scene_state.PICTURE_ASKED == key:
        return
    scene_state.PICTURE_ASKED = key
    label = entry.label
    host = host_port.current()

    def read():
        if scene_state.PICTURE_ASKED != key:
            return None
        if state_of(None).loading:
            return 0.5
        scene_state.read_picture(key, label)
        _sync_picker(state_of(None))
        host.redraw()
        return None

    host.schedule(0.0, read)


def _draw_map(layout, state, entry):
    """The selected place's map with the window outlined on it, and the buttons that change the window."""
    picture = scene_state.picture_of(entry.key)
    if picture is None:
        if scene_state.PICTURE_FOR == entry.key:
            layout.label(text=scene_state.PICTURE_NOTE, icon="ERROR")
        else:
            _want_picture(entry)
            layout.label(text="Reading the map...", icon="TIME")
        return
    window = _rect(state)
    layout.picture(picture, window)
    row = layout.row(align=True)
    row.operator(PICK.id, icon="BORDERMOVE")
    row.operator(WHOLE.id, text="", icon="ZOOM_ALL")
    cell = _cell(state)
    layout.label(text=app_picture.describe(window)[0], icon="EMPTY_AXIS")
    if cell:
        layout.label(text="{0:.0f} x {1:.0f} cells of {2:.0f} m".format(
            (window[2] - window[0]) / cell, (window[3] - window[1]) / cell, cell), icon="GRID")


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

    if entry is not None and host_port.RegionPicking in host_port.current().capabilities:
        _draw_map(layout, state, entry)

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
    BOUND.close()
    scene_state.reset()
