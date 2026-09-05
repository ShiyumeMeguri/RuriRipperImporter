"""The Unreal module's two contributions to the host panel.

``draw_source_options`` hands the host's generic form the ONE fact this module owns:
which dataset states the schema of the values an Unreal install is read with. The
form itself -- rows, widgets, apply -- is the host's (``cabmap_panel.draw_source_options``),
so no option name is spelled here and a decoder that adds one needs no edit here.

``draw_unreal_tab`` shows what the mounted session says about itself, read off the
decoder's datasets the moment the tab is drawn from a cache the operator fills --
a draw callback never crosses the CLR boundary.
"""

from __future__ import annotations

import bpy

from ... import cabmap_panel
from . import datasets

_SESSION = {"row": None, "archives": []}


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


_CLASSES = (RURI_OT_unreal_refresh,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
