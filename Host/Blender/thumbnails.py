"""Bounded process-only raster previews and native selectable icon grids."""
from __future__ import annotations

import os
import tempfile

import bpy
import bpy.utils.previews

from ...Kernel import host as host_port

MAX_BYTES = 2 * 1024 * 1024
MAX_PREVIEWS = 20
_previews = None
_temporary_paths = set()
_key_paths = {}


def publish(key, encoded):
    """Decode using Blender's preview API, never bpy.data.images or Scene data."""
    global _previews
    if not encoded or len(encoded) > MAX_BYTES:
        return False
    if _previews is None:
        _previews = bpy.utils.previews.new()
    if key in _previews:
        del _previews[key]
    if len(_previews) >= MAX_PREVIEWS:
        return False
    suffix = (".png" if encoded.startswith(b"\x89PNG\r\n\x1a\n") else
              ".jpg" if encoded.startswith(b"\xff\xd8\xff") else
              ".webp" if encoded[:4] == b"RIFF" and encoded[8:12] == b"WEBP" else "")
    if not suffix:
        return False
    old_path = _key_paths.pop(key, None)
    if old_path is not None:
        _remove_temporary(old_path)
    path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="ruri-ui-preview-", suffix=suffix, delete=False) as stream:
            path = stream.name
            _temporary_paths.add(path)
            _key_paths[key] = path
            stream.write(encoded)
        _previews.load(key, path, "IMAGE", force_reload=True)
        return True
    except Exception:
        if key in _previews:
            del _previews[key]
        if path is not None:
            _key_paths.pop(key, None)
            _remove_temporary(path)
        return False


def _remove_temporary(path, attempt=0):
    try:
        os.remove(path)
        _temporary_paths.discard(path)
    except FileNotFoundError:
        _temporary_paths.discard(path)
    except OSError:
        # Preview decoders may hold a handle briefly. Retry only this owned
        # file, never scan/delete arbitrary temp paths, and never break the UI.
        if attempt < 3:
            host_port.current().schedule(0.2, lambda: _remove_temporary(path, attempt + 1))


def release(keys):
    if _previews is not None:
        for key in keys:
            if key in _previews:
                del _previews[key]
            path = _key_paths.pop(key, None)
            if path is not None:
                _remove_temporary(path)


def draw(target, spec, context):
    entries = spec.get("entries", ())
    if not entries:
        return
    columns = max(1, int(spec.get("columns", 4)))
    selected_key = spec.get("selected", "")
    command_id = spec.get("command", "")

    grid = target.grid_flow(row_major=True, columns=columns, even_columns=True, even_rows=True, align=False)
    for entry in entries:
        key = entry["key"]
        is_selected = (key == selected_key)
        card = grid.box()
        col = card.column(align=True)

        preview = _previews.get(key) if _previews is not None else None
        img_row = col.row(align=True)
        img_row.alignment = "CENTER"
        placeholder = bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items["IMAGE_DATA"].value
        icon = preview.icon_id if preview is not None and preview.icon_id else placeholder
        img_row.template_icon(icon_value=icon, scale=4.5)

        btn_row = col.row(align=True)
        btn = btn_row.operator(
            command_id,
            text=entry.get("label", ""),
            icon="CHECKBOX_HLT" if is_selected else "CHECKBOX_DEHLT",
            depress=is_selected,
        )
        for k, v in entry.get("values", {}).items():
            setattr(btn, k, v)


def unregister():
    global _previews
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
    _key_paths.clear()
    for path in tuple(_temporary_paths):
        _remove_temporary(path, attempt=3)
