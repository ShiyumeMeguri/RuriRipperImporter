"""Build a Blender armature from a Unity prefab transform hierarchy.

Every transform becomes a bone whose rest matrix is the node's Unity world
matrix conjugated into Blender space.  Bones are not connected head-to-tail
(Unity joints are arbitrary), and bone length is purely cosmetic: skinning and
animation correctness depend only on the rest matrix being consistent between
bind and pose, which this guarantees.

Returns maps the rest of the importer relies on:
    file_id   -> final (uniquified) bone name
    node path -> final bone name
"""

from __future__ import annotations

import json
import zlib

try:
    from . import coordinate, hierarchy
    from .ruri_pybridge.unity import skinning
except ImportError:  # standalone (non-package) testing
    import coordinate
    import hierarchy
    from ruri_pybridge.unity import skinning

import bpy
from mathutils import Matrix, Vector

_DEFAULT_BONE_LENGTH = 0.03

# Custom-property key under which build_armature stamps the Unity rig identity
# (transform path -> bone name + Unity-space local rest TRS) onto the armature
# OBJECT. Persisted in the .blend, so a standalone animation import in a LATER
# session can rebuild exactly the maps build_action needs from the armature the
# user has selected -- no live import-session state required. See
# prefab_importer.maps_from_stamped_armature.
UNITY_RIG_PROP = "ruri_unity_rig"

# Custom-property key under which a character import records the cabmap CABs its closure was
# seeded from. The humanoid muscle solve happens on the C# side, at export time, and it can only
# use an Avatar that is IN the exported closure -- so a later standalone clip import re-seeds these
# CABs to put this character's own Avatar back in scope.
#
# That matters because a clip's own dependency neighbourhood does not reliably contain the
# character rig at all: battle AnimatorControllers are routinely attached by game code rather than
# through bundle dependencies, so a battle clip's closure reaches only its controller, whose sole
# Avatar is a weapon stub. The rig identity therefore travels with the skeleton the user selects,
# exactly as the retired Python retargeter's avatar-YAML stamp did -- but carrying the CAB identity
# instead of the parsed avatar, so the C# pass gets the real asset rather than a re-parse.
CHARACTER_CABS_PROP = "ruri_character_cabs"


def stamp_character_cabs(arm_obj, cab_names):
    """Record the CABs this character was imported from (see CHARACTER_CABS_PROP)."""
    if arm_obj is None or not cab_names:
        return
    existing = read_character_cabs(arm_obj)
    merged = list(existing)
    for cab in cab_names:
        if cab and cab not in merged:
            merged.append(cab)
    arm_obj[CHARACTER_CABS_PROP] = json.dumps(merged, separators=(",", ":"))


def read_character_cabs(arm_obj):
    """The CABs stamped by stamp_character_cabs, or [] when the armature carries none."""
    if arm_obj is None:
        return []
    raw = arm_obj.get(CHARACTER_CABS_PROP)
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(c) for c in value] if isinstance(value, list) else []


def _bone_length(node):
    """Cosmetic length: distance to the nearest child, else a small default."""
    head = node.world.translation
    best = None
    for child in node.children:
        d = (child.world.translation - head).length
        if d > 1e-5 and (best is None or d < best):
            best = d
    if best is None:
        return _DEFAULT_BONE_LENGTH
    return max(min(best, 0.3), 0.005)


def build_armature(context, unity_file, name="UnityArmature"):
    nodes, roots = hierarchy.build_hierarchy(unity_file)

    arm_data = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm_data)
    context.collection.objects.link(arm_obj)

    view_layer = context.view_layer
    view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    file_id_to_bone = {}
    edit_bones = arm_data.edit_bones

    # Create bones in parent-before-child order so parenting is always valid.
    ordered = []
    stack = list(roots)
    while stack:
        node = stack.pop()
        ordered.append(node)
        stack.extend(node.children)

    node_to_editbone = {}
    for node in ordered:
        eb = edit_bones.new(node.name)
        length = _bone_length(node)
        eb.head = (0.0, 0.0, 0.0)
        eb.tail = (0.0, length, 0.0)
        eb.matrix = coordinate.convert_matrix(node.world)
        # Re-assert length along the bone's own axis (matrix set can rescale).
        eb.length = length
        node_to_editbone[node.file_id] = eb
        file_id_to_bone[node.file_id] = eb.name  # Blender may uniquify the name

    for node in ordered:
        if node.parent is not None:
            node_to_editbone[node.file_id].parent = node_to_editbone[node.parent.file_id]

    bpy.ops.object.mode_set(mode="OBJECT")

    path_to_bone = {}
    for node in nodes.values():
        bone_name = file_id_to_bone.get(node.file_id)
        if bone_name and node.path:
            path_to_bone[node.path] = bone_name

    file_id_to_world = hierarchy.world_matrices(nodes)

    # Stamp the Unity rig identity onto the armature object (persists in the
    # .blend): per pathed node, its final bone name and Unity-space LOCAL rest
    # matrix (16 floats, row-major -- exactly the node.local that build_action's
    # rest-pose math consumes). This is what lets "import an animation onto the
    # armature the user has selected" work standalone, in any later session,
    # without the character's import-time maps being alive; see
    # prefab_importer.maps_from_stamped_armature.
    stamped = {}
    for node in nodes.values():
        bone_name = file_id_to_bone.get(node.file_id)
        if bone_name and node.path:
            local = [v for row in node.local for v in row]
            stamped[node.path] = {"bone": bone_name, "local": local}
    arm_obj[UNITY_RIG_PROP] = json.dumps({"paths": stamped}, separators=(",", ":"))

    return arm_obj, {
        "nodes": nodes,
        "roots": roots,
        "file_id_to_bone": file_id_to_bone,
        "path_to_bone": path_to_bone,
        "file_id_to_world": file_id_to_world,
    }


def build_armature_from_avatar(context, avatar_file, name="Avatar"):
    """Build a Blender armature straight from an Avatar asset's OWN embedded
    skeleton (bone hierarchy + rest pose) -- unlike build_armature, this needs
    no accompanying rig FBX/prefab transform hierarchy at all, since Unity's
    Avatar format already carries one (see HumanoidRetargeter.skeleton_nodes,
    which does the actual parsing; this only turns those already-resolved
    nodes into edit-bones, mirroring build_armature's own bone-creation loop
    above). Works for Generic avatars too, not just Humanoid ones -- the raw
    skeleton is populated either way.

    Returns the armature object; the caller stamps the avatar YAML onto it so
    a later standalone AnimationClip import can find the mapping."""
    try:
        from .ruri_pybridge.unity import avatar as avatar_module
    except ImportError:
        from ruri_pybridge.unity import avatar as avatar_module
    from mathutils import Quaternion

    avatar_doc = avatar_file.first("Avatar")
    if avatar_doc is None:
        raise ValueError("file has no Avatar object")
    raw_nodes = [(name, parent, Vector(t), Quaternion(q))
                 for name, parent, t, q in avatar_module.skeleton_nodes(avatar_doc.data)]

    # World matrix per node, parent-before-child via memoized recursion --
    # same composition _provisional_fk uses for posed data, applied here to
    # the avatar's REST data instead.
    worlds = [None] * len(raw_nodes)

    def world_of(index):
        if worlds[index] is not None:
            return worlds[index]
        bone_name, parent, t, q = raw_nodes[index]
        local = Matrix.Translation(t) @ q.to_matrix().to_4x4()
        if parent < 0 or parent >= len(raw_nodes):
            worlds[index] = local
        else:
            worlds[index] = world_of(parent) @ local
        return worlds[index]

    for index in range(len(raw_nodes)):
        world_of(index)

    arm_data = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm_data)
    context.collection.objects.link(arm_obj)

    view_layer = context.view_layer
    view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    edit_bones = arm_data.edit_bones
    index_to_editbone = {}

    # Parent-before-child order: children-of map + a stack walk from roots,
    # same shape build_armature uses from hierarchy.build_hierarchy's nodes.
    children_of = {}
    roots = []
    for index, (_bone_name, parent, _t, _q) in enumerate(raw_nodes):
        if parent < 0 or parent >= len(raw_nodes):
            roots.append(index)
        else:
            children_of.setdefault(parent, []).append(index)
    ordered = []
    stack = list(roots)
    while stack:
        index = stack.pop()
        ordered.append(index)
        stack.extend(children_of.get(index, ()))

    node_positions = [world_of(i).translation for i in range(len(raw_nodes))]
    for index in ordered:
        bone_name, parent, _t, _q = raw_nodes[index]
        eb = edit_bones.new(bone_name)
        length = _DEFAULT_BONE_LENGTH
        for child_index in children_of.get(index, ()):
            d = (node_positions[child_index] - node_positions[index]).length
            if d > 1e-5:
                length = max(min(d, 0.3), 0.005)
                break
        eb.head = (0.0, 0.0, 0.0)
        eb.tail = (0.0, length, 0.0)
        eb.matrix = coordinate.convert_matrix(worlds[index])
        eb.length = length
        index_to_editbone[index] = eb

    for index in ordered:
        _bone_name, parent, _t, _q = raw_nodes[index]
        if parent in index_to_editbone:
            index_to_editbone[index].parent = index_to_editbone[parent]

    bpy.ops.object.mode_set(mode="OBJECT")
    # A standalone avatar import is always top-level: the root yaw rides on the
    # armature object, leaving every bone in pure-C armature space.
    arm_obj.matrix_world = coordinate.root_matrix()
    return arm_obj


def _crc32(text):
    return zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF


def _rest_from_bind_pose(bind_pose):
    """Blender-space rest world matrix from a Unity bind pose (b's inverse is its
    rest world; the stored 4x4 is column-major, so transpose first)."""
    matrix = Matrix([[float(v) for v in row] for row in bind_pose])
    matrix.transpose()
    return coordinate.convert_matrix(matrix.inverted_safe())


def _bone_name_for(path, key):
    """Real leaf name from a reconstructed path, or the hash when unresolved."""
    return path.rsplit("/", 1)[-1] if path else "bone_{0:08x}".format(key)


class SkeletonBinder:
    """One armature for an assembled character, named and parented from the
    template skeleton's own transform paths, posed from the part meshes' bind poses.

    A part mesh authored against a shared skeleton arrives on its own: it carries
    a bind pose per bone (whose inverse is that bone's rest world matrix) and a
    CRC32 name hash per bone, but no armature and no hierarchy. The template's
    Avatar carries the whole skeleton's ``crc32(path) -> path`` table, so every
    hash resolves to its real Unity path -- real leaf name, real parent chain.
    Each skinned bone is placed at its own bind-pose rest (exact); the structural
    ancestors those paths imply (Root, Bip001 -- carried by no mesh) are created
    too, at their children's positions, so the rig runs unbroken to the root the
    way a shipped one does. The first mesh builds the rig; later parts EXTEND it.
    Any hash the table does not name is reconstructed from the template's leaf
    names via skinning.resolve_bone_paths, or kept hash-named as a last resort.

    The rig is one armature OBJECT carrying the top-level root yaw; its bones stay
    in pure-C armature space. Generic: the caller supplies the paths and leaves."""

    def __init__(self, name, skeleton_paths=(), leaf_names=()):
        self.name = name
        self._leaves = [str(n) for n in (leaf_names or ()) if n]
        self.armature = None
        # crc32(path) -> path, seeded from the template skeleton's own table.
        self._path_by_hash = {_crc32(str(p)): str(p) for p in (skeleton_paths or ()) if p}
        self._rest_by_hash = {}   # crc32 -> Blender rest matrix (skinned bones only)
        self._hash_to_bone = {}   # crc32 -> final (uniquified) bone name on the rig

    def bind(self, context, decoded):
        """Ensure every bone ``decoded`` references (and their structural
        ancestors) exist on the rig -- building it on the first call, extending it
        after -- and return the ``(smr_bones, file_id_to_bone)`` pair
        build_mesh_object skins with: ``[{"fileID": i}]`` and ``{i: bone_name}``.
        (None, None) when the mesh carries no usable bind data."""
        hashes = getattr(decoded, "bone_name_hashes", None)
        binds = getattr(decoded, "bind_poses", None)
        if (hashes is None or len(hashes) == 0 or binds is None
                or len(binds) != len(hashes)):
            return None, None
        keys = [int(h) & 0xFFFFFFFF for h in hashes]

        unresolved = [k for k in keys if k not in self._path_by_hash]
        if unresolved:
            found = skinning.resolve_bone_paths(
                unresolved, self._leaves, seed_paths=list(self._path_by_hash.values()))
            self._path_by_hash.update(found)

        for index, key in enumerate(keys):
            self._rest_by_hash.setdefault(key, _rest_from_bind_pose(binds[index]))
        self._grow(context, keys)

        smr_bones = [{"fileID": i} for i in range(len(keys))]
        file_id_to_bone = {i: self._hash_to_bone.get(keys[i], _bone_name_for(None, keys[i]))
                           for i in range(len(keys))}
        return smr_bones, file_id_to_bone

    def _grow(self, context, skinned_keys):
        """Add every not-yet-built bone this mesh needs: its own skinned bones plus
        the structural ancestors their paths imply."""
        needed = self._with_ancestors(skinned_keys)
        new_keys = [key for key in needed if key not in self._hash_to_bone]
        if not new_keys:
            return

        if self.armature is None:
            data = bpy.data.armatures.new(self.name)
            self.armature = bpy.data.objects.new(self.name, data)
            context.scene.collection.objects.link(self.armature)
            # A rebuilt skeleton is a top-level import object: the root yaw rides
            # on the object; its bones stay in pure-C armature space.
            self.armature.matrix_world = coordinate.root_matrix()

        previous = context.view_layer.objects.active
        context.view_layer.objects.active = self.armature
        bpy.ops.object.mode_set(mode="EDIT")
        edit_bones = self.armature.data.edit_bones

        # Shallow paths first, so a child's parent bone already exists.
        ordered = sorted(new_keys, key=self._depth)
        for key in ordered:
            path = self._path_by_hash.get(key)
            eb = edit_bones.new(_bone_name_for(path, key))
            eb.head = (0.0, 0.0, 0.0)
            eb.tail = (0.0, _DEFAULT_BONE_LENGTH, 0.0)
            rest = self._rest_by_hash.get(key)
            if rest is not None:
                eb.matrix = rest
                eb.length = _DEFAULT_BONE_LENGTH
            self._hash_to_bone[key] = eb.name  # Blender may uniquify

        for key in ordered:
            path = self._path_by_hash.get(key)
            parent = self._parent_bone(path) if path else None
            if parent is not None and parent in edit_bones:
                edit_bones[self._hash_to_bone[key]].parent = edit_bones[parent]

        # Structural bones (no bind pose) sit at their children's heads, deepest
        # first so a child is placed before its own parent reads it. They carry no
        # weight, so position is cosmetic -- only the parent chain matters.
        structural = [key for key in new_keys
                      if key not in self._rest_by_hash and self._path_by_hash.get(key)]
        for key in sorted(structural, key=self._depth, reverse=True):
            eb = edit_bones.get(self._hash_to_bone[key])
            heads = [child.head.copy() for child in eb.children] if eb else []
            if heads:
                mean = sum(heads, Vector((0.0, 0.0, 0.0))) / len(heads)
                eb.head = mean
                eb.tail = mean + Vector((0.0, _DEFAULT_BONE_LENGTH, 0.0))

        # Cosmetic length only (never direction -- that would rotate the rest and
        # break a later animation): reach toward the nearest child head.
        for name in self._hash_to_bone.values():
            eb = edit_bones.get(name)
            if eb is None:
                continue
            distances = [(eb.head - child.head).length for child in eb.children]
            near = min((d for d in distances if d > 1e-5), default=0.0)
            if near:
                eb.length = max(min(near, 0.3), 0.005)

        bpy.ops.object.mode_set(mode="OBJECT")
        context.view_layer.objects.active = previous

    def _with_ancestors(self, keys):
        """``keys`` plus every ancestor hash their paths imply, recording each
        ancestor's path so it is named and parented like any other bone."""
        needed = set(keys)
        for key in keys:
            path = self._path_by_hash.get(key)
            if not path:
                continue
            segments = path.split("/")
            for cut in range(1, len(segments)):
                ancestor = "/".join(segments[:cut])
                ancestor_key = _crc32(ancestor)
                self._path_by_hash.setdefault(ancestor_key, ancestor)
                needed.add(ancestor_key)
        return needed

    def _depth(self, key):
        path = self._path_by_hash.get(key)
        return path.count("/") if path else 0

    def _parent_bone(self, path):
        """The rig bone name of the nearest strict ancestor of ``path`` that is
        itself a rig bone."""
        segments = path.split("/")
        for cut in range(len(segments) - 1, 0, -1):
            bone = self._hash_to_bone.get(_crc32("/".join(segments[:cut])))
            if bone:
                return bone
        return None


def graft_armature(context, target, source):
    """Fold ``source`` into ``target`` so an assembled character ends on ONE rig.

    Bones shared by name are already the same joint (both rigs name bones by
    their real path leaf), so only source's OWN bones -- the ones carrying weight
    below them, e.g. a part's extra ear joints -- are added, at their existing
    world rest; a pure structural artifact (no weight, no weighted descendant) is
    left behind. Every mesh the source drove is re-pointed onto target with its
    world transform preserved, then the emptied source rig and any empty parent
    shells above it are removed."""
    if source is None or target is None or source is target:
        return

    driven = [o for o in context.scene.objects
              if o.type == "MESH" and any(m.type == "ARMATURE" and m.object is source
                                          for m in o.modifiers)]
    weighted = set()
    for obj in driven:
        weighted.update(group.name for group in obj.vertex_groups)

    def subtree_has_weight(bone):
        stack = [bone]
        while stack:
            current = stack.pop()
            if current.name in weighted:
                return True
            stack.extend(current.children)
        return False

    target_names = {bone.name for bone in target.data.bones}
    to_add = [bone for bone in source.data.bones
              if bone.name not in target_names and subtree_has_weight(bone)]
    if to_add:
        _graft_bones(context, target, source, to_add)

    for obj in driven:
        world = obj.matrix_world.copy()
        for modifier in obj.modifiers:
            if modifier.type == "ARMATURE" and modifier.object is source:
                modifier.object = target
        obj.parent = target
        obj.matrix_world = world

    _remove_armature_and_empty_parents(source)


def _graft_bones(context, target, source, bones):
    """Add ``bones`` (source Bones) to ``target`` at their current world rest,
    parented to the nearest ancestor already present on target."""
    source_world = source.matrix_world
    target_inverse = target.matrix_world.inverted_safe()
    previous = context.view_layer.objects.active
    context.view_layer.objects.active = target
    bpy.ops.object.mode_set(mode="EDIT")
    edit_bones = target.data.edit_bones

    for bone in sorted(bones, key=lambda b: len(b.parent_recursive)):
        rest = target_inverse @ (source_world @ bone.matrix_local)
        eb = edit_bones.new(bone.name)
        eb.head = (0.0, 0.0, 0.0)
        eb.tail = (0.0, _DEFAULT_BONE_LENGTH, 0.0)
        eb.matrix = rest
        eb.length = _DEFAULT_BONE_LENGTH
        ancestor = bone.parent
        while ancestor is not None and ancestor.name not in edit_bones:
            ancestor = ancestor.parent
        if ancestor is not None:
            eb.parent = edit_bones[ancestor.name]

    bpy.ops.object.mode_set(mode="OBJECT")
    context.view_layer.objects.active = previous


def _remove_armature_and_empty_parents(armature_obj):
    """Delete an emptied armature object and any Empty parent shells left with no
    other children above it."""
    parent = armature_obj.parent
    data = armature_obj.data
    bpy.data.objects.remove(armature_obj, do_unlink=True)
    if data is not None and data.users == 0:
        bpy.data.armatures.remove(data)
    while parent is not None:
        grandparent = parent.parent
        if parent.type == "EMPTY" and not parent.children:
            bpy.data.objects.remove(parent, do_unlink=True)
            parent = grandparent
        else:
            break
