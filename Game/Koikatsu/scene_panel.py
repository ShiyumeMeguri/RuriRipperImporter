"""Browse the game's places the way it lists them, and import one.

One list, because the game has one set of places under two names: every one is a
Unity level, and picking one and importing it is the whole interaction -- nothing
here is streamed, so there is no window to choose.

The list itself comes from the game's hook (``koikatsu.scene.places``) and the
filtering runs on the same C# engine the bundle browser uses, over that dataset's
own handle. Nothing on this side reads a byte of the game.
"""

from __future__ import annotations

import bpy
from bpy.props import (BoolProperty, CollectionProperty, IntProperty,
                       PointerProperty, StringProperty)

from ... import filter_ui, prefab_importer
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import bridge_asset_db, class_registry
from . import datasets

_FILTER_FIELDS = (("name", "Name"), ("bundle", "Bundle"), ("group", "Group"))

SCENE_FILTER_SPEC = filter_ui.register_spec(filter_ui.FilterSpec(
    key="Koikatsu:scene", fields=_FILTER_FIELDS,
    state_for=lambda context: context.scene.ruri_kk_scene,
    apply=lambda context: _rebuild(context.scene.ruri_kk_scene)))

# What a level contributes, by class NAME -- the ids come from the shared
# all-version registry, so nothing here can drift against the file format. The
# exclusions are the point: a closure carries catalogue art a build never looks at.
_GEOMETRY = ("GameObject", "Transform", "Mesh", "SkinnedMeshRenderer", "MeshRenderer",
             "MeshFilter", "MonoBehaviour", "MonoScript")
_MATERIALS = ("Material", "Shader")
_TEXTURES = ("Texture2D",)
_LIGHTING = ("Light", "Cubemap", "LightProbes", "RenderSettings", "LightmapSettings",
             "ReflectionProbe")

STATUS = "Refresh to read the game's scene list."


def _class_ids(options):
    names = list(_GEOMETRY) + list(_LIGHTING)
    if options.get("import_materials", True):
        names.extend(_MATERIALS)
        if options.get("import_textures", True):
            names.extend(_TEXTURES)
    resolved = []
    for name in names:
        class_id = class_registry.id_for_name(name)
        if class_id is not None and class_id not in resolved:
            resolved.append(class_id)
    return resolved


def _report_exception(operator, prefix, exc):
    import traceback
    traceback.print_exc()
    operator.report({"ERROR"}, "{0}: {1}: {2} (full traceback in console)".format(
        prefix, type(exc).__name__, exc))


def _selected(state):
    if 0 <= state.active_index < len(state.entries):
        entry = state.entries[state.active_index]
        if not entry.is_group:
            return entry
    return None


def _selected_place(state):
    entry = _selected(state)
    if entry is None:
        return None
    table = datasets.table(datasets.PLACES)
    if table is None:
        return None
    for index in range(len(table)):
        if table.cell(index, "id") == entry.key:
            return {name: table.cell(index, name) for name in table.names}
    return None


def _rebuild(state):
    global STATUS
    state.entries.clear()
    matched, table = datasets.search(datasets.PLACES, (), state.search.strip(), state.filter_rules)
    if table is None:
        STATUS = "Load a cabmap, then refresh."
        return

    rows = [{name: table.cell(index, name) for name in table.names} for index in matched]
    rows.sort(key=lambda row: (row["group"], row["name"]))
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
        entry.label = row["name"]
        entry.key = row["id"]
    STATUS = "{0} of {1} place(s).".format(len(rows), len(table))
    if state.active_index >= len(state.entries):
        state.active_index = 0


def _on_search_edit(self, context):
    _rebuild(self)


class RURI_PG_kk_scene_entry(bpy.types.PropertyGroup):
    label: StringProperty()
    key: StringProperty()
    is_group: BoolProperty(default=False)


class RURI_PG_kk_scene(filter_ui.FilterStateMixin, bpy.types.PropertyGroup):
    FILTER_SPEC_KEY = "Koikatsu:scene"

    search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"}, update=_on_search_edit,
                           description="Filter by displayed name or bundle")
    entries: CollectionProperty(type=RURI_PG_kk_scene_entry)
    active_index: IntProperty()
    reset_scene: BoolProperty(
        name="Reset Scene", default=True,
        description="Delete existing scene objects before importing")


class RURI_UL_kk_scenes(bpy.types.UIList):
    bl_idname = "RURI_UL_kk_scenes"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if item.is_group:
            row = layout.row()
            row.enabled = False
            row.label(text=item.label, icon="OUTLINER_COLLECTION")
            return
        row = layout.row(align=True)
        row.label(text=item.label, icon="WORLD")
        identifier = row.row()
        identifier.enabled = False
        identifier.alignment = "RIGHT"
        identifier.label(text="" if item.key == item.label else item.key)

    def filter_items(self, context, data, propname):
        return [], []


class RURI_OT_kk_scene_refresh(bpy.types.Operator):
    """Read the game's own scene list."""
    bl_idname = "ruri.kk_scene_refresh"
    bl_label = "Refresh Scenes"
    bl_description = "Read every place the game names, out of its own tables"

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        try:
            datasets.table(datasets.PLACES, refresh=True)
        except Exception as exc:
            _report_exception(self, "Scene list failed", exc)
            return {"CANCELLED"}
        _rebuild(context.scene.ruri_kk_scene)
        self.report({"INFO"}, STATUS)
        return {"FINISHED"}


class RURI_OT_kk_scene_import(bpy.types.Operator):
    """Import the selected place whole."""
    bl_idname = "ruri.kk_scene_import"
    bl_label = "Import"
    bl_description = "Resolve this place's dependency closure and import it"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None
                and _selected(context.scene.ruri_kk_scene) is not None)

    def execute(self, context):
        state = context.scene.ruri_kk_scene
        place = _selected_place(state)
        if place is None:
            self.report({"ERROR"}, "Nothing selected.")
            return {"CANCELLED"}
        cabs = datasets.cabs_for([place["bundle"]])
        if not cabs:
            self.report({"WARNING"}, "'{0}' is not in the loaded cabmap.".format(place["bundle"]))
            return {"CANCELLED"}

        if state.reset_scene:
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)

        options = context.scene.ruri_cabmap.as_options()
        try:
            assets, roots, _seeds, _clips, scene_roots = cabmap_state.BRIDGE.import_cabs(
                cabs, _class_ids(options))
        except Exception as exc:
            _report_exception(self, "Scene import (bridge) failed", exc)
            return {"CANCELLED"}

        db = bridge_asset_db.BridgeAssetDatabase(
            assets, mesh_blobs=cabmap_state.BRIDGE.mesh_blobs_by_guid,
            asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid)
        # A level exports as scenes; either way the hierarchy IS the place, so the
        # generic importer builds it as-is.
        wanted = sorted(scene_roots) if scene_roots else list(roots)
        built = 0
        objects = 0
        try:
            for guid in wanted:
                unity_file = db.load_guid(guid)
                if unity_file is None:
                    continue
                report = prefab_importer.import_prefab_from_db(context, db, unity_file, options)
                built += 1
                objects += len(report.mesh_objects)
        except Exception as exc:
            _report_exception(self, "Scene build failed", exc)
            return {"CANCELLED"}

        self.report({"INFO"}, "{0}: {1} root(s), {2} object(s) from {3} CAB(s).".format(
            place["name"], built, objects, len(cabs)))
        return {"FINISHED"}


class RURI_OT_kk_scene_reveal(bpy.types.Operator):
    """Open where the selected place lives, over in the bundle browser."""
    bl_idname = "ruri.kk_scene_reveal"
    bl_label = "Open Containing Folder"
    bl_description = "Switch to the bundle browser and open this place's bundle"

    @classmethod
    def poll(cls, context):
        return _selected(context.scene.ruri_kk_scene) is not None

    def execute(self, context):
        place = _selected_place(context.scene.ruri_kk_scene)
        if place is None:
            return {"CANCELLED"}
        cabs = datasets.cabs_for([place["bundle"]])
        return bpy.ops.ruri.cabmap_reveal(query=place["asset"], cab=cabs[0] if cabs else "")


def draw_scene_tab(layout, context):
    state = context.scene.ruri_kk_scene
    filter_ui.draw_search_row(layout, state,
                              extra_operator=(RURI_OT_kk_scene_refresh.bl_idname, "FILE_REFRESH"))
    layout.template_list(RURI_UL_kk_scenes.bl_idname, "", state, "entries",
                         state, "active_index", rows=10)
    layout.label(text=STATUS, icon="INFO")

    place = _selected_place(state)
    actions = layout.column(align=True)
    actions.enabled = place is not None
    if place is not None:
        info = actions.box()
        info.label(text="{0}  ->  {1}".format(place["bundle"], place["asset"]))
        info.label(text="{0} CAB(s) · named by {1}".format(
            len(datasets.cabs_for([place["bundle"]])), place["sources"]))
    actions.prop(state, "reset_scene")
    actions.operator(RURI_OT_kk_scene_import.bl_idname, icon="IMPORT")
    actions.operator(RURI_OT_kk_scene_reveal.bl_idname, icon="FILE_FOLDER")


_CLASSES = (
    RURI_PG_kk_scene_entry,
    RURI_PG_kk_scene,
    RURI_UL_kk_scenes,
    RURI_OT_kk_scene_refresh,
    RURI_OT_kk_scene_import,
    RURI_OT_kk_scene_reveal,
)


def register():
    filter_ui.register_spec(SCENE_FILTER_SPEC)
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_kk_scene = PointerProperty(type=RURI_PG_kk_scene)


def unregister():
    del bpy.types.Scene.ruri_kk_scene
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
