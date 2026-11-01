"""Mount what a statement placed onto a bone of a rig already in the document, and take a failed batch back out.

A part states the facts: its slot, what it hangs from, the bone, the offset in this host's basis, whether its meshes
show. The rig it hangs on is never edited and nothing here names a game. ``materialise`` knows none of this: a part is
placed like anything else and mounted afterwards.
"""
from contextlib import contextmanager

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import rig_identity

KEY = "ruri_attachment_key"
SEED = "ruri_attachment_seed"
BONE_PATH = "ruri_attachment_bone_path"


def refusal(context, owner, parts):
    """Why these parts cannot go onto ``owner`` now, or "". Never changes the document."""
    try:
        if owner is None or context.scene.objects.get(owner.name) != owner or owner.type != "ARMATURE":
            return "The equipment target is no longer in this scene. Run Find again."
        if owner.library or owner.data.library or owner.mode != "OBJECT":
            return "The target must be a local rig in Object Mode."
        if rig_identity.seed_of(owner) != parts[0]["actor_seed"]:
            return "The target identity changed. Run Find again."
        if list(rig_identity.bone_paths(owner).values()).count(parts[0]["bone_path"]) != 1:
            return "The target bone path is missing or ambiguous. Run Find again."
        if any(obj.get(KEY) == parts[-1]["key"] for obj in owner.children_recursive):
            return "This slot is already equipped; this action appends, it does not replace."
    except ReferenceError:
        return "The equipment target was removed. Run Find again."
    return ""


def mounted_rig(context, binding, selected):
    """The rig a binding's part brought, under the owner the selection stands for; never the owner itself."""
    owner = selected
    while owner is not None:
        if owner.type == "ARMATURE" and rig_identity.seed_of(owner) == binding["actor_seed"]:
            break
        owner = owner.parent
    if owner is None:
        raise ValueError("Select the equipment's owner or one of its mounted rigs first.")
    mounts = [obj for obj in owner.children_recursive
              if obj.get(KEY) == binding["key"] and obj.get(SEED) == binding["seed"]]
    rigs = [rig for mount in mounts for rig in mount.children
            if rig.type == "ARMATURE" and rig_identity.seed_of(rig) == binding["seed"]]
    if not rigs:
        raise ValueError("This equipment is not mounted. Use Append to Character before importing its animations.")
    if len(rigs) != 1:
        raise ValueError("More than one mounted rig matches this equipment. Resolve the duplicate mounts first.")
    if context.scene.objects.get(rigs[0].name) != rigs[0] or rigs[0].library or rigs[0].data.library or rigs[0].mode != "OBJECT":
        raise ValueError("The mounted animation target must be a local rig in Object Mode.")
    return rigs[0]


def _mount(context, stated, built, target, part):
    names = [name for name, path in rig_identity.bone_paths(target).items() if path == part["bone_path"]]
    if len(names) != 1:
        raise ValueError("The bone this part hangs from is missing or ambiguous: {0}".format(part["bone_path"]))
    roots = list(stated.roots)
    if len(roots) != 1:
        raise ValueError("An attachment must state one root frame.")
    if not built.objects:
        raise ValueError("The selected attachment produced no objects.")
    root = roots[0]
    source_frame = (Matrix.Translation(Vector(root.position))
                    @ Quaternion((root.rotation[3], *root.rotation[:3])).to_matrix().to_4x4()
                    @ Matrix.Diagonal(Vector(root.scale)).to_4x4())
    values = part["offset"]
    offset = Matrix([values[index:index + 4] for index in range(0, 16, 4)])
    # A parent chain just built has not been evaluated yet, and world frames must be read before any parent changes.
    context.view_layer.update()
    made = set(built.objects)
    frame = offset @ source_frame.inverted()
    locals_by_object = {obj: frame @ obj.matrix_world.copy() for obj in built.objects if obj.parent not in made}
    # A constraint and not bone parenting: it follows the bone's origin, without bone parenting's offset to the tail.
    mount = bpy.data.objects.new(part["label"] + " attachment", None)
    context.collection.objects.link(mount)
    mount.parent = target
    mount[KEY] = part["key"]
    mount[SEED] = part["seed"]
    mount[BONE_PATH] = part["bone_path"]
    mount.empty_display_size = 0.03
    follow = mount.constraints.new("COPY_TRANSFORMS")
    follow.name = "Follow attachment bone"
    follow.target = target
    follow.subtarget = names[0]
    follow.target_space = "WORLD"
    follow.owner_space = "WORLD"
    for obj in built.objects:
        if obj in locals_by_object:
            obj.parent = mount
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_basis = locals_by_object[obj]
        if not part["visible"] and obj.type == "MESH":
            obj.hide_render = True
            obj.hide_viewport = True
    built.objects.append(mount)
    built.imported += 1
    built.warnings.insert(0, "Equipped on {0} / {1}; existing rig unchanged.".format(target.name, names[0]))
    context.view_layer.update()


def _clear_selection(context):
    """A rig being built enters Edit Mode with every selected armature, so nothing else may stay selected."""
    for obj in list(context.selected_objects):
        obj.select_set(False)
    context.view_layer.objects.active = None


class Session:
    """One batch of parts, parent first, onto one owner rig."""

    def __init__(self, context, owner):
        self.context = context
        self.owner = owner
        self.rigs = {}

    def _target(self, part):
        rig = self.rigs[part["parent_key"]] if part["parent_key"] else self.owner
        if rig is None:
            raise ValueError("The part this one hangs from carries no rig.")
        if self.context.scene.objects.get(rig.name) != rig or rig_identity.seed_of(rig) != part["actor_seed"]:
            raise ValueError("Attachment target identity changed.")
        return rig

    def reuse(self, part, final):
        """True when this part already hangs where it is asked to and the batch goes on from it. The one a batch is
        for must be new: this appends, it does not replace."""
        mounts = [obj for obj in self.owner.children_recursive if obj.get(KEY) == part["key"]]
        if not mounts:
            return False
        if final or len(mounts) != 1:
            raise ValueError("This slot is already equipped; this action does not replace it.")
        mount = mounts[0]
        if (mount.parent != self._target(part) or mount.get(SEED) != part["seed"]
                or mount.get(BONE_PATH) != part["bone_path"]):
            raise ValueError("The existing parent attachment does not match this binding.")
        rigs = [obj for obj in mount.children if obj.type == "ARMATURE" and rig_identity.seed_of(obj) == part["seed"]]
        if len(rigs) != 1:
            raise ValueError("The existing parent attachment has no unique rig.")
        self.rigs[part["key"]] = rigs[0]
        return True

    def accept(self, part, stated, built):
        """Mount what this part just placed."""
        rigs = [obj for obj in built.objects if obj.type == "ARMATURE" and rig_identity.seed_of(obj) == part["seed"]]
        if len(rigs) > 1:
            raise ValueError("A part may bring at most one rig of its own.")
        _mount(self.context, stated, built, self._target(part), part)
        self.rigs[part["key"]] = rigs[0] if rigs else None
        _clear_selection(self.context)


@contextmanager
def transaction(context, owner):
    """A synchronous batch of parts: everything it made goes back out if it fails, and the selection comes back
    either way. Nothing it did not make is touched."""
    groups = (bpy.data.objects, bpy.data.meshes, bpy.data.armatures, bpy.data.materials, bpy.data.images, bpy.data.node_groups)
    before = [set(group) for group in groups]
    active, selected = context.view_layer.objects.active, list(context.selected_objects)
    _clear_selection(context)
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
        _clear_selection(context)
        for obj in selected:
            if context.scene.objects.get(obj.name) == obj:
                obj.select_set(True)
        context.view_layer.objects.active = active
