"""The Unreal module's Actors tab: every Blueprint actor the install ships, listed off the
decoder's own dataset (``unreal.actors``) so a character or a prop is found by name and
kind, not by walking the raw package tree for one of its meshes.

The decoder states each actor's engine ancestry (Character / Pawn / other actor) and how
many skeletal and static mesh packages it imports directly; this panel only filters and
draws those rows, and importing one is the browser's own import of that package -- one
import path, so a fix there is a fix here.
"""
from __future__ import annotations

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty

from ... import cabmap_panel
from ...RuriRipperPyBridge.session import cabmap_state
from . import datasets

# The dataset rows as last read, module state like the Unreal tab's own caches: a draw
# never crosses the CLR boundary, an operator fills this and the rows redraw from it.
_ROWS = []

KIND_ALL = "all"
KINDS = (
    (KIND_ALL, "All", "Every actor class the install ships"),
    ("Character", "Characters", "Classes descending from the engine's Character"),
    ("Pawn", "Pawns", "Classes descending from the engine's Pawn but not Character"),
    ("Actor", "Actors", "Every other actor: props, level pieces, controllers"),
)
KIND_ICONS = {"Character": "OUTLINER_OB_ARMATURE", "Pawn": "OUTLINER_OB_ARMATURE", "Actor": "OUTLINER_OB_MESH"}


class RURI_PG_unreal_actor(bpy.types.PropertyGroup):
    """One listed actor class, as the decoder states it."""
    name: StringProperty()
    package: StringProperty()
    kind: StringProperty()
    parent: StringProperty()
    native: StringProperty()
    skeletal: IntProperty()
    statics: IntProperty()


def _matches(row, state):
    if state.kind != KIND_ALL and row.get("kind", "") != state.kind:
        return False
    if state.only_meshed and int(row.get("skeletal", 0)) + int(row.get("static", 0)) == 0:
        return False
    needle = state.filter.strip().lower()
    if not needle:
        return True
    return any(needle in str(row.get(field, "")).lower() for field in ("name", "package", "parent", "native"))


def _rebuild(state):
    state.entries.clear()
    for row in _ROWS:
        if not _matches(row, state):
            continue
        entry = state.entries.add()
        entry.name = row.get("name", "")
        entry.package = row.get("package", "")
        entry.kind = row.get("kind", "")
        entry.parent = row.get("parent", "")
        entry.native = row.get("native", "")
        entry.skeletal = int(row.get("skeletal", 0))
        entry.statics = int(row.get("static", 0))
    state.active = min(state.active, max(len(state.entries) - 1, 0))
    state.status = "{0} of {1} actor(s)".format(len(state.entries), len(_ROWS))


def _on_filter(self, _context):
    _rebuild(self)


class RURI_PG_unreal_actors(bpy.types.PropertyGroup):
    """The Actors tab's own state: the cut the list is drawn with, and the rows it drew."""
    filter: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"}, update=_on_filter,
                           description="Keep the actors whose name, package, parent or engine class contains this")
    kind: EnumProperty(name="Kind", items=KINDS, default=KIND_ALL, update=_on_filter)
    only_meshed: BoolProperty(name="With meshes", default=True, update=_on_filter,
                              description="Keep only the actors that import a skeletal or static mesh package directly; "
                                          "a child class inheriting its meshes from its parent imports none itself")
    entries: CollectionProperty(type=RURI_PG_unreal_actor)
    active: IntProperty()
    status: StringProperty()


def _selected(state):
    if 0 <= state.active < len(state.entries):
        return state.entries[state.active]
    return None


class RURI_UL_unreal_actors(bpy.types.UIList):
    bl_idname = "RURI_UL_unreal_actors"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        row.label(text=item.name, icon=KIND_ICONS.get(item.kind, "OUTLINER_OB_EMPTY"))
        lineage = row.row()
        lineage.enabled = False
        lineage.label(text=item.native if item.parent == item.native else "{0} < {1}".format(item.parent, item.native))
        meshes = row.row()
        meshes.alignment = "RIGHT"
        meshes.label(text="{0} skel  {1} static".format(item.skeletal, item.statics))

    def filter_items(self, context, data, propname):
        # The cut is made in _rebuild against the decoder's own fields; the list draws as is.
        return [], []


class RURI_OT_unreal_actors_refresh(bpy.types.Operator):
    """Read every Blueprint actor the install ships off the decoder"""
    bl_idname = "ruri.unreal_actors_refresh"
    bl_label = "List Actors"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = context.scene.ruri_unreal_actors
        try:
            _ROWS[:] = datasets.actors()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            state.status = "{0}: {1}".format(type(exc).__name__, exc)
            self.report({"ERROR"}, "Unreal actors: " + state.status)
            return {"CANCELLED"}
        _rebuild(state)
        return {"FINISHED"}


class RURI_OT_unreal_actor_import(bpy.types.Operator):
    """Import the selected actor's package: the actor with its components, meshes, materials and textures, as the browser would"""
    bl_idname = "ruri.unreal_actor_import"
    bl_label = "Import Actor"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None
                and _selected(context.scene.ruri_unreal_actors) is not None)

    def execute(self, context):
        entry = _selected(context.scene.ruri_unreal_actors)
        if cabmap_state.rows_by_cab().get(entry.package) is None:
            self.report({"ERROR"}, "'{0}' is not in this cabmap; rebuild the cabmap.".format(entry.package))
            return {"CANCELLED"}
        cabmap_state.SELECTED_CABS.clear()
        cabmap_state.SELECTED_CABS.add(entry.package)
        cabmap_panel._reapply_and_refresh(context)
        return bpy.ops.ruri.import_selected()


class RURI_OT_unreal_actor_reveal(bpy.types.Operator):
    """Show the selected actor's package in the file browser"""
    bl_idname = "ruri.unreal_actor_reveal"
    bl_label = "Reveal"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return RURI_OT_unreal_actor_import.poll(context)

    def execute(self, context):
        entry = _selected(context.scene.ruri_unreal_actors)
        return bpy.ops.ruri.cabmap_reveal(cab=entry.package, query=entry.name)


def draw_actors_tab(layout, context):
    state = context.scene.ruri_unreal_actors
    head = layout.row(align=True)
    head.operator(RURI_OT_unreal_actors_refresh.bl_idname, icon="FILE_REFRESH")
    head.label(text=state.status)
    if not _ROWS:
        layout.label(text="List the actors to pick a character or a prop.", icon="INFO")
        return
    layout.prop(state, "filter", text="", icon="VIEWZOOM")
    cut = layout.row(align=True)
    cut.prop(state, "kind", expand=True)
    cut.prop(state, "only_meshed")
    layout.template_list(RURI_UL_unreal_actors.bl_idname, "", state, "entries", state, "active", rows=12)
    actions = layout.row(align=True)
    actions.operator(RURI_OT_unreal_actor_import.bl_idname, icon="IMPORT")
    actions.operator(RURI_OT_unreal_actor_reveal.bl_idname, icon="FILE_FOLDER")


_CLASSES = (RURI_PG_unreal_actor, RURI_PG_unreal_actors, RURI_UL_unreal_actors,
            RURI_OT_unreal_actors_refresh, RURI_OT_unreal_actor_import, RURI_OT_unreal_actor_reveal)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_unreal_actors = bpy.props.PointerProperty(type=RURI_PG_unreal_actors)


def unregister():
    del bpy.types.Scene.ruri_unreal_actors
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
