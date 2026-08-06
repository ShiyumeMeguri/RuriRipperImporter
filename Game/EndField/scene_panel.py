"""Browse the game's scenes the way the game itself lists them, and import one.

Two tabs, because the game itself ships two kinds of scene (see ``scene_state``
for how they are told apart -- by the game's own ``isSingleLevel`` flag, never by
the shape of a name):

``Scene``   the self-contained ones -- a dungeon, a station interior. Small
            enough to hold whole, so there is nothing to choose: pick it, import
            it.
``World``   the open-world maps, map01 and map02. Far too big to hold at once
            (map02 whole resolves to 26811 CABs), and the running game never
            holds one either -- it streams a window around the player. So these
            are imported one named PLACE at a time, at the size the game itself
            gives that place (供能高地 is x[-640..384] z[-128..896], its own
            published rect), with a scale in case you want more or less of it.

Both lists behave like the roster's next door: type to filter, click to select.
Names are the real localized ones, in whichever language Blender is running in --
``bpy.app.translations.locale`` picks which text container the C# side joins
through.

The two draw functions below are what this game's tabs declare (see the package's
GAME_MODULE) -- NOT their own stacked bl_parent_id sub-panels, so they share the
core panel's hard gate (nothing here is reachable before a cabmap is loaded) and
tab bar instead of always being visible below it.
"""

from __future__ import annotations

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       FloatProperty, IntProperty, PointerProperty, StringProperty)

from ...ruri_pybridge.session import cabmap_state
from . import roster, scene_importer, scene_state

SELF_CONTAINED = scene_state.SELF_CONTAINED
STREAMING = scene_state.STREAMING

# Kept alive at module scope -- Blender's dynamic EnumProperty items callback
# requires the returned list to outlive the call (a fresh list literal returned
# each time is a well-documented footgun: the C-level enum can end up pointing at
# already-freed Python string memory).
_state_items_cache = [("0", "0", "")]
_map_items_cache = [("", "(refresh first)", "")]


_machine_memory_gb = ()


def _system_memory_gb():
    """Read once: a redraw runs this, and the machine's RAM does not change."""
    global _machine_memory_gb
    if _machine_memory_gb == ():
        _machine_memory_gb = scene_state.system_memory_gb()
    return _machine_memory_gb


def _report_exception(op, prefix, exc):
    import traceback
    traceback.print_exc()
    op.report({"ERROR"}, f"{prefix}: {type(exc).__name__}: {exc} (full traceback in console)")


def _language():
    """The game language these lists are shown in -- Blender's own locale, mapped
    onto the languages the game ships."""
    return roster.language_for_locale(bpy.app.translations.locale)


def _state(context, kind):
    return context.scene.ruri_scene_box if kind == SELF_CONTAINED else context.scene.ruri_scene_world


def _rows(state):
    """What this tab lists: whole scenes, or one streaming map's named places."""
    if state.KIND == SELF_CONTAINED:
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
    if state.KIND == STREAMING:
        return state.world_map
    entry = _selected(state)
    return entry.key if entry else ""


def _rect(state):
    """The world rect to read. A self-contained scene is loaded whole; a place
    inside a streaming map is loaded at the rect the game gives it, scaled."""
    if state.KIND == SELF_CONTAINED:
        return scene_state.WHOLE
    entry = _selected(state)
    if entry is None:
        return None
    for row in _rows(state):
        if row["id"] == entry.key:
            return scene_state.scaled(row["rect"], state.scale)
    return None


def _chunks(state):
    """The relevant map's chunk inventory, or None when it has not been read yet.
    Never reads it itself: a redraw runs this, and a redraw must not touch the
    VFS."""
    name = _map_name(state)
    return scene_state.CHUNKS.get(name) if name else None


def _rebuild(state):
    """Rebuild the drawn line list, filtered by the search box. Whole scenes are
    grouped by the id family the game files them under; a map's places are not --
    there are a handful and they are all siblings."""
    state.entries.clear()
    needle = state.search.strip().lower()
    rows = [row for row in _rows(state)
            if not needle or needle in row["id"].lower() or needle in row["label"].lower()]
    grouped = state.KIND == SELF_CONTAINED
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
    if state.active_index >= len(state.entries):
        state.active_index = 0


def _read_chunks(state, context, map_name):
    """Read one map's chunk inventory (manifest-only, cached per map) and aim the
    scene-state selector at the lowest state it ships."""
    if not map_name or cabmap_state.BRIDGE is None:
        return
    try:
        chunks = scene_state.load_chunks(cabmap_state.BRIDGE,
                                         context.scene.ruri_cabmap.game_root, map_name)
    except Exception as exc:
        scene_state.STATUS = "{0}: {1}".format(type(exc).__name__, exc)
        print("[RuriRipper] chunk inventory for '{0}' failed: {1}".format(map_name, exc))
        return
    states = scene_state.scene_states(chunks)
    if states:
        state.scene_state_id = str(states[0])


def _map_items(self, context):
    global _map_items_cache
    _map_items_cache = [(row["id"], row["label"], row["id"])
                        for row in scene_state.SCENES[STREAMING]] or [("", "(refresh first)", "")]
    return _map_items_cache


def _state_items(self, context):
    global _state_items_cache
    chunks = _chunks(self)
    states = scene_state.scene_states(chunks) if chunks else []
    _state_items_cache = [(str(s), str(s), "Scene state {0}".format(s)) for s in states] \
        or [("0", "0", "")]
    return _state_items_cache


def _on_search_edit(self, context):
    _rebuild(self)


def _on_selection_change(self, context):
    """Selecting a whole scene IS the intent to look at it, so its inventory is
    read then. A place inside a map needs nothing: its map's inventory was read
    when the map was picked."""
    if self.KIND == SELF_CONTAINED:
        entry = _selected(self)
        if entry is not None:
            _read_chunks(self, context, entry.key)


def _on_map_change(self, context):
    _read_chunks(self, context, self.world_map)
    _rebuild(self)


class RURI_PG_scene_entry(bpy.types.PropertyGroup):
    """One drawn line: either a family header or a scene/place."""
    label: StringProperty()
    key: StringProperty()
    is_group: BoolProperty(default=False)


class _SceneListMixin:
    """Everything both tabs hold. Blender picks annotations up through the class
    hierarchy, so this is one declaration rather than two that can drift."""
    search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"}, update=_on_search_edit,
                           description="Filter by displayed name or id")
    entries: CollectionProperty(type=RURI_PG_scene_entry)
    active_index: IntProperty(update=_on_selection_change)
    scene_state_id: EnumProperty(name="Scene State", items=_state_items,
                                 description="Which dressing of this world to read. States are "
                                             "alternates of the same place, so one at a time")
    lod0_only: BoolProperty(name="LOD0 Only", default=True,
                            description="Keep only the best available LOD sibling per placed instance; "
                                        "affects both the estimate and the import")
    reset_scene: BoolProperty(name="Reset Scene", default=True,
                              description="Delete existing scene objects before importing")


class RURI_PG_scene_box(_SceneListMixin, bpy.types.PropertyGroup):
    KIND = SELF_CONTAINED


class RURI_PG_scene_world(_SceneListMixin, bpy.types.PropertyGroup):
    KIND = STREAMING
    world_map: EnumProperty(name="Map", items=_map_items, update=_on_map_change,
                            description="Which open-world map's places to list")
    scale: FloatProperty(name="Size", default=1.0, min=0.1, soft_max=3.0,
                         description="How much of the place to take, against the size the game itself "
                                     "gives it. 1.0 is exactly that")


class RURI_UL_scenes(bpy.types.UIList):
    bl_idname = "RURI_UL_scenes"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if item.is_group:
            row = layout.row()
            row.enabled = False
            row.label(text=item.label, icon="OUTLINER_COLLECTION")
            return
        row = layout.row(align=True)
        row.label(text=item.label, icon="WORLD")
        # The game's own id, dimmed -- skipped when the name already IS the id, so
        # a scene the game ships no name for is not printed twice.
        identifier = row.row()
        identifier.enabled = False
        identifier.alignment = "RIGHT"
        identifier.label(text="" if item.key == item.label else item.key)

    def filter_items(self, context, data, propname):
        # Filtering already happened in _rebuild, against the game's own fields
        # rather than the drawn string -- leave the list untouched.
        return [], []


class RURI_OT_scene_refresh(bpy.types.Operator):
    """Read the game's own scene list, with the names it shows for them."""
    bl_idname = "ruri.scene_refresh"
    bl_label = "Refresh Scenes"
    bl_description = "Read every scene the game ships streaming data for, under its own name"

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        try:
            scene_state.load_scenes(cabmap_state.BRIDGE, context.scene.ruri_cabmap.game_root,
                                    _language())
        except Exception as exc:
            _report_exception(self, "Scene list failed", exc)
            return {"CANCELLED"}
        if not scene_state.SCENES[SELF_CONTAINED] and not scene_state.SCENES[STREAMING]:
            self.report({"WARNING"}, "No scenes with streaming data under this game root's VFS.")
            return {"CANCELLED"}
        _rebuild(context.scene.ruri_scene_box)
        world = context.scene.ruri_scene_world
        maps = scene_state.SCENES[STREAMING]
        if maps:
            # Assigning an EnumProperty its CURRENT value does not fire the update
            # callback -- read the inventory explicitly so the first map is ready
            # either way.
            world.world_map = maps[0]["id"]
            _read_chunks(world, context, world.world_map)
        _rebuild(world)
        self.report({"INFO"}, scene_state.STATUS)
        return {"FINISHED"}


class RURI_OT_scene_discover(bpy.types.Operator):
    """Read the selection's placements and price it -- what it resolves to in
    CABs is the number that says whether the import fits in memory."""
    bl_idname = "ruri.scene_discover"
    bl_label = "Read"
    bl_description = "Decode this selection's chunks and estimate what importing it would cost"
    bl_options = {"REGISTER"}

    kind: StringProperty(default=SELF_CONTAINED, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = _state(context, self.kind)
        rect = _rect(state)
        map_name = _map_name(state)
        if rect is None or not map_name:
            self.report({"ERROR"}, "Nothing selected.")
            return {"CANCELLED"}
        try:
            scene_state.discover_placements(
                cabmap_state.BRIDGE, context.scene.ruri_cabmap.game_root, map_name,
                rect, int(state.scene_state_id))
            scene_state.resolve_cabs(cabmap_state.BRIDGE, state.lod0_only)
        except Exception as exc:
            _report_exception(self, "Read failed", exc)
            return {"CANCELLED"}
        est = scene_state.estimate(state.lod0_only)
        self.report({"INFO"}, "{0} placeable, {1} distinct assets -> {2} CAB(s) in closure.".format(
            est["placeable"], est["distinct_assets"], est["closure_cabs"]))
        return {"FINISHED"}


class RURI_OT_scene_import(bpy.types.Operator):
    bl_idname = "ruri.scene_import"
    bl_label = "Import"
    bl_description = "Resolve the selection's dependency closure and import it"
    bl_options = {"REGISTER", "UNDO"}

    kind: StringProperty(default=SELF_CONTAINED, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = _state(context, self.kind)
        rect = _rect(state)
        map_name = _map_name(state)
        if rect is None or not map_name:
            self.report({"ERROR"}, "Nothing selected.")
            return {"CANCELLED"}
        # Staleness guard: whatever path led here, NEVER import something other
        # than what is currently selected -- re-read in place if they disagree.
        window = rect + (int(state.scene_state_id),)
        if scene_state.CURRENT_MAP != map_name or scene_state.CURRENT_WINDOW != window:
            if "FINISHED" not in bpy.ops.ruri.scene_discover(kind=self.kind):
                return {"CANCELLED"}
        if not scene_state.RESOLVED_CABS:
            self.report({"WARNING"}, "This selection resolves to nothing importable.")
            return {"CANCELLED"}
        if state.reset_scene:
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)

        try:
            assets, roots, _seed_roots, _clips_by_cab, _scene_roots = \
                cabmap_state.BRIDGE.import_cabs(scene_state.RESOLVED_CABS)
        except Exception as exc:
            _report_exception(self, "Scene import (bridge) failed", exc)
            return {"CANCELLED"}

        db = _bridge_asset_db_module().BridgeAssetDatabase(
            assets, clip_curve_blobs=cabmap_state.BRIDGE.clip_curves_by_guid,
            mesh_blobs=cabmap_state.BRIDGE.mesh_blobs_by_guid,
            asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid)
        try:
            imported, placed, unresolved = scene_importer.import_scene_placements(
                context, db, scene_state.placeable(state.lod0_only), roots,
                context.scene.ruri_cabmap.as_options())
        except Exception as exc:
            _report_exception(self, "Scene placement build failed", exc)
            return {"CANCELLED"}

        self.report({"INFO"}, f"Imported {imported} distinct asset(s), placed {placed} object(s)"
                              + (f", {unresolved} unresolved" if unresolved else "") + ".")
        return {"FINISHED"}


def _bridge_asset_db_module():
    """Imported lazily: only the bridge path needs it, and the scene tabs draw
    long before any bridge session exists."""
    from ...ruri_pybridge.unity import bridge_asset_db
    return bridge_asset_db


def _draw_list(layout, state):
    head = layout.row(align=True)
    head.prop(state, "search", icon="VIEWZOOM", text="")
    head.operator(RURI_OT_scene_refresh.bl_idname, text="", icon="FILE_REFRESH")
    layout.template_list(RURI_UL_scenes.bl_idname, "", state, "entries",
                         state, "active_index", rows=10)
    layout.label(text=scene_state.STATUS, icon="INFO")


def _draw_estimate(layout, state):
    if not scene_state.PLACEMENTS:
        return
    est = scene_state.estimate(state.lod0_only)
    box = layout.box()
    min_x, min_z, max_x, max_z, scene_state_id = scene_state.CURRENT_WINDOW
    box.label(text="{0} state {1}".format(scene_state.CURRENT_MAP, scene_state_id)
              if min_x == float("-inf") else
              "{0} x[{1:.0f}..{2:.0f}] z[{3:.0f}..{4:.0f}] state {5}".format(
                  scene_state.CURRENT_MAP, min_x, max_x, min_z, max_z, scene_state_id))
    box.label(text="{0} placement(s), {1} distinct asset(s)".format(
        est["total_placements"], est["distinct_assets"]))
    box.label(text="{0} placeable, {1} excluded (no transform)".format(
        est["placeable"], est["no_transform"])
        + (", {0} non-LOD0 duplicates skipped".format(est["lod_filtered"])
           if est["lod_filtered"] else ""))
    box.label(text="{0} seed CAB(s) -> {1} in closure".format(
        est["resolved_cabs"], est["closure_cabs"]))
    projected = scene_state.projected_peak_gb(est["closure_cabs"])
    machine = _system_memory_gb()
    over = machine is not None and projected > machine * 0.9
    box.label(text="~{0:.0f} GB peak".format(projected) if machine is None else
                   "~{0:.0f} GB peak, this machine has {1:.0f} GB".format(projected, machine),
              icon="ERROR" if over else "NONE")
    if over and state.KIND == STREAMING:
        box.label(text="Turn Size down, or expect it to swap.", icon="INFO")


def _draw_actions(layout, state, enabled):
    options = layout.column(align=True)
    options.enabled = enabled
    options.prop(state, "scene_state_id")
    options.prop(state, "lod0_only")
    options.operator(RURI_OT_scene_discover.bl_idname, icon="VIEWZOOM").kind = state.KIND
    _draw_estimate(layout, state)
    tail = layout.column(align=True)
    tail.enabled = enabled
    tail.prop(state, "reset_scene")
    tail.operator(RURI_OT_scene_import.bl_idname, icon="IMPORT").kind = state.KIND


def draw_scene_tab(layout, context):
    """The self-contained scenes: nothing to window, so nothing to choose."""
    state = context.scene.ruri_scene_box
    _draw_list(layout, state)
    _draw_actions(layout, state, _selected(state) is not None)


def draw_world_tab(layout, context):
    """The open-world maps: pick a map, then one of the places the game itself
    names in it, at the size the game itself gives that place."""
    state = context.scene.ruri_scene_world
    if not scene_state.SCENES[STREAMING]:
        layout.label(text="Refresh to read the game's scene list.", icon="INFO")
        layout.operator(RURI_OT_scene_refresh.bl_idname, icon="FILE_REFRESH")
        return

    layout.prop(state, "world_map")
    _draw_list(layout, state)

    entry = _selected(state)
    chunks = _chunks(state)
    if chunks is not None:
        summary = scene_state.inventory_summary(chunks)
        box = layout.box()
        box.label(text="{0}: {1} cell chunk(s), {2:.0f} MB whole".format(
            state.world_map, summary["anchored_files"], summary["anchored_bytes"] / 1048576.0))
        box.label(text="+ {0} map-wide/dynamic chunk(s), {1:.0f} MB, bounded to the selection".format(
            summary["floating_files"], summary["floating_bytes"] / 1048576.0))

    size = layout.row(align=True)
    size.enabled = entry is not None
    size.prop(state, "scale")
    rect = _rect(state) if entry is not None else None
    if rect is not None:
        size.label(text="{0:.0f} x {1:.0f} m".format(rect[2] - rect[0], rect[3] - rect[1]))
    _draw_actions(layout, state, entry is not None)


_CLASSES = (
    RURI_PG_scene_entry,
    RURI_PG_scene_box,
    RURI_PG_scene_world,
    RURI_UL_scenes,
    RURI_OT_scene_refresh,
    RURI_OT_scene_discover,
    RURI_OT_scene_import,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_scene_box = PointerProperty(type=RURI_PG_scene_box)
    bpy.types.Scene.ruri_scene_world = PointerProperty(type=RURI_PG_scene_world)


def unregister():
    del bpy.types.Scene.ruri_scene_world
    del bpy.types.Scene.ruri_scene_box
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    scene_state.reset()
