"""The top of the Look tab: whether the generated shaders are the generation installed beside the add-on, and the
button that brings them current.

A shader that is an older generation draws a picture that looks almost right, so it is said out loud here instead
of being left to a comparison by eye. The update replaces only the generated shader; the file's inputs -- every
parameter, texture and tiling a material's record keeps -- stay as they are. Where the new generation spells an
input differently, the old name is listed with the new names that could carry it and the user chooses; an input
left unchosen stays in its record, unread.
"""

from __future__ import annotations

import traceback

import bpy

from . import shader_version
from ...Kernel import host as host_port
from ...Kernel.app import look

STAMP_DIGITS = 8
LEGACY_LISTED = 6


class RURI_PG_shader_candidate(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()


class RURI_PG_shader_rename(bpy.types.PropertyGroup):
    key: bpy.props.StringProperty()
    part: bpy.props.StringProperty()
    kind: bpy.props.StringProperty()
    old: bpy.props.StringProperty()
    carriers: bpy.props.IntProperty()
    linked: bpy.props.IntProperty()
    candidates: bpy.props.CollectionProperty(type=RURI_PG_shader_candidate)
    target: bpy.props.StringProperty(
        name="Becomes", description="The name the new shaders read this input under. Left empty, the input stays "
                                    "as the record keeps it and the new shader does not read it")


def _identity(row):
    return (row.key, row.part, row.kind, row.old)


def _sync_rows(manager):
    """Make the window manager's rename rows the changes the current scan found, keeping the choices already
    made for the ones that are still there."""
    wanted = [change for state in shader_version.current() for change in state.changes]
    chosen = {_identity(row): row.target for row in manager.ruri_shader_renames}
    manager.ruri_shader_renames.clear()
    for change in wanted:
        row = manager.ruri_shader_renames.add()
        row.key, row.part, row.kind, row.old = change.key, change.part, change.kind, change.name
        row.carriers, row.linked = change.carriers, change.linked
        for name in change.candidates:
            row.candidates.add().name = name
        row.target = chosen.get((change.key, change.part, change.kind, change.name), "")


_PENDING = [False]


def _sync_soon():
    """Drawing may not write the window manager: the rows are made on the next tick, then the tab is redrawn."""
    if _PENDING[0]:
        return
    _PENDING[0] = True

    def run():
        _PENDING[0] = False
        _sync_rows(bpy.context.window_manager)
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
        return None

    bpy.app.timers.register(run, first_interval=0.0)


def _short(stamp):
    return stamp[:STAMP_DIGITS] if stamp else "none"


class RURI_OT_shader_scan(bpy.types.Operator):
    bl_idname = "ruri.shader_scan"
    bl_label = "Check Shader Version"
    bl_description = "Compare the shaders this session draws with against the products installed beside the add-on"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        shader_version.current(force=True)
        _sync_rows(context.window_manager)
        return {"FINISHED"}


class RURI_OT_shader_update(bpy.types.Operator):
    bl_idname = "ruri.shader_update"
    bl_label = "Update Shaders"
    bl_description = ("Replace the generated shaders with the installed generation and compile every material again "
                      "from its record. Parameters, textures and tiling are not touched; only the names you chose "
                      "below are moved. The window waits while materials compile")
    bl_options = {"REGISTER"}

    def execute(self, context):
        renames = [(row.key, row.part, row.kind, row.old, row.target)
                   for row in context.window_manager.ruri_shader_renames if row.target]
        try:
            result = shader_version.update(renames)
        except Exception as error:
            traceback.print_exc()
            self.report({"ERROR"}, "Shader update stopped: {0}".format(error))
            return {"CANCELLED"}
        _sync_rows(context.window_manager)
        self.report({"WARNING" if result.failures else "INFO"}, result.summary())
        return {"FINISHED"}


def _draw_rename(layout, row):
    line = layout.row(align=True)
    split = line.split(factor=0.5)
    held = "{0}: ".format(row.part) if row.part else ""
    split.label(text="{0}{1}".format(held, row.old))
    split.prop_search(row, "target", row, "candidates", text="", icon="FORWARD")
    detail = "{0} material(s)".format(row.carriers)
    if row.linked:
        detail += ", {0} linked (change them in their own file)".format(row.linked)
    layout.label(text="    " + detail)


def _draw_outdated(box, states):
    column = box.column()
    column.alert = True
    column.label(text="Shaders are an older generation than the installed plugin", icon="ERROR")
    for state in states:
        for stack in state.stacks:
            if stack.replaced:
                box.label(text="{0}: running {1}, installed {2}".format(
                    state.label, _short(stack.loaded), _short(stack.installed)), icon="DOT")
        if state.stale_materials:
            box.label(text="{0}: {1} material(s) were built by an older generation".format(
                state.label, state.stale_materials), icon="DOT")
        if state.uncompiled_materials:
            box.label(text="{0}: {1} material(s) have no compiled shader".format(
                state.label, state.uncompiled_materials), icon="DOT")


def draw_shader_version_section(layout, context):
    """Made of this application's own objects -- a search field over the new names -- so drawn natively."""
    layout.native(_draw_native)


def _draw_native(layout, context):
    states = shader_version.current()
    failed = [state for state in states if state.failure]
    outdated = [state for state in states if state.outdated]
    legacy = [state for state in states if state.legacy_materials]
    manager = context.window_manager
    wanted = sum(len(state.changes) for state in states)
    if wanted != len(manager.ruri_shader_renames):
        _sync_soon()
    if not outdated and not failed and not legacy:
        line = layout.row(align=True)
        stamps = ", ".join("{0} {1}".format(state.label, _short(state.stacks[0].loaded)) for state in states
                           if state.stacks)
        line.label(text="Shaders are current" + ("  ·  " + stamps if stamps else ""), icon="CHECKMARK")
        line.operator(RURI_OT_shader_scan.bl_idname, text="", icon="FILE_REFRESH")
        return
    box = layout.box()
    for state in failed:
        alert = box.column()
        alert.alert = True
        alert.label(text="{0}: cannot read the installed products ({1})".format(state.label, state.failure), icon="ERROR")
    for state in legacy:
        names = state.legacy_materials
        alert = box.column()
        alert.alert = True
        alert.label(text="{0}: {1} linked material(s) draw with shader data an older version saved into their "
                         "library".format(state.label, len(names)), icon="ERROR")
        for name in names[:LEGACY_LISTED]:
            alert.label(text=name, icon="DOT")
        if len(names) > LEGACY_LISTED:
            alert.label(text="... and {0} more".format(len(names) - LEGACY_LISTED), icon="DOT")
        box.label(text="This file cannot replace them: open the library file, update there and save it.", icon="INFO")
    if outdated:
        _draw_outdated(box, outdated)
        box.label(text="Update replaces the generated shader only. Every parameter, texture and tiling stays.",
                  icon="INFO")
        if len(manager.ruri_shader_renames):
            renames = box.box()
            renames.label(text="The new shaders spell these inputs differently -- say where each goes:")
            for row in manager.ruri_shader_renames:
                _draw_rename(renames, row)
        blockers = [text for state in outdated for text in state.blockers]
        for text in blockers:
            alert = box.column()
            alert.alert = True
            alert.label(text=text, icon="CANCEL")
        action = box.row(align=True)
        action.enabled = not blockers
        action.operator(RURI_OT_shader_update.bl_idname, icon="FILE_REFRESH")
        action.operator(RURI_OT_shader_scan.bl_idname, text="", icon="VIEWZOOM")


_CLASSES = (
    RURI_PG_shader_candidate,
    RURI_PG_shader_rename,
    RURI_OT_shader_scan,
    RURI_OT_shader_update,
)

_SECTION = ("shader_version", "Shader version", draw_shader_version_section, host_port.NodeMaterials)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.ruri_shader_renames = bpy.props.CollectionProperty(type=RURI_PG_shader_rename)
    key, label, draw, requires = _SECTION
    look.register_section(key, label, draw, requires)


def unregister():
    look.drop_section(_SECTION[0])
    del bpy.types.WindowManager.ruri_shader_renames
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
