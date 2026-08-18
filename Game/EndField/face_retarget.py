"""Wire one character's baked facial performance onto the rig in the scene.

A UI or cutscene clip carries the face IN THE BONE TRACKS -- an animator keyed it, and
no SkeletalMorph asset exists for it (measured: of 581 of one character's body clips,
only the 20 battle ones have a companion morph asset). Playing such a clip on a
different character therefore cannot go through the game's own asset join; the
performance has to be READ OFF the geometry and re-stated in the destination's own
expression vocabulary.

**Nothing here decides anything.** Which face tables exist, which one the clip was
authored on, which Euler convention they are written in, which named expressions the
performance IS and at what strength -- all of it is measured by the hook
(``EndfieldFaceRetarget``), against the game's own data read straight out of the cabmap.

What lives here is only what Blender alone knows and only Blender can do:

* the bones the rig in the scene actually has, with their Unity-space rest transforms;
* the sampled clip curves, as the float32 the hook reads;
* keying the answer back onto pose bones.
"""

from __future__ import annotations

import json
import time as _time

import bpy
import numpy as np

from ... import coordinate
from ...RuriRipperPyBridge.session import cabmap_state


class FaceRetargetError(RuntimeError):
    """A facial retarget that cannot proceed, worded for the panel that shows it."""


def _rig_bones(armature, maps):
    """Every bone of this rig that carries a Unity-space rest local, as the hook's wire
    shape. The rest is the rig identity stamped at import time -- the same matrices the
    clip importer keys transform curves through -- so a rig this add-on did not build
    states none and the retarget says so rather than posing a face wrongly."""
    rest_by_bone = {}
    path_to_bone = maps.get("path_to_bone") or {}
    for node in (maps.get("nodes") or {}).values():
        bone = path_to_bone.get(getattr(node, "path", ""))
        if bone and bone not in rest_by_bone and bone in armature.pose.bones:
            rest_by_bone[bone] = node.local
    return [{"name": name, "rest": [float(value) for row in matrix for value in row]}
            for name, matrix in sorted(rest_by_bone.items())]


def _sample_performance(clip, bone_order, path_to_bone, times):
    """The clip's own local transforms for ``bone_order``, as the float32 the hook reads.
    A bone the clip never keys rides its own rest, which is what the game does with it
    too."""
    channels = {}
    for kind, channel_list in (("rot", clip.rotations), ("pos", clip.positions),
                               ("scale", clip.scales)):
        for channel in channel_list:
            bone = path_to_bone.get(channel.path)
            if bone:
                channels.setdefault(bone, {})[kind] = channel

    samples = np.zeros((len(times), len(bone_order), 10), dtype=np.float32)
    samples[:, :, 6] = 1.0
    samples[:, :, 7:10] = 1.0
    for index, bone in enumerate(bone_order):
        tracks = channels.get(bone)
        if not tracks:
            continue
        position = tracks.get("pos")
        if position is not None:
            samples[:, index, 0:3] = position.sample(times)
        rotation = tracks.get("rot")
        if rotation is not None:
            samples[:, index, 3:7] = rotation.sample(times)
        scale = tracks.get("scale")
        if scale is not None:
            samples[:, index, 7:10] = scale.sample(times)
    return samples


def _clip_bones(clip, path_to_bone):
    """The bones this clip animates, in a stable order."""
    bones = set()
    for channels in clip.transform_channel_lists():
        for channel in channels:
            bone = path_to_bone.get(channel.path)
            if bone:
                bones.add(bone)
    return sorted(bones)


# ── what the host's clip loader calls ─────────────────────────────────────────

def provide(context, armature, clip, options, into=None):
    """Play this clip's face on ``armature``, as the host's clip-loading path asks for it
    (see ``Game.GameModule.face_retarget``). Returns a one-line report, or None when there
    is nothing facial in the clip.

    ``clip`` is anchored to the rig it was AUTHORED on (see
    ``cross_game_retarget.source_anchored_clip``), so its curve paths name that rig's own
    bones; the hook measures which character that is. ``into`` is that clip's own
    (action, slot) to write the face INTO -- an object plays one action, so a face given
    its own would replace the body it came with."""
    from . import character_panel, roster_panel
    from ... import prefab_importer

    bridge = cabmap_state.BRIDGE
    if bridge is None or not bridge.has_map:
        raise FaceRetargetError("No cabmap session -- load a cabmap first.")

    maps = prefab_importer.maps_from_stamped_armature(armature)
    if not maps:
        raise FaceRetargetError(
            "'{0}' carries no Unity rig identity, so it has no rest pose to state -- import "
            "the character through this add-on once.".format(armature.name))
    bones = _rig_bones(armature, maps)
    if not bones:
        raise FaceRetargetError(
            "'{0}' has no bone carrying a Unity rest transform.".format(armature.name))

    path_to_bone = {channel.path: channel.path.rsplit("/", 1)[-1]
                    for channels in clip.transform_channel_lists()
                    for channel in channels if channel.path}
    clip_bones = _clip_bones(clip, path_to_bone)
    if not clip_bones:
        return None

    rate = float(clip.sample_rate or context.scene.render.fps or 60.0)
    duration = float(clip.max_time())
    frame_count = max(2, int(round(duration * rate)) + 1)
    times = np.arange(frame_count, dtype=np.float64) / rate
    samples = _sample_performance(clip, clip_bones, path_to_bone, times)

    # Which face this rig wears is the GAME's own declaration, never the rig's name: it
    # routinely gives an entity one name and its face table a completely different one.
    template = roster_panel.npc_template(armature.name)
    request = {
        "declaration": roster_panel.declared_face_morph(template) if template else "",
        "tagId": roster_panel.character_tag(
            context.scene.ruri_character.character_token or armature.name),
        "bones": bones,
        "clipBones": clip_bones,
        "frameCount": frame_count,
        "sampleRate": rate,
    }

    started = _time.perf_counter()
    try:
        report, poses = bridge.solve_face_retarget(json.dumps(request), samples.tobytes())
        answer = json.loads(report)
    except Exception as exc:
        raise FaceRetargetError(str(exc)) from exc
    print("[face] {0}: {1} frame(s) · {2} shared bone(s) of '{3}' (next '{4}' at {5:.3f}) · "
          "{6} candidate(s) · euler {7} · rest fit {8:.2f}deg · rest agreement {9:.1f}/{10:.1f}deg "
          "· {11} layer(s) · similarity {12:.1%} · {13:.1f}s".format(
              clip.name, frame_count, answer["sharedBones"], answer["source"],
              answer["runnerUp"], answer["runnerUpScore"], answer["candidates"],
              answer["eulerOrder"], answer["sourceRestFitDegrees"],
              answer["restAgreementMedian"], answer["restAgreementWorst"],
              answer["layers"], answer["similarity"], _time.perf_counter() - started),
          flush=True)

    posed_bones = answer["bones"]
    if not posed_bones:
        return None
    _key_poses(armature, maps, posed_bones, answer["poseFrames"], poses, rate, clip.name, into)

    named = [segment["name"] for segment in answer["timeline"]]
    distinct = sorted(set(named))
    return ("face: read '{0}' ({1:.0%} explained, matched at {2:.3f}) -> {3} plays {4} "
            "expression(s) ({5} distinct) over {6} layer(s) through its own table · "
            "{7} bone(s) posed by {8} ctrl(s){9}".format(
                answer["source"], answer["similarity"], answer["sourceScore"],
                armature.name, len(named), len(distinct), answer["layers"],
                len(posed_bones), answer["drivenCtrls"],
                " · " + ", ".join(distinct[:4]) if distinct else ""))


def _key_poses(armature, maps, bones, frame_count, payload, rate, clip_name, into):
    """Key the composed poses onto the rig.

    Exactly the path an imported clip's own transform curves take -- the same rest
    conjugation and the same writer (``animation_builder``) -- because that is what these
    are: per-frame Unity-space locals for named bones. The face goes INTO the clip's own
    action, replacing the facial channels the body build keyed there from the source
    character; an object plays one action, so a second one beside it would not play."""
    from ... import animation_builder

    rest_by_bone = {}
    path_to_bone = maps.get("path_to_bone") or {}
    for node in (maps.get("nodes") or {}).values():
        bone = path_to_bone.get(getattr(node, "path", ""))
        if bone:
            rest_by_bone.setdefault(bone, node.local)

    values = np.frombuffer(payload, dtype=np.float32).reshape(frame_count, len(bones), 10)
    frames = np.arange(frame_count, dtype=np.float64)
    conversion = coordinate.conversion_matrix()

    if into is not None:
        action, slot = into
        fcurves = animation_builder.channels_of(action, slot)
        replaced = animation_builder.release_bones(armature, fcurves, bones)
        print("[face] {0}: replaced {1} source facial channel(s) over {2} posed bone(s)"
              .format(clip_name, replaced, len(bones)), flush=True)
    else:
        action = bpy.data.actions.new("RT_{0}".format(clip_name))
        if hasattr(action, "use_fake_user"):
            action.use_fake_user = True
        fcurves, slot = animation_builder._prepare_channels(action, action.name, "OBJECT")

    for index, bone in enumerate(bones):
        rest = rest_by_bone.get(bone)
        if rest is None or bone not in armature.pose.bones:
            continue
        locations, quaternions, scales = animation_builder._conjugated_pose_arrays(
            values[:, index, 0:3].astype(np.float64),
            values[:, index, (6, 3, 4, 5)].astype(np.float64),
            values[:, index, 7:10].astype(np.float64),
            rest.inverted_safe(), conversion)
        animation_builder._write_bone_fcurves(fcurves, bone, frames,
                                              locations, quaternions, scales)

    if into is None:
        animation_builder.adopt_action(armature, action, slot, frame_range=False)
