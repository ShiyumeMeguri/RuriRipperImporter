"""Browse the game's scenes the way the game itself lists them, and import one
streaming window of the chosen one.

The list comes from the game's own config containers (see ``scene_state``): every
scene it ships streaming data for, under the name it shows for it, in whichever
language Blender is running in -- ``bpy.app.translations.locale`` picks which text
container the C# side joins through, exactly as the roster does. It behaves like
the roster's list next door: type to filter, click to select.

What gets imported is a WINDOW, not a map. A real map is thousands of chunk files
whose dependency closure no machine holds at once -- map02 alone resolves to 26811
CABs -- and the running game never holds one either: it streams a disc of chunk
cells around the player and gates them by scene state. The controls below are that
same disc, so the estimate box is the honest cost of what Import will do.

draw_scene_tab() is the draw function this game's "Scene" tab declares (see the
package's GAME_MODULE) -- NOT its own stacked bl_parent_id sub-panel, so it
shares the core panel's hard gate (nothing here is reachable before a cabmap is
loaded) and tab bar instead of always being visible below it."""

from __future__ import annotations

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       IntProperty, PointerProperty, StringProperty)

from ...ruri_pybridge.session import cabmap_state
from . import roster, scene_importer, scene_state

# Kept alive at module scope -- Blender's dynamic EnumProperty items callback
# requires the returned list to outlive the call (a fresh list literal returned
# each time is a well-documented footgun: the C-level enum can end up pointing at
# already-freed Python string memory).
_state_items_cache = [("0", "0", "")]


def _report_exception(op, prefix, exc):
    import traceback
    traceback.print_exc()
    op.report({"ERROR"}, f"{prefix}: {type(exc).__name__}: {exc} (full traceback in console)")


def _language():
    """The game language this list is shown in -- Blender's own locale, mapped
    onto the languages the game ships."""
    return roster.language_for_locale(bpy.app.translations.locale)


def _selected(state):
    if 0 <= state.active_index < len(state.entries):
        entry = state.entries[state.active_index]
        if not entry.is_group:
            return entry
    return None


def _chunks(state):
    """The selected scene's chunk inventory, or None when nothing is selected or
    the inventory has not been read yet. Never reads it itself: a redraw runs
    this, and a redraw must not touch the VFS."""
    entry = _selected(state)
    return scene_state.CHUNKS.get(entry.key) if entry else None


def _rebuild(state):
    """Rebuild the drawn line list from scene_state.MAPS, filtered by the search
    box and grouped by the id family the game files scenes under."""
    state.entries.clear()
    needle = state.search.strip().lower()
    rows = [row for row in scene_state.MAPS
            if not needle or needle in row["id"].lower() or needle in row["label"].lower()]
    rows.sort(key=lambda row: (row["group"], row["id"]))

    counts = {}
    for row in rows:
        counts[row["group"]] = counts.get(row["group"], 0) + 1

    current_group = None
    for row in rows:
        if row["group"] != current_group:
            current_group = row["group"]
            header = state.entries.add()
            header.label = "{0}  ({1})".format(current_group, counts[current_group])
            header.is_group = True
        entry = state.entries.add()
        entry.label = row["label"]
        entry.key = row["id"]
    if state.active_index >= len(state.entries):
        state.active_index = 0


def _state_items(self, context):
    global _state_items_cache
    chunks = _chunks(context.scene.ruri_scene_import)
    states = scene_state.scene_states(chunks) if chunks else []
    _state_items_cache = [(str(s), str(s), "Scene state {0}".format(s)) for s in states] \
        or [("0", "0", "")]
    return _state_items_cache


def _on_search_edit(self, context):
    _rebuild(self)


def _on_selection_change(self, context):
    """Selecting a scene IS the intent to look at it: read its chunk inventory
    (manifest-only, and cached per scene) and aim the window at the cell it ships
    the most bytes for, so the controls below are immediately meaningful."""
    entry = _selected(self)
    if entry is None or cabmap_state.BRIDGE is None:
        return
    try:
        chunks = scene_state.load_chunks(cabmap_state.BRIDGE, context.scene.ruri_cabmap.game_root,
                                         entry.key)
    except Exception as exc:
        scene_state.STATUS = "{0}: {1}".format(type(exc).__name__, exc)
        print("[RuriRipper] chunk inventory for '{0}' failed: {1}".format(entry.key, exc))
        return
    states = scene_state.scene_states(chunks)
    if states:
        self.scene_state_id = str(states[0])
    cell = scene_state.busiest_cell(chunks, states[0] if states else 0)
    if cell is not None:
        self.center_x, self.center_y = cell


class RURI_PG_scene_entry(bpy.types.PropertyGroup):
    """One drawn line: either a family header or a scene."""
    label: StringProperty()
    key: StringProperty()
    is_group: BoolProperty(default=False)


class RURI_PG_scene_import(bpy.types.PropertyGroup):
    search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"}, update=_on_search_edit,
                           description="Filter by displayed name or scene id")
    entries: CollectionProperty(type=RURI_PG_scene_entry)
    active_index: IntProperty(update=_on_selection_change)

    center_x: IntProperty(name="Cell X", default=0,
                          description="Chunk cell the window is centred on, in the map's own grid")
    center_y: IntProperty(name="Cell Y", default=0,
                          description="Chunk cell the window is centred on, in the map's own grid")
    radius: IntProperty(name="Radius", default=scene_state.DEFAULT_RADIUS, min=0, soft_max=8,
                        description="Cells around the centre, each way -- radius 1 is a 3x3 window. "
                                    "The whole of a real map does not fit in memory")
    scene_state_id: EnumProperty(name="Scene State", items=_state_items,
                                 description="Which dressing of these cells to read. States are "
                                             "alternates of the same world, so one at a time")

    lod0_only: BoolProperty(name="LOD0 Only", default=True,
                            description="Keep only the best available LOD sibling per placed instance; "
                                        "affects both the estimate and the import")
    reset_scene: BoolProperty(name="Reset Scene", default=True,
                              description="Delete existing scene objects before importing")


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
        state = context.scene.ruri_scene_import
        try:
            rows = scene_state.load_maps(cabmap_state.BRIDGE, context.scene.ruri_cabmap.game_root,
                                         _language())
        except Exception as exc:
            _report_exception(self, "Scene list failed", exc)
            return {"CANCELLED"}
        if not rows:
            self.report({"WARNING"}, "No scenes with streaming data under this game root's VFS.")
            return {"CANCELLED"}
        _rebuild(state)
        self.report({"INFO"}, scene_state.STATUS)
        return {"FINISHED"}


class RURI_OT_scene_discover(bpy.types.Operator):
    """Read the selected window's placements and price it -- what it resolves to
    in CABs is the number that says whether the import fits in memory."""
    bl_idname = "ruri.scene_discover"
    bl_label = "Read Window"
    bl_description = "Decode this window's chunks and estimate what importing it would cost"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return (context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None
                and _selected(context.scene.ruri_scene_import) is not None)

    def execute(self, context):
        state = context.scene.ruri_scene_import
        entry = _selected(state)
        try:
            scene_state.discover_placements(
                cabmap_state.BRIDGE, context.scene.ruri_cabmap.game_root, entry.key,
                state.center_x, state.center_y, state.radius, int(state.scene_state_id))
            scene_state.resolve_cabs(cabmap_state.BRIDGE, state.lod0_only)
        except Exception as exc:
            _report_exception(self, "Window read failed", exc)
            return {"CANCELLED"}
        est = scene_state.estimate(state.lod0_only)
        self.report({"INFO"}, "{0} placeable, {1} distinct assets -> {2} CAB(s) in closure.".format(
            est["placeable"], est["distinct_assets"], est["closure_cabs"]))
        return {"FINISHED"}


class RURI_OT_scene_import(bpy.types.Operator):
    bl_idname = "ruri.scene_import"
    bl_label = "Import Window"
    bl_description = "Resolve the read window's dependency closure and import it"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None
                and len(scene_state.RESOLVED_CABS) > 0)

    def execute(self, context):
        state = context.scene.ruri_scene_import
        entry = _selected(state)
        if entry is None:
            self.report({"ERROR"}, "No scene selected.")
            return {"CANCELLED"}
        # Staleness guard: whatever path led here, NEVER import a window other
        # than the one currently set -- re-read in place if they disagree.
        window = (state.center_x, state.center_y, state.radius, int(state.scene_state_id))
        if scene_state.CURRENT_MAP != entry.key or scene_state.CURRENT_WINDOW != window:
            if "FINISHED" not in bpy.ops.ruri.scene_discover():
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
    """Imported lazily: only the bridge path needs it, and the scene tab draws
    long before any bridge session exists."""
    from ...ruri_pybridge.unity import bridge_asset_db
    return bridge_asset_db


def draw_scene_tab(layout, context):
    """Draw the Scene tab's content into `layout`. The core panel has already
    handled the loaded/not-loaded gate and lock message for the whole gated area
    (every tab shares it), so this only draws the scene controls."""
    state = context.scene.ruri_scene_import

    head = layout.row(align=True)
    head.prop(state, "search", icon="VIEWZOOM", text="")
    head.operator(RURI_OT_scene_refresh.bl_idname, text="", icon="FILE_REFRESH")
    layout.template_list(RURI_UL_scenes.bl_idname, "", state, "entries",
                         state, "active_index", rows=10)
    layout.label(text=scene_state.STATUS, icon="INFO")

    entry = _selected(state)
    window = layout.column(align=True)
    window.enabled = entry is not None

    chunks = _chunks(state)
    if chunks is not None:
        summary = scene_state.inventory_summary(chunks)
        extent = scene_state.grid_extent(chunks)
        box = window.box()
        box.label(text="{0}: {1} cell chunk(s), {2:.0f} MB".format(
            entry.key, summary["anchored_files"], summary["anchored_bytes"] / 1048576.0))
        box.label(text="+ {0} map-wide/dynamic chunk(s), {1:.0f} MB, bounded to the window".format(
            summary["floating_files"], summary["floating_bytes"] / 1048576.0))
        if extent is not None:
            box.label(text="grid x[{0}..{1}] y[{2}..{3}]".format(*extent))

    window.prop(state, "scene_state_id")
    cells = window.row(align=True)
    cells.prop(state, "center_x")
    cells.prop(state, "center_y")
    cells.prop(state, "radius")
    window.prop(state, "lod0_only")
    window.operator(RURI_OT_scene_discover.bl_idname, icon="VIEWZOOM")

    if scene_state.PLACEMENTS:
        est = scene_state.estimate(state.lod0_only)
        box = layout.box()
        box.label(text="Window: {0} ({1},{2}) r{3} state {4}".format(
            scene_state.CURRENT_MAP, *scene_state.CURRENT_WINDOW))
        box.label(text="{0} placement(s), {1} distinct asset(s)".format(
            est["total_placements"], est["distinct_assets"]))
        box.label(text="{0} placeable, {1} excluded (no transform)".format(
            est["placeable"], est["no_transform"])
            + (", {0} non-LOD0 duplicates skipped".format(est["lod_filtered"])
               if est["lod_filtered"] else ""))
        box.label(text="{0} seed CAB(s) -> {1} in closure".format(
            est["resolved_cabs"], est["closure_cabs"]),
            icon="ERROR" if est["closure_cabs"] > 8000 else "NONE")

    layout.prop(state, "reset_scene")
    layout.operator(RURI_OT_scene_import.bl_idname, icon="IMPORT")


_CLASSES = (
    RURI_PG_scene_entry,
    RURI_PG_scene_import,
    RURI_UL_scenes,
    RURI_OT_scene_refresh,
    RURI_OT_scene_discover,
    RURI_OT_scene_import,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_scene_import = PointerProperty(type=RURI_PG_scene_import)


def unregister():
    del bpy.types.Scene.ruri_scene_import
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    scene_state.reset()
