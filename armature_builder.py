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

import numpy as np

try:
    from . import coordinate, hierarchy
    from .RuriRipperPyBridge.unity import skinning
except ImportError:  # standalone (non-package) testing
    import coordinate
    import hierarchy
    from RuriRipperPyBridge.unity import skinning

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

# Custom-property key under which a character import bakes the rig's WHOLE Avatar document
# (Unity's own m_Avatar/m_Human/m_AxesArray/m_TOS field tree, JSON-encoded) onto the armature
# OBJECT. This is the humanoid retarget contract: the skeleton carries everything the muscle
# solve needs, so ANY muscle-encoded .anim -- this project's or another's -- solves against the
# selected armature by handing this stamp plus the clip's float channels to the C# Animator
# (RipperBlenderBridge.SolveHumanoidClip). Nothing is looked up at import time and no avatar has
# to be in any export scope: the identity travels with the skeleton, in the .blend.
UNITY_AVATAR_PROP = "ruri_unity_avatar"


# Custom-property key under which an import records WHICH GAME this skeleton came from (the
# upstream GameType member, e.g. "EndField"/"Koikatu"). Two rigs' stamps are what select a
# cross-game retarget table by identity instead of by guessing at bone-name shapes -- see
# cross_game_retarget.
UNITY_GAME_PROP = "ruri_source_game"


def stamp_rig(arm_obj, paths):
    """Record the Unity rig identity -- {transform path: {"bone", "local"}} -- on the
    armature OBJECT (see UNITY_RIG_PROP).

    THE one writer: every code path that builds an armature out of Unity data owes
    the same stamp, and a second copy of this encoding is how a rig ends up carrying
    a shape the reader does not accept. See read_rig for the other half."""
    if arm_obj is None:
        return
    arm_obj[UNITY_RIG_PROP] = json.dumps({"paths": paths}, separators=(",", ":"))


def read_rig(arm_obj):
    """The rig identity stamped on an armature, or None when it carries none (built
    by another tool, or by a build older than the stamping)."""
    if arm_obj is None:
        return None
    raw = arm_obj.get(UNITY_RIG_PROP)
    if not raw:
        return None
    try:
        paths = json.loads(str(raw))["paths"]
    except (ValueError, KeyError, TypeError):
        return None
    return paths if isinstance(paths, dict) else None


def stamp_avatar(arm_obj, avatar_data):
    """Bake an Avatar document tree (a parsed Avatar object's ``.data``) onto the armature."""
    if arm_obj is None or not avatar_data:
        return
    arm_obj[UNITY_AVATAR_PROP] = json.dumps(avatar_data, separators=(",", ":"))


def stamp_game(arm_obj, game_name):
    """Record the game this skeleton was imported from (see UNITY_GAME_PROP)."""
    if arm_obj is None or not game_name:
        return
    arm_obj[UNITY_GAME_PROP] = str(game_name)


def read_game(arm_obj):
    """The game stamped on an armature, or "" when it carries none."""
    if arm_obj is None:
        return ""
    return str(arm_obj.get(UNITY_GAME_PROP) or "")


def read_avatar_json(arm_obj):
    """The armature's stamped Avatar document JSON, or None when it carries no stamp."""
    if arm_obj is None:
        return None
    raw = arm_obj.get(UNITY_AVATAR_PROP)
    return str(raw) if raw else None


# Which collection a humanoid slot lands in. Keyed on the WORD Unity's own slot
# names are built from, not on the slots themselves: the enum is systematic
# (side prefix + region), so a table over regions covers every present and future
# member of it, while a table over the 65 slots would restate the enum.
_HUMANOID_REGIONS = (
    ("Finger", "Fingers"),
    ("Thumb", "Fingers"), ("Index", "Fingers"), ("Middle", "Fingers"),
    ("Ring", "Fingers"), ("Little", "Fingers"),
    ("Shoulder", "Arms"), ("Arm", "Arms"), ("Hand", "Arms"),
    ("Leg", "Legs"), ("Foot", "Legs"), ("Toe", "Legs"),
    ("Eye", "Head"), ("Jaw", "Head"), ("Head", "Head"), ("Neck", "Head"),
    ("Hips", "Torso"), ("Spine", "Torso"), ("Chest", "Torso"),
)


def _humanoid_collection(slot):
    """The collection name a human slot belongs to: '<Region>' or '<Region>.L/R'."""
    side = ""
    body = slot
    for prefix, suffix in (("Left", ".L"), ("Right", ".R")):
        if slot.startswith(prefix):
            side = suffix
            body = slot[len(prefix):]
            break
    for word, region in _HUMANOID_REGIONS:
        if word in body:
            return region + side
    return "Other" + side


def build_bone_collections(arm_obj, bone_slots):
    """Sort a humanoid rig's bones into Blender bone collections.

    ``bone_slots`` is {bone name: human slot name} as the avatar states it (see
    pythonnet_bridge.describe_humanoid_bones). A generic rig hands in nothing and
    gets nothing: there is no statement anywhere of what its bones are, and
    guessing from names is how a rig ends up mis-sorted with no way to tell.
    Bones the avatar never mentions (twist helpers, skirt, hair, props) are left
    out of every collection rather than swept into a bucket -- an empty
    collection list means 'unclassified', which is the truth about them.
    Returns the number of bones assigned."""
    if not bone_slots or arm_obj is None or arm_obj.type != "ARMATURE":
        return 0
    armature = arm_obj.data
    if not hasattr(armature, "collections"):
        return 0
    assigned = 0
    by_collection = {}
    for bone in armature.bones:
        slot = bone_slots.get(bone.name)
        if slot:
            by_collection.setdefault(_humanoid_collection(slot), []).append(bone)
    for name in sorted(by_collection):
        collection = armature.collections.get(name) or armature.collections.new(name)
        for bone in by_collection[name]:
            collection.assign(bone)
            assigned += 1
    return assigned


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


DISPLAY_TYPE = "STICK"


def new_armature_data(name):
    """The one place an armature datablock is born, so every rig this add-on
    builds looks the same in the viewport.

    Stick, because a game rig routinely carries a few hundred bones whose lengths
    are cosmetic (see this module's header): octahedral bodies at those lengths
    bury the mesh they drive, while sticks stay readable at any bone count."""
    data = bpy.data.armatures.new(name)
    data.display_type = DISPLAY_TYPE
    return data


def build_armature(context, unity_file, name="UnityArmature"):
    nodes, roots = hierarchy.build_hierarchy(unity_file)

    arm_data = new_armature_data(name)
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
    stamp_rig(arm_obj, stamped)

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
    Avatar format already carries one (avatar.skeleton_nodes does the actual
    parsing; this only turns those already-resolved nodes into edit-bones,
    mirroring build_armature's own bone-creation loop above). Works for
    Generic avatars too, not just Humanoid ones -- the raw skeleton is
    populated either way.

    The result is a complete retarget target on its own: the rig identity
    (UNITY_RIG_PROP, TOS paths + SkeletonPose local rests) and the whole
    avatar document (UNITY_AVATAR_PROP) are both stamped here, so any clip --
    generic or muscle-encoded -- binds to it standalone."""
    try:
        from .RuriRipperPyBridge.unity import avatar as avatar_module
    except ImportError:
        from RuriRipperPyBridge.unity import avatar as avatar_module
    from mathutils import Quaternion

    avatar_doc = avatar_file.first("Avatar")
    if avatar_doc is None:
        raise ValueError("file has no Avatar object")
    raw_nodes = [(name, parent, Vector(t), Quaternion(q), path)
                 for name, parent, t, q, path in avatar_module.skeleton_nodes(avatar_doc.data)]

    # Local rest per node (Unity space), and world matrices composed from them
    # parent-before-child via memoized recursion -- same composition
    # _provisional_fk uses for posed data, applied here to the REST data.
    locals_ = [Matrix.Translation(t) @ q.to_matrix().to_4x4()
               for _name, _parent, t, q, _path in raw_nodes]
    worlds = [None] * len(raw_nodes)

    def world_of(index):
        if worlds[index] is not None:
            return worlds[index]
        parent = raw_nodes[index][1]
        if parent < 0 or parent >= len(raw_nodes):
            worlds[index] = locals_[index]
        else:
            worlds[index] = world_of(parent) @ locals_[index]
        return worlds[index]

    for index in range(len(raw_nodes)):
        world_of(index)

    arm_data = new_armature_data(name)
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
    for index, (_bone_name, parent, _t, _q, _path) in enumerate(raw_nodes):
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
        bone_name = raw_nodes[index][0]
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
        parent = raw_nodes[index][1]
        if parent in index_to_editbone:
            index_to_editbone[index].parent = index_to_editbone[parent]

    # Bone names may have been uniquified; capture them while edit-bones live.
    index_to_bone_name = {index: eb.name for index, eb in index_to_editbone.items()}
    bpy.ops.object.mode_set(mode="OBJECT")
    # A standalone avatar import is always top-level: the root yaw rides on the
    # armature object, leaving every bone in pure-C armature space.
    arm_obj.matrix_world = coordinate.root_matrix()

    # The same rig identity build_armature stamps, sourced from the avatar's
    # own tables: TOS full path -> (bone, SkeletonPose local rest). A node TOS
    # does not name gets no entry (its hash-named bone can't anchor a curve).
    stamped = {}
    for index, (_bone_name, _parent, _t, _q, path) in enumerate(raw_nodes):
        bone_name = index_to_bone_name.get(index)
        if not bone_name or not path:
            continue
        stamped[path] = {"bone": bone_name,
                         "local": [v for row in locals_[index] for v in row]}
    stamp_rig(arm_obj, stamped)
    stamp_avatar(arm_obj, avatar_doc.data)
    return arm_obj


def _crc32(text):
    return zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF


def _bone_name_for(path, key):
    """Real leaf name from a reconstructed path, or the hash when unresolved."""
    return path.rsplit("/", 1)[-1] if path else "bone_{0:08x}".format(key)


class SkeletonBinder:
    """One armature for an assembled character, posed from the template skeleton's
    OWN standing rest -- not from the part meshes' bind poses.

    A part mesh authored against a shared skeleton arrives on its own, in its
    authoring-local space (a 3ds-Max Biped exports Z-up), carrying a bind pose and
    a CRC32 name hash per bone but no armature and no standing pose. The template's
    Avatar carries both halves of what a shipped rig gets from its prefab: the
    ``crc32(path) -> path`` table (real leaf names + parent chain) AND the whole
    skeleton's world rest (m_AvatarSkeleton posed by the array avatar.py's
    _SKELETON_POSE_FIELD names, Unity Y-up -- which array that is, and why the
    other one shears a face apart, is stated there and only there). Each bone --
    skinned or purely structural (Root, Bip001) -- is
    placed at that world rest; each part mesh is bind-BAKED against it
    (``skinning.bake_bind_pose``: v_world = sum w * restWorld * bindpose * v_local)
    exactly as the prefab path bakes against its transform hierarchy, which is why
    the result stands instead of lying flat. The first mesh builds the rig; later
    parts EXTEND it. A hash the table does not name is reconstructed from the leaf
    names via skinning.resolve_bone_paths, or kept hash-named as a last resort.

    The rig is one armature OBJECT carrying the top-level root yaw; its bones stay
    in pure-C armature space. Generic: the caller supplies the avatar tables.

    The result is a first-class animation target, not a display-only rig: it carries
    the same Unity rig identity (and, when the caller hands one over, the same Avatar
    document) a shipped prefab's armature gets, so a clip attaches to an assembled
    character exactly as it does to a shipped one -- see stamp_rig."""

    def __init__(self, name, world_rests=None, paths=None, leaf_names=(), avatar_data=None):
        self.name = name
        self._leaves = [str(n) for n in (leaf_names or ()) if n]
        self._avatar_data = avatar_data
        self.armature = None
        self._world_rest = {}      # crc32(path) -> Unity world rest 4x4 (numpy), aligned
        self._path_by_hash = {}    # crc32(path) -> full path
        self._world_by_name = {}   # leaf name -> aligned world rest (for attachment)
        self._hash_to_bone = {}    # crc32(path) -> final (uniquified) bone name
        self.add_skeleton(world_rests, paths)

    def add_skeleton(self, world_rests, paths):
        """Merge one avatar's skeleton into the rig, ALIGNED onto what is already
        there. The template avatar brings the base standing skeleton; a part
        (face, ear) is often authored at its OWN origin, so its bones are shifted
        so the shared bone it hangs under (its attachment) lands on the standing
        skeleton's copy of that bone. setdefault keeps the first, authoritative
        rest for a bone a later part references again."""
        world_rests = world_rests or {}
        name_by_hash = {}
        for path in (paths or ()):
            if path:
                key = _crc32(str(path))
                self._path_by_hash.setdefault(key, str(path))
                name_by_hash.setdefault(key, str(path).rsplit("/", 1)[-1])
        offset = self._alignment(world_rests, name_by_hash)
        for key, rest in world_rests.items():
            aligned = offset @ np.asarray(rest, dtype=np.float64)
            self._world_rest.setdefault(key, aligned)
            name = name_by_hash.get(key)
            if name:
                self._world_by_name.setdefault(name, aligned)

    def _alignment(self, world_rests, name_by_hash):
        """The 4x4 that moves this part onto the shared skeleton: it lands the
        part's ATTACHMENT bone (the shared bone most of the part's own bones hang
        under -- e.g. a face rig hangs under Bip001_Head) onto the standing copy
        already on the rig. Identity for the base skeleton, or a part already
        authored in the standing frame."""
        if not self._world_by_name:
            return np.eye(4)
        part_by_name = {}
        for key in world_rests:
            name = name_by_hash.get(key)
            if name:
                part_by_name.setdefault(name, world_rests[key])
        counts = {}
        for key in world_rests:
            name = name_by_hash.get(key)
            path = self._path_by_hash.get(key)
            if not path or name in self._world_by_name:
                continue  # a bone already on the rig is not what THIS part hangs by
            segments = path.split("/")
            for cut in range(len(segments) - 1, 0, -1):
                ancestor = segments[cut - 1]
                if ancestor in self._world_by_name and ancestor in part_by_name:
                    counts[ancestor] = counts.get(ancestor, 0) + 1
                    break
        if not counts:
            return np.eye(4)
        attach = max(counts, key=counts.get)
        return (np.asarray(self._world_by_name[attach], dtype=np.float64)
                @ np.linalg.inv(np.asarray(part_by_name[attach], dtype=np.float64)))

    def bind(self, context, decoded):
        """Bind-bake ``decoded`` onto the standing rest and ensure every bone it
        references (plus their structural ancestors) exists on the rig, returning
        the ``(smr_bones, file_id_to_bone)`` pair build_mesh_object skins with:
        ``[{"fileID": i}]`` and ``{i: bone_name}``. (None, None) when the mesh
        carries no usable bind data (it is placed unskinned instead)."""
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

        smr_bones = [{"fileID": i} for i in range(len(keys))]
        # Bake mesh-local vertices to their standing WORLD positions against the
        # avatar rest -- the same bake the prefab path runs against its hierarchy.
        file_id_to_world = {i: self._world_rest[keys[i]]
                            for i in range(len(keys)) if keys[i] in self._world_rest}
        skinning.bake_bind_pose(decoded, smr_bones, file_id_to_world)
        self._grow(context, keys)

        file_id_to_bone = {i: self._hash_to_bone.get(keys[i], _bone_name_for(None, keys[i]))
                           for i in range(len(keys))}
        return smr_bones, file_id_to_bone

    def _grow(self, context, mesh_keys):
        """Add every not-yet-built bone this mesh needs -- its own bones that the
        avatar rests, plus the structural ancestors their paths imply -- each at
        its avatar world rest, conjugated into Blender armature space."""
        rooted = [key for key in mesh_keys if key in self._world_rest]
        needed = self._with_ancestors(rooted)
        new_keys = [key for key in needed
                    if key not in self._hash_to_bone and key in self._world_rest]
        if not new_keys:
            return

        if self.armature is None:
            data = new_armature_data(self.name)
            self.armature = bpy.data.objects.new(self.name, data)
            context.scene.collection.objects.link(self.armature)
            # A rebuilt skeleton is a top-level import object: the root yaw rides
            # on the object; its bones stay in pure-C armature space.
            self.armature.matrix_world = coordinate.root_matrix()
            stamp_avatar(self.armature, self._avatar_data)

        previous = context.view_layer.objects.active
        context.view_layer.objects.active = self.armature
        bpy.ops.object.mode_set(mode="EDIT")
        edit_bones = self.armature.data.edit_bones

        # Shallow paths first, so a child's parent bone already exists.
        ordered = sorted(new_keys, key=self._depth)
        created = []
        for key in ordered:
            path = self._path_by_hash.get(key)
            bone_name = _bone_name_for(path, key)
            # A bone this name already exists (an earlier part's copy of a shared
            # bone) IS that joint -- reuse it, so a part rebinds onto the shared
            # skeleton instead of duplicating it.
            existing = edit_bones.get(bone_name)
            if existing is not None:
                self._hash_to_bone[key] = existing.name
                continue
            eb = edit_bones.new(bone_name)
            eb.head = (0.0, 0.0, 0.0)
            eb.tail = (0.0, _DEFAULT_BONE_LENGTH, 0.0)
            eb.matrix = coordinate.convert_matrix(self._world_rest[key])
            eb.length = _DEFAULT_BONE_LENGTH
            self._hash_to_bone[key] = eb.name  # Blender may uniquify
            created.append(key)

        for key in created:
            path = self._path_by_hash.get(key)
            parent = self._parent_bone(path) if path else None
            if parent is not None and parent in edit_bones:
                edit_bones[self._hash_to_bone[key]].parent = edit_bones[parent]

        # Cosmetic length only (never direction -- that would rotate the rest and
        # break a later animation): reach toward the nearest child head.
        for key in created:
            eb = edit_bones.get(self._hash_to_bone[key])
            if eb is None:
                continue
            distances = [(eb.head - child.head).length for child in eb.children]
            near = min((d for d in distances if d > 1e-5), default=0.0)
            if near:
                eb.length = max(min(near, 0.3), 0.005)

        bpy.ops.object.mode_set(mode="OBJECT")
        context.view_layer.objects.active = previous
        # Stamped after every growth, not once by the caller: a part that extends
        # the rig extends its identity too, and an assembler that forgot the final
        # call would leave a rig no animation can bind to.
        self._stamp_rig()

    def _stamp_rig(self):
        """The Unity rig identity for what is on the rig right now: per bone, the
        transform path it IS and its Unity-space LOCAL rest.

        ``local`` is relative to the bone's PARENT ON THIS RIG, which is what a
        clip's local TRS is relative to and what build_action's delta composes
        along. The alignment a part is merged under cancels out of it: a part's
        own bones and the shared bone it hangs by are offset by the same matrix."""
        if self.armature is None:
            return
        stamped = {}
        for key, bone_name in self._hash_to_bone.items():
            path = self._path_by_hash.get(key)
            world = self._world_rest.get(key)
            if not path or world is None:
                continue
            parent = self._parent_rest(path)
            local = world if parent is None else np.linalg.inv(parent) @ world
            stamped[path] = {"bone": bone_name,
                             "local": [float(value) for row in local for value in row]}
        stamp_rig(self.armature, stamped)

    def _parent_rest(self, path):
        """The world rest of the bone _parent_bone parents ``path`` to, or None when
        it is a root of this rig."""
        segments = path.split("/")
        for cut in range(len(segments) - 1, 0, -1):
            key = _crc32("/".join(segments[:cut]))
            if key in self._hash_to_bone:
                return self._world_rest.get(key)
        return None

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
                self._path_by_hash.setdefault(_crc32(ancestor), ancestor)
                needed.add(_crc32(ancestor))
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


class _BoneRest:
    """One source bone as plain VALUES -- its name, its ancestors' names nearest-first,
    and its rest matrix. What survives an operator call; a bpy Bone does not."""

    __slots__ = ("name", "ancestors", "rest")

    def __init__(self, name, ancestors, rest):
        self.name = name
        self.ancestors = ancestors
        self.rest = rest


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
    # READ EVERYTHING OFF THE SOURCE BONES NOW. _graft_bones enters and leaves edit
    # mode, and a bpy Bone is a pointer into data that a mode switch rebuilds -- the
    # wrappers survive the call as Python objects but no longer point at a bone, so
    # reading .name afterwards hands back whatever occupies that memory and raises
    # UnicodeDecodeError on a different garbage byte every run. Plain values cross;
    # references do not.
    grafts = [_BoneRest(bone.name,
                        [ancestor.name for ancestor in bone.parent_recursive],
                        bone.matrix_local.copy())
              for bone in source.data.bones
              if bone.name not in target_names and subtree_has_weight(bone)]
    if grafts:
        _graft_bones(context, target, source, grafts)
    _merge_identity(target, source, {graft.name for graft in grafts})

    for obj in driven:
        world = obj.matrix_world.copy()
        for modifier in obj.modifiers:
            if modifier.type == "ARMATURE" and modifier.object is source:
                modifier.object = target
        obj.parent = target
        obj.matrix_world = world

    _remove_armature_and_empty_parents(source)


def _merge_identity(target, source, added_names):
    """Fold the source rig's Unity identity into the target's as its bones come
    across: the paths of the bones actually grafted (a shared joint keeps the
    target's own entry, being the same joint), and the Avatar document when the
    source carries one and the target does not.

    The source object is deleted at the end of the graft, so an identity left on
    it is an identity destroyed -- and a clip that binds by the grafted part's own
    transform path would find nothing on the rig that now owns those bones."""
    source_paths = read_rig(source)
    if source_paths:
        merged = read_rig(target) or {}
        for path, entry in source_paths.items():
            if entry.get("bone") in added_names and path not in merged:
                merged[path] = entry
        stamp_rig(target, merged)
    if read_avatar_json(target) is None:
        raw = read_avatar_json(source)
        if raw is not None:
            target[UNITY_AVATAR_PROP] = raw
    if not read_game(target):
        stamp_game(target, read_game(source))


def _graft_bones(context, target, source, grafts):
    """Add ``grafts`` (see _BoneRest) to ``target`` at their current world rest,
    parented to the nearest ancestor already present on target.

    Takes VALUES, not source Bones: this enters edit mode, which is exactly when a
    Bone reference stops being one (see graft_armature)."""
    source_world = source.matrix_world
    target_inverse = target.matrix_world.inverted_safe()
    previous = context.view_layer.objects.active
    context.view_layer.objects.active = target
    bpy.ops.object.mode_set(mode="EDIT")
    edit_bones = target.data.edit_bones

    for graft in sorted(grafts, key=lambda entry: len(entry.ancestors)):
        eb = edit_bones.new(graft.name)
        eb.head = (0.0, 0.0, 0.0)
        eb.tail = (0.0, _DEFAULT_BONE_LENGTH, 0.0)
        eb.matrix = target_inverse @ (source_world @ graft.rest)
        eb.length = _DEFAULT_BONE_LENGTH
        for ancestor in graft.ancestors:
            if ancestor in edit_bones:
                eb.parent = edit_bones[ancestor]
                break

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
