"""The Unreal module's two contributions to the host panel.

``draw_source_options`` hands the host's generic form the ONE fact this module owns:
which dataset states the schema of the values an Unreal install is read with. The
form itself -- rows, widgets, apply -- is the host's (``cabmap_panel.draw_source_options``),
so no option name is spelled here and a decoder that adds one needs no edit here.

``draw_unreal_tab`` shows what the mounted session says about itself, read off the
decoder's datasets the moment the tab is drawn from a cache the operator fills --
a draw callback never crosses the CLR boundary.

The World Partition block on that tab is the same shape: the decoder publishes the
worlds and, per world, the streaming cells already cut to a window and a level; the
panel only shows the rows and hands the chosen cells' packages to the host's own
Import Selected, where they load like any other browser rows.
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty

from ... import cabmap_panel
from ...RuriRipperPyBridge.session import cabmap_state
from . import datasets

_SESSION = {"row": None, "archives": []}
_WORLD = {"worlds": [], "cells": [], "error": ""}
ALL_LEVELS = -1
CELL_ROWS_SHOWN = 24


def draw_source_options(layout, context, config):
    cabmap_panel.draw_source_options(layout, context, config, datasets.SETTINGS_SCHEMA)


class RURI_OT_unreal_refresh(bpy.types.Operator):
    """Re-read the mounted session and its archives off the decoder."""

    bl_idname = "ruri.unreal_refresh"
    bl_label = "Refresh"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        try:
            _SESSION["row"] = datasets.session()
            _SESSION["archives"] = datasets.archives()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"Unreal session: {type(exc).__name__}: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class RURI_PG_unreal_world(bpy.types.PropertyGroup):
    """Which world the World Partition block reads and the window it cuts the cells to,
    in the decoder's own units (Unreal centimetres) so no second unit scale lives here."""

    world: StringProperty(name="World", description="The partitioned world whose cells are listed")
    use_window: BoolProperty(name="Window", default=True,
                             description="Keep only the cells whose bounds cross the window below")
    use_always_loaded: BoolProperty(name="Always loaded", default=False,
                                    description="Also select the always-loaded cells, whose actors the cook folded into the world's own package -- the whole persistent level")
    min_x: FloatProperty(name="Min X", default=0.0, precision=0, step=10000)
    min_y: FloatProperty(name="Min Y", default=0.0, precision=0, step=10000)
    max_x: FloatProperty(name="Max X", default=0.0, precision=0, step=10000)
    max_y: FloatProperty(name="Max Y", default=0.0, precision=0, step=10000)
    level: IntProperty(name="Level", default=0, min=ALL_LEVELS,
                       description="The hierarchical level to list (0 = leaf cells, -1 = every level)")


def _window_args(state):
    args = {}
    if state.use_window:
        args.update(minX=state.min_x, minY=state.min_y, maxX=state.max_x, maxY=state.max_y)
    if state.level != ALL_LEVELS:
        args["level"] = state.level
    return args


def _read_cells(context):
    state = context.scene.ruri_unreal_world
    _WORLD["cells"] = datasets.world_cells(state.world, **_window_args(state))
    _WORLD["error"] = ""


class RURI_OT_unreal_worlds_refresh(bpy.types.Operator):
    """Re-read the worlds the install ships off the decoder."""

    bl_idname = "ruri.unreal_worlds_refresh"
    bl_label = "Worlds"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        try:
            _WORLD["worlds"] = datasets.worlds()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"Unreal worlds: {type(exc).__name__}: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class RURI_OT_unreal_world_pick(bpy.types.Operator):
    """List this world's streaming cells, cut to the window and level stated below."""

    bl_idname = "ruri.unreal_world_pick"
    bl_label = "Cells"
    bl_options = {"INTERNAL"}
    world: StringProperty()

    def execute(self, context):
        context.scene.ruri_unreal_world.world = self.world
        return bpy.ops.ruri.unreal_cells_refresh()


class RURI_OT_unreal_cells_refresh(bpy.types.Operator):
    """Re-read the picked world's cells with the current window and level."""

    bl_idname = "ruri.unreal_cells_refresh"
    bl_label = "Refresh"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.ruri_unreal_world.world)

    def execute(self, context):
        try:
            _read_cells(context)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            _WORLD["error"] = f"{type(exc).__name__}: {exc}"
            self.report({"ERROR"}, f"Unreal cells: {_WORLD['error']}")
            return {"CANCELLED"}
        return {"FINISHED"}


class RURI_OT_unreal_cells_select(bpy.types.Operator):
    """Select every listed cell's level package in the browser, so Import Selected loads them."""

    bl_idname = "ruri.unreal_cells_select"
    bl_label = "Select cells"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return bool(_WORLD["cells"]) and context.scene.ruri_cabmap.loaded

    def execute(self, context):
        known = cabmap_state.rows_by_cab()
        chosen = []
        missing = 0
        state = context.scene.ruri_unreal_world
        for cell in _WORLD["cells"]:
            if not state.use_always_loaded and str(cell.get("alwaysLoaded", "0")) == "1":
                continue
            package = cell.get("level", "")
            if known.get(package) is None:
                missing += 1
                continue
            chosen.append(package)
        cabmap_state.SELECTED_CABS.clear()
        cabmap_state.SELECTED_CABS.update(chosen)
        cabmap_panel._reapply_and_refresh(context)
        self.report({"INFO"} if chosen else {"WARNING"},
                    f"{len(set(chosen))} cell package(s) selected" + (f", {missing} not in this cabmap" if missing else ""))
        return {"FINISHED"} if chosen else {"CANCELLED"}


class RURI_OT_unreal_cells_import(bpy.types.Operator):
    """Select the listed cells and import them: one scene per cell, actors at their world places."""

    bl_idname = "ruri.unreal_cells_import"
    bl_label = "Import cells"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return RURI_OT_unreal_cells_select.poll(context)

    def execute(self, context):
        if bpy.ops.ruri.unreal_cells_select() != {"FINISHED"}:
            return {"CANCELLED"}
        return bpy.ops.ruri.import_selected()


def _draw_world_partition(layout, context):
    state = context.scene.ruri_unreal_world
    box = layout.box()
    head = box.row(align=True)
    head.label(text="World Partition", icon="WORLD")
    head.operator(RURI_OT_unreal_worlds_refresh.bl_idname, icon="FILE_REFRESH")
    worlds = _WORLD["worlds"]
    if not worlds:
        box.label(text="Read the worlds to pick one.", icon="INFO")
        return
    table = box.column(align=True)
    for world in worlds:
        line = table.row(align=True)
        partitioned = str(world.get("partitioned", "0")) == "1"
        line.label(text=world.get("name", ""), icon="OUTLINER_OB_GROUP_INSTANCE" if partitioned else "FILE_3D")
        line.label(text="{0} cells".format(world.get("cells", 0)) if partitioned else "not partitioned")
        if partitioned:
            line.operator(RURI_OT_unreal_world_pick.bl_idname, icon="VIEWZOOM").world = world.get("world", "")
    if not state.world:
        return
    cut = box.box()
    cut.label(text=state.world, icon="OUTLINER_OB_GROUP_INSTANCE")
    grid = cut.grid_flow(columns=2, align=True)
    grid.prop(state, "min_x")
    grid.prop(state, "min_y")
    grid.prop(state, "max_x")
    grid.prop(state, "max_y")
    knobs = cut.row(align=True)
    knobs.prop(state, "use_window")
    knobs.prop(state, "use_always_loaded")
    knobs.prop(state, "level")
    knobs.operator(RURI_OT_unreal_cells_refresh.bl_idname, icon="FILE_REFRESH")
    if _WORLD["error"]:
        cut.label(text=_WORLD["error"], icon="ERROR")
    cells = _WORLD["cells"]
    cut.label(text="{0} cell(s) in the cut".format(len(cells)))
    rows = cut.column(align=True)
    for cell in cells[:CELL_ROWS_SHOWN]:
        line = rows.row(align=True)
        always = str(cell.get("alwaysLoaded", "0")) == "1"
        line.label(text=cell.get("cell", ""), icon="PINNED" if always else "MESH_GRID")
        line.label(text="L{0}".format(cell.get("hlevel", "")))
        line.label(text="{0:.0f}..{1:.0f} / {2:.0f}..{3:.0f}".format(
            float(cell.get("minX", 0)), float(cell.get("maxX", 0)), float(cell.get("minY", 0)), float(cell.get("maxY", 0))))
    if len(cells) > CELL_ROWS_SHOWN:
        rows.label(text="... {0} more".format(len(cells) - CELL_ROWS_SHOWN))
    tail = cut.row(align=True)
    tail.operator(RURI_OT_unreal_cells_select.bl_idname, icon="RESTRICT_SELECT_OFF")
    tail.operator(RURI_OT_unreal_cells_import.bl_idname, icon="IMPORT")


def draw_unreal_tab(layout, context):
    layout.operator(RURI_OT_unreal_refresh.bl_idname, icon="FILE_REFRESH")
    row = _SESSION["row"]
    if row is None:
        layout.label(text="Refresh to read the mounted session.", icon="INFO")
        return
    box = layout.box()
    box.label(text="{0}  ·  {1}".format(row.get("project", ""), row.get("displayName", "")), icon="FILE_3D")
    box.label(text="Engine {0} ({1})".format(row.get("engineVersion", ""), row.get("engine", "")))
    box.label(text="Files {0}  ·  Archives {1}/{2}  ·  Missing keys {3}".format(
        row.get("files", ""), row.get("mounted", ""), row.get("archives", ""), row.get("missingKeys", "")))
    schema = row.get("mappings", "")
    box.label(text="Schema: {0} ({1} structs)".format(schema or "none", row.get("structs", "0")),
              icon="CHECKMARK" if schema else "ERROR")
    archives = _SESSION["archives"]
    if archives:
        table = layout.box().column(align=True)
        for archive in archives:
            line = table.row(align=True)
            mounted = str(archive.get("mounted", "0")) == "1"
            line.label(text=archive.get("name", ""), icon="PACKAGE" if mounted else "LOCKED")
            line.label(text="{0} files".format(archive.get("files", "")))
            if str(archive.get("encrypted", "0")) == "1":
                line.label(text=archive.get("keyGuid", ""))
    _draw_world_partition(layout, context)


_CLASSES = (RURI_OT_unreal_refresh, RURI_PG_unreal_world, RURI_OT_unreal_worlds_refresh, RURI_OT_unreal_world_pick,
            RURI_OT_unreal_cells_refresh, RURI_OT_unreal_cells_select, RURI_OT_unreal_cells_import)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_unreal_world = bpy.props.PointerProperty(type=RURI_PG_unreal_world)


def unregister():
    del bpy.types.Scene.ruri_unreal_world
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
