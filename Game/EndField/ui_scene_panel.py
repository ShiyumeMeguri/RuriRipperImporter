"""The StreamingScene tab's third half: the game's UI display stages.

``Scene`` and ``World`` next door browse places you walk around in. This one
browses the little lit stages an interface puts a model on -- CharInfo (the
character screen), CharFormation, WeaponInfo, the dialog stages -- and drops one
into the Blender scene whole: its own sun, its own ambient, its own
character-lighting overrides, and its art.

The list is not a hand-written one. ``ui_scene_state`` finds these by asking the
game's own assets what they are (see its docstring); a version that ships another
display stage grows another row here with no code change.

Nothing is deleted on load unless asked: the point of a stage is to put it around
a character that is already in the scene.
"""

from __future__ import annotations

import bpy
from bpy.props import (BoolProperty, CollectionProperty, IntProperty,
                       PointerProperty, StringProperty)

from ...ruri_pybridge.session import cabmap_state
from . import ui_scene_importer, ui_scene_state

UI_SCENE = "ui"


def _report_exception(op, prefix, exc):
    import traceback
    traceback.print_exc()
    op.report({"ERROR"}, f"{prefix}: {type(exc).__name__}: {exc} (full traceback in console)")


def _bridge_asset_db_module():
    from ...ruri_pybridge.unity import bridge_asset_db
    return bridge_asset_db


def _container_paths():
    """Every container path the loaded cabmap knows -- the input discovery reads
    the game's filing out of."""
    rows = cabmap_state.ROWS
    if rows is None:
        return []
    paths = set()
    for index in range(rows.count):
        for slot in range(rows.container_path_count(index)):
            path = rows.container_path(index, slot)
            if path:
                paths.add(path)
    return paths


def _rebuild(state):
    """Refill the drawn list from what discovery found, grouped by the folder
    the game itself filed each stage in."""
    state.entries.clear()
    needle = (state.search or "").strip().lower()
    rows = [row for row in ui_scene_state.SCENES
            if not needle or needle in row["label"].lower() or needle in row["group"].lower()]
    current_group = None
    for row in sorted(rows, key=lambda r: (r["group"], r["label"])):
        if row["group"] != current_group:
            current_group = row["group"]
            header = state.entries.add()
            header.label = current_group
            header.is_group = True
        entry = state.entries.add()
        entry.label = row["label"]
        entry.key = row["id"]
    if state.active_index >= len(state.entries):
        state.active_index = 0


def _on_search_edit(self, context):
    _rebuild(self)


def _selected(state):
    if 0 <= state.active_index < len(state.entries):
        entry = state.entries[state.active_index]
        if not entry.is_group:
            return ui_scene_state.row_by_id(entry.key)
    return None


class RURI_PG_ui_scene_entry(bpy.types.PropertyGroup):
    label: StringProperty()
    key: StringProperty()
    is_group: BoolProperty(default=False)


class RURI_PG_ui_scene(bpy.types.PropertyGroup):
    search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"}, update=_on_search_edit,
                           description="Filter by stage or folder name")
    entries: CollectionProperty(type=RURI_PG_ui_scene_entry)
    active_index: IntProperty()
    import_art: BoolProperty(name="Stage Art", default=True,
                             description="Import the stage's own geometry, when the game ships any "
                                         "for it, into its own collection")
    apply_environment: BoolProperty(name="Sun + Ambient", default=True,
                                    description="Build the stage's directional light and set the "
                                                "world colour from its own sky SH")
    apply_character_params: BoolProperty(name="Character Params", default=True,
                                         description="Push the stage's HGCharacterVolume onto every "
                                                     "Endfield material already in the scene")
    reset_scene: BoolProperty(name="Reset Scene", default=False,
                              description="Delete existing objects first. Off by default: a stage is "
                                          "normally loaded AROUND a character that is already here")


class RURI_UL_ui_scenes(bpy.types.UIList):
    bl_idname = "RURI_UL_ui_scenes"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if item.is_group:
            row = layout.row()
            row.enabled = False
            row.label(text=item.label, icon="FILE_FOLDER")
            return
        layout.label(text=item.label, icon="LIGHT_AREA")


class RURI_OT_ui_scene_refresh(bpy.types.Operator):
    bl_idname = "ruri.ui_scene_refresh"
    bl_label = "Refresh"
    bl_description = "Read the game's UI display stages out of its own config assets"

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        try:
            ui_scene_state.discover(cabmap_state.BRIDGE, _container_paths())
        except Exception as exc:
            _report_exception(self, "UI stage discovery failed", exc)
            return {"CANCELLED"}
        _rebuild(context.scene.ruri_ui_scene)
        self.report({"INFO"}, ui_scene_state.STATUS)
        return {"FINISHED"}


class RURI_OT_ui_scene_load(bpy.types.Operator):
    bl_idname = "ruri.ui_scene_load"
    bl_label = "Load Stage"
    bl_description = "Put the selected display stage into the scene"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = context.scene.ruri_ui_scene
        row = _selected(state)
        if row is None:
            self.report({"ERROR"}, "Nothing selected.")
            return {"CANCELLED"}
        if state.reset_scene:
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)

        done = []
        if state.apply_environment:
            env_data = ui_scene_state.document(row["env"]["path"])
            if env_data:
                sun = ui_scene_importer.apply_environment(context, env_data, row["label"] + " Sun")
                done.append("sun " + (sun.name if sun else "(no direction in asset)"))

        if state.apply_character_params:
            for char_volume in row["char_volumes"]:
                touched, written = ui_scene_importer.apply_character_params(char_volume["data"])
                done.append("{0} param(s) onto {1} material(s)".format(written, touched))

        if state.import_art and row["art_root"]:
            try:
                built = self._import_art(context, row)
            except Exception as exc:
                _report_exception(self, "Stage art import failed", exc)
                return {"CANCELLED"}
            done.append("{0} art root(s)".format(built))

        self.report({"INFO"}, "{0}: {1}".format(row["label"], "; ".join(done) or "nothing selected to apply"))
        return {"FINISHED"}

    def _import_art(self, context, row):
        paths = [p for p in _container_paths() if p.startswith(row["art_root"])
                 and p.endswith((".fbx", ".prefab"))]
        if not paths:
            return 0
        bridge = cabmap_state.BRIDGE
        cabs = list(bridge.resolve_cabs_for_paths(paths))
        if not cabs:
            return 0
        assets, roots, _seed_roots, _clips, _scene_roots = bridge.import_cabs(cabs)
        db = _bridge_asset_db_module().BridgeAssetDatabase(
            assets, clip_curve_blobs=bridge.clip_curves_by_guid,
            mesh_blobs=bridge.mesh_blobs_by_guid,
            asset_paths=bridge.asset_paths_by_guid)
        return ui_scene_importer.import_stage(
            context, db, roots, context.scene.ruri_cabmap.as_options())


def draw_ui_scene_tab(layout, context):
    """The UI display stages: pick one, load it around whatever is already here."""
    state = context.scene.ruri_ui_scene

    head = layout.row(align=True)
    head.prop(state, "search", text="", icon="VIEWZOOM")
    head.operator(RURI_OT_ui_scene_refresh.bl_idname, text="", icon="FILE_REFRESH")

    if not ui_scene_state.SCENES:
        layout.label(text=ui_scene_state.STATUS, icon="INFO")
        layout.operator(RURI_OT_ui_scene_refresh.bl_idname, icon="FILE_REFRESH")
        return

    layout.template_list(RURI_UL_ui_scenes.bl_idname, "", state, "entries",
                         state, "active_index", rows=10)
    layout.label(text=ui_scene_state.STATUS, icon="INFO")

    row = _selected(state)
    if row is not None:
        box = layout.box()
        box.label(text=row["env"]["path"].rsplit("/", 1)[-1], icon="WORLD")
        for volume in row["volumes"]:
            box.label(text="{0}  ({1} override(s))".format(volume["name"], len(volume["components"])),
                      icon="MODIFIER")
        for char_volume in row["char_volumes"]:
            box.label(text=char_volume["name"], icon="OUTLINER_OB_ARMATURE")
        box.label(text=row["art_root"] or "(this stage ships no art of its own)", icon="MESH_DATA")

    options = layout.column(align=True)
    options.enabled = row is not None
    options.prop(state, "apply_environment")
    options.prop(state, "apply_character_params")
    options.prop(state, "import_art")
    options.prop(state, "reset_scene")
    options.operator(RURI_OT_ui_scene_load.bl_idname, icon="IMPORT")


_CLASSES = (
    RURI_PG_ui_scene_entry,
    RURI_PG_ui_scene,
    RURI_UL_ui_scenes,
    RURI_OT_ui_scene_refresh,
    RURI_OT_ui_scene_load,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_ui_scene = PointerProperty(type=RURI_PG_ui_scene)


def unregister():
    del bpy.types.Scene.ruri_ui_scene
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    ui_scene_state.reset()
