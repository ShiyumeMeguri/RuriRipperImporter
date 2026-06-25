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
except ImportError:
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


def build_action(clip_doc, armature_obj, maps, path_to_meshobjects=None, options=None):
    """Create a Blender action from a parsed AnimationClip document."""
    options = options or {}
    data = clip_doc.data
    name = data.get("m_Name", "Clip")
    sample_rate = data.get("m_SampleRate", 60.0) or 60.0
    nodes = maps["nodes"]
    path_to_bone = maps["path_to_bone"]
    # Build a path -> node lookup for rest transforms.
    path_to_node = {n.path: n for n in nodes.values() if n.path}

    # Collect curves keyed by transform path.
    rot = {}     # path -> {x,y,z,w: curve}
    pos = {}     # path -> {x,y,z: curve}
    scale = {}   # path -> {x,y,z: curve}
    euler = {}   # path -> {x,y,z: curve}
    for entry in data.get("m_RotationCurves") or []:
        rot[entry.get("path", "")] = _read_vector_curve(entry, ("x", "y", "z", "w"))
    for entry in data.get("m_PositionCurves") or []:
        pos[entry.get("path", "")] = _read_vector_curve(entry, ("x", "y", "z"))
    for entry in data.get("m_ScaleCurves") or []:
        scale[entry.get("path", "")] = _read_vector_curve(entry, ("x", "y", "z"))
    for entry in data.get("m_EulerCurves") or []:
        euler[entry.get("path", "")] = _read_vector_curve(entry, ("x", "y", "z"))

    duration = _max_time(rot, pos, scale, euler)
    for entry in data.get("m_FloatCurves") or []:
        c = _read_vector_curve(entry, ("v",))
        if len(c["v"].times):
            duration = max(duration, float(c["v"].times[-1]))
    n_frames = max(1, int(round(duration * sample_rate)) + 1)
    times = np.arange(n_frames, dtype=np.float64) / sample_rate

    action = bpy.data.actions.new(name)
    if hasattr(action, "use_fake_user"):
        action.use_fake_user = True
    bone_fcurves, slot = _prepare_channels(action, name, "OBJECT")

    animated_paths = set(rot) | set(pos) | set(scale) | set(euler)
    conv = coordinate.conversion_matrix()

    for path in animated_paths:
        bone_name = path_to_bone.get(path)
        node = path_to_node.get(path)
        if not bone_name or node is None:
            continue
        rest_loc = node.local.translation.copy()
        rest_quat = node.local.to_quaternion()
        rest_scale = node.local.to_scale()
        l_rest_inv = node.local.inverted_safe()

        rc = rot.get(path)
        pc = pos.get(path)
        sc = scale.get(path)
        ec = euler.get(path)

        locs = np.empty((n_frames, 3), dtype=np.float32)
        quats = np.empty((n_frames, 4), dtype=np.float32)
        scales = np.empty((n_frames, 3), dtype=np.float32)

        for fi, t in enumerate(times):
            if pc:
                tr = (pc["x"].evaluate(t), pc["y"].evaluate(t), pc["z"].evaluate(t))
            else:
                tr = (rest_loc.x, rest_loc.y, rest_loc.z)
            if rc:
                q = Quaternion((rc["w"].evaluate(t), rc["x"].evaluate(t),
                                rc["y"].evaluate(t), rc["z"].evaluate(t)))
                if q.magnitude < 1e-8:
                    q = rest_quat.copy()
                else:
                    q.normalize()
            elif ec:
                from mathutils import Euler
                e = Euler((np.radians(ec["x"].evaluate(t)),
                           np.radians(ec["y"].evaluate(t)),
                           np.radians(ec["z"].evaluate(t))), "XYZ")
                q = e.to_quaternion()
            else:
                q = rest_quat
            if sc:
                sv = (sc["x"].evaluate(t), sc["y"].evaluate(t), sc["z"].evaluate(t))
            else:
                sv = (rest_scale.x, rest_scale.y, rest_scale.z)

            l_anim = (Matrix.Translation(tr)
                      @ q.to_matrix().to_4x4()
                      @ Matrix.Diagonal((sv[0], sv[1], sv[2], 1.0)))
            basis = conv @ (l_rest_inv @ l_anim) @ conv
            bloc, bquat, bscale = basis.decompose()
            locs[fi] = (bloc.x, bloc.y, bloc.z)
            quats[fi] = (bquat.w, bquat.x, bquat.y, bquat.z)
            scales[fi] = (bscale.x, bscale.y, bscale.z)

        _write_bone_fcurves(bone_fcurves, bone_name, times * sample_rate,
                            locs, quats, scales)

    if path_to_meshobjects:
        _apply_float_curves(action, data, path_to_meshobjects, sample_rate, times)

    return action, slot, n_frames


def _prepare_channels(action, slot_name, id_type):
    """Return (fcurves_collection, slot) for the new slotted Action API,
    falling back to legacy ``action.fcurves`` on older Blender."""
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


def _write_bone_fcurves(fcurves, bone_name, frames, locs, quats, scales):
    base = f'pose.bones["{_escape(bone_name)}"]'
    channels = [
        (base + ".location", 3, locs),
        (base + ".rotation_quaternion", 4, quats),
        (base + ".scale", 3, scales),
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


def _apply_float_curves(action, data, path_to_meshobjects, sample_rate, times):
    n = len(times)
    for entry in data.get("m_FloatCurves") or []:
        attribute = entry.get("attribute", "")
        path = entry.get("path", "")
        if not attribute.startswith("blendShape."):
            continue
        shape_name = attribute[len("blendShape."):]
        objs = path_to_meshobjects.get(path) or []
        curve = _read_vector_curve(entry, ("v",))["v"]
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
                co[1::2] = [curve.evaluate(t) / 100.0 for t in times]
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
        clip_file = asset_db.load_guid(guid)
        if not clip_file:
            continue
        clip_doc = clip_file.first("AnimationClip")
        if clip_doc is None:
            continue
        action, slot, _ = build_action(clip_doc, armature_obj, maps,
                                       path_to_meshobjects, options)
        actions.append((action, slot))
    return actions
