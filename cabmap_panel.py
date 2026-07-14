"""N-panel UI: cabmap gate, browsable/filterable/sortable row list, and
import-with-dependencies actions. Mirrors the WinForms 'Virtual Asset List'
browser's feature set (columns, search, tri-state sort, load/import actions)
adapted to Blender's bpy UI toolkit -- right-click context-menu actions become
buttons, since UIList has no native per-row context menu.

Hard gate (no single-file import path exists in this panel at all): every
widget below the cabmap picker lives in a sub-layout with
`enabled = state.loaded`, every operator's poll() re-checks the same flag,
and this module never calls prefab_importer with a user-picked path -- only
with bridge-sourced in-memory data from a resolved cabmap selection.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import (BoolProperty, CollectionProperty, IntProperty,
                        PointerProperty, StringProperty)

try:
    from . import cabmap_state, pythonnet_bootstrap, bridge_asset_db, prefab_importer
except ImportError:  # standalone (non-package) testing
    import cabmap_state
    import pythonnet_bootstrap
    import bridge_asset_db
    import prefab_importer

_HOOK_IDS_DEFAULT = "EndField_1.3.3"
_SORT_COLUMNS = (("name", "Name"), ("type_names", "Type"), ("deps", "Deps"), ("source", "Source"))


def _rebuild_window(state):
    total, window = cabmap_state.display_window()
    state.window.clear()
    for idx, row in window:
        item = state.window.add()
        item.row_index = idx
        item.cab = row["cab"]
        item.name = row["name"]
        item.container = row["container"]
        item.type_names = row["type_names"]
        item.source = row["source"]
        item.deps = row["deps"]
    shown = len(window)
    cap_note = (f" (capped at {cabmap_state.DISPLAY_CAP} -- narrow your search to see the rest)"
                if total > shown else "")
    state.status = f"Showing {shown} / {total} matching virtual files{cap_note}."


def _on_search_edit(self, context):
    def _refresh():
        _rebuild_window(context.scene.ruri_cabmap)
        screen = getattr(context, "screen", None)
        for area in (screen.areas if screen else []):
            area.tag_redraw()
    cabmap_state.schedule_filter(self.search, _refresh)


def _hook_ids(state):
    return [h.strip() for h in state.hook_ids.split(",") if h.strip()]


class RURI_PG_cabmap_row(bpy.types.PropertyGroup):
    """One windowed/displayed row -- a small proxy, never the full 260k-row set."""
    row_index: IntProperty()
    cab: StringProperty()
    name: StringProperty()
    container: StringProperty()
    type_names: StringProperty()
    source: StringProperty()
    deps: IntProperty()


class RURI_PG_cabmap(bpy.types.PropertyGroup):
    game_root: StringProperty(name="Game Root", subtype="DIR_PATH",
                              description="The game's install root directory")
    cabmap_path: StringProperty(name="Cabmap", subtype="FILE_PATH",
                                description="Existing cabmap to load, or output path to build one")
    hook_ids: StringProperty(name="Hook", default=_HOOK_IDS_DEFAULT,
                             description="Comma-separated Ruri.RipperHook hook id(s), e.g. EndField_1.3.3")
    loaded: BoolProperty(default=False)
    search: StringProperty(name="Search", update=_on_search_edit,
                           description="Filter by Name / Container / Source / Type")
    status: StringProperty(default="No cabmap loaded.")
    window: CollectionProperty(type=RURI_PG_cabmap_row)
    active_index: IntProperty()

    lod0_only: BoolProperty(name="LOD0 Only", default=True)
    import_materials: BoolProperty(name="Import Materials", default=True)
    import_textures: BoolProperty(name="Import Textures", default=True)
    import_skeleton: BoolProperty(name="Import Skeleton", default=True)
    import_animations: BoolProperty(name="Import Animations", default=True)

    def as_options(self):
        return {
            "lod0_only": self.lod0_only,
            "import_materials": self.import_materials,
            "import_textures": self.import_textures,
            "import_skeleton": self.import_skeleton,
            "import_animations": self.import_animations,
        }


class RURI_OT_build_cabmap(bpy.types.Operator):
    bl_idname = "ruri.build_cabmap"
    bl_label = "Build Cabmap"
    bl_description = "Scan the game root and build a fresh cabmap (can take a long time for a full game)"

    @classmethod
    def poll(cls, context):
        return pythonnet_bootstrap.is_ready()

    def execute(self, context):
        state = context.scene.ruri_cabmap
        root = bpy.path.abspath(state.game_root) if state.game_root else ""
        out = bpy.path.abspath(state.cabmap_path) if state.cabmap_path else ""
        if not root or not os.path.isdir(root):
            self.report({"ERROR"}, "Pick a valid game root directory first.")
            return {"CANCELLED"}
        if not out:
            self.report({"ERROR"}, "Pick an output path for the cabmap file first.")
            return {"CANCELLED"}
        try:
            bridge = cabmap_state.ensure_bridge(_hook_ids(state))
            code = bridge.build_cab_map(root, out)
            if code != 0:
                self.report({"ERROR"}, f"Build failed (exit {code}) -- see console.")
                return {"CANCELLED"}
            bridge.load_cab_map(out)
            cabmap_state.load_rows()
            _rebuild_window(state)
            state.loaded = True
        except Exception as exc:
            self.report({"ERROR"}, f"Build cabmap failed: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Cabmap built: {len(cabmap_state.ROWS)} CABs.")
        return {"FINISHED"}


class RURI_OT_load_cabmap(bpy.types.Operator):
    bl_idname = "ruri.load_cabmap"
    bl_label = "Load Cabmap"
    bl_description = "Load an existing cabmap file -- required before browsing/importing anything"

    @classmethod
    def poll(cls, context):
        return pythonnet_bootstrap.is_ready()

    def execute(self, context):
        state = context.scene.ruri_cabmap
        path = bpy.path.abspath(state.cabmap_path) if state.cabmap_path else ""
        if not path or not os.path.isfile(path):
            self.report({"ERROR"}, "Pick a valid cabmap file first.")
            return {"CANCELLED"}
        try:
            bridge = cabmap_state.ensure_bridge(_hook_ids(state))
            bridge.load_cab_map(path)
            cabmap_state.load_rows()
            _rebuild_window(state)
            state.loaded = True
        except Exception as exc:
            self.report({"ERROR"}, f"Load cabmap failed: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Cabmap loaded: {len(cabmap_state.ROWS)} CABs.")
        return {"FINISHED"}


class RURI_OT_cabmap_sort(bpy.types.Operator):
    bl_idname = "ruri.cabmap_sort"
    bl_label = "Sort"
    column: StringProperty()

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded

    def execute(self, context):
        cabmap_state.cycle_sort(self.column)
        _rebuild_window(context.scene.ruri_cabmap)
        return {"FINISHED"}


def _selected_cabs(state):
    if 0 <= state.active_index < len(state.window):
        return [state.window[state.active_index].cab]
    return []


class RURI_OT_import_selected(bpy.types.Operator):
    bl_idname = "ruri.import_selected"
    bl_label = "Import Selected"
    bl_description = "Resolve the selected row's dependency closure in memory and import it into the scene"
    bl_options = {"REGISTER", "UNDO"}
    reset_scene: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = context.scene.ruri_cabmap
        cabs = _selected_cabs(state)
        if not cabs:
            self.report({"WARNING"}, "No row selected.")
            return {"CANCELLED"}

        if self.reset_scene:
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)

        try:
            documents, textures, roots = cabmap_state.BRIDGE.import_cabs(cabs)
        except Exception as exc:
            self.report({"ERROR"}, f"Import (bridge) failed: {exc}")
            return {"CANCELLED"}
        if not roots:
            self.report({"WARNING"}, "No importable (.prefab) asset found in the resolved closure.")
            return {"CANCELLED"}

        db = bridge_asset_db.BridgeAssetDatabase(documents, textures)
        options = state.as_options()
        imported = 0
        for root_guid in roots:
            prefab_file = db.load_guid(root_guid)
            if prefab_file is None:
                continue
            report = prefab_importer.import_prefab_from_db(context, db, prefab_file, options)
            imported += 1
            for warning in report.warnings[:5]:
                self.report({"WARNING"}, warning)
        self.report({"INFO"}, f"Imported {imported} asset(s) from {len(cabs)} selected row(s).")
        return {"FINISHED"}


class RURI_UL_cabmap(bpy.types.UIList):
    bl_idname = "RURI_UL_cabmap"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        split = layout.split(factor=0.34)
        split.label(text=item.name or item.cab)
        rest = split.split(factor=0.32)
        rest.label(text=item.type_names)
        tail = rest.split(factor=0.15)
        tail.label(text=str(item.deps))
        tail.label(text=item.source)


class RURI_PT_cabmap(bpy.types.Panel):
    bl_idname = "RURI_PT_cabmap"
    bl_label = "RuriRipper"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RuriRipper"

    def draw(self, context):
        layout = self.layout
        state = context.scene.ruri_cabmap

        if not pythonnet_bootstrap.is_ready():
            err = pythonnet_bootstrap.last_error()
            if err:
                layout.label(text="pythonnet install failed:", icon="ERROR")
                layout.label(text=err[:60])
            else:
                layout.label(text="Installing pythonnet bridge...", icon="INFO")
            return

        top = layout.column()
        top.prop(state, "hook_ids")
        top.prop(state, "game_root")
        top.prop(state, "cabmap_path")
        row = top.row(align=True)
        row.operator(RURI_OT_build_cabmap.bl_idname, text="Build")
        row.operator(RURI_OT_load_cabmap.bl_idname, text="Load")

        layout.separator()
        gated = layout.column()
        gated.enabled = state.loaded
        if not state.loaded:
            layout.label(text="Build or load a cabmap to browse/import.", icon="LOCKED")

        gated.prop(state, "search", icon="VIEWZOOM")
        sort_col, sort_dir = cabmap_state.sort_state()
        sort_row = gated.row(align=True)
        for col_key, col_label in _SORT_COLUMNS:
            arrow = ""
            if sort_col == col_key:
                arrow = " ▲" if sort_dir == 1 else (" ▼" if sort_dir == 2 else "")
            op = sort_row.operator(RURI_OT_cabmap_sort.bl_idname, text=col_label + arrow)
            op.column = col_key

        gated.template_list(RURI_UL_cabmap.bl_idname, "", state, "window",
                            state, "active_index", rows=12)
        gated.label(text=state.status)

        opts = gated.box()
        opts.prop(state, "lod0_only")
        opts.prop(state, "import_materials")
        opts.prop(state, "import_textures")
        opts.prop(state, "import_skeleton")
        opts.prop(state, "import_animations")

        actions = gated.row(align=True)
        op = actions.operator(RURI_OT_import_selected.bl_idname, text="Import (Append)")
        op.reset_scene = False
        op = actions.operator(RURI_OT_import_selected.bl_idname, text="Import (Reset Scene)")
        op.reset_scene = True


_CLASSES = (
    RURI_PG_cabmap_row,
    RURI_PG_cabmap,
    RURI_UL_cabmap,
    RURI_PT_cabmap,
    RURI_OT_build_cabmap,
    RURI_OT_load_cabmap,
    RURI_OT_cabmap_sort,
    RURI_OT_import_selected,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_cabmap = PointerProperty(type=RURI_PG_cabmap)


def unregister():
    del bpy.types.Scene.ruri_cabmap
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    cabmap_state.reset()
