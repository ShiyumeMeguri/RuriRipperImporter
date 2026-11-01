"""Realize a bone-relative binding, without game names or rig reconstruction."""
import math

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import rig_identity

KEY = "ruri_attachment_key"


def validate(context, rig, binding):
    try:
        if rig is None or context.scene.objects.get(rig.name) != rig or rig.type != "ARMATURE":
            return "The equipment target is no longer in this scene. Run Find again."
        if rig.library or rig.data.library or rig.mode != "OBJECT":
            return "The target must be a local rig in Object Mode."
        paths = rig_identity.bone_paths(rig)
        if list(paths.values()).count(binding["bone_path"]) != 1:
            return "The target bone path is missing or ambiguous. Run Find again."
        if rig_identity.seed_of(rig) != binding["actor_seed"]:
            return "The target identity changed. Run Find again."
        if any(obj.parent == rig and obj.get(KEY) == binding["key"] for obj in context.scene.objects):
            return "This slot is already equipped; this action appends, it does not replace."
        offset = binding["offset"]
        if len(offset) != 16 or not all(math.isfinite(float(v)) for v in offset):
            return "The binding has an invalid offset matrix."
        if binding["status"] != "ready" or not binding["seed"] or not binding["key"]:
            return "Binding is not ready: {0}".format(binding.get("detail", binding["status"]))
    except (ReferenceError, KeyError, TypeError, ValueError):
        return "The equipment target or binding expired. Run Find again."
    return ""


def bind(context, stated, built, rig, binding):
    reason = validate(context, rig, binding)
    if reason:
        raise ValueError(reason)
    paths = rig_identity.bone_paths(rig)
    bone = next(name for name, path in paths.items() if path == binding["bone_path"])
    roots = list(stated.roots)
    if len(roots) != 1:
        raise ValueError("An attachment must state one root frame.")
    root = roots[0]
    source_frame = (Matrix.Translation(Vector(root.position))
                    @ Quaternion((root.rotation[3], *root.rotation[:3])).to_matrix().to_4x4()
                    @ Matrix.Diagonal(Vector(root.scale)).to_4x4())
    inverse = source_frame.inverted()
    values = binding["offset"]
    offset = Matrix([values[i:i + 4] for i in range(0, 16, 4)])
    if not built.objects:
        raise ValueError("The selected attachment produced no objects.")
    # Newly materialised parent chains have not necessarily been evaluated yet.
    # Read every standalone world frame before changing any of those parents.
    context.view_layer.update()
    made = set(built.objects)
    locals_by_object = {obj: offset @ inverse @ obj.matrix_world.copy()
                        for obj in built.objects if obj.parent not in made}
    # The mount is a normal saved object. The constraint follows the bone's
    # origin/orientation (not Blender bone-parenting's cosmetic tail offset).
    mount = bpy.data.objects.new(binding["label"] + " attachment", None)
    context.collection.objects.link(mount)
    mount.parent = rig
    mount[KEY] = binding["key"]
    mount["ruri_attachment_seed"] = binding["seed"]
    mount["ruri_attachment_bone_path"] = binding["bone_path"]
    mount.empty_display_size = 0.03
    follow = mount.constraints.new("COPY_TRANSFORMS")
    follow.name = "Follow attachment bone"
    follow.target = rig
    follow.subtarget = bone
    follow.target_space = "WORLD"
    follow.owner_space = "WORLD"
    for obj in built.objects:
        if obj in locals_by_object:
            # All coordinates were already converted by the reader. Remove
            # only the source's standalone frame, retaining its authored local.
            obj.parent = mount
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_basis = locals_by_object[obj]
        if not binding["visible"] and obj.type == "MESH":
            obj.hide_render = True
            obj.hide_viewport = True
    built.objects.append(mount)
    built.imported += 1
    built.warnings.insert(0, "Equipped on {0} / {1}; existing rig unchanged.".format(rig.name, bone))
    context.view_layer.update()
    return built
