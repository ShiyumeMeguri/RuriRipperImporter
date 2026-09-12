"""Drive the blend shapes of the meshes a rig deforms.

What to set and to what came off a game (a named expression, a facial library);
which OBJECT carries a mesh, and which of its key blocks is index N, is this
application's answer and the only thing here. So a statement is
``{mesh name: {index: value}}`` and nothing below knows what an expression is.

Indices are stated in the SOURCE's own order -- everything after Basis -- because
that is the order the engine that authored them counts in; the Basis key is this
host's own addition to represent "no deformation".
"""

from __future__ import annotations

import bpy


def drive(context, rig, weights):
    """Set the stated blend shapes on the meshes ``rig`` drives.

    Returns (meshes touched, warnings). A name the rig has no mesh for and an
    index past the end of that mesh's keys are both reported rather than skipped:
    an expression that half applied looks exactly like one that fully applied."""
    meshes = meshes_by_name(rig)
    touched = set()
    warnings = []
    for name, values in (weights or {}).items():
        obj = meshes.get(str(name).lower())
        if obj is None:
            warnings.append("'{0}' is not a mesh this rig drives".format(name))
            continue
        keys = shape_keys(obj)
        if not keys:
            warnings.append("{0} has no shape keys".format(obj.name))
            continue
        wrote = False
        for index, value in values.items():
            index = int(index)
            if not 0 <= index < len(keys):
                warnings.append("{0}: blend shape {1} is outside its {2}".format(
                    obj.name, index, len(keys)))
                continue
            keys[index].value = float(value)
            wrote = True
        if wrote:
            touched.add(obj.name)
    return len(touched), warnings


def shape_keys(obj):
    """The mesh's blend shapes in the source's own order -- everything after Basis."""
    keys = getattr(obj.data, "shape_keys", None) if obj is not None else None
    return list(keys.key_blocks)[1:] if keys is not None else []


def meshes_by_name(rig):
    """Every mesh the rig drives, keyed by the name it was imported under.

    Blender uniquifies a repeated name with a ``.001`` tail, which is not part of
    the identity the game states -- and an assembled character genuinely has two
    ``o_tang`` (the head's and the separate tongue part's). Of a repeated name the
    one carrying shape keys wins, because a target of an expression system is by
    definition the one with the blend shapes on it."""
    found = {}
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        driven = obj.parent is rig or any(
            modifier.type == "ARMATURE" and modifier.object is rig
            for modifier in obj.modifiers)
        if not driven:
            continue
        key = _base_name(obj.name).lower()
        current = found.get(key)
        if current is None or (not shape_keys(current) and shape_keys(obj)):
            found[key] = obj
    return found


def _base_name(name):
    head, separator, tail = name.rpartition(".")
    return head if separator and tail.isdigit() and len(tail) == 3 else name
