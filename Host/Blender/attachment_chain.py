"""Transactional scene realization of opaque parent-first attachment bindings."""
from contextlib import contextmanager

import bpy
from mathutils import Matrix

from . import attachments, rig_identity

RESERVATION = "ruri_attachment_reservation"


def animation_target(context, binding, selected):
    """Resolve only saved mounts beneath the selected owner; never use the owner as fallback."""
    owner = selected
    while owner is not None:
        if owner.type == "ARMATURE" and rig_identity.seed_of(owner) == binding["actor_seed"]:
            break
        owner = owner.parent
    if owner is None:
        raise ValueError("Select the equipment's owner or one of its mounted rigs first.")
    mounts = [obj for obj in owner.children_recursive
              if obj.get(attachments.KEY) == binding["key"] and not obj.get(RESERVATION)
              and obj.get("ruri_attachment_seed") == binding["seed"]]
    rigs = [rig for mount in mounts for rig in mount.children
            if rig.type == "ARMATURE" and rig_identity.seed_of(rig) == binding["seed"]]
    if not rigs:
        raise ValueError("This equipment is not mounted. Use Append to Character before importing its animations.")
    if len(rigs) != 1:
        raise ValueError("More than one mounted rig matches this equipment. Resolve the duplicate mounts first.")
    rig = rigs[0]
    if context.scene.objects.get(rig.name) != rig or rig.library or rig.data.library or rig.mode != "OBJECT":
        raise ValueError("The mounted animation target must be a local rig in Object Mode.")
    return rig


class Session:
    def __init__(self, context, owner):
        self.context = context
        self.owner = owner
        self.rigs = {}

    def target(self, part):
        rig = self.rigs[part["parent_key"]] if part["parent_key"] else self.owner
        if (self.context.scene.objects.get(rig.name) != rig
                or rig_identity.seed_of(rig) != part["actor_seed"]):
            raise ValueError("Attachment target identity changed.")
        return rig

    def reuse(self, part, final):
        target = self.target(part)
        candidates = [obj for obj in self.owner.children_recursive
                      if obj.get(attachments.KEY) == part["key"] and not obj.get(RESERVATION)]
        markers = [obj for obj in self.owner.children if obj.get(attachments.KEY) == part["key"]]
        if not candidates:
            if markers:
                raise ValueError("A reserved attachment slot is stale; inspect it before importing.")
            return False
        if final or len(candidates) != 1:
            raise ValueError("This slot is already equipped; this action does not replace it.")
        mount = candidates[0]
        if (mount.parent != target or mount.get("ruri_attachment_seed") != part["seed"]
                or mount.get("ruri_attachment_bone_path") != part["bone_path"]):
            raise ValueError("The existing parent attachment does not match this binding.")
        constraints = list(mount.constraints)
        paths = rig_identity.bone_paths(target)
        names = [name for name, path in paths.items() if path == part["bone_path"]]
        if (len(names) != 1 or len(constraints) != 1 or constraints[0].type != "COPY_TRANSFORMS"
                or constraints[0].target != target or constraints[0].subtarget != names[0]
                or constraints[0].target_space != "WORLD" or constraints[0].owner_space != "WORLD"
                or constraints[0].influence != 1 or constraints[0].mute):
            raise ValueError("The existing parent attachment constraint was changed.")
        rigs = [obj for obj in mount.children if obj.type == "ARMATURE"
                and rig_identity.seed_of(obj) == part["seed"]]
        if len(rigs) != 1 or rigs[0].library or rigs[0].data.library or rigs[0].mode != "OBJECT":
            raise ValueError("The existing parent has no unique editable rig.")
        rig = rigs[0]
        expected = Matrix([part["offset"][i:i + 4] for i in range(0, 16, 4)])
        if any(abs(rig.matrix_basis[r][c] - expected[r][c]) > 1e-5 for r in range(4) for c in range(4)):
            raise ValueError("The existing parent attachment offset was changed.")
        if any(abs(rig.matrix_parent_inverse[r][c] - float(r == c)) > 1e-5
               for r in range(4) for c in range(4)):
            raise ValueError("The existing parent inverse frame was changed.")
        # Old direct attachments predate receipts. Check their visible geometry;
        # new chain parts carry the exact requested visibility for later reuse.
        visible = mount.get("ruri_attachment_visible")
        if visible is None:
            meshes = [obj for obj in mount.children_recursive if obj.type == "MESH"]
            visible = any(not obj.hide_viewport and not obj.hide_render for obj in meshes)
        if bool(visible) != bool(part["visible"]):
            raise ValueError("The existing parent uses a different visibility variant.")
        self.rigs[part["key"]] = rig
        return True

    def accept(self, part, built):
        target = self.target(part)
        rigs = [obj for obj in built.objects if obj.type == "ARMATURE"
                and rig_identity.seed_of(obj) == part["seed"]]
        if len(rigs) != 1:
            raise ValueError("A chain part must produce exactly one rig.")
        mounts = [obj for obj in built.objects if obj.parent == target
                  and obj.get(attachments.KEY) == part["key"]]
        if len(mounts) != 1:
            raise ValueError("The new attachment has no unique mount.")
        mounts[0]["ruri_attachment_visible"] = part["visible"]
        self.rigs[part["key"]] = rigs[0]
        if target != self.owner:
            # The original direct path checks immediate children. A normal saved
            # reservation preserves that old check without changing its logic.
            marker = bpy.data.objects.new(part["label"] + " slot reservation", None)
            self.context.collection.objects.link(marker)
            marker.parent = self.owner
            marker[attachments.KEY] = part["key"]
            marker[RESERVATION] = mounts[0]
            marker.hide_render = True
            marker.hide_viewport = True


@contextmanager
def transaction(context, owner):
    if (owner is None or owner.type != "ARMATURE" or context.scene.objects.get(owner.name) != owner
            or owner.library or owner.data.library or owner.mode != "OBJECT"):
        raise ValueError("Select a local owner rig in Object Mode.")
    groups = (bpy.data.objects, bpy.data.meshes, bpy.data.armatures,
              bpy.data.materials, bpy.data.images, bpy.data.node_groups)
    before = [set(group) for group in groups]
    active, selected = context.view_layer.objects.active, list(context.selected_objects)
    try:
        yield Session(context, owner)
    except BaseException:
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in set(bpy.data.objects) - before[0]:
            bpy.data.objects.remove(obj, do_unlink=True)
        for group, previous in zip(groups[1:], before[1:]):
            for block in set(group) - previous:
                if block.users == 0:
                    group.remove(block)
        raise
    finally:
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in selected:
            if context.scene.objects.get(obj.name) == obj:
                obj.select_set(True)
        context.view_layer.objects.active = active
