"""EndField-specific limb IK rig: live Blender constraints targeting the
clip's own IK bones, as a USER-POSABLE aid -- influence stays 0 by default
and playback is bit-identical to the raw FK.

EndField's rig carries animated IK target bones -- the game runtime's IK
interface (no MonoBehaviour config exists; the logic lives in game code):

    IK_Foot_L_001 / IK_Foot_R_001   foot targets   (parented to Root)
    IK_Knee_L_001 / IK_Knee_R_001   knee pole hints (parented to Root,
                                     rest 0.30m straight in front of knee)
    IK_Hand_L_001 / IK_Hand_R_001   hand targets   (parented to IK_Root)

At REST every target is EXACTLY coincident with its FK bone (zero position
delta, identity rotation offset).

With the decode fully faithful (fork muscle-attribute remap + verbatim
beyond-range muscle playback), the imported FK IS the authored animation,
and the IK bones turn out to be a redundant encoding of the same pose:
measured across battle and locomotion clips alike, they track the FK ends
within 1-3cm for essentially every frame. Every earlier scheme that DERIVED
per-frame IK weights from FK-vs-target distance was calibrated against
broken FK (misassigned muscle curves, then a [-1,1] muscle clamp) where the
gap carried signal; against faithful FK the distance is pure tracking noise,
and auto-engaging on it only risks dragging the pose toward the IK bones'
small rotation divergences (measured up to ~26deg on walk hands) for zero
fidelity gain. Runtime divergence between the two encodings is GAME-CODE
adjustment (terrain adaptation, weapon grips), not authored pose.

So this module builds the rig and nothing else:

  * the muscle FK curves stay PURE authored data (untouched, editable);
  * each chain gets a hidden rigid effector-helper bone (knee->ankle, IK
    axes locked; see _ensure_effector_helper for why Blender's tail-only
    effector forces one) carrying a standard two-bone IK constraint that
    rotates the real lower+upper bones (target = the clip's IK bone, no
    pole -- see the _CHAINS comment), plus a world-space Copy Rotation on
    the end bone (rest offset identity);
  * ALL influences stay at 0: playback equals raw FK exactly. Drag a
    constraint's influence up (or keyframe it) to pose a limb against its
    animated IK bone -- or against wherever you keyframe that bone.

Applied only to humanoid (muscle) clips on rigs that expose the convention,
and only when the import option asks for it -- see
prefab_importer.build_selected_animations for the gate.
"""

from __future__ import annotations

import re

import bpy
from mathutils import Vector

# (human upper, human lower, human end, target bone)
#
# NO pole targets, deliberately -- not even the rig's own IK_Knee hints.
# Engaged from the (correct) keyed FK pose the solver perturbs minimally and
# the bend plane follows FK by construction. A pole OVERRIDES that with its
# own plane, and whenever the hint drifts near the root->target axis the
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

_REQUIRED_TARGETS = ("IK_Foot_L_001", "IK_Foot_R_001", "IK_Hand_L_001", "IK_Hand_R_001")

_IK_CONSTRAINT = "Ruri IK"
_ROT_CONSTRAINT = "Ruri IK Rotation"

_BONE_PATH_RE = re.compile(r'pose\.bones\["(.+?)"\]\.(\w+)')


def detect_rig(arm_obj):
    """True when the armature exposes EndField's IK-bone convention."""
    bones = arm_obj.data.bones
    return all(name in bones for name in _REQUIRED_TARGETS)


def _animated_bones(action):
    """Bone names the action carries any fcurve for."""
    bag = action.layers[0].strips[0].channelbag(action.slots[0]) if hasattr(action, "layers") else None
    if bag is None:
        return set()
    names = set()
    for fc in bag.fcurves:
        match = _BONE_PATH_RE.match(fc.data_path)
        if match:
            names.add(match.group(1))
    return names


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
    """Create (or reuse) the chain's constraints. Influence stays at 0 --
    the rig is a posing aid; playback is raw FK."""
    pose_bones = arm_obj.pose.bones
    helper = _ensure_effector_helper(arm_obj, lower, end)

    # Chain = helper + the parent-hops end->upper (2 on the real rig:
    # calf+thigh / forearm+upperarm; the Twist helpers are siblings, not
    # links), counted rather than assumed so an intermediate link in a
    # variant rig still solves the intended pair. Counted long, the legs
    # recruit the hip bone and both arms the clavicles -- measured as the
    # whole torso folding toward whichever target engages.
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


def apply_to_action(arm_obj, action, bone_targets, frame_start, frame_end):
    """Set up the posing-aid constraints for every chain whose IK target the
    action animates. Neither the FK curves nor the action are modified in any
    way (no influence keys are baked -- influences stay 0). bone_targets:
    {human name: bone name} (from the humanoid retargeter). Returns a
    per-chain summary string list (empty when the rig/chains didn't
    resolve)."""
    animated = _animated_bones(action)
    bones = arm_obj.data.bones

    applied = []
    for h_upper, h_lower, h_end, target_name in _CHAINS:
        upper = bone_targets.get(h_upper)
        lower = bone_targets.get(h_lower)
        end = bone_targets.get(h_end)
        if not (upper and lower and end and upper in bones and lower in bones and end in bones
                and target_name in bones):
            continue
        if target_name not in animated:
            continue  # clip does not drive this target
        _ensure_constraints(arm_obj, upper, lower, end, target_name)
        applied.append(f"{upper}->{end} via {target_name} (influence 0, posing aid)")
    return applied
