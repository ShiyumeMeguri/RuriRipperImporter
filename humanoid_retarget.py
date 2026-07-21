"""Unity humanoid muscle -> bone-local rotation retarget.

A humanoid AnimationClip stores the main skeleton's motion as *muscle* float
curves (``m_FloatCurves`` named e.g. ``"Left Arm Down-Up"``), not as per-bone
transform curves -- only the ~272 auxiliary bones (hair/cloth/twist) and the IK
markers carry transform curves.  Without reconstructing the muscles every human
bone (spine/chest/arms/legs/head) stays frozen at its rest pose: that is exactly
the "humanoid bones don't play" symptom in Blender, which has no muscle solver.

This module rebuilds each human bone's local rotation from the muscles using the
avatar's Muscle Referential (the per-bone Axes: preQ/postQ/sgn/limit), so the
importer can bake the body the same way it bakes the auxiliary bones.

Per bone, per frame::

    R_char_anim = preQ @ swing_twist(angles) @ postQ^-1

preQ and postQ are DIFFERENT quaternions (verified empirically against live Unity
Mecanim ground truth via AnimationMode.SampleAnimationClip on a real Avatar+clip:
preQ/postQ differ by 60-270+ degrees per bone on a real rig) -- the sandwich is
asymmetric, not a similarity-transform conjugation by postQ alone.

This is the bone's FULL absolute local rotation for the frame -- NOT a delta,
and NOT identity at muscle=0.  The caller uses it directly in place of the
bone's rest local rotation; it does not compose with or divide by any rest
quaternion (this character's own FBX/prefab rest, or the avatar's
``m_SkeletonPose`` "normalized rest") -- both were tried in earlier revisions
of this module and both were either wrong or a pointless no-op; see the
RETRACTION below.

Validated against live Unity Mecanim ground truth across 18 body bones at 11
frames each (187 samples): grand-average error 4.93 degrees with this exact
formula, used directly, no per-bone table, no rest division of any kind.

RETRACTION (2026-07-10, two stages):

1. An earlier revision divided by ``norm_rest`` (the avatar's ``m_SkeletonPose``
   "normalized skeleton" rest) for a hand-picked subset of bones
   (``_NEEDS_NORM_REST_CORRECTION``), treating the result as a delta the caller
   would compose onto its own rest.  That table was derived from a ground-truth
   comparison that mixed two incompatible quantities: the Unity-side
   verification script printed ``cur`` (absolute local rotation) as the ground
   truth for four left-limb bones tested in an earlier batch, but printed
   ``delta`` (rest^-1 @ cur) for every other bone tested in later batches -- so
   the "some bones need norm_rest, some don't" pattern was validating this
   module's output against a mix of ABSOLUTE and DELTA ground truth for
   different bones, which cannot mean anything.  Re-sampling the four
   contaminated bones with correct, uniform ``delta`` ground truth showed the
   ``norm_rest`` model was wrong for arms (46-341 degrees error) and only
   "worked" for legs/torso/shoulders/hands because ``norm_rest`` happens to
   coincide with this character's own rest on this avatar for those bones.
2. The immediate fix divided by the CHARACTER's own rest instead of
   ``norm_rest`` (``inv(rest_char) @ preQ @ swing_twist @ postQ^-1``, still
   framed as "a delta for the caller to compose onto rest_char").  This scored
   correctly in an isolated unit test, but plugged into the real pipeline
   (``rest_char @ (inv(rest_char) @ raw))``) it algebraically cancels to
   exactly ``raw`` -- the division and the caller's later multiplication by the
   same quaternion undo each other on every call, for zero effect and two
   wasted quaternion ops.  The isolated test happened to score well only
   because comparing ``inv(rest_char) @ raw`` against a ``delta`` ground truth
   is mathematically identical to comparing ``raw`` against ``cur`` directly
   (left-multiplying two quaternions by the same ``inv(rest_char)`` doesn't
   change the angle between them) -- so the test was accidentally measuring the
   right thing (stage 3 below) while the implementation still carried a
   pointless round trip.
3. The current formula: no rest division at all, ``raw`` used directly as the
   bone's absolute local rotation.  Same numeric result as stage 2's pipeline
   behavior, with the dead round-trip removed.

Lesson from all three stages: a ground-truth EXTRACTION script needs the same
adversarial scrutiny as the code it validates (stage 1), and a formula that
"validates correctly" in isolation must still be checked for what it actually
computes once wired into the real call site, not assumed correct because the
test passed (stage 2).

The muscle->angle->swing-twist follows Unity's muscle space (community
reverse-engineering by lox9973): each per-axis muscle in [-1,1] scales to an
angle via the bone's min/max limit and sign, and the three axes compose as a
swing (around the combined Y-Z axis) times a twist (around X).

Coverage is Unity's full 95-muscle space: 55 body muscles (incl. eyes/jaw) via
``m_HumanBoneIndex`` plus 40 finger muscles via ``m_LeftHand``/``m_RightHand``
``m_HandBoneIndex`` (finger-major: Thumb P/I/D, Index P/I/D, ...). Bone names
resolve through HumanDescription first and fall back to the avatar's ``m_TOS``
(skeleton ``m_ID`` path hash -> transform path), so runtime-only avatars and
unmapped fingers still bind. The same referential/table is mirrored 1:1 by the
C# GLB exporter (Ruri-RipperHook GlbExporter/AvatarMuscleReferential.cs).

References:
    Avatar Axes layout  -- VibeStudio AssetStudio/Classes/Avatar.cs:38
    Muscle naming/order -- VibeStudio AssetStudio.Utility/YAML/MuscleHelper.cs
"""

from __future__ import annotations

import math

try:
    from mathutils import Matrix, Quaternion, Vector
except ImportError:  # allow import without Blender (parsing/inspection only)
    Matrix = Quaternion = Vector = None


# --- muscle -> (human bone, degree-of-freedom axis) -------------------------
#
# dof axis: 0 = X (twist), 1 = Y, 2 = Z.  The "Twist"/"Turn" muscle drives X;
# the primary bend drives Y; the secondary bend drives Z.  Bones without a twist
# (shoulder/hand) leave X unused (the avatar locks that axis' limit to 0).  The
# exact Y/Z assignment and signs are validated against the clip's IK markers.
_MUSCLE_DOF = {
    "Spine Front-Back": ("Spine", 2),
    "Spine Left-Right": ("Spine", 1),
    "Spine Twist Left-Right": ("Spine", 0),
    "Chest Front-Back": ("Chest", 2),
    "Chest Left-Right": ("Chest", 1),
    "Chest Twist Left-Right": ("Chest", 0),
    "UpperChest Front-Back": ("UpperChest", 2),
    "UpperChest Left-Right": ("UpperChest", 1),
    "UpperChest Twist Left-Right": ("UpperChest", 0),
    "Neck Nod Down-Up": ("Neck", 2),
    "Neck Tilt Left-Right": ("Neck", 1),
    "Neck Turn Left-Right": ("Neck", 0),
    "Head Nod Down-Up": ("Head", 2),
    "Head Tilt Left-Right": ("Head", 1),
    "Head Turn Left-Right": ("Head", 0),
    "Left Upper Leg Front-Back": ("LeftUpperLeg", 2),
    "Left Upper Leg In-Out": ("LeftUpperLeg", 1),
    "Left Upper Leg Twist In-Out": ("LeftUpperLeg", 0),
    "Left Lower Leg Stretch": ("LeftLowerLeg", 2),
    "Left Lower Leg Twist In-Out": ("LeftLowerLeg", 0),
    "Left Foot Up-Down": ("LeftFoot", 2),
    "Left Foot Twist In-Out": ("LeftFoot", 1),
    "Left Toes Up-Down": ("LeftToes", 1),
    "Right Upper Leg Front-Back": ("RightUpperLeg", 2),
    "Right Upper Leg In-Out": ("RightUpperLeg", 1),
    "Right Upper Leg Twist In-Out": ("RightUpperLeg", 0),
    "Right Lower Leg Stretch": ("RightLowerLeg", 2),
    "Right Lower Leg Twist In-Out": ("RightLowerLeg", 0),
    "Right Foot Up-Down": ("RightFoot", 2),
    "Right Foot Twist In-Out": ("RightFoot", 1),
    "Right Toes Up-Down": ("RightToes", 1),
    "Left Shoulder Down-Up": ("LeftShoulder", 2),
    "Left Shoulder Front-Back": ("LeftShoulder", 1),
    "Left Arm Down-Up": ("LeftUpperArm", 2),
    "Left Arm Front-Back": ("LeftUpperArm", 1),
    "Left Arm Twist In-Out": ("LeftUpperArm", 0),
    "Left Forearm Stretch": ("LeftLowerArm", 2),
    "Left Forearm Twist In-Out": ("LeftLowerArm", 0),
    "Left Hand Down-Up": ("LeftHand", 2),
    "Left Hand In-Out": ("LeftHand", 1),
    "Right Shoulder Down-Up": ("RightShoulder", 2),
    "Right Shoulder Front-Back": ("RightShoulder", 1),
    "Right Arm Down-Up": ("RightUpperArm", 2),
    "Right Arm Front-Back": ("RightUpperArm", 1),
    "Right Arm Twist In-Out": ("RightUpperArm", 0),
    "Right Forearm Stretch": ("RightLowerArm", 2),
    "Right Forearm Twist In-Out": ("RightLowerArm", 0),
    "Right Hand Down-Up": ("RightHand", 2),
    "Right Hand In-Out": ("RightHand", 1),
    # Eyes/jaw follow the same per-bone axis pattern as the head group: the primary
    # (vertical) DoF drives Z, the secondary (horizontal) DoF drives Y, no twist.
    "Left Eye Down-Up": ("LeftEye", 2),
    "Left Eye In-Out": ("LeftEye", 1),
    "Right Eye Down-Up": ("RightEye", 2),
    "Right Eye In-Out": ("RightEye", 1),
    "Jaw Close": ("Jaw", 2),
    "Jaw Left-Right": ("Jaw", 1),
}

# Finger muscles ("LeftHand.Thumb.1 Stretched" ...): 2 hands x 5 fingers x 4 DoF = 40.
# 1/2/3 Stretched curl the proximal/intermediate/distal phalange about Z; Spread swings the
# proximal about Y (Unity muscle taxonomy, mirrored from the C# AvatarMuscleReferential).
# Keys into the same _axes table as the body via Unity's HumanDescription human names
# ("Left Thumb Proximal" ...).
_FINGER_NAMES = ("Thumb", "Index", "Middle", "Ring", "Little")
_PHALANGE_NAMES = ("Proximal", "Intermediate", "Distal")

def _build_finger_dof():
    for side, hand in (("Left", "LeftHand"), ("Right", "RightHand")):
        for finger in _FINGER_NAMES:
            proximal = f"{side} {finger} Proximal"
            intermediate = f"{side} {finger} Intermediate"
            distal = f"{side} {finger} Distal"
            _MUSCLE_DOF[f"{hand}.{finger}.1 Stretched"] = (proximal, 2)
            _MUSCLE_DOF[f"{hand}.{finger}.Spread"] = (proximal, 1)
            _MUSCLE_DOF[f"{hand}.{finger}.2 Stretched"] = (intermediate, 2)
            _MUSCLE_DOF[f"{hand}.{finger}.3 Stretched"] = (distal, 2)

_build_finger_dof()

# BoneType enum order (Unity HumanBodyBones / MuscleHelper.BoneType); indexes the
# avatar's m_HumanBoneIndex array.
_BONE_TYPE_NAMES = (
    "Hips", "LeftUpperLeg", "RightUpperLeg", "LeftLowerLeg", "RightLowerLeg",
    "LeftFoot", "RightFoot", "Spine", "Chest", "UpperChest", "Neck", "Head",
    "LeftShoulder", "RightShoulder", "LeftUpperArm", "RightUpperArm",
    "LeftLowerArm", "RightLowerArm", "LeftHand", "RightHand", "LeftToes",
    "RightToes", "LeftEye", "RightEye", "Jaw",
)


# The four limb segments (upper/lower arm, upper/lower leg) apply their own
# twist-DOF (axis 0) muscle at a fraction of the angle every other
# twist-driven bone (Spine/Chest/UpperChest/Neck/Head) uses at the same
# muscle value -- root-caused (not curve-fit) by reading Unity.dll directly
# (IDA Pro): ``mecanim::human::TwistSolve`` calls ``HumanFixTwist`` for 8
# fixed (parent, child, avatar field) pairs, in exactly this order, after
# every bone's own independent muscle solve:
#
#     (LeftLowerArm,  LeftHand,      m_ForeArmTwist)
#     (LeftUpperArm,  LeftLowerArm,  m_ArmTwist)
#     (RightLowerArm, RightHand,     m_ForeArmTwist)
#     (RightUpperArm, RightLowerArm, m_ArmTwist)
#     (LeftLowerLeg,  LeftFoot,      m_LegTwist)
#     (LeftUpperLeg,  LeftLowerLeg,  m_UpperLegTwist)
#     (RightLowerLeg, RightFoot,     m_LegTwist)
#     (RightUpperLeg, RightLowerLeg, m_UpperLegTwist)
#
# Each pair rescales the PARENT's own twist angle by the avatar-configurable
# factor (all four default to 0.5, confirmed for this avatar) and adjusts the
# CHILD's local rotation to keep the child's WORLD orientation unchanged (see
# ``HumanoidRetargeter.body_local_quats`` for the derivation and the exact
# order this is applied in -- order matters, since e.g. LeftLowerArm is a
# CHILD in pair 1 and a PARENT in pair 2, applied sequentially).  An isolated
# single-bone test (only that bone's own muscles active, reading that SAME
# bone back) can only ever observe the "rescale this bone's own twist" half
# of this -- never the child-compensation half, which needs the PARENT's
# twist muscle active and the CHILD's own resulting rotation read back.
_TWIST_SOLVE_PAIRS = (
    ("LeftLowerArm", "LeftHand", "fore_arm_twist"),
    ("LeftUpperArm", "LeftLowerArm", "arm_twist"),
    ("RightLowerArm", "RightHand", "fore_arm_twist"),
    ("RightUpperArm", "RightLowerArm", "arm_twist"),
    ("LeftLowerLeg", "LeftFoot", "leg_twist"),
    ("LeftUpperLeg", "LeftLowerLeg", "upper_leg_twist"),
    ("RightLowerLeg", "RightFoot", "leg_twist"),
    ("RightUpperLeg", "RightLowerLeg", "upper_leg_twist"),
)

_HEXDIGITS = set("0123456789abcdefABCDEF")


# --- root motion (Hips) reconstruction ---------------------------------------
#
# Humanoid clips do not store the Hips bone's own local transform: RootT/RootQ
# encode Unity's internal mecanim::human::Human "root reference" -- a
# mass-weighted center of mass across the 25 body bones (RootT) and an
# orientation frame built from the shoulder/hip bone positions (RootQ),
# relative to the same quantities computed once from the avatar's rest pose.
# Both were confirmed by decompiling Unity's own native
# HumanComputeBoneMassCenter/HumanComputeOrientation/HumanSetupAxes (IDA Pro
# on Unity.dll) and validated against live Unity ground truth (Animator +
# AnimationClip.SampleAnimation on a real walk-cycle clip): the
# orientation-frame formula matches Unity's own rest-pose computation to
# 0.00002 degrees, and the resulting Hips rotation and (Y, X) position track
# live Unity output to a few degrees / centimeters across a full multi-frame
# gait cycle.
#
# To recover Hips' own local transform, a PROVISIONAL pose is built with Hips
# fixed at the origin with identity rotation (every other body bone at its
# frame's absolute local rotation, via _axes_local). Placing Hips at an
# actual (T, R) instead rigidly transforms every descendant, so
# mass_center(actual) = T + R @ mass_center(provisional) and
# orientation(actual) = R @ orientation(provisional); solving both against
# the frame's RootT/RootQ (and the avatar's own rest reference m_RootX) gives
# Hips' true rotation and position directly.
#
# KNOWN LIMITATION: clips authored without a separate MotionT/MotionQ curve
# (ground-projected root motion) still bake full world-space walking
# progress into RootT itself; since that motion belongs on the character's
# root GameObject rather than on Hips, this shows up as drift along the walk
# direction when no MotionT curve exists to subtract it back out first. This
# is the same class of issue the MotionT subtraction below already exists
# for -- it is a pre-existing limitation for MotionT-less clips, not a
# regression introduced by this formula.
_MASS_CENTER_FORMULA = {
    "Hips": (("LeftUpperLeg", 1.0 / 3.0), ("RightUpperLeg", 1.0 / 3.0), ("Spine", 1.0 / 3.0)),
    "LeftUpperLeg": (("LeftUpperLeg", 0.5), ("LeftLowerLeg", 0.5)),
    "RightUpperLeg": (("RightUpperLeg", 0.5), ("RightLowerLeg", 0.5)),
    "LeftLowerLeg": (("LeftLowerLeg", 0.5), ("LeftFoot", 0.5)),
    "RightLowerLeg": (("RightLowerLeg", 0.5), ("RightFoot", 0.5)),
    "Spine": (("Spine", 0.5), ("Chest", 0.5)),
    "Chest": (("Chest", 0.5), ("UpperChest", 0.5)),
    "UpperChest": (("UpperChest", 0.25), ("Neck", 0.25), ("LeftShoulder", 0.25), ("RightShoulder", 0.25)),
    "Neck": (("Neck", 0.5), ("Head", 0.5)),
    "LeftShoulder": (("LeftShoulder", 0.5), ("LeftUpperArm", 0.5)),
    "RightShoulder": (("RightShoulder", 0.5), ("RightUpperArm", 0.5)),
    "LeftUpperArm": (("LeftUpperArm", 0.5), ("LeftLowerArm", 0.5)),
    "RightUpperArm": (("RightUpperArm", 0.5), ("RightLowerArm", 0.5)),
    "LeftLowerArm": (("LeftLowerArm", 0.5), ("LeftHand", 0.5)),
    "RightLowerArm": (("RightLowerArm", 0.5), ("RightHand", 0.5)),
}
# Any body bone not listed above (feet, hands, toes, eyes, head, jaw) uses its
# own provisional position directly, matching HumanComputeBoneMassCenter's
# default case.


def is_muscle(attribute):
    """True if a float-curve attribute string is a body muscle this module drives."""
    return attribute in _MUSCLE_DOF


def is_root(attribute):
    """True if a float-curve attribute is a body root-motion channel: the hips'
    pose in the animation-root frame (RootT/RootQ) or the root motion
    (MotionT/MotionQ) used to make it root-local.  None are muscles."""
    return attribute.split(".", 1)[0] in ("RootT", "RootQ", "MotionT", "MotionQ")


# --- parsing helpers --------------------------------------------------------

def _unwrap(value):
    """Peel Unity's OffsetPtr ``{data: ...}`` indirection."""
    while isinstance(value, dict) and len(value) == 1 and "data" in value:
        value = value["data"]
    return value


def _int_array(blob):
    """Parse a little-endian hex int32 array.

    Tolerant of the trailing ``ffffff/f...`` run AssetRipper emits as -1 padding:
    clean 8-hex-digit groups are read from the front and parsing stops at the
    first non-hex group (every value we need precedes the padding)."""
    if isinstance(blob, (list, tuple)):
        return [int(x) for x in blob]
    text = str(blob)
    out = []
    i = 0
    while i + 8 <= len(text):
        chunk = text[i:i + 8]
        if all(c in _HEXDIGITS for c in chunk):
            out.append(int.from_bytes(bytes.fromhex(chunk), "little", signed=True))
            i += 8
        else:
            break
    return out


def _quat(d):
    """Unity ``{x,y,z,w}`` -> mathutils Quaternion ``(w,x,y,z)``."""
    return Quaternion((d["w"], d["x"], d["y"], d["z"]))


def _vec3(d):
    return (float(d["x"]), float(d["y"]), float(d["z"]))


def _find_bone_name_map(data):
    """Collect the HumanDescription ``m_BoneName <-> m_HumanName`` pairs from
    anywhere in the avatar structure; returns ``{human name: bone name}``."""
    result = {}

    def walk(node):
        if isinstance(node, dict):
            bone_name = node.get("m_BoneName")
            human_name = node.get("m_HumanName")
            if isinstance(bone_name, str) and isinstance(human_name, str):
                result[human_name] = bone_name
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return result


def _parse_tos(data):
    """The avatar's ``m_TOS`` (CRC32 path hash -> transform path) as ``{int: str}``.

    The fallback bone-name source when HumanDescription doesn't map a bone (runtime-only
    avatars, and most finger mappings): the human skeleton's ``m_ID[node]`` is the same
    hash space, so ``TOS[id]`` recovers the bone's full path."""
    tos = data.get("m_TOS")
    result = {}
    if isinstance(tos, list):
        for pair in tos:
            if not isinstance(pair, dict):
                continue
            key = pair.get("first")
            value = pair.get("second")
            if value is None and isinstance(key, dict):  # {key: path} flow-map variant
                for k, v in key.items():
                    key, value = k, v
            try:
                result[int(key) & 0xFFFFFFFF] = str(value)
            except (TypeError, ValueError):
                continue
    elif isinstance(tos, dict):
        for key, value in tos.items():
            try:
                result[int(key) & 0xFFFFFFFF] = str(value)
            except (TypeError, ValueError):
                continue
    return result


# --- muscle math ------------------------------------------------------------

def _muscle_angle(muscle, sgn, limit_min, limit_max):
    """Map a normalized muscle to a radian angle via the per-axis limit and
    sign: -1 -> min, 0 -> 0, +1 -> max, values beyond +-1 extrapolating
    linearly past the limit.

    Deliberately NOT clamped to [-1, 1]. Baked EndField clips store
    beyond-range values as a matter of course (e.g. "Right Shoulder Down-Up"
    living at -1.73..-1.33 for a whole battle clip), and those values must
    play back verbatim, measured two ways:

      * a [-1,1] clamp VISIBLY BREAKS smooth animation: the encoder's inverse
        muscle solve distributes an arm pose across the redundant
        shoulder+arm DoFs with complementary per-frame wobble (measured on
        battle_attack_04 f208-226: "Right Shoulder Down-Up" and "Right Arm
        Down-Up" jitter in lockstep with OPPOSITE signs) that cancels exactly
        on forward composition -- the game plays it smooth. Flattening the
        beyond-range shoulder at -1 deletes its half of the cancellation and
        the arm's half surfaces as a 2-4deg/frame sawtooth on the whole limb
        (the reported ForeTwist jitter);
      * an earlier "clamp like the Unity runtime" rationale was calibrated
        against PRE-remap data whose arm-region curves were misassigned
        entirely (the "+2.996 wrist" that motivated it was not wrist data);
        this fork's clips are authored against ITS avatar limits, and the
        avatar supplying limit_min/limit_max here is the fork's own asset --
        muscle * limit reproduces the authored angle exactly."""
    scale = limit_max if muscle >= 0.0 else -limit_min
    return sgn * muscle * scale


def _swing_twist(angle_x, angle_y, angle_z):
    """Compose three per-axis angles into a quaternion via Unity's own
    tan-half-angle (Rodrigues parameter) formula:

        tx, ty, tz = tan(angle_x/2), tan(angle_y/2), tan(angle_z/2)
        q = normalize(1, tx, ty + tx*tz, tz - tx*ty)   # (w, x, y, z)

    This is NOT ``swing(Y,Z) @ twist(X)`` (a plain sequential quaternion
    product of a combined-axis swing and a separate twist) -- that formula
    only agrees with Unity in the small-angle limit and diverges sharply as
    the swing grows, because Unity's twist and swing couple through the
    ``tx*tz``/``tx*ty`` cross terms below, and even twist-free swing itself
    is the RAW per-axis tan-half pair ``(ty, tz)``, not a single combined
    axis-angle rotation around the vector ``(0, angle_y, angle_z)``.

    Reverse-engineered by reading (not guessing) Unity.dll's own
    ``math::FromAxes_2`` (IDA Pro decompilation, the ``kZYRoll`` case all 25
    human body bones use per ``setupAxesInfoArray``): it computes
    ``halfTan`` of the muscle-scaled angle vector, then combines the X
    (twist) lane with the Y/Z (swing) lanes via exactly the cross terms
    above before normalizing the whole 4-vector as a quaternion.  Verified
    bit-exact (5 decimal places) against live Unity ground truth
    (``HumanPoseHandler.SetHumanPose`` on the SaionNanae avatar,
    LeftUpperArm, several two-axis muscle combinations) -- the previous
    combined-axis-angle formula matched single-axis-only ground truth
    perfectly (no cross term to get wrong) but diverged 5-85 degrees the
    moment two axes were simultaneously non-zero, exactly the "torso bones
    (small swings) look almost right, limb bones (large gait-cycle swings)
    are wildly wrong" pattern seen when baking a full walk clip."""
    tx = math.tan(angle_x * 0.5)
    ty = math.tan(angle_y * 0.5)
    tz = math.tan(angle_z * 0.5)
    return Quaternion((1.0, tx, ty + tx * tz, tz - tx * ty)).normalized()


# --- public API -------------------------------------------------------------

class _BoneAxes:
    __slots__ = ("bone_name", "human_name", "post_q", "pre_q", "sgn",
                 "limit_min", "limit_max", "dof_muscles", "node_index")


class HumanoidRetargeter:
    """Reconstructs human-bone local rotations from a clip's muscle curves using
    one avatar's Muscle Referential."""

    def __init__(self, avatar_file, fallback_tos=None):
        """fallback_tos: optional {CRC32 path hash: transform path} used when the
        avatar's own m_TOS is empty. EndField (and likely other stripped release
        builds) ships runtime avatars with m_TOS emptied AND no HumanDescription
        -- both bone-name sources gone, so every _add_bone silently failed and
        bone_targets() came back empty (humanoid clips imported as motionless
        bodies). The skeleton's m_ID entries are the SAME hash space as
        AnimationClip curve-path hashes: CRC32 of the transform path (verified
        empirically, crc32(b"Root") == the exported 0xB6C65665 placeholder), so a
        caller that knows the TARGET armature's bone paths can hand the exact
        equivalent table here -- hash the paths the skeleton actually has, and
        m_ID lookups resolve just as an intact m_TOS would."""
        avatar_doc = avatar_file.first("Avatar")
        if avatar_doc is None:
            raise ValueError("file has no Avatar object")
        # Which document this referential came from (UnityFile.path carries the
        # guid in bridge mode, a disk path in disk mode) -- lets an importer
        # stamp the avatar's raw text onto the armature it builds (see
        # prefab_importer._stamp_avatar_on_armature) so later standalone clip
        # imports can rebuild this exact retargeter from the armature alone.
        self.source_key = getattr(avatar_file, "path", None)
        data = avatar_doc.data
        constant = _unwrap(data["m_Avatar"])
        human = _unwrap(constant["m_Human"])
        skeleton = _unwrap(human["m_Skeleton"])
        self._nodes = skeleton["m_Node"]
        self._axes_array = skeleton["m_AxesArray"]
        self._skel_ids = [v & 0xFFFFFFFF for v in _int_array(skeleton.get("m_ID") or [])]
        human_bone_index = _int_array(human["m_HumanBoneIndex"])
        self._human_to_bone = _find_bone_name_map(data)
        self._tos = _parse_tos(data)
        if not self._tos and fallback_tos:
            self._tos = {int(k) & 0xFFFFFFFF: str(v) for k, v in fallback_tos.items()}

        # Root-motion (Hips) reconstruction inputs: the avatar's own per-bone
        # mass table, its rest-computed root orientation reference, and the
        # rest LOCAL transform of every raw skeleton node (not just the 25
        # human-mapped ones -- some human bones' raw parent is an unmapped
        # intermediate node, so the node hierarchy has to be walked generically).
        # See body_transform()/_hips_world_transform() for how these combine.
        self._node_parent = [int(n.get("m_ParentId", -1)) for n in self._nodes]
        skeleton_pose = _unwrap(human.get("m_SkeletonPose") or {})
        node_rest_x = skeleton_pose.get("m_X") or []
        self._node_rest_t = [Vector(_vec3(x["t"])) for x in node_rest_x]
        self._node_rest_q = [_quat(x["q"]) for x in node_rest_x]
        self._human_bone_mass = [float(v) for v in (human.get("m_HumanBoneMass") or [])]
        root_x = human.get("m_RootX") or {}
        self._q_rest = _quat(root_x["q"]) if "q" in root_x else Quaternion()

        # TwistSolve parent<->child redistribution factors (see
        # _TWIST_SOLVE_PAIRS/body_local_quats) -- avatar-configurable per
        # mecanim::human::TwistSolve, default 0.5 for all four.
        self._arm_twist = float(human.get("m_ArmTwist", 0.5))
        self._fore_arm_twist = float(human.get("m_ForeArmTwist", 0.5))
        self._upper_leg_twist = float(human.get("m_UpperLegTwist", 0.5))
        self._leg_twist = float(human.get("m_LegTwist", 0.5))

        self._axes = {}        # human name -> _BoneAxes
        for bone_type, node_index in enumerate(human_bone_index):
            if bone_type >= len(_BONE_TYPE_NAMES):
                break
            self._add_bone(_BONE_TYPE_NAMES[bone_type], node_index)

        # Fingers: m_LeftHand/m_RightHand.m_HandBoneIndex, 15 entries per hand in
        # finger-major order (Thumb P/I/D, Index P/I/D, ...), same human-skeleton node space.
        for hand_key, side in (("m_LeftHand", "Left"), ("m_RightHand", "Right")):
            hand = _unwrap(human.get(hand_key) or {})
            if not isinstance(hand, dict):
                continue
            hand_bone_index = _int_array(hand.get("m_HandBoneIndex") or [])
            for i, node_index in enumerate(hand_bone_index[:15]):
                finger = _FINGER_NAMES[i // 3]
                phalange = _PHALANGE_NAMES[i % 3]
                self._add_bone(f"{side} {finger} {phalange}", node_index)

        for muscle, (human_name, dof) in _MUSCLE_DOF.items():
            axes = self._axes.get(human_name)
            if axes is not None:
                axes.dof_muscles[dof] = muscle

    def _add_bone(self, human_name, node_index):
        """Register one human bone's Muscle Referential row, resolving its skeleton bone
        name via HumanDescription first and the avatar TOS (m_ID hash -> path) second."""
        if node_index < 0 or node_index >= len(self._nodes):
            return
        bone_name = self._human_to_bone.get(human_name)
        if not bone_name and node_index < len(self._skel_ids):
            path = self._tos.get(self._skel_ids[node_index])
            if path:
                bone_name = path.rsplit("/", 1)[-1]
        if not bone_name:
            return
        axes_id = self._nodes[node_index].get("m_AxesId", -1)
        if axes_id < 0 or axes_id >= len(self._axes_array):
            return
        entry = self._axes_array[axes_id]
        axes = _BoneAxes()
        axes.bone_name = bone_name
        axes.human_name = human_name
        axes.post_q = _quat(entry["m_PostQ"])
        axes.pre_q = _quat(entry["m_PreQ"])
        axes.sgn = _vec3(entry["m_Sgn"])
        limit = entry["m_Limit"]
        axes.limit_min = _vec3(limit["m_Min"])
        axes.limit_max = _vec3(limit["m_Max"])
        axes.dof_muscles = [None, None, None]
        axes.node_index = node_index
        self._axes[human_name] = axes

    def bone_targets(self):
        """``{human name: Bip001 bone name}`` for every bone this retargeter drives."""
        return {human: axes.bone_name for human, axes in self._axes.items()}

    def local_quat(self, human_name, muscle_lookup):
        """The bone's FULL absolute local rotation for this frame, WITH the
        TwistSolve parent<->child correction applied where relevant (see
        ``body_local_quats``) -- NOT a delta; the caller uses it directly in
        place of the bone's rest local rotation, it does not compose with it.

        ``muscle_lookup`` is a callable ``attribute -> float|None`` (None or a
        missing muscle leaves that axis at 0).  Returns None for bones the
        retargeter does not drive.  Recomputes the full ``body_local_quats``
        dict on every call -- callers that need more than one bone's rotation
        for the same frame (baking, provisional FK) should call
        ``body_local_quats`` once themselves instead."""
        if human_name not in self._axes:
            return None
        return self.body_local_quats(muscle_lookup).get(human_name)

    @staticmethod
    def _compute_angles(axes, muscle_lookup):
        """The bone's per-axis (twist, Y, Z) angles in radians for this
        frame's muscle values, BEFORE any TwistSolve redistribution."""
        angles = [0.0, 0.0, 0.0]
        for dof in range(3):
            muscle = axes.dof_muscles[dof]
            if muscle is None:
                continue
            value = muscle_lookup(muscle)
            if value is None:
                continue
            angles[dof] = _muscle_angle(value, axes.sgn[dof],
                                        axes.limit_min[dof], axes.limit_max[dof])
        return angles

    @staticmethod
    def _compose_from_angles(axes, angles):
        """preQ @ swing_twist(angles) @ inv(postQ) -- the bone's FULL absolute
        local rotation in the avatar's own frame for the given per-axis
        angles.  See ``_axes_local``'s docstring for the full contract."""
        swing_twist = _swing_twist(angles[0], angles[1], angles[2])
        return axes.pre_q @ swing_twist @ axes.post_q.inverted()

    @staticmethod
    def _axes_local(axes, muscle_lookup):
        """preQ @ swing_twist(muscle) @ inv(postQ) -- the bone's FULL absolute
        local rotation in the avatar's own frame for this frame's muscle values,
        WITHOUT any TwistSolve parent<->child correction (see
        ``body_local_quats`` for the corrected, final version every caller
        outside this class should use).

        This is NOT a delta and is NOT identity at muscle=0: it already IS the
        answer the caller wants in place of the bone's rest local rotation, with
        no further composition or division by any rest quaternion (character's
        own, or the avatar's ``m_SkeletonPose`` "normalized rest") needed or
        correct.  preQ and postQ are NOT interchangeable -- on a real rig they
        differ by 60-270+ degrees per bone, so the sandwich is asymmetric, not a
        similarity-transform conjugation by postQ alone.

        Verified against live Unity Mecanim ground truth (Editor
        AnimationMode.SampleAnimationClip + Animator.GetBoneTransform on a real
        humanoid Avatar+clip) across 18 body bones at 11 frames each (187
        samples): grand-average error 4.93 degrees with the pre/post-Q sandwich
        used directly, no per-bone table, no rest division of any kind -- BUT
        that validation happened to exercise mostly single-DOF-at-a-time poses,
        so it did not catch the separate swing/twist composition bug (see
        ``_swing_twist``) or the TwistSolve parent<->child redistribution (see
        ``body_local_quats``) fixed later, both of which are small at low
        muscle values and large at high ones.  See the module docstring's
        RETRACTION note for two earlier, wrong versions of this method (one
        dividing by the avatar's ``norm_rest`` for a hand-picked subset of
        bones, one dividing by the character's own rest for every bone) and why
        each one's own validation was invalid or pointless."""
        return HumanoidRetargeter._compose_from_angles(axes, HumanoidRetargeter._compute_angles(axes, muscle_lookup))

    def body_local_quats(self, muscle_lookup):
        """``{human name: Quaternion}`` for every body/finger bone this
        retargeter drives (Hips included, as its OWN independent muscle
        solve -- ``body_transform`` still reconstructs its actual transform
        separately), for this frame's muscle values, WITH Unity's TwistSolve
        parent<->child twist redistribution applied.

        Mecanim does not solve each human bone independently: after every
        bone's own preQ/swingTwist/postQ solve (``_axes_local``), Unity runs
        ``mecanim::human::TwistSolve`` (IDA Pro decompilation of Unity.dll),
        which for 8 fixed (parent, child, avatar-configurable factor) pairs --
        (UpperArm, LowerArm, m_ArmTwist), (LowerArm, Hand, m_ForeArmTwist),
        (UpperLeg, LowerLeg, m_UpperLegTwist), (LowerLeg, Foot, m_LegTwist),
        each on both sides, in that exact order -- rescales the PARENT's own
        twist angle by the factor (default 0.5 for all four) and adjusts the
        CHILD's local rotation to keep the child's WORLD orientation exactly
        unchanged.  Confirmed missing entirely from this module previously:
        an isolated single-bone test (only that bone's own muscles active)
        cannot detect it, since the affected bone's OWN twist-rescale is the
        only part of TwistSolve visible when reading that SAME bone back --
        the child-compensation half is invisible unless the PARENT's twist
        muscle is active AND the CHILD's own resulting rotation is read.

        Derivation of the child compensation (algebraic necessity, not
        curve-fit): child_world = parent_world @ child_local must hold both
        before and after the parent's local rotation changes by
        ``delta = parent_local_old.inverted() @ parent_local_new`` (only the
        parent's OWN local changes; its ancestors don't) --
        parent_world_new = parent_world_old @ delta, so preserving
        child_world requires child_local_new = delta.inverted() @
        child_local_old.  Uses this module's own already-validated
        ``_swing_twist``/pre/post-Q formula for the parent's before/after
        local rotation (validated exact against live Unity ground truth) as
        the basis for ``delta``, rather than re-deriving Unity's internal
        ``math::ToAxes``/``SkeletonAlign`` bit-for-bit."""
        quats = {}
        for name, axes in self._axes.items():
            if name == "Hips":
                continue
            quats[name] = self._axes_local(axes, muscle_lookup)

        for parent_name, child_name, factor_field in _TWIST_SOLVE_PAIRS:
            if parent_name not in quats or child_name not in quats:
                continue
            parent_axes = self._axes[parent_name]
            angles = self._compute_angles(parent_axes, muscle_lookup)
            parent_old = quats[parent_name]
            angles[0] *= getattr(self, "_" + factor_field)
            parent_new = self._compose_from_angles(parent_axes, angles)
            delta = (parent_old.inverted() @ parent_new).normalized()
            quats[parent_name] = parent_new
            quats[child_name] = (delta.inverted() @ quats[child_name]).normalized()
        return quats

    def hips_bone(self):
        """The Bip001 bone name mapped to the Hips, or None."""
        axes = self._axes.get("Hips")
        return axes.bone_name if axes is not None else None

    def skeleton_nodes(self):
        """The avatar's own embedded skeleton, independent of any human/muscle
        mapping (populated for Generic avatars too, whose _axes stays empty --
        m_Human.m_Skeleton is the full raw node tree, m_HumanBoneIndex is just
        which of those nodes double as the 25 human bones). One entry per raw
        skeleton node, in the SAME order/index space m_ParentId indexes into:
        ``(bone_name, parent_index, rest_local_translation, rest_local_rotation)``.
        ``bone_name`` resolves through the avatar's own m_TOS (m_ID hash ->
        path leaf), same source _add_bone uses, falling back to ``bone_{i}``
        for any node the hash table doesn't cover."""
        result = []
        for index in range(len(self._nodes)):
            parent = self._node_parent[index] if index < len(self._node_parent) else -1
            name = None
            if index < len(self._skel_ids):
                path = self._tos.get(self._skel_ids[index])
                if path:
                    name = path.rsplit("/", 1)[-1]
            if not name:
                name = f"bone_{index}"
            t = self._node_rest_t[index] if index < len(self._node_rest_t) else Vector((0.0, 0.0, 0.0))
            q = self._node_rest_q[index] if index < len(self._node_rest_q) else Quaternion()
            result.append((name, parent, t, q))
        return result

    def _provisional_fk(self, muscle_lookup):
        """Every body bone's world position+rotation for this frame, treating
        Hips as sitting at the origin with identity rotation (see the module
        docstring's root-motion section).  Returns ``{human name: (Vector,
        Quaternion)}``.  Walks the RAW skeleton node hierarchy (not just
        human-to-human parenting) since some human bones' immediate parent is
        an unmapped intermediate node (e.g. a separate "Pelvis" node distinct
        from the node Unity maps as "Hips")."""
        local_rot = self.body_local_quats(muscle_lookup)

        node_to_name = {axes.node_index: name for name, axes in self._axes.items()
                        if name in _BONE_TYPE_NAMES}
        hips_axes = self._axes.get("Hips")
        hips_node = hips_axes.node_index if hips_axes is not None else -1

        memo = {}

        def solve(node_index):
            if node_index in memo:
                return memo[node_index]
            if node_index == hips_node or node_index < 0:
                memo[node_index] = (Vector((0.0, 0.0, 0.0)), Quaternion())
                return memo[node_index]
            parent_index = (self._node_parent[node_index]
                            if node_index < len(self._node_parent) else -1)
            parent_pos, parent_rot = solve(parent_index)
            name = node_to_name.get(node_index)
            rot = local_rot[name] if name in local_rot else self._node_rest_q[node_index]
            world_rot = parent_rot @ rot
            world_pos = parent_pos + (parent_rot @ self._node_rest_t[node_index])
            memo[node_index] = (world_pos, world_rot)
            return memo[node_index]

        result = {}
        for name in _BONE_TYPE_NAMES:
            axes = self._axes.get(name)
            if axes is not None:
                result[name] = solve(axes.node_index)
        return result

    @staticmethod
    def _mass_center_of(name, fk):
        """Port of mecanim::human::HumanComputeBoneMassCenter's per-body-slot
        table: most bones use the midpoint of their own and an adjacent
        bone's provisional position; see _MASS_CENTER_FORMULA."""
        formula = _MASS_CENTER_FORMULA.get(name)
        if formula is None:
            return fk[name][0]
        total = Vector((0.0, 0.0, 0.0))
        for neighbor, weight in formula:
            total += fk[neighbor][0] * weight
        return total

    def _compute_mass_center(self, fk):
        """Port of the mass-weighted center-of-mass loop in
        mecanim::human::HumanSetupAxes/RetargetTo."""
        total = Vector((0.0, 0.0, 0.0))
        total_mass = 0.0
        for index, name in enumerate(_BONE_TYPE_NAMES):
            if name not in fk or index >= len(self._human_bone_mass):
                continue
            mass = self._human_bone_mass[index]
            if mass < 0.0:
                continue
            total += self._mass_center_of(name, fk) * mass
            total_mass += mass
        return total * (1.0 / total_mass)

    @staticmethod
    def _compute_orientation(fk):
        """Port of mecanim::human::HumanComputeOrientation: the body's
        orientation frame from the shoulder-center/hip-center world
        positions.  Matches Unity's own rest-pose computation of this same
        formula to 0.00002 degrees (validated against m_RootX.q).

        The raw right vector is NOT orthogonal to up away from rest (a
        twisted pose points the arm-diff+leg-diff sum well off the torso
        plane), and quaternion extraction is only defined for orthonormal
        matrices: feeding the unorthogonalized frame to to_quaternion()
        made its trace-branch selection discontinuous -- measured on
        battle_skill_ult's spin, one frame (153->154) jumped the body frame
        5.75deg while every muscle input and the shoulder/hip centers moved
        smoothly, kicking the hips 4.9deg about world X. Rebuilding right
        from up x forward closes the frame orthonormally; at rest the
        vectors are orthogonal anyway, so the validated rest agreement is
        untouched."""
        shoulder_center = (fk["RightUpperArm"][0] + fk["LeftUpperArm"][0]) * 0.5
        hip_center = (fk["LeftUpperLeg"][0] + fk["RightUpperLeg"][0]) * 0.5
        up = (shoulder_center - hip_center).normalized()
        right = ((fk["RightUpperArm"][0] - fk["LeftUpperArm"][0]) +
                (fk["RightUpperLeg"][0] - fk["LeftUpperLeg"][0])).normalized()
        forward = right.cross(up).normalized()
        right = up.cross(forward)
        return Matrix((right, up, forward)).transposed().to_quaternion()

    def body_transform(self, muscle_lookup, keep_position_xz=True, keep_position_y=True,
                       keep_orientation=True):
        """The hips' own local position+rotation for this frame, reconstructed
        from the clip's Root curves (see the module docstring's root-motion
        section for the full derivation).  Returns ``(Vector hips_position,
        Quaternion hips_rotation, (Vector motion_position, Quaternion
        motion_rotation))`` or None when the clip carries no root curves.

        ``keep_position_xz``/``keep_position_y``/``keep_orientation`` mirror
        the clip's own ``m_AnimationClipSettings.m_KeepOriginalPosition{XZ,Y}``/
        ``m_KeepOriginalOrientation``.  When a setting is False, Unity extracts
        that component as root motion belonging to the character's root
        GameObject rather than the hips: confirmed by decompiling
        mecanim::animation::EvaluateRootMotion/GetClipX/GetCycleX (IDA Pro on
        Unity.dll) and validated against live Unity ground truth (a real
        walk-cycle clip with m_KeepOriginalPositionXZ=False) -- treating RootT's
        X/Z as zero for that clip drops the hips position error from an
        averaged 0.56 (unbounded, growing every frame along the walk
        direction) to 0.04 (bounded, matching the rotation formula's own
        precision).  Without this, RootT's raw X/Z value keeps the full
        world-space walking progress, which has nowhere to go but directly
        onto the hips bone.

        The third return value is exactly that extracted amount (zero for any
        axis where the corresponding ``keep_*`` is True).  The caller MUST
        bake it onto the character's root object separately (see
        ``_bake_muscles``/``BakeRootMotion``) -- dropping it entirely leaves
        the hips correctly stationary but with no compensating locomotion
        anywhere, so a walk clip would animate the stride in place instead of
        actually walking anywhere."""
        tx = muscle_lookup("RootT.x")
        ty = muscle_lookup("RootT.y")
        tz = muscle_lookup("RootT.z")
        if tx is None and ty is None and tz is None:
            return None
        full_t = Vector((tx or 0.0, ty or 0.0, tz or 0.0))

        qw = muscle_lookup("RootQ.w")
        full_q = (Quaternion() if qw is None else
                 Quaternion((qw, muscle_lookup("RootQ.x") or 0.0,
                             muscle_lookup("RootQ.y") or 0.0,
                             muscle_lookup("RootQ.z") or 0.0)).normalized())

        has_motion_curves = (muscle_lookup("MotionT.x") is not None
                             or muscle_lookup("MotionT.z") is not None
                             or muscle_lookup("MotionQ.w") is not None)
        if has_motion_curves:
            # Trajectory-authored clip: MotionT/MotionQ ARE the character
            # root's motion (the trajectory the runtime applies to the
            # GameObject), and RootT/RootQ are the ABSOLUTE body reference
            # (they include the displacement -- verified against the real
            # game: a 0.94m dash clip carries MotionT.z 0->-0.9378 with
            # RootT.z tracking the same span, and MotionQ stays identity for
            # the whole swing-heavy clip, i.e. turning belongs to the BODY,
            # not the trajectory). So the split is exact and settings-free:
            # object gets Motion, hips get the trajectory-relative remainder,
            # and their scene composition (motion outer, hips inner)
            # reconstructs the absolute Root curves bit-for-bit. The
            # m_KeepOriginalPosition*/Orientation flags only parameterize the
            # runtime's applyRootMotion deltas and loop blending -- consuming
            # them HERE is what previously (a) annihilated the displacement
            # (extracted motion was RootT-MotionT's bounded sway instead of
            # MotionT) and (b) shook the character (yaw carved out of RootQ
            # per frame and recomposed against independently-interpolated
            # curves -- while the data says the trajectory never turns).
            motion_t = Vector((muscle_lookup("MotionT.x") or 0.0,
                               muscle_lookup("MotionT.y") or 0.0,
                               muscle_lookup("MotionT.z") or 0.0))
            mqw = muscle_lookup("MotionQ.w")
            motion_q = (Quaternion() if mqw is None else
                        Quaternion((mqw, muscle_lookup("MotionQ.x") or 0.0,
                                    muscle_lookup("MotionQ.y") or 0.0,
                                    muscle_lookup("MotionQ.z") or 0.0)).normalized())
            root_t_simple = full_t - motion_t
            root_q = (motion_q.inverted() @ full_q).normalized()
        elif keep_position_xz and keep_position_y and keep_orientation:
            root_t_simple = full_t
            root_q = full_q
            motion_q = Quaternion()
            motion_t = Vector((0.0, 0.0, 0.0))
        else:
            # Motion-less clip (older authoring): the walking progress is
            # baked straight into RootT/RootQ and the keep-flags say which
            # components the runtime would extract as root motion. Approximate
            # that extraction (validated against live Unity on a real
            # walk-cycle clip -- see git history for the error numbers).
            root_x = full_t.x if keep_position_xz else 0.0
            root_y = full_t.y if keep_position_y else 0.0
            root_z = full_t.z if keep_position_xz else 0.0
            root_t_simple = Vector((root_x, root_y, root_z))
            motion_t = full_t - root_t_simple
            if keep_orientation:
                root_q = full_q
                motion_q = Quaternion()
            else:
                # Extract only the yaw (Y-axis) twist component, matching
                # Unity's own Y-up convention for "orientation". Composition
                # order: the root object's rotation is the OUTER transform
                # (world = motion_q @ r_hips), so the residual must be
                # root_q = motion_q^-1 @ full_q -- the other order solves the
                # opposite composition and breaks whenever there is lean.
                twist_y = Quaternion((full_q.w, 0.0, full_q.y, 0.0)).normalized()
                root_q = (twist_y.inverted() @ full_q).normalized()
                motion_q = twist_y
        # root_t_simple is expressed in the same (unrotated) frame as full_t;
        # since the object's rotation (motion_q) sits between it and the
        # hips in the scene composition, the hips' own position must be
        # counter-rotated by the same amount so world = motion_q @ (root_t +
        # ...) recomposes back to root_t_simple exactly (see the rotation
        # comment above for why order matters here).
        root_t = motion_q.inverted() @ root_t_simple
        motion = (motion_t, motion_q)

        fk = self._provisional_fk(muscle_lookup)
        if not all(name in fk for name in
                  ("LeftUpperArm", "RightUpperArm", "LeftUpperLeg", "RightUpperLeg")):
            return root_t, root_q, motion  # avatar too incomplete to solve; best-effort fallback

        q_provisional = self._compute_orientation(fk)
        mass_center_provisional = self._compute_mass_center(fk)

        r_hips = (root_q @ self._q_rest @ q_provisional.inverted()).normalized()
        t_hips = root_t - (r_hips @ mass_center_provisional)
        return t_hips, r_hips, motion
