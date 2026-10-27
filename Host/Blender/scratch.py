"""The plugin's scratch: datablocks made for one session, never written to a .blend.

Everything a generated stack puts into a document -- its template node groups, the material trees compiled from
them, the vertex stage's trees and modifiers, the images those read -- is ordinary data the .blend keeps whole, so a
file opens the same on any machine and every value a person set on a node stays on that node. The generation that
made it is stamped (``ruri_stamp``) and is replaced as a unit by an explicit update (:mod:`shader_version`), which
keeps every input by name and replaces only what is inside.

What is born here lives for one session: the copy source a template material is, the probes that measure a
material, a linked material's twin, the compositor tree a post stage builds for the output size it draws at, the
volume the medium stands up from the level's statement, the picture a region picker draws from. Each is made again
from the document's content whenever a session wants it, and none is a user of anything the document must keep.

Before every save a guard makes anything still carrying the mark runtime and names it. That is a bug report, not a
fallback: a datablock that carries the mark and is not runtime was born outside :func:`mark`, or let go of its mark
wrongly.
"""

from __future__ import annotations

import bpy

#: The mark every scratch datablock carries, beside ``is_runtime_data``.
MARK = "ruri_scratch"

#: Every kind of datablock the plugin makes for one session.
KINDS = ("node_groups", "materials", "images", "textures", "objects", "lights", "meshes", "collections", "texts")


def mark(block):
    """The birth of every datablock the plugin makes for one session: runtime data, never written to a .blend."""
    block.is_runtime_data = True
    block[MARK] = 1
    return block


def content(block):
    """A scratch datablock that becomes the document's own -- a material instance copied off a template: written
    with the file from now on."""
    if MARK in block:
        del block[MARK]
    block.is_runtime_data = False
    return block


@bpy.app.handlers.persistent
def _forbid_scratch_in_blend(*_args):
    """The guard at the only way out of a session: whatever carries the mark but is not runtime would be
    written -- it is made runtime before the write, and named."""
    leaked = []
    for kind in KINDS:
        for block in getattr(bpy.data, kind):
            if block.library is None and block.get(MARK) and not block.is_runtime_data:
                block.is_runtime_data = True
                leaked.append(block.name_full)
    if leaked:
        print("[scratch] !! {0} scratch datablock(s) were about to be written and are kept out of the file "
              "(they lost their runtime flag): {1}".format(len(leaked), leaked[:20]), flush=True)


def register():
    if _forbid_scratch_in_blend not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_forbid_scratch_in_blend)


def unregister():
    if _forbid_scratch_in_blend in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_forbid_scratch_in_blend)
