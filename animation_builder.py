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
    from . import coordinate
except ImportError:  # standalone (non-package) testing
    import coordinate

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


def _channels_by_path(channels, kind, clip_name):
    """``{path: channel}`` for one curve kind, saying so out loud when a path
    carries more than one.

    A second channel on a path the clip already binds is a malformed clip: which
    one drives the bone is nobody's decision, and keying by path quietly keeps
    only the last. That is how a whole body's placement can vanish with nothing
    reported -- the hips' real transform track shadowed by a later duplicate."""
    by_path = {}
    for channel in channels:
        previous = by_path.get(channel.path)
        if previous is not None:
            print("[RuriRipper] {0}: {1} curve on '{2}' is bound twice -- the later one wins; "
                  "the clip is malformed.".format(clip_name, kind, channel.path))
        by_path[channel.path] = channel
    return by_path


SAMPLE_RATE_KEY = "ruri_sample_rate"


def build_action(clip, armature_obj, maps, path_to_meshobjects=None, options=None,
                 display_name=None):
    """Create a Blender action from a clip_curves.ClipCurves (the one clip
    form both the bridge blob and the disk raw-text parser produce).

    ``display_name`` overrides the action's name when the caller holds a better
    one than the clip's own m_Name -- a game whose catalog carries human-readable
    labels while its clips are named after internal controller states."""
    options = options or {}
    # Pose bones default to QUATERNION already; the armature OBJECT itself
    # defaults to XYZ Euler, so object-level rotation_quaternion f-curves
    # (a clip's extracted root motion rides the animator root path) would silently do nothing
    # without this -- Blender only evaluates the channel matching the
    # current rotation_mode.
    armature_obj.rotation_mode = 'QUATERNION'
    name = display_name or clip.name
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
    rot = _channels_by_path(clip.rotations, "rotation", name)
    pos = _channels_by_path(clip.positions, "position", name)
    scale = _channels_by_path(clip.scales, "scale", name)
    euler = _channels_by_path(clip.eulers, "euler", name)

    duration = clip.max_time()
    n_frames = max(1, int(round(duration * sample_rate)) + 1)
    times = np.arange(n_frames, dtype=np.float64) / sample_rate

    action = bpy.data.actions.new(name)
    if hasattr(action, "use_fake_user"):
        action.use_fake_user = True
    # The rate these frames were sampled at, stamped on the artifact itself: a
    # consumer placing this action on a timeline needs seconds, and deriving the
    # rate back out of the frame count needs a clip length that is not always the
    # one the asset states (a decoded clip can run a few frames past its own
    # stopTime).
    action[SAMPLE_RATE_KEY] = float(sample_rate)
    # action.name, not the clip name: Blender has already uniquified it against every
    # existing action, which is exactly the guarantee the slot identifier needs (see
    # _prepare_channels). Importing the same clip twice would otherwise mint a second
    # slot with an identical identifier and re-arm the 5.1.2 crash.
    bone_fcurves, slot = _prepare_channels(action, action.name, "OBJECT")

    animated_paths = set(rot) | set(pos) | set(scale) | set(euler)
    conv = coordinate.conversion_matrix()

    # Every clip reaching here is generic: any muscle encoding was already resolved into these
    # same per-bone transform curves by the C# solver, against the target armature's own stamped
    # avatar (prefab_importer._solve_humanoid_curves), so there is one kind of curve to bake and
    # no muscle solver in this importer at all.
    frames = times * sample_rate
    for path in animated_paths:
        bone_name = path_to_bone.get(path)
        node = path_to_node.get(path)
        if not bone_name or node is None:
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

    if path_to_meshobjects:
        _apply_float_curves(action, clip, path_to_meshobjects, sample_rate, times)

    return action, slot, n_frames



def _prepare_channels(action, slot_name, id_type):
    """Return (fcurves_collection, slot) for the new slotted Action API,
    falling back to legacy ``action.fcurves`` on older Blender.

    The slot is deliberately named after the ACTION (slot_name = action.name,
    already uniquified by Blender), NOT uniformly after the armature. Do not
    "improve" this to a shared name: Blender 5.1.2 has an empirically-pinned
    segfault (reproduced 5/5 headless, same crash address every time) when an
    ARMATURE object has an action + explicitly-set slot assigned and two
    actions exist whose slots share one identifier -- the very next
    ``animation_data.action`` write (assign, switch, or even ``= None``) dies
    in the identifier-matched auto-pick path. Per-action identifiers keep that
    branch unreachable even when the same clip is imported more than once.
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


# ── putting an action ON something ───────────────────────────────────────────
#
# The ONE way anything in this add-on makes an action play. Building an action
# and leaving it in bpy.data is only half the job: the user asked to see an
# animation, and an action nobody assigned is invisible -- it does not play, the
# timeline still spans whatever the last thing spanned, and finding it means
# hunting through the Action editor. Every importer used to end with its own
# copy of "assign it, maybe", they disagreed about the slot and none of them
# touched the frame range, so which of them ran decided whether the clip played.

def set_frame_range(scene, start, end, move_playhead=True):
    """Make the scene's playable range exactly [start, end].

    Rounded to the integer frames a scene range is stated in, and never
    collapsed to a single frame: a degenerate span would leave the timeline
    unable to play at all, which reads as "the import produced nothing"."""
    if scene is None:
        return None
    first = int(round(start))
    last = max(int(round(end)), first + 1)
    scene.frame_start = first
    scene.frame_end = last
    if move_playhead:
        # A playhead left outside the new range shows the rest pose and looks
        # like the clip failed to import.
        scene.frame_set(first)
    return first, last


def action_frame_range(action):
    """The span an action's own curves cover, or None when it has none.

    ``Action.frame_range`` is the truth for slotted actions too (verified on
    5.2: a channelbag-only action reports its curves' real span), while
    ``Action.fcurves`` does not exist on them at all -- so nothing here may
    reach for the fcurves to work this out."""
    span = getattr(action, "frame_range", None)
    if span is None:
        return None
    first, last = float(span[0]), float(span[1])
    return None if last <= first else (first, last)


def adopt_action(owner, action, slot=None, scene=None, frame_range=True):
    """Make ``action`` the one PLAYING on ``owner``, and aim the scene at it.

    ``owner`` is any AnimData holder this add-on animates -- an armature or
    mesh OBJECT, a shape-key datablock. The slot must be set explicitly or the
    action evaluates to nothing (Blender 4.4+; see the repair section below for
    why relying on auto-pick is not an option).

    ``frame_range`` True (the default -- importing an animation IS the request
    to look at it) retimes the scene to the action's own span. Pass False for a
    caller that owns the range itself, e.g. one laying several clips onto a
    timeline, where any single clip's span is the wrong answer.

    ``scene`` defaults to the scene the user is looking at."""
    if owner is None or action is None:
        return None
    if owner.animation_data is None:
        owner.animation_data_create()
    animation = owner.animation_data
    animation.action = action
    if slot is not None and hasattr(animation, "action_slot"):
        animation.action_slot = slot
    if not frame_range:
        return action
    span = action_frame_range(action)
    if span is not None:
        set_frame_range(scene if scene is not None else bpy.context.scene, span[0], span[1])
    return action


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
                key_action = (shape_keys.animation_data.action
                              if shape_keys.animation_data is not None else None)
                if key_action is None:
                    key_action = bpy.data.actions.new(action.name + "_shapekeys")
                fcurves, slot = _prepare_channels(key_action, shape_name, "KEY")
                # The shape-key half of THIS clip: assigned through the one
                # adopter like everything else, but never retiming -- the caller
                # aims the scene once, at the clip's own transform action.
                adopt_action(shape_keys, key_action, slot, frame_range=False)
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
