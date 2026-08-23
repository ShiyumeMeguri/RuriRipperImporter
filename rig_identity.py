"""What a rig IS in Unity's own terms, whatever Blender happens to call it today.

Three vocabularies meet on every rig this add-on builds, and until they are told
apart a rename silently breaks half the features:

    transform path    ``Root/Bip001/.../browLf01Joint`` -- what an AnimationClip
                      binds its curves by, and what this add-on's rest table keys on.
    Unity bone name   ``browLf01Joint``, that path's leaf -- what a game states a
                      face table, a physics chain or any other per-bone table in,
                      because those tables carry names and no paths at all.
    Blender bone      ``Brow01Joint_L``, or whatever the user renamed it to.

Animation already survived a rename, because a clip speaks PATHS and the stamp
translated them. Everything keyed on the Unity BONE NAME did not, because nothing
translated that direction -- it only ever worked while a rig was still called what
Unity called it, and a renamed rig bound zero facial ctrls with no error anywhere.

So identity lives ON THE BONE. ``bone["ruri_unity_path"]`` is the transform that
bone IS, and a custom property rides the datablock: Blender's rename rewrites the
name, the vertex groups, the fcurve paths and the constraint subtargets, never a
property. The armature object keeps only the per-path REST matrix, which is
geometry rather than identity and carries no name at all -- so there is exactly
one place a bone's identity is written down, and no rename can reach it.

THE one reader and THE one writer both live here. A second copy of either
encoding is how a rig ends up carrying a shape the other half does not accept.
"""

from __future__ import annotations

import json

from mathutils import Matrix

# The transform path a bone IS, stamped on the BONE (armature data, not the pose
# bone: the pose is per-object, the identity is per-skeleton).
BONE_PATH_PROP = "ruri_unity_path"

# The rig's Unity-space LOCAL rest per transform path, stamped on the armature
# OBJECT: {"paths": {path: {"local": [16 floats, row-major]}}}. Row-major and
# flat because that is exactly the node.local build_action's rest math consumes.
REST_PROP = "ruri_unity_rig"

# Stamped on a bone whose rest AXES are load-bearing: something outside this file
# states per-bone deltas in them, so re-aiming the bone silently invalidates that
# data instead of breaking anything a tool could notice.
#
# A game's facial table is the case that forced it. Those deltas are 97.6%
# TRANSLATION, written in each bone's own axes, so a bone re-aimed by a rigging
# convenience pass (connecting bones to their children, aligning rolls) plays every
# expression along the wrong vectors -- and nothing errors, the face is just wrong.
# The name is deliberately game-blind: any tool that re-aims bones can honour it
# without knowing who wrote it or why (ShiyumeBlender's auto_bone_orientation does).
LOCKED_ORIENTATION_PROP = "ruri_locked_orientation"

# The key a builder hands ``stamp`` the bone name under. It exists only to FIND
# the bone being described and is never written to the file, which is the whole
# point: no name of any kind reaches the identity, so no rename can invalidate it.
_BONE_KEY = "bone"


class _Node:
    """The two fields animation_builder.build_action reads off ``maps["nodes"]``
    values: the Unity transform path, and the Unity-space LOCAL rest matrix."""

    __slots__ = ("path", "local")

    def __init__(self, path, local):
        self.path = path
        self.local = local


class RigIdentity:
    """One rig's join between the three vocabularies, resolved once.

    Built from the bone properties (identity) plus the object's rest table
    (geometry). Every lookup a feature needs goes through here, so "which bone of
    this rig is the game talking about" has one answer and one implementation.
    """

    __slots__ = ("armature", "bone_to_path", "path_to_bone", "rest_by_path",
                 "_unity_to_bone", "ambiguous_unity_names")

    def __init__(self, armature, bone_to_path, rest_by_path):
        self.armature = armature
        self.bone_to_path = bone_to_path
        self.path_to_bone = {path: bone for bone, path in bone_to_path.items()}
        self.rest_by_path = rest_by_path
        self._unity_to_bone = {}
        # A game's per-bone table names a LEAF, so two transforms sharing one leaf
        # are a bone the game itself cannot address unambiguously. Resolve it
        # deterministically (first by path order) and keep the collisions, so a
        # panel can say which bones are ambiguous instead of quietly picking one.
        self.ambiguous_unity_names = set()
        for path in sorted(bone_to_path.values()):
            leaf = unity_name_of_path(path)
            if leaf in self._unity_to_bone:
                self.ambiguous_unity_names.add(leaf)
                continue
            self._unity_to_bone[leaf] = self.path_to_bone[path]

    # -- the three directions -------------------------------------------------

    def bone_of(self, unity_name):
        """This rig's bone for a Unity BONE NAME (a face table's vocabulary), or
        None when this rig has no such transform."""
        return self._unity_to_bone.get(unity_name)

    def bone_for_path(self, path):
        return self.path_to_bone.get(path)

    def path_of(self, bone_name):
        return self.bone_to_path.get(bone_name)

    def unity_name_of(self, bone_name):
        path = self.bone_to_path.get(bone_name)
        return unity_name_of_path(path) if path else None

    def unity_names(self):
        return set(self._unity_to_bone)

    def to_rig(self, unity_names):
        """[(unity name, this rig's bone)] for the names this rig actually has,
        in the given order. What a caller iterates instead of ``pose.bones.get``
        on a name the game wrote."""
        pairs = []
        for name in unity_names:
            bone = self._unity_to_bone.get(name)
            if bone is not None:
                pairs.append((name, bone))
        return pairs

    # -- geometry -------------------------------------------------------------

    def rest_of_path(self, path):
        """The Unity-space LOCAL rest of a transform, or None when the rig states
        none for it (a bone grafted on after the rest table was written)."""
        return self.rest_by_path.get(path)

    def rest_of(self, bone_name):
        path = self.bone_to_path.get(bone_name)
        return self.rest_by_path.get(path) if path else None

    def rest_by_bone(self):
        rests = {}
        for bone, path in self.bone_to_path.items():
            rest = self.rest_by_path.get(path)
            if rest is not None:
                rests[bone] = rest
        return rests

    def rest_by_unity_name(self):
        rests = {}
        for unity_name, bone in self._unity_to_bone.items():
            rest = self.rest_by_path.get(self.bone_to_path[bone])
            if rest is not None:
                rests[unity_name] = rest
        return rests

    # -- what the clip importer consumes --------------------------------------

    def maps(self):
        """The maps dict build_action keys transform curves through, or None when
        no bone of this rig carries both an identity and a rest."""
        nodes = {}
        path_to_bone = {}
        for index, (bone, path) in enumerate(sorted(self.bone_to_path.items())):
            rest = self.rest_by_path.get(path)
            if rest is None:
                continue
            nodes[index] = _Node(path, rest)
            path_to_bone[path] = bone
        if not path_to_bone:
            return None
        return {
            "nodes": nodes,
            "roots": [],
            "file_id_to_bone": {},
            "path_to_bone": path_to_bone,
            "file_id_to_world": {},
        }

    def __len__(self):
        return len(self.bone_to_path)

    def __repr__(self):
        return "<RigIdentity {0}: {1} bone(s), {2} rest(s)>".format(
            self.armature.name if self.armature else "?",
            len(self.bone_to_path), len(self.rest_by_path))


def unity_name_of_path(path):
    return path.rsplit("/", 1)[-1] if path else ""


# ── reading ───────────────────────────────────────────────────────────────────

def bone_paths(arm_obj):
    """{bone name: transform path} straight off the bones -- the identity alone,
    with no rest table parsed.

    Cheap enough for a draw callback (a walk over bones, no JSON), which is why
    "is this rig bound, and how much of it" asks this rather than ``of``."""
    if arm_obj is None or arm_obj.type != "ARMATURE":
        return {}
    paths = {}
    for bone in arm_obj.data.bones:
        path = bone.get(BONE_PATH_PROP)
        if path:
            paths[bone.name] = str(path)
    return paths


def rest_table(arm_obj):
    """{transform path: Unity-space local rest} off the armature object, or {}."""
    if arm_obj is None:
        return {}
    raw = arm_obj.get(REST_PROP)
    if not raw:
        return {}
    try:
        entries = json.loads(str(raw))["paths"]
    except (ValueError, KeyError, TypeError):
        return {}
    if not isinstance(entries, dict):
        return {}
    rests = {}
    for path, entry in entries.items():
        flat = entry.get("local") if isinstance(entry, dict) else None
        if flat and len(flat) == 16:
            rests[path] = Matrix((flat[0:4], flat[4:8], flat[8:12], flat[12:16]))
    return rests


def of(arm_obj):
    """This rig's identity, or None when its bones carry none.

    None means exactly one thing -- these bones were never built by this add-on --
    and every caller words it that way, never as "nothing here to drive"."""
    paths = bone_paths(arm_obj)
    if not paths:
        return None
    return RigIdentity(arm_obj, paths, rest_table(arm_obj))


def bone_for_unity_name(arm_obj, unity_name):
    """This rig's bone for a Unity BONE NAME, or "" -- the whole join in one call.

    The flat shape exists for callers that are not Python neighbours: a generated
    shading stack states this translation as a MODULE + FUNCTION NAME in its recipe
    and reaches it by name, so what it binds to has to be a plain function over
    plain arguments rather than an object protocol."""
    identity = of(arm_obj)
    return (identity.bone_of(unity_name) or "") if identity is not None else ""


def unity_name_for_bone(arm_obj, bone_name):
    """The other direction: what Unity calls the bone this rig currently calls
    ``bone_name``, or "" when that bone carries no identity. Whoever records a
    bone reference records THIS, never the Blender name -- a name is what a rename
    takes away."""
    identity = of(arm_obj)
    return (identity.unity_name_of(bone_name) or "") if identity is not None else ""


# ── writing ───────────────────────────────────────────────────────────────────

def stamp(arm_obj, paths):
    """THE writer: record ``{transform path: {"bone", "local"}}`` as this rig's
    identity, splitting it into the two halves that survive a rename.

    Takes the builders' own shape unchanged -- every path they resolved, the bone
    they made for it, and its Unity-space local rest -- and writes the bone half
    onto the bones and the rest half onto the object. Callers must be in OBJECT
    mode: ``data.bones`` is not the live collection while an armature is in edit
    mode."""
    if arm_obj is None:
        return
    bones = arm_obj.data.bones
    rests = {}
    for path, entry in paths.items():
        bone_name = entry.get(_BONE_KEY)
        bone = bones.get(bone_name) if bone_name else None
        if bone is not None:
            bone[BONE_PATH_PROP] = path
        flat = entry.get("local")
        if flat and len(flat) == 16:
            rests[path] = {"local": [float(value) for value in flat]}
    arm_obj[REST_PROP] = json.dumps({"paths": rests}, separators=(",", ":"))


def lock_orientation(arm_obj, bone_names):
    """Mark these bones' rest axes as load-bearing (LOCKED_ORIENTATION_PROP).

    Called by whoever KNOWS -- the side holding the per-bone table that states
    deltas in those axes -- because nothing about a bone itself says whether its
    orientation is a rigging convenience or part of a contract. Returns how many
    bones were marked."""
    if arm_obj is None:
        return 0
    marked = 0
    for name in bone_names:
        bone = arm_obj.data.bones.get(name)
        if bone is not None and not bone.get(LOCKED_ORIENTATION_PROP):
            bone[LOCKED_ORIENTATION_PROP] = True
            marked += 1
    return marked


def orientation_locked(arm_obj):
    """The bones whose rest axes are marked load-bearing."""
    if arm_obj is None or arm_obj.type != "ARMATURE":
        return set()
    return {bone.name for bone in arm_obj.data.bones if bone.get(LOCKED_ORIENTATION_PROP)}


def merge(target, source, added_bone_names):
    """Fold ``source``'s identity into ``target``'s as its bones come across.

    The bones themselves carry their own identity, so only the REST table has to
    move: a path the target already states keeps the target's rest (the same joint,
    at the rig it is actually on), and everything else the grafted bones need comes
    over with them."""
    if target is None or source is None:
        return
    source_paths = bone_paths(source)
    wanted = {path for bone, path in source_paths.items() if bone in added_bone_names}
    if not wanted:
        return
    source_rests = rest_table(source)
    merged = {path: {"local": [value for row in matrix for value in row]}
              for path, matrix in rest_table(target).items()}
    for path in wanted:
        matrix = source_rests.get(path)
        if matrix is not None and path not in merged:
            merged[path] = {"local": [value for row in matrix for value in row]}
    target[REST_PROP] = json.dumps({"paths": merged}, separators=(",", ":"))
