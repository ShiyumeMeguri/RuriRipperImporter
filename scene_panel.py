"""UI for whole-scene import: pick a map, discover its placements in memory
(cheap -- no dependency closure resolved yet), see a cost/fidelity estimate,
then commit. Mirrors the animation browser's discover-then-commit shape
(cabmap_panel.py's RURI_PT_animation_browser).

draw_scene_tab() is called directly by cabmap_panel.py's RURI_PT_cabmap.draw()
for its "Scene" tab (see RURI_PG_cabmap.active_tab) -- NOT drawn as its own
stacked bl_parent_id sub-panel, so it shares the same hard gate (nothing here
is reachable before a cabmap is loaded) and tab bar as the AssetBundle browser
instead of always being visible below it regardless of which tab is active."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty

try:
    from . import cabmap_state, prefab_importer, scene_state
except ImportError:  # standalone (non-package) testing
    import cabmap_state
    import prefab_importer
    import scene_state

# Kept alive at module scope -- Blender's dynamic EnumProperty items callback
# requires the returned list to outlive the call (a fresh list literal
# returned each time is a well-documented footgun: the C-level enum can end
# up pointing at already-freed Python string memory).
_map_items_cache = [("", "(discover maps first)", "")]


def _map_items(self, context):
    global _map_items_cache
    _map_items_cache = [(m, m, "") for m in scene_state.MAPS] or [("", "(discover maps first)", "")]
    return _map_items_cache


def _report_exception(op, prefix, exc):
    import traceback
    traceback.print_exc()
    op.report({"ERROR"}, f"{prefix}: {type(exc).__name__}: {exc} (full traceback in console)")


class RURI_PG_scene_import(bpy.types.PropertyGroup):
    map_name: EnumProperty(name="Map", items=_map_items)
    lod0_only: BoolProperty(name="LOD0 Only", default=True,
                            description="Skip placements of non-zero LOD variants (_lod1, _lod2, ...); "
                                        "affects both the discovery estimate and the import")
    reset_scene: BoolProperty(name="Reset Scene", default=True,
                              description="Delete existing scene objects before importing")


class RURI_OT_scene_discover_maps(bpy.types.Operator):
    bl_idname = "ruri.scene_discover_maps"
    bl_label = "Discover Maps"
    bl_description = "List every map with streaming-chunk scene data in the game's VFS"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = context.scene.ruri_cabmap
        try:
            maps = scene_state.discover_maps(cabmap_state.BRIDGE, state.game_root)
        except Exception as exc:
            _report_exception(self, "Discover maps failed", exc)
            return {"CANCELLED"}
        if not maps:
            self.report({"WARNING"}, "No maps found under this game root's VFS.")
            return {"CANCELLED"}
        context.scene.ruri_scene_import.map_name = maps[0]
        self.report({"INFO"}, f"Found {len(maps)} map(s).")
        return {"FINISHED"}


class RURI_OT_scene_discover_placements(bpy.types.Operator):
    bl_idname = "ruri.scene_discover_placements"
    bl_label = "Discover Placements"
    bl_description = "Discover the selected map's placements and resolve them to CABs (no import yet)"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return (context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None
                and context.scene.ruri_scene_import.map_name)

    def execute(self, context):
        cab_state = context.scene.ruri_cabmap
        scene_import = context.scene.ruri_scene_import
        try:
            scene_state.discover_placements(cabmap_state.BRIDGE, cab_state.game_root, scene_import.map_name)
            scene_state.resolve_cabs(cabmap_state.BRIDGE, scene_import.lod0_only)
        except Exception as exc:
            _report_exception(self, "Discover placements failed", exc)
            return {"CANCELLED"}
        est = scene_state.estimate(scene_import.lod0_only)
        self.report({"INFO"}, f"{est['placeable']} placeable, {est['distinct_assets']} distinct "
                              f"assets -> {est['resolved_cabs']} CAB(s).")
        return {"FINISHED"}


class RURI_OT_scene_import(bpy.types.Operator):
    bl_idname = "ruri.scene_import"
    bl_label = "Import Scene"
    bl_description = "Resolve the discovered placements' dependency closure and import the whole map"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None
                and len(scene_state.RESOLVED_CABS) > 0)

    def execute(self, context):
        scene_import = context.scene.ruri_scene_import
        if scene_import.reset_scene:
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)

        try:
            documents, textures, roots, _seed_roots = cabmap_state.BRIDGE.import_cabs(scene_state.RESOLVED_CABS)
        except Exception as exc:
            _report_exception(self, "Scene import (bridge) failed", exc)
            return {"CANCELLED"}

        bridge_asset_db = _bridge_asset_db_module()
        db = bridge_asset_db.BridgeAssetDatabase(documents, textures)
        try:
            imported, placed, unresolved = prefab_importer.import_scene_placements(
                context, db, scene_state.placeable(scene_import.lod0_only), roots,
                context.scene.ruri_cabmap.as_options())
        except Exception as exc:
            _report_exception(self, "Scene placement build failed", exc)
            return {"CANCELLED"}

        self.report({"INFO"}, f"Imported {imported} distinct asset(s), placed {placed} object(s)"
                              + (f", {unresolved} unresolved" if unresolved else "") + ".")
        return {"FINISHED"}


def _bridge_asset_db_module():
    try:
        from . import bridge_asset_db
    except ImportError:
        import bridge_asset_db
    return bridge_asset_db


def draw_scene_tab(layout, context):
    """Draw the Scene tab's content into `layout` -- called from cabmap_panel.py's
    RURI_PT_cabmap.draw() when RURI_PG_cabmap.active_tab == 'scene'. The caller has already
    handled the loaded/not-loaded gate and lock message for the whole gated area (both tabs
    share it), so this only draws the scene-import controls themselves."""
    scene_import = context.scene.ruri_scene_import

    row = layout.row(align=True)
    row.prop(scene_import, "map_name")
    row.operator(RURI_OT_scene_discover_maps.bl_idname, text="", icon="FILE_REFRESH")

    layout.prop(scene_import, "lod0_only")
    layout.operator(RURI_OT_scene_discover_placements.bl_idname, icon="VIEWZOOM")

    if scene_state.PLACEMENTS:
        est = scene_state.estimate(scene_import.lod0_only)
        box = layout.box()
        box.label(text=f"Map: {scene_state.CURRENT_MAP}")
        box.label(text=f"{est['total_placements']} placement(s), {est['distinct_assets']} distinct asset(s)")
        box.label(text=f"{est['placeable']} placeable, {est['excluded']} excluded (no transform)")
        box.label(text=f"Resolves to {est['resolved_cabs']} CAB(s) (re-click Discover after "
                      f"toggling LOD0 Only to refresh this number)")

    layout.prop(scene_import, "reset_scene")
    layout.operator(RURI_OT_scene_import.bl_idname, icon="IMPORT")


_CLASSES = (
    RURI_PG_scene_import,
    RURI_OT_scene_discover_maps,
    RURI_OT_scene_discover_placements,
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
