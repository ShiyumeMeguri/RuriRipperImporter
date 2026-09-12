"""A rig's rest pose out, per-frame bone poses in.

Two verbs a host owes anything that solves a performance somewhere else and hands
the answer back: what this rig's bones ARE (their rest locals, under the source's
own names, which is the vocabulary such a solve is stated in), and how to write a
block of per-frame locals onto them.

Nothing here knows which game asked or what it solved. The names crossing in are
the source's; translating them to this rig's is this file's whole contribution to
the join, because the rig's identity -- what Unity transform each of its bones IS
-- is stamped on the bones by the importer and readable only here.
"""

from __future__ import annotations

import bpy
import numpy as np

from . import animation_builder, coordinate, rig_identity


def rest(context, rig):
    """``[{"name", "rest"}]`` -- every bone of this rig that carries a source-space
    rest local, in the SOURCE's bone names, sorted.

    Stated in the source's names, not this rig's: a solve is a join between name
    sets the game wrote, and a rig whose bones the user renamed would share exactly
    zero of them while the geometry is perfectly present. The matrices are this
    rig's own, so what crosses is this rig under the game's vocabulary."""
    identity = rig_identity.of(rig)
    if identity is None:
        return []
    found = {}
    for bone_name, matrix in identity.rest_by_bone().items():
        source_name = identity.unity_name_of(bone_name)
        if source_name and source_name not in found and bone_name in rig.pose.bones:
            found[source_name] = matrix
    return [{"name": name, "rest": [float(value) for row in matrix for value in row]}
            for name, matrix in sorted(found.items())]


def key(context, rig, source_names, frame_count, payload, name, into=None):
    """Key a block of per-frame locals onto this rig's bones.

    Exactly the path an imported clip's own transform curves take -- the same rest
    conjugation and the same writer -- because that is what these are: per-frame
    source-space locals for named bones.

    ``source_names`` come back in the vocabulary the request went out in, so this is
    where the answer lands back on THIS rig's bones: the payload rows stay indexed by
    the solver's order, and only the name each row is written under is translated.
    ``into`` is an existing (action, slot) whose channels for these bones this
    replaces -- an object plays one action, so a second one beside it would not
    play."""
    identity = rig_identity.of(rig)
    if identity is None or not source_names:
        return 0
    values = np.frombuffer(payload, dtype=np.float32).reshape(
        frame_count, len(source_names), 10)
    frames = np.arange(frame_count, dtype=np.float64)
    conversion = coordinate.conversion_matrix()

    posed = []
    for index, source_name in enumerate(source_names):
        bone = identity.bone_of(source_name)
        bone_rest = identity.rest_of(bone) if bone else None
        if bone is not None and bone_rest is not None and bone in rig.pose.bones:
            posed.append((index, bone, bone_rest))
    if not posed:
        return 0

    if into is not None:
        action, slot = into
        fcurves = animation_builder.channels_of(action, slot)
        replaced = animation_builder.release_bones(
            rig, fcurves, [bone for _index, bone, _rest in posed])
        print("[face] {0}: replaced {1} source facial channel(s) over {2} posed "
              "bone(s)".format(name, replaced, len(posed)), flush=True)
    else:
        action = bpy.data.actions.new("RT_{0}".format(name))
        if hasattr(action, "use_fake_user"):
            action.use_fake_user = True
        fcurves, slot = animation_builder._prepare_channels(action, action.name, "OBJECT")

    for index, bone, bone_rest in posed:
        locations, quaternions, scales = animation_builder._conjugated_pose_arrays(
            values[:, index, 0:3].astype(np.float64),
            values[:, index, (6, 3, 4, 5)].astype(np.float64),
            values[:, index, 7:10].astype(np.float64),
            bone_rest.inverted_safe(), conversion)
        animation_builder._write_bone_fcurves(fcurves, bone, frames,
                                              locations, quaternions, scales)

    if into is None:
        animation_builder.adopt_action(rig, action, slot, frame_range=False)
    return len(posed)
