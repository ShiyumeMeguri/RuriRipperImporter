"""EndField-specific limb IK: live Blender constraints driven by the clip's
own IK target bones.

EndField's rig carries animated IK target bones -- the game runtime's IK
interface (no MonoBehaviour config exists; the logic lives in game code):

    IK_Foot_L_001 / IK_Foot_R_001   foot targets   (parented to Root)
    IK_Knee_L_001 / IK_Knee_R_001   knee pole hints (parented to Root,
                                     rest 0.30m straight in front of knee)
    IK_Hand_L_001 / IK_Hand_R_001   hand targets   (parented to IK_Root)

At REST every target is EXACTLY coincident with its FK bone (zero position
delta, identity rotation offset), so the runtime contract is simply "end
bone world pose = IK target world pose" -- whenever the runtime engages it.

Measured against the real game data (with the fork muscle-attribute remap in
place, i.e. CORRECT muscle FK -- earlier "the FK is garbage" conclusions all
predate that fix and are void):

  * the muscle FK is self-consistent to millimeters at authored contacts:
    support feet track their IK targets within 4-22mm across every locomotion
    clip, and sprint_stepon's hand-plant frames put the target 1-8mm from the
    FK hand -- those near-coincidences ARE the authored pins;
  * loose followers must not engage: locomotion hand targets hover 10-17cm
    from the FK hands (tracking, not pinning) and battle hand targets sit
    0.4-0.8m away (weapon-space anchors, never wrist pins).

So the import maps the runtime model 1:1 onto Blender's native machinery
instead of baking a solve into the FK curves:

  * the muscle FK curves stay PURE authored data (untouched, editable);
  * each chain gets a hidden rigid effector-helper bone (knee->ankle, IK
    axes locked; see _ensure_effector_helper for why Blender's tail-only
    effector forces one) carrying a standard two-bone IK constraint that
    rotates the real lower+upper bones (target = the clip's IK bone, no
    pole -- see the _CHAINS comment), plus a world-space Copy Rotation on
    the end bone (rest offset identity);
  * per-frame constraint INFLUENCE is baked from the target-vs-FK-end
    distance band below -- 1 at authored pins, 0 at loose followers, so a
    clip that never pins simply plays its FK while pinned contacts snap the
    residual quantization error, exactly like the runtime.

Constraints default to influence 0, so generic clips (and clips that do not
drive a chain's target at all) are bit-identical to plain FK playback.

Applied only to humanoid (muscle) clips on rigs that expose the convention
-- see prefab_importer.build_selected_animations for the gate.
"""

from __future__ import annotations

import re

import numpy as np

import bpy
from mathutils import Matrix, Quaternion, Vector

# (human upper, human lower, human end, target bone)
#
# NO pole targets, deliberately -- not even the rig's own IK_Knee hints. The
# solver only ever engages when the target is within 5cm of the FK end, so
# each frame it perturbs minimally from the (correct) keyed FK pose and the
# bend plane follows FK by construction. A pole OVERRIDES that with its own
# plane, and whenever the hint drifts near the root->target axis the
# projection side is ill-conditioned: measured on battle_attack_04, the
# poled left knee snapped straight backwards between frames 126->127 while
# FK itself was clean. The old analytic solver needed poles (it re-solved
# limbs across half-meter gaps from garbage FK) and carried a temporal
# tracker to bridge exactly these degeneracies; live constraints have no
# cross-frame memory, and with trustworthy FK they don't need one.
_CHAINS = (
    ("LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "IK_Foot_L_001"),
    ("RightUpperLeg", "RightLowerLeg", "RightFoot", "IK_Foot_R_001"),
    ("LeftUpperArm", "LeftLowerArm", "LeftHand", "IK_Hand_L_001"),
    ("RightUpperArm", "RightLowerArm", "RightHand", "IK_Hand_R_001"),
)

# Per-frame IK influence from target-vs-FK-end distance: full IK at/below
# _IK_FULL_DIST, pure muscle FK at/above _IK_OFF_DIST, linear in between.
# 2cm/5cm separates the two measured populations with a wide margin on every
# sampled clip: authored pins are <=2.2cm, loose followers >=7cm (see the
# module doc). Arms without a pole bone stay FK-adjacent by construction:
# the solver only ever engages when the target is within 5cm of the FK hand,
# and Blender's IK perturbs minimally from the keyframed pose.
_IK_FULL_DIST = 0.02
_IK_OFF_DIST = 0.05

_REQUIRED_TARGETS = ("IK_Foot_L_001", "IK_Foot_R_001", "IK_Hand_L_001", "IK_Hand_R_001")

_IK_CONSTRAINT = "Ruri IK"
_ROT_CONSTRAINT = "Ruri IK Rotation"

_BONE_PATH_RE = re.compile(r'pose\.bones\["(.+?)"\]\.(\w+)')


def detect_rig(arm_obj):
    """True when the armature exposes EndField's IK-bone convention."""
    bones = arm_obj.data.bones
    return all(name in bones for name in _REQUIRED_TARGETS)


class _FcurveSampler:
    """Per-bone basis sampling straight off an action's baked fcurves -- no
    scene/depsgraph evaluation, so the influence bake is exact against what
    was imported and orders of magnitude faster per frame."""

    def __init__(self, channelbag):
        self._by_key = {}
        for fc in channelbag.fcurves:
            match = _BONE_PATH_RE.match(fc.data_path)
            if match:
                self._by_key[(match.group(1), match.group(2), fc.array_index)] = fc

    def basis(self, bone_name, frame):
        def read(prop, index, default):
            fc = self._by_key.get((bone_name, prop, index))
            return fc.evaluate(frame) if fc is not None else default

        loc = Vector((read("location", 0, 0.0), read("location", 1, 0.0), read("location", 2, 0.0)))
        quat = Quaternion((read("rotation_quaternion", 0, 1.0), read("rotation_quaternion", 1, 0.0),
                           read("rotation_quaternion", 2, 0.0), read("rotation_quaternion", 3, 0.0)))
        if quat.magnitude < 1e-8:
            quat = Quaternion((1.0, 0.0, 0.0, 0.0))
        scale = Vector((read("scale", 0, 1.0), read("scale", 1, 1.0), read("scale", 2, 1.0)))
        return Matrix.LocRotScale(loc, quat.normalized(), scale)

    def bone_animated(self, bone_name):
        """Whether this action carries ANY fcurve for the bone. For the IK
        target bones this is the per-clip IK enable switch: a clip that ships
        no curves for a target parks it at rest, and there is nothing to pin
        against -- that chain plays pure FK (influence stays 0)."""
        return any(key[0] == bone_name for key in self._by_key)


def _ancestors_first(arm_obj, names):
    """The given bones plus every ancestor, parents before children."""
    needed = set()
    for name in names:
        bone = arm_obj.data.bones.get(name)
        while bone is not None and bone.name not in needed:
            needed.add(bone.name)
            bone = bone.parent

    def depth(name):
        d, bone = 0, arm_obj.data.bones[name]
        while bone.parent is not None:
            d += 1
            bone = bone.parent
        return d

    return sorted(needed, key=depth)


def _ensure_effector_helper(arm_obj, lower, end):
    """A rigid effector-carrier bone for the chain, because Blender's IK
    effector is ALWAYS a chain bone's TAIL and the imported armature's tails
    are cosmetic fabrications (Unity ships joints only; the builder invents
    tail direction and caps length at 0.3m -- a calf's tail is nowhere near
    the ankle; measured: pinning it dragged the foot 0.55m off its target,
    and use_tail=False merely re-roots the chain one bone up rather than
    switching the effector to a head).

    The helper parents to the real lower bone, spans knee->ankle (a NEW bone
    owes nothing to the Unity axis contract, so it may aim at the joint), has
    all three IK axes locked (a rigid extension, never a solve link), and
    carries the IK constraint: chain helper+lower+upper therefore solves
    exactly lower+upper with the TRUE child joint as effector -- the game's
    own contract -- writing the result straight onto the real bones. No
    deform, hidden, idempotent by name."""
    name = f"RuriIK.{end}"
    if name not in arm_obj.data.bones:
        prev_active = bpy.context.view_layer.objects.active
        bpy.context.view_layer.objects.active = arm_obj
        arm_obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        edit_bones = arm_obj.data.edit_bones
        eb = edit_bones.new(name)
        eb.head = edit_bones[lower].head.copy()
        eb.tail = edit_bones[end].head.copy()
        if (eb.tail - eb.head).length < 1e-5:
            eb.tail = eb.head + Vector((0.0, 0.05, 0.0))
        eb.parent = edit_bones[lower]
        eb.use_deform = False
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.objects.active = prev_active
        arm_obj.data.bones[name].hide = True
    helper_pb = arm_obj.pose.bones[name]
    helper_pb.lock_ik_x = helper_pb.lock_ik_y = helper_pb.lock_ik_z = True
    return name


def _ensure_constraints(arm_obj, upper, lower, end, target_name):
    """Create (or reuse) the chain's live constraints. Static influence stays
    0 so any action without baked influence keys plays pure FK."""
    pose_bones = arm_obj.pose.bones
    helper = _ensure_effector_helper(arm_obj, lower, end)

    # Chain = helper + the parent-hops end->upper (2 on the real rig:
    # calf+thigh / forearm+upperarm; the Twist helpers are siblings, not
    # links), counted rather than assumed so an intermediate link in a
    # variant rig still solves the intended pair. Counted long, the legs
    # recruit the hip bone and both arms the clavicles -- measured as the
    # whole torso folding toward whichever pin engages.
    hops = 2
    walk = arm_obj.data.bones[end]
    for h in range(1, 8):
        walk = walk.parent
        if walk is None:
            break
        if walk.name == upper:
            hops = h
            break
    chain_count = hops + 1  # + the helper itself

    # Stale layouts from earlier revisions kept the IK constraint on the real
    # bones -- clear them so the only solver is the helper's.
    for stale in (lower, end):
        old = pose_bones[stale].constraints.get(_IK_CONSTRAINT)
        if old is not None:
            pose_bones[stale].constraints.remove(old)

    ik = pose_bones[helper].constraints.get(_IK_CONSTRAINT)
    if ik is None:
        ik = pose_bones[helper].constraints.new("IK")
        ik.name = _IK_CONSTRAINT
        ik.influence = 0.0
    ik.target = arm_obj
    ik.subtarget = target_name
    ik.chain_count = chain_count
    ik.use_stretch = False
    ik.pole_target = None  # no pole, deliberately -- see the _CHAINS comment

    rot = pose_bones[end].constraints.get(_ROT_CONSTRAINT)
    if rot is None:
        rot = pose_bones[end].constraints.new("COPY_ROTATION")
        rot.name = _ROT_CONSTRAINT
        rot.influence = 0.0
    rot.target = arm_obj
    rot.subtarget = target_name
    rot.mix_mode = "REPLACE"
    rot.target_space = "WORLD"
    rot.owner_space = "WORLD"

    return (f'pose.bones["{helper}"].constraints["{_IK_CONSTRAINT}"].influence',
            f'pose.bones["{end}"].constraints["{_ROT_CONSTRAINT}"].influence')


def _write_influence(bag, data_path, frames, values):
    for fc in [fc for fc in bag.fcurves if fc.data_path == data_path]:
        bag.fcurves.remove(fc)
    fc = bag.fcurves.new(data_path)
    n = len(frames)
    fc.keyframe_points.add(n)
    co = np.empty(n * 2, dtype=np.float64)
    co[0::2] = frames
    co[1::2] = values
    fc.keyframe_points.foreach_set("co", co)
    fc.keyframe_points.foreach_set("interpolation", np.full(n, 1, dtype=np.int32))  # LINEAR
    fc.update()


def apply_to_action(arm_obj, action, bone_targets, frame_start, frame_end):
    """Set up the chains' live IK constraints and bake their per-frame
    influence into `action` from the distance band. The FK curves are never
    touched. bone_targets: {human name: bone name} (from the humanoid
    retargeter). Returns a per-chain summary string list (empty when the
    rig/chains didn't resolve)."""
    bag = action.layers[0].strips[0].channelbag(action.slots[0]) if hasattr(action, "layers") else None
    if bag is None:
        return []
    sampler = _FcurveSampler(bag)
    bones = arm_obj.data.bones

    chains = []
    for h_upper, h_lower, h_end, target_name in _CHAINS:
        upper = bone_targets.get(h_upper)
        lower = bone_targets.get(h_lower)
        end = bone_targets.get(h_end)
        if not (upper and lower and end and upper in bones and lower in bones and end in bones
                and target_name in bones):
            continue
        if not sampler.bone_animated(target_name):
            continue  # clip does not drive this target -- chain stays pure FK
        chains.append((upper, lower, end, target_name))
    if not chains:
        return []

    # World matrices for FK ends + targets come from the action's own curves
    # (matrix recursion over rest offsets), NOT the scene -- the constraints
    # this module creates must never feed back into the distances.
    involved = [n for chain in chains for n in (chain[2], chain[3])]
    ordered = _ancestors_first(arm_obj, involved)
    rest = {n: bones[n].matrix_local for n in ordered}
    rest_rel = {}
    parent_of = {}
    for n in ordered:
        parent = bones[n].parent
        parent_of[n] = parent.name if parent is not None else None
        rest_rel[n] = (rest[parent.name].inverted() @ rest[n]) if parent is not None else rest[n]

    frames = np.arange(int(frame_start), int(frame_end) + 1, dtype=np.float64)
    weights = {chain: np.zeros(len(frames)) for chain in chains}
    inv_band = 1.0 / (_IK_OFF_DIST - _IK_FULL_DIST)
    for fi, frame in enumerate(frames):
        pose = {}
        for n in ordered:
            local = rest_rel[n] @ sampler.basis(n, frame)
            parent = parent_of[n]
            pose[n] = (pose[parent] @ local) if parent is not None else local
        for chain in chains:
            d = (pose[chain[3]].translation - pose[chain[2]].translation).length
            w = (_IK_OFF_DIST - d) * inv_band
            weights[chain][fi] = 0.0 if w < 0.0 else (1.0 if w > 1.0 else w)

    for chain in chains:
        upper, lower, end, target_name = chain
        ik_path, rot_path = _ensure_constraints(arm_obj, upper, lower, end, target_name)
        _write_influence(bag, ik_path, frames, weights[chain])
        _write_influence(bag, rot_path, frames, weights[chain])

    return [f"{upper}->{end} via {target_name}"
            f" (pins {int(round((weights[(upper, lower, end, target_name)] > 0.99).sum()))}"
            f"/{len(frames)} frames)"
            for upper, lower, end, target_name in chains]
