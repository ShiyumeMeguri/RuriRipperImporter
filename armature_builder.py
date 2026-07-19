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

try:
    from . import coordinate, hierarchy
except ImportError:
    import coordinate
    import hierarchy

import numpy as np

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

# Custom-property key under which the character import stamps the WORKING
# humanoid Avatar's raw YAML (zlib+base64) onto the armature object -- the
# muscle referential travels with the skeleton, because a clip's own
# dependency neighborhood does not reliably contain the character rig at all
# (battle controllers are attached by game code, not bundle dependencies).
# See prefab_importer._stamp_avatar_on_armature / retargeter_from_stamped_armature.
AVATAR_YAML_PROP = "ruri_avatar_yaml"


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

    file_id_to_world = {fid: np.array(node.world, dtype=np.float64)
                        for fid, node in nodes.items()}

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

    Returns (arm_obj, retargeter) -- the caller (prefab_importer.
    import_avatar_from_db) uses the retargeter's source_key to stamp the
    avatar YAML onto the armature via the SAME _stamp_avatar_on_armature a
    full character import already uses, so a later standalone AnimationClip
    import can retarget straight onto this armature."""
    try:
        from . import humanoid_retarget
    except ImportError:
        import humanoid_retarget

    retargeter = humanoid_retarget.HumanoidRetargeter(avatar_file)
    raw_nodes = retargeter.skeleton_nodes()

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
    return arm_obj, retargeter
