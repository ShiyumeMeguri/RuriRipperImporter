"""Import Unity AnimationClips (.anim) as Blender actions.

Each clip is baked frame-by-frame onto the armature's pose bones.  Unity stores
rotation/position/scale curves as cubic-Hermite keys per transform path; we
evaluate them at every frame, compose the animated local matrix, and express it
as a pose-bone basis via the conjugation identity:

    matrix_basis(f) = C @ (L_rest_unity^-1 @ L_anim_unity(f)) @ C

Baking is written straight into fcurves with ``foreach_set`` for speed.
Blendshape (``blendShape.*``) float curves are applied to shape-key values when a
matching mesh object is known.
"""

from __future__ import annotations

import numpy as np

try:
    from . import clip_curves, coordinate, humanoid_retarget
except ImportError:
    import clip_curves
    import coordinate
    import humanoid_retarget

import bpy
from mathutils import Matrix, Quaternion


class _HermiteCurve:
    """A single scalar Unity AnimationCurve channel."""

    def __init__(self):
        self.times = []
        self.values = []
        self.in_slopes = []
        self.out_slopes = []

    def add(self, t, v, in_s, out_s):
        self.times.append(t)
        self.values.append(v)
        self.in_slopes.append(in_s)
        self.out_slopes.append(out_s)

    def finalize(self):
        order = np.argsort(self.times)
        self.times = np.asarray(self.times, dtype=np.float64)[order]
        self.values = np.asarray(self.values, dtype=np.float64)[order]
        self.in_slopes = np.asarray(self.in_slopes, dtype=np.float64)[order]
        self.out_slopes = np.asarray(self.out_slopes, dtype=np.float64)[order]

    def evaluate(self, t):
        times = self.times
        n = len(times)
        if n == 0:
            return 0.0
        if t <= times[0]:
            return float(self.values[0])
        if t >= times[-1]:
            return float(self.values[-1])
        i = int(np.searchsorted(times, t) - 1)
        i = max(0, min(i, n - 2))
        t0, t1 = times[i], times[i + 1]
        dt = t1 - t0
        if dt <= 1e-9:
            return float(self.values[i])
        u = (t - t0) / dt
        v0, v1 = self.values[i], self.values[i + 1]
        m0 = self.out_slopes[i] * dt
        m1 = self.in_slopes[i + 1] * dt
        u2 = u * u
        u3 = u2 * u
        h00 = 2 * u3 - 3 * u2 + 1
        h10 = u3 - 2 * u2 + u
        h01 = -2 * u3 + 3 * u2
        h11 = u3 - u2
        return float(h00 * v0 + h10 * m0 + h01 * v1 + h11 * m1)


def _read_vector_curve(curve_entry, components):
    """Build a dict component -> _HermiteCurve from one m_RotationCurves entry."""
    out = {c: _HermiteCurve() for c in components}
    keys = (curve_entry.get("curve") or {}).get("m_Curve") or []
    for k in keys:
        t = k.get("time", 0.0)
        value = k.get("value")
        in_s = k.get("inSlope")
        out_s = k.get("outSlope")
        if isinstance(value, dict):
            for c in components:
                out[c].add(t, value.get(c, 0.0),
                           (in_s or {}).get(c, 0.0) if isinstance(in_s, dict) else 0.0,
                           (out_s or {}).get(c, 0.0) if isinstance(out_s, dict) else 0.0)
        else:
            c = components[0]
            out[c].add(t, value or 0.0,
                       in_s if isinstance(in_s, (int, float)) else 0.0,
                       out_s if isinstance(out_s, (int, float)) else 0.0)
    for c in out.values():
        c.finalize()
    return out


def _max_time(*curve_dicts):
    m = 0.0
    for cd in curve_dicts:
        for curve in cd.values():
            for entry in curve.values():
                if len(entry.times):
                    m = max(m, float(entry.times[-1]))
    return m


# ── vectorized pose math ──────────────────────────────────────────────────────
#
# The per-frame mathutils compose/decompose (Matrix.Translation @ quat @ scale,
# conjugate, .decompose()) was the bake loop's second bottleneck after curve
# evaluation: n_frames x n_paths python-object round-trips. These helpers do
# the identical math for a whole channel at once on (n, ...) arrays.

def _quats_to_matrices(quats_wxyz):
    """(n,4) wxyz unit quaternions -> (n,3,3) rotation matrices."""
    w = quats_wxyz[:, 0]
    x = quats_wxyz[:, 1]
    y = quats_wxyz[:, 2]
    z = quats_wxyz[:, 3]
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    m = np.empty((len(w), 3, 3), dtype=np.float64)
    m[:, 0, 0] = 1.0 - 2.0 * (yy + zz)
    m[:, 0, 1] = 2.0 * (xy - wz)
    m[:, 0, 2] = 2.0 * (xz + wy)
    m[:, 1, 0] = 2.0 * (xy + wz)
    m[:, 1, 1] = 1.0 - 2.0 * (xx + zz)
    m[:, 1, 2] = 2.0 * (yz - wx)
    m[:, 2, 0] = 2.0 * (xz - wy)
    m[:, 2, 1] = 2.0 * (yz + wx)
    m[:, 2, 2] = 1.0 - 2.0 * (xx + yy)
    return m


def _matrices_to_quats(matrices):
    """(n,3,3) normalized rotation matrices -> (n,4) wxyz quaternions.

    A faithful vectorization of Blender's own mat3_normalized_to_quat_fast
    (math_rotation.cc, Mike Day's branch selection + w>=0 sign fixups) --
    NOT a generic Shepperd: for genuinely orthonormal input every method
    agrees, but the bake conjugates through rest matrices whose non-uniform
    scale leaves SHEAR in the 3x3, and there the branch structure decides the
    answer. Matching mathutils' branches keeps the vectorized bake
    bit-for-branch equivalent to the per-frame decompose() it replaced.
    Blender's mat[a][b] is column-major: mat[a][b] == matrices[:, b, a]."""
    r = matrices
    quats = np.zeros((len(r), 4), dtype=np.float64)

    branch_x = (r[:, 2, 2] < 0.0) & (r[:, 0, 0] > r[:, 1, 1])
    branch_y = (r[:, 2, 2] < 0.0) & ~branch_x
    branch_z = (r[:, 2, 2] >= 0.0) & (r[:, 0, 0] < -r[:, 1, 1])
    branch_w = (r[:, 2, 2] >= 0.0) & ~branch_z

    mask = branch_x
    if mask.any():
        m = r[mask]
        trace = 1.0 + m[:, 0, 0] - m[:, 1, 1] - m[:, 2, 2]
        s = 2.0 * np.sqrt(np.maximum(trace, 0.0))
        s = np.where(m[:, 2, 1] < m[:, 1, 2], -s, s)  # mat[1][2] < mat[2][1]
        quats[mask, 1] = 0.25 * s
        inv = 1.0 / np.where(s == 0.0, 1.0, s)
        quats[mask, 0] = (m[:, 2, 1] - m[:, 1, 2]) * inv
        quats[mask, 2] = (m[:, 1, 0] + m[:, 0, 1]) * inv
        quats[mask, 3] = (m[:, 0, 2] + m[:, 2, 0]) * inv
        degenerate = ((trace == 1.0) & (quats[mask, 0] == 0.0)
                      & (quats[mask, 2] == 0.0) & (quats[mask, 3] == 0.0))
        if degenerate.any():
            rows = np.flatnonzero(mask)[degenerate]
            quats[rows, 1] = 1.0

    mask = branch_y
    if mask.any():
        m = r[mask]
        trace = 1.0 - m[:, 0, 0] + m[:, 1, 1] - m[:, 2, 2]
        s = 2.0 * np.sqrt(np.maximum(trace, 0.0))
        s = np.where(m[:, 0, 2] < m[:, 2, 0], -s, s)  # mat[2][0] < mat[0][2]
        quats[mask, 2] = 0.25 * s
        inv = 1.0 / np.where(s == 0.0, 1.0, s)
        quats[mask, 0] = (m[:, 0, 2] - m[:, 2, 0]) * inv
        quats[mask, 1] = (m[:, 1, 0] + m[:, 0, 1]) * inv
        quats[mask, 3] = (m[:, 2, 1] + m[:, 1, 2]) * inv
        degenerate = ((trace == 1.0) & (quats[mask, 0] == 0.0)
                      & (quats[mask, 1] == 0.0) & (quats[mask, 3] == 0.0))
        if degenerate.any():
            rows = np.flatnonzero(mask)[degenerate]
            quats[rows, 2] = 1.0

    mask = branch_z
    if mask.any():
        m = r[mask]
        trace = 1.0 - m[:, 0, 0] - m[:, 1, 1] + m[:, 2, 2]
        s = 2.0 * np.sqrt(np.maximum(trace, 0.0))
        s = np.where(m[:, 1, 0] < m[:, 0, 1], -s, s)  # mat[0][1] < mat[1][0]
        quats[mask, 3] = 0.25 * s
        inv = 1.0 / np.where(s == 0.0, 1.0, s)
        quats[mask, 0] = (m[:, 1, 0] - m[:, 0, 1]) * inv
        quats[mask, 1] = (m[:, 0, 2] + m[:, 2, 0]) * inv
        quats[mask, 2] = (m[:, 2, 1] + m[:, 1, 2]) * inv
        degenerate = ((trace == 1.0) & (quats[mask, 0] == 0.0)
                      & (quats[mask, 1] == 0.0) & (quats[mask, 2] == 0.0))
        if degenerate.any():
            rows = np.flatnonzero(mask)[degenerate]
            quats[rows, 3] = 1.0

    mask = branch_w
    if mask.any():
        m = r[mask]
        trace = 1.0 + m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
        s = 2.0 * np.sqrt(np.maximum(trace, 0.0))
        quats[mask, 0] = 0.25 * s
        inv = 1.0 / np.where(s == 0.0, 1.0, s)
        quats[mask, 1] = (m[:, 2, 1] - m[:, 1, 2]) * inv
        quats[mask, 2] = (m[:, 0, 2] - m[:, 2, 0]) * inv
        quats[mask, 3] = (m[:, 1, 0] - m[:, 0, 1]) * inv
        degenerate = ((trace == 1.0) & (quats[mask, 1] == 0.0)
                      & (quats[mask, 2] == 0.0) & (quats[mask, 3] == 0.0))
        if degenerate.any():
            rows = np.flatnonzero(mask)[degenerate]
            quats[rows, 0] = 1.0

    # decompose() hands the branch result through normalize_qt.
    norms = np.linalg.norm(quats, axis=1)
    norms[norms < 1e-20] = 1.0
    return quats / norms[:, None]


def _conjugated_pose_arrays(locs, quats_wxyz, scales, l_rest_inv, conv):
    """The whole-channel form of the per-frame bake identity

        basis(f) = conv @ (l_rest_inv @ (T(loc) R(quat) S(scale))) @ conv
        loc/quat/scale(f) = basis(f).decompose()

    on (n,3)/(n,4)/(n,3) arrays at once. Decompose parity with mathutils:
    translation from the 4th column, scale from the 3x3 column lengths (X
    negated when the determinant is negative, Blender's own convention),
    rotation from the scale-normalized columns."""
    n = len(locs)
    rotation = _quats_to_matrices(quats_wxyz)
    m = np.zeros((n, 4, 4), dtype=np.float64)
    # T @ R @ S: the 3x3 block is R with column j scaled by scale_j.
    m[:, :3, :3] = rotation * scales[:, None, :]
    m[:, :3, 3] = locs
    m[:, 3, 3] = 1.0

    left = np.asarray(conv @ l_rest_inv, dtype=np.float64)
    right = np.asarray(conv, dtype=np.float64)
    basis = left[None] @ m @ right[None]

    out_locs = basis[:, :3, 3]
    linear = basis[:, :3, :3]
    out_scales = np.linalg.norm(linear, axis=1)  # column lengths
    safe = np.where(out_scales < 1e-12, 1.0, out_scales)
    normalized = linear / safe[:, None, :]
    # mat3_to_rot_size parity: a negative determinant negates the WHOLE
    # normalized rotation and ALL THREE sizes (Blender's convention), not
    # just one axis.
    negative = np.linalg.det(normalized) < 0.0
    if negative.any():
        normalized[negative] = -normalized[negative]
        out_scales[negative] = -out_scales[negative]
    out_quats = _matrices_to_quats(normalized)
    return (out_locs.astype(np.float32),
            out_quats.astype(np.float32),
            out_scales.astype(np.float32))


def build_action(clip_doc, armature_obj, maps, path_to_meshobjects=None, options=None):
    """Create a Blender action from a clip -- either an already-built
    clip_curves.ClipCurves (the bridge's zero-parse blob path) or a parsed
    AnimationClip YAML document (converted here)."""
    options = options or {}
    # Pose bones default to QUATERNION already; the armature OBJECT itself
    # defaults to XYZ Euler, so object-level rotation_quaternion f-curves
    # (extracted root motion, see _bake_muscles) would silently do nothing
    # without this -- Blender only evaluates the channel matching the
    # current rotation_mode.
    armature_obj.rotation_mode = 'QUATERNION'
    if isinstance(clip_doc, clip_curves.ClipCurves):
        clip = clip_doc
    else:
        clip = clip_curves.ClipCurves.from_document(clip_doc.data)
    name = clip.name
    sample_rate = clip.sample_rate or 60.0
    nodes = maps["nodes"]
    path_to_bone = maps["path_to_bone"]
    # Build a path -> node lookup for rest transforms.
    path_to_node = {n.path: n for n in nodes.values() if n.path}
    # bone-name -> node, used by the muscle retarget which keys bones by name.
    name_to_node = {}
    for _n in nodes.values():
        if _n.path:
            _bone_name = path_to_bone.get(_n.path)
            if _bone_name:
                name_to_node[_bone_name] = _n

    # Curves keyed by transform path (one Channel per path per kind).
    rot = {channel.path: channel for channel in clip.rotations}
    pos = {channel.path: channel for channel in clip.positions}
    scale = {channel.path: channel for channel in clip.scales}
    euler = {channel.path: channel for channel in clip.eulers}

    duration = clip.max_time()
    n_frames = max(1, int(round(duration * sample_rate)) + 1)
    times = np.arange(n_frames, dtype=np.float64) / sample_rate

    action = bpy.data.actions.new(name)
    if hasattr(action, "use_fake_user"):
        action.use_fake_user = True
    bone_fcurves, slot = _prepare_channels(action, name, "OBJECT")

    animated_paths = set(rot) | set(pos) | set(scale) | set(euler)
    conv = coordinate.conversion_matrix()

    # Bones the humanoid retargeter drives take that data's motion, not any
    # co-existing generic transform curve for the same path: a Human bone can
    # collide by name with a literal skeleton node (e.g. Hips mapped to a bone
    # literally named "Root", which also carries its own root-motion Position/
    # Rotation curves at path "") -- Unity's own Mecanim runtime always plays a
    # humanoid Avatar-bound clip through the muscle system for these bones, and
    # _bake_muscles unconditionally writes every retargeter.bone_targets() bone
    # further down, so skip them here to avoid writing the same fcurve twice.
    retargeter = maps.get("retargeter")
    muscle_bone_names = set(retargeter.bone_targets().values()) if retargeter is not None else set()

    frames = times * sample_rate
    for path in animated_paths:
        bone_name = path_to_bone.get(path)
        node = path_to_node.get(path)
        if not bone_name or node is None or bone_name in muscle_bone_names:
            continue
        rest_quat = node.local.to_quaternion()
        l_rest_inv = node.local.inverted_safe()

        rc = rot.get(path)
        pc = pos.get(path)
        sc = scale.get(path)
        ec = euler.get(path)

        if pc is not None:
            locs = pc.sample(times)
        else:
            rest_loc = node.local.translation
            locs = np.tile((rest_loc.x, rest_loc.y, rest_loc.z), (n_frames, 1))

        if rc is not None:
            xyzw = rc.sample(times)
            quats = xyzw[:, (3, 0, 1, 2)]
            norms = np.linalg.norm(quats, axis=1)
            tiny = norms < 1e-8
            quats = quats / np.where(tiny, 1.0, norms)[:, None]
            if tiny.any():
                quats[tiny] = (rest_quat.w, rest_quat.x, rest_quat.y, rest_quat.z)
        elif ec is not None:
            # Rare path (generic scene assets): defer to mathutils per frame
            # for exact XYZ euler-order parity rather than re-deriving it.
            from mathutils import Euler
            degrees = ec.sample(times)
            quats = np.empty((n_frames, 4), dtype=np.float64)
            for fi in range(n_frames):
                q = Euler(np.radians(degrees[fi]), "XYZ").to_quaternion()
                quats[fi] = (q.w, q.x, q.y, q.z)
        else:
            quats = np.tile((rest_quat.w, rest_quat.x, rest_quat.y, rest_quat.z),
                            (n_frames, 1))

        if sc is not None:
            scales = sc.sample(times)
        else:
            rest_scale = node.local.to_scale()
            scales = np.tile((rest_scale.x, rest_scale.y, rest_scale.z), (n_frames, 1))

        out_locs, out_quats, out_scales = _conjugated_pose_arrays(
            locs, quats, scales, l_rest_inv, conv)
        _write_bone_fcurves(bone_fcurves, bone_name, frames,
                            out_locs, out_quats, out_scales)

    if retargeter is not None:
        _bake_muscles(retargeter, clip, name_to_node, bone_fcurves, conv,
                      times, n_frames, sample_rate)

    if path_to_meshobjects:
        _apply_float_curves(action, clip, path_to_meshobjects, sample_rate, times)

    return action, slot, n_frames


def _bake_muscles(retargeter, clip, name_to_node, bone_fcurves, conv, times,
                  n_frames, sample_rate):
    """Reconstruct and bake every human bone's rotation from the clip's muscle
    curves -- the body's only motion in a humanoid clip.

    Mirrors the transform-curve baking: the muscles give each bone a local-frame
    rotation delta, applied to its rest local matrix and conjugated into the
    pose-bone basis exactly as an animated transform path is.
    """
    sampled = {}
    for channel in clip.floats:
        attribute = channel.attribute
        if humanoid_retarget.is_muscle(attribute) or humanoid_retarget.is_root(attribute):
            sampled[attribute] = channel.sample(times)[:, 0]
    if not sampled:
        return
    # Whichever Root axes the clip doesn't "keep original" for are extracted
    # as root motion belonging to the character's root, not the hips -- see
    # humanoid_retarget.py's body_transform() docstring.
    keep_position_xz = clip.keep_position_xz
    keep_position_y = clip.keep_position_y
    keep_orientation = clip.keep_orientation
    # Every channel is sampled at every frame in ONE vectorized pass above;
    # the per-frame dicts below are just cheap views for the muscle solver's
    # lookup interface, reused across all driven bones.
    attributes = list(sampled)
    columns = [sampled[a] for a in attributes]
    values = [{attributes[ci]: columns[ci][fi] for ci in range(len(attributes))}
              for fi in range(n_frames)]

    frames = times * sample_rate
    hips_bone = retargeter.hips_bone()
    # Root motion body_transform() extracts (whichever axes keep_position_xz/y/
    # keep_orientation are False) belongs on the character's own root object,
    # not the hips -- collected here while baking the hips bone below, then
    # written onto the armature object's own transform afterward.  Defaults to
    # identity (matches an object with no root-motion track).
    motion_locs = np.zeros((n_frames, 3), dtype=np.float32)
    motion_quats = np.tile(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32), (n_frames, 1))
    has_motion = False

    # Every non-hips bone's rotation for every frame, WITH Unity's TwistSolve
    # parent<->child redistribution already applied (see
    # humanoid_retarget.py's body_local_quats) -- computed once per frame
    # here rather than once per (bone, frame) inside the loop below, since
    # TwistSolve needs several bones' rotations together, not one at a time.
    body_quats_by_frame = [retargeter.body_local_quats(values[fi].get) for fi in range(n_frames)]

    for human_name, bone_name in retargeter.bone_targets().items():
        node = name_to_node.get(bone_name)
        if node is None:
            continue
        rest_loc = node.local.translation
        rest_quat = node.local.to_quaternion()
        rest_scale = node.local.to_scale()
        l_rest_inv = node.local.inverted_safe()
        is_hips = bone_name == hips_bone

        in_locs = np.tile((rest_loc.x, rest_loc.y, rest_loc.z), (n_frames, 1))
        in_quats = np.tile((rest_quat.w, rest_quat.x, rest_quat.y, rest_quat.z),
                           (n_frames, 1))
        in_scales = np.tile((rest_scale.x, rest_scale.y, rest_scale.z), (n_frames, 1))
        if is_hips:
            # body_transform() reconstructs the hips' FULL absolute local
            # transform directly (see humanoid_retarget.py's root-motion
            # section: RootT/RootQ are the avatar's mass-center/orientation
            # reference, not the hips' own transform, so this composes a
            # provisional FK against them rather than reading RootT/RootQ
            # as a hips-local delta).  Used directly, like the muscle
            # branch below -- not composed with rest_quat. A None result
            # leaves this frame at rest (= node.local, which IS the rest TRS).
            for fi in range(n_frames):
                body = retargeter.body_transform(values[fi].get,
                                                 keep_position_xz=keep_position_xz,
                                                 keep_position_y=keep_position_y,
                                                 keep_orientation=keep_orientation)
                if body is None:
                    continue
                position, rotation, motion = body
                in_locs[fi] = (position.x, position.y, position.z)
                in_quats[fi] = (rotation.w, rotation.x, rotation.y, rotation.z)
                motion_t, motion_q = motion
                motion_locs[fi] = (motion_t.x, motion_t.y, motion_t.z)
                motion_quats[fi] = (motion_q.w, motion_q.x, motion_q.y, motion_q.z)
                # Data-driven: write object motion iff any frame actually
                # carries some (trajectory clips always do; the settings
                # flags no longer decide -- see body_transform).
                if (motion_t.length_squared > 1e-10
                        or abs(motion_q.w) < 0.99999995):
                    has_motion = True
        else:
            # The muscle gives this bone's FULL absolute local rotation for the
            # frame directly (preQ @ swingTwist @ inv(postQ), then TwistSolve's
            # parent<->child redistribution) -- not a delta, and not composed
            # with rest_quat (see humanoid_retarget.py's module docstring
            # RETRACTION for why an earlier revision's rest_quat
            # division/recomposition here was a no-op that happened to still work).
            for fi in range(n_frames):
                anim_quat = body_quats_by_frame[fi].get(human_name)
                if anim_quat is not None:
                    in_quats[fi] = (anim_quat.w, anim_quat.x, anim_quat.y, anim_quat.z)
        locs, quats, scales = _conjugated_pose_arrays(
            in_locs, in_quats, in_scales, l_rest_inv, conv)
        _write_bone_fcurves(bone_fcurves, bone_name, frames, locs, quats, scales)

    # Bake whatever body_transform() extracted as root motion onto the
    # armature object's own transform, in Unity world/root space -- there is
    # no "rest" to subtract here (the object's own rest is identity), so each
    # frame is a straight coordinate.convert_matrix of the extracted TRS.
    # has_motion is now set per-frame off the ACTUAL extracted values (see the
    # hips branch above) -- a trajectory clip writes its object track whatever
    # the keep-flags say, and a genuinely motion-free clip writes none.
    if has_motion:
        # Same conjugation as every bone channel, with an identity "rest": the
        # object's own rest IS identity, so left = conv @ I.
        obj_locs, obj_quats, _decomposed_scales = _conjugated_pose_arrays(
            motion_locs.astype(np.float64), motion_quats.astype(np.float64),
            np.ones((n_frames, 3), dtype=np.float64), Matrix.Identity(4), conv)
        _write_bone_fcurves(bone_fcurves, None, frames, obj_locs, obj_quats,
                            np.ones((n_frames, 3), dtype=np.float32))


def _prepare_channels(action, slot_name, id_type):
    """Return (fcurves_collection, slot) for the new slotted Action API,
    falling back to legacy ``action.fcurves`` on older Blender.

    The slot is deliberately named after the CLIP (slot_name = the clip's
    m_Name), NOT uniformly after the armature. Do not "improve" this to a
    shared name: Blender 5.1.2 has an empirically-pinned segfault (reproduced
    5/5 headless, same crash address every time) when an ARMATURE object has
    an action + explicitly-set slot assigned and two actions exist whose slots
    share one identifier -- the very next ``animation_data.action`` write
    (assign, switch, or even ``= None``) dies in the identifier-matched
    auto-pick path. Unique per-clip identifiers keep that branch unreachable.
    The cost of uniqueness -- Blender auto-picks no slot when the user assigns
    one of these actions by hand -- is repaired by the msgbus watcher below
    (_on_animdata_action_changed), which explicitly assigns the action's own
    single slot instead of relying on identifier matching."""
    if hasattr(action, "layers"):
        try:
            slot = action.slots.new(id_type=id_type, name=slot_name[:63])
        except TypeError:
            slot = action.slots.new(id_type, slot_name[:63])
        layer = action.layers.new("Layer")
        strip = layer.strips.new(type="KEYFRAME")
        channelbag = strip.channelbag(slot, ensure=True)
        return channelbag.fcurves, slot
    return action.fcurves, None


# ── slotted-action assignment repair ─────────────────────────────────────────
#
# Blender 4.4+ slotted actions: assigning ``animation_data.action`` alone plays
# NOTHING -- the evaluated channels live under a slot, and ``action_slot`` must
# also be set. The Action editor's own assignment picks a slot; most other UI
# surfaces (and plain Python assignment) do not, verified headless on 5.1.2:
# a fresh object assigning a single-slot action auto-picks None, every time.
# That is exactly the reported "assigned the action directly onto the armature
# and it does not play, but the Action editor works" behavior -- the imported
# data is fine, the slot linkage is just absent.
#
# Two repair layers close the gap, both explicitly assigning the action's own
# single suitable slot -- explicit assignment of a UNIQUE-identifier slot is
# the one shape the 5.1.2 crash matrix proved safe (it is also what the
# importer itself has always done for the first imported clip):
#
#   1. a msgbus watcher on (AnimData, "action") -- cheap, but msgbus only
#      notifies for changes made THROUGH THE UI. A plain Python assignment
#      (``obj.animation_data.action = act`` from any script, driver, or tool)
#      never publishes, and even some UI surfaces proved unreliable in
#      practice -- exactly the reported "I still have to pick the slot in the
#      Action editor every time I switch clips";
#   2. a depsgraph_update_post handler -- the reliable net. EVERY assignment
#      path (UI dropdown, Python, other addons) causes a depsgraph update, so
#      an action-without-slot is healed by the very next update tick and the
#      clip just plays. The scan is a couple of attribute reads per object
#      (micro-seconds at real scene sizes) and self-quiesces: once every slot
#      is assigned it writes nothing, so it cannot ping-pong the depsgraph.

_MSGBUS_OWNER = object()


def _repair_unassigned_action_slots():
    """Assign the action's single suitable slot anywhere an action was
    assigned without one -- armature/object actions and shape-key actions
    both (the two AnimData owners this importer creates)."""
    for obj in bpy.data.objects:
        _repair_adt(obj.animation_data, "OBJECT")
    for shape_keys in bpy.data.shape_keys:
        _repair_adt(shape_keys.animation_data, "KEY")


def _repair_adt(adt, target_id_type):
    if adt is None or adt.action is None or adt.action_slot is not None:
        return
    action = adt.action
    if not hasattr(action, "slots"):
        return
    suitable = [s for s in action.slots if getattr(s, "target_id_type", "") == target_id_type]
    if len(suitable) == 1:
        try:
            adt.action_slot = suitable[0]
        except Exception:
            pass  # restricted context / unexpected state -- leave it to the user


def _on_animdata_action_changed():
    try:
        _repair_unassigned_action_slots()
    except Exception:
        pass  # never let a notify callback throw into Blender's message bus


def _subscribe_msgbus():
    bpy.msgbus.clear_by_owner(_MSGBUS_OWNER)
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.AnimData, "action"),
        owner=_MSGBUS_OWNER,
        args=(),
        notify=_on_animdata_action_changed,
    )


import bpy.app.handlers  # noqa: E402  (handlers submodule, used by the persistent hooks below)


@bpy.app.handlers.persistent
def _resubscribe_on_load(_dummy=None):
    # msgbus subscriptions do not survive loading a .blend -- re-arm after every load.
    _subscribe_msgbus()


@bpy.app.handlers.persistent
def _repair_on_depsgraph(_scene=None, _depsgraph=None):
    try:
        _repair_unassigned_action_slots()
    except Exception:
        pass  # a handler must never throw into the depsgraph


def register_slot_autofix():
    _subscribe_msgbus()
    if _resubscribe_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_resubscribe_on_load)
    if _repair_on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_repair_on_depsgraph)


def unregister_slot_autofix():
    bpy.msgbus.clear_by_owner(_MSGBUS_OWNER)
    if _resubscribe_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_resubscribe_on_load)
    if _repair_on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_repair_on_depsgraph)


def _write_bone_fcurves(fcurves, bone_name, frames, locs, quats, scales):
    """``bone_name=None`` writes directly to the object's own transform
    (no ``pose.bones[...]`` prefix) -- used to bake extracted root motion
    onto the armature object itself rather than any of its pose bones."""
    # q and -q encode the same rotation, and every upstream source canonicalizes
    # its hemisphere PER FRAME (ACL's drop-w reconstruction is w>=0 by
    # construction; the generic path's matrix decompose() picks a hemisphere),
    # so any rotation crossing 180deg lands antipodal between consecutive keys.
    # Each key's POSE is still exact, but componentwise fcurve interpolation
    # between an antipodal pair sweeps through a degenerate quaternion -- the
    # reported one-frame whole-bone twitch (Bip001 on battle_skill_ult, plus
    # every IK/Footsteps helper doing full turns). Align each key with its
    # predecessor: XOR-accumulated signs of raw consecutive dots give every
    # key's final hemisphere in one vector pass.
    if len(quats) > 1:
        flips = np.cumsum(np.einsum("ij,ij->i", quats[1:], quats[:-1]) < 0.0) % 2
        if flips.any():
            quats = quats.copy()
            quats[1:][flips == 1] *= -1.0
    prefix = f'pose.bones["{_escape(bone_name)}"].' if bone_name is not None else ""
    channels = [
        (prefix + "location", 3, locs),
        (prefix + "rotation_quaternion", 4, quats),
        (prefix + "scale", 3, scales),
    ]
    n = len(frames)
    for data_path, count, values in channels:
        for axis in range(count):
            fcurve = fcurves.new(data_path, index=axis)
            fcurve.keyframe_points.add(n)
            co = np.empty(n * 2, dtype=np.float64)
            co[0::2] = frames
            co[1::2] = values[:, axis]
            fcurve.keyframe_points.foreach_set("co", co)
            fcurve.keyframe_points.foreach_set(
                "interpolation", np.full(n, 1, dtype=np.int32))  # LINEAR
            fcurve.update()


def _apply_float_curves(action, clip, path_to_meshobjects, sample_rate, times):
    n = len(times)
    for channel in clip.floats:
        attribute = channel.attribute
        path = channel.path
        if not attribute.startswith("blendShape."):
            continue
        shape_name = attribute[len("blendShape."):]
        objs = path_to_meshobjects.get(path) or []
        sampled_values = channel.sample(times)[:, 0]
        for obj in objs:
            mesh = obj.data
            if not mesh.shape_keys or shape_name not in mesh.shape_keys.key_blocks:
                continue
            key = mesh.shape_keys.key_blocks[shape_name]
            # Shape-key value curves live on the mesh's Key datablock, animated
            # through its own action so they bind to the correct id type.
            try:
                shape_keys = mesh.shape_keys
                if shape_keys.animation_data is None:
                    shape_keys.animation_data_create()
                key_action = shape_keys.animation_data.action
                if key_action is None:
                    key_action = bpy.data.actions.new(action.name + "_shapekeys")
                    shape_keys.animation_data.action = key_action
                fcurves, slot = _prepare_channels(key_action, shape_name, "KEY")
                if slot is not None:
                    shape_keys.animation_data.action_slot = slot
                data_path = key.path_from_id("value")
                fcurve = fcurves.new(data_path)
                fcurve.keyframe_points.add(n)
                co = np.empty(n * 2, dtype=np.float64)
                co[0::2] = times * sample_rate
                co[1::2] = sampled_values / 100.0
                fcurve.keyframe_points.foreach_set("co", co)
                fcurve.update()
            except Exception:
                continue


def _escape(name):
    return name.replace("\\", "\\\\").replace('"', '\\"')


def import_clips_from_controller(context, controller_file, asset_db, armature_obj,
                                 maps, path_to_meshobjects=None, options=None):
    """Resolve every AnimationClip referenced by a controller and build actions."""
    guids = []
    seen = set()
    for doc in controller_file.documents:
        motion = doc.data.get("m_Motion") if isinstance(doc.data, dict) else None
        if isinstance(motion, dict) and motion.get("guid") and motion["guid"] not in seen:
            seen.add(motion["guid"])
            guids.append(motion["guid"])
        # AnimatorState may also hold m_Motion indirectly already handled above.

    actions = []
    for guid in guids:
        # Disk fast path: read the .anim's raw text and regex-extract the
        # curves (see clip_curves.from_yaml_text) -- the guid resolves to a
        # real file path in disk mode; bridge-mode databases resolve to the
        # guid itself, which open() can't hit, so they fall through.
        clip = None
        clip_path = asset_db.resolve_guid(guid)
        if clip_path and isinstance(clip_path, str) and clip_path.lower().endswith(".anim"):
            try:
                with open(clip_path, "r", encoding="utf-8", errors="ignore") as handle:
                    clip = clip_curves.ClipCurves.from_yaml_text(handle.read())
            except (OSError, ValueError):
                clip = None
        if clip is None:
            clip_file = asset_db.load_guid(guid)
            if not clip_file:
                continue
            clip_doc = clip_file.first("AnimationClip")
            if clip_doc is None:
                continue
            clip = clip_doc
        action, slot, _ = build_action(clip, armature_obj, maps,
                                       path_to_meshobjects, options)
        actions.append((action, slot))
    return actions
