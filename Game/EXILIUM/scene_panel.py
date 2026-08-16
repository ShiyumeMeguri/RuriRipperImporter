"""Every scene the game ships, under the path its own catalog states.

A scene is the one asset family whose address survives this game's build as a real
path, so the tree drawn here is the game's own folder tree rather than anything
reconstructed. Picking one and pressing Load runs the bundle browser's own import
over the cabs that scene resolved to.
"""

from __future__ import annotations

import bpy
from bpy.props import (BoolProperty, CollectionProperty, IntProperty, StringProperty)

from ... import filter_ui
from ...RuriRipperPyBridge.session import cabmap_state
from . import datasets, roster_panel

_TABLE = {}

_FIELD_LABELS = {"key": "Path", "label": "Scene", "group": "Family", "detail": "Folder",
                 "archives": "Archives", "shipped": "Downloaded"}


def _filter_fields():
    table = _TABLE.get("scenes")
    if table is None:
        return (("label", "Scene"),)
    names = sorted(table.names, key=lambda name: 0 if name == "label" else 1)
    return tuple((name, _FIELD_LABELS.get(name, name.replace("_", " ").title()))
                 for name in names)


SCENE_FILTER_SPEC = filter_ui.register_spec(filter_ui.FilterSpec(
    key="EXILIUM:scene", fields=_filter_fields,
    state_for=lambda context: context.scene.ruri_exilium_scene,
    apply=lambda context: _rebuild(context.scene.ruri_exilium_scene)))


class RURI_PG_exilium_scene_entry(bpy.types.PropertyGroup):
    label: StringProperty()
    key: StringProperty()
    group: StringProperty()
    detail: StringProperty()
    shipped: BoolProperty(default=False)
    is_group: BoolProperty(default=False)


def _on_filter_edit(self, context):
    _rebuild(self)


class RURI_PG_exilium_scene(filter_ui.FilterStateMixin, bpy.types.PropertyGroup):
    FILTER_SPEC_KEY = "EXILIUM:scene"

    search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"}, update=_on_filter_edit,
                           description="Filter by scene name, folder or family")
    entries: CollectionProperty(type=RURI_PG_exilium_scene_entry)
    active_index: IntProperty()
    status: StringProperty(default="Load a cabmap, then refresh the scene list.")
    downloaded_only: BoolProperty(
        name="Downloaded",
        default=True,
        description="Hide the scenes the catalog names but this install never downloaded")


def _rebuild(state):
    with filter_ui.rebuilding():
        _fill(state)


def _fill(state):
    chosen = filter_ui.selected_key(state)
    state.entries.clear()
    table = _TABLE.get("scenes")
    if table is None:
        return
    matched = cabmap_state.BRIDGE.search_data_table(table, state.search.strip(), state.filter_rules)
    labels = table.values("label")
    groups = table.values("group")
    shipped = table.values("shipped")
    order = sorted((int(index) for index in matched
                    if not state.downloaded_only or shipped[int(index)]),
                   key=lambda index: (groups[index], labels[index]))
    matched_count = len(order)
    rows = [{name: table.cell(index, name)
             for name in ("key", "label", "group", "detail", "shipped")}
            for index in order[:cabmap_state.LIST_CAP]]

    counts = {}
    for row in rows:
        counts[row["group"]] = counts.get(row["group"], 0) + 1

    current_group = None
    for row in rows:
        if row["group"] and row["group"] != current_group:
            current_group = row["group"]
            header = state.entries.add()
            header.label = "{0}  ({1})".format(current_group, counts[current_group])
            header.group = current_group
            header.is_group = True
        entry = state.entries.add()
        entry.label = row["label"]
        entry.key = row["key"]
        entry.group = row["group"]
        entry.detail = row["detail"]
        entry.shipped = bool(float(row["shipped"] or 0))
    state.status = "{0} of {1} scene(s){2}".format(
        matched_count, table.row_count,
        "" if matched_count == len(rows) else
        " · showing {0}, narrow your search to see the rest".format(len(rows)))
    filter_ui.restore_selection(state, chosen)


def _selected(state):
    if 0 <= state.active_index < len(state.entries):
        entry = state.entries[state.active_index]
        if not entry.is_group:
            return entry
    return None


class RURI_UL_exilium_scene(bpy.types.UIList):
    bl_idname = "RURI_UL_exilium_scene"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if item.is_group:
            row = layout.row()
            row.enabled = False
            row.label(text=item.label, icon="OUTLINER_COLLECTION")
            return
        row = layout.row(align=True)
        row.label(text=item.label, icon="SCENE_DATA" if item.shipped else "LIBRARY_DATA_BROKEN")

    def filter_items(self, context, data, propname):
        return [], []


class RURI_OT_exilium_scene_refresh(bpy.types.Operator):
    """Read every scene the game's own catalog names."""
    bl_idname = "ruri.exilium_scene_refresh"
    bl_label = "Refresh Scenes"
    bl_description = "Read the scene list out of the game's own catalog"

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = context.scene.ruri_exilium_scene
        try:
            _TABLE["scenes"] = datasets.scenes()
        except Exception as exc:
            state.status = "{0}: {1}".format(type(exc).__name__, exc)
            self.report({"WARNING"}, state.status)
            return {"CANCELLED"}
        _rebuild(state)
        return {"FINISHED"}


class RURI_OT_exilium_scene_load(bpy.types.Operator):
    """Import the selected scene through the browser's own import."""
    bl_idname = "ruri.exilium_scene_load"
    bl_label = "Load Scene"
    bl_description = "Import this scene, exactly as the bundle browser would"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.scene.ruri_cabmap.loaded
                and cabmap_state.BRIDGE is not None
                and _selected(context.scene.ruri_exilium_scene) is not None)

    def execute(self, context):
        entry = _selected(context.scene.ruri_exilium_scene)
        if entry is None:
            return {"CANCELLED"}
        return roster_panel.load_address(self, entry.key, entry.label)


class RURI_OT_exilium_scene_reveal(bpy.types.Operator):
    """Open where the selected scene lives, over in the bundle browser."""
    bl_idname = "ruri.exilium_scene_reveal"
    bl_label = "Open Containing Folder"
    bl_description = "Switch to the bundle browser and open where this scene lives"

    @classmethod
    def poll(cls, context):
        return _selected(context.scene.ruri_exilium_scene) is not None

    def execute(self, context):
        entry = _selected(context.scene.ruri_exilium_scene)
        if entry is None:
            return {"CANCELLED"}
        return roster_panel.reveal_address(entry.key, entry.label)


def draw_scene_tab(layout, context):
    state = context.scene.ruri_exilium_scene

    head = layout.row(align=True)
    head.label(text="Scenes", icon="SCENE_DATA")
    head.operator(RURI_OT_exilium_scene_refresh.bl_idname, text="", icon="FILE_REFRESH")

    filter_ui.draw_search_row(layout, state)
    layout.template_list(RURI_UL_exilium_scene.bl_idname, "", state, "entries",
                         state, "active_index", rows=10)
    layout.label(text=state.status, icon="INFO")

    options = layout.column(align=True)
    options.prop(state, "downloaded_only", toggle=True, icon="IMPORT")
    actions = options.column(align=True)
    actions.enabled = _selected(state) is not None
    actions.operator(RURI_OT_exilium_scene_load.bl_idname, icon="IMPORT")
    actions.operator(RURI_OT_exilium_scene_reveal.bl_idname, icon="FILE_FOLDER")


_CLASSES = (
    RURI_PG_exilium_scene_entry,
    RURI_PG_exilium_scene,
    RURI_UL_exilium_scene,
    RURI_OT_exilium_scene_refresh,
    RURI_OT_exilium_scene_load,
    RURI_OT_exilium_scene_reveal,
)


def register():
    filter_ui.register_spec(SCENE_FILTER_SPEC)
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_exilium_scene = bpy.props.PointerProperty(type=RURI_PG_exilium_scene)


def unregister():
    del bpy.types.Scene.ruri_exilium_scene
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _TABLE.clear()
