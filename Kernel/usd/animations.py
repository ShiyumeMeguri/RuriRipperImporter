"""A clip as the performance of one rig: a SkelAnimation layer of its own.

The statement hands a clip over already re-anchored onto the rig's joint paths and, for a muscle-encoded clip,
solved into ordinary bone curves -- in the source engine's own axes, as every animation curve is. This samples
those curves at the clip's rate, carries each key through the target basis' reflection, and writes the joints the clip
moves as time samples. Joints it does not mention keep their rest.
"""

from __future__ import annotations

import numpy as np
from pxr import Gf, Sdf, Usd, UsdSkel, Vt

from .. import statement as kernel_statement
from . import stages
from .names import Namer, identifier

#: The rate a clip is sampled at when it states none.
DEFAULT_RATE = 30.0

#: How blend shape curves are named in a clip, and what their weight is measured in against the target's.
BLEND_SHAPE_PREFIX = "blendShape."
BLEND_SHAPE_UNIT = 100.0


def _reflection():
    """The basis' reflection as a 3x3, and the sign a quaternion's vector part takes crossing it."""
    matrix = np.asarray(kernel_statement.basis(kernel_statement.USD)["matrix"])[:3, :3]
    return matrix, (-1.0 if np.linalg.det(matrix) < 0 else 1.0)


def _quaternion_of_euler(degrees):
    """Quaternions (x, y, z, w) from Euler angles in the source engine's order: about z, then x, then y."""
    result = np.empty((len(degrees), 4))
    for index, (x, y, z) in enumerate(np.radians(degrees)):
        qx = (np.sin(x / 2), 0.0, 0.0, np.cos(x / 2))
        qy = (0.0, np.sin(y / 2), 0.0, np.cos(y / 2))
        qz = (0.0, 0.0, np.sin(z / 2), np.cos(z / 2))
        result[index] = _product(_product(qy, qx), qz)
    return result


def _product(left, right):
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (lw * rx + lx * rw + ly * rz - lz * ry, lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw, lw * rw - lx * rx - ly * ry - lz * rz)


def _continuous(quaternions):
    """The same rotations with each one on the hemisphere of the one before, so interpolation takes the short way."""
    out = quaternions.copy()
    for index in range(1, len(out)):
        if np.dot(out[index], out[index - 1]) < 0:
            out[index] = -out[index]
    return out


def author(layer_path, name, rig, hierarchy, clip):
    """Write ``clip`` as the performance of ``rig`` to ``layer_path``. Returns counts of what it moves, or None when the
    clip moves none of the rig's joints."""
    matrix, vector_sign = _reflection()
    magnitude = np.abs(matrix)
    rate = clip.sample_rate or DEFAULT_RATE
    duration = clip.duration
    times = (np.minimum(np.arange(int(np.ceil(duration * rate)) + 1) / rate, duration) if duration > 0
             else np.zeros(1))
    frames = len(times)
    joint_of_source = {source: joint for joint, source in enumerate(rig.sources)}
    moved = {}
    for kind in ("pos", "rot", "euler", "scale"):
        for channel in clip.of_kind(kind):
            joint = joint_of_source.get(channel.path)
            if joint is not None:
                moved.setdefault(joint, {})[kind] = channel.sample(times)
    shapes = {}
    for channel in clip.of_kind("float"):
        if channel.attribute.startswith(BLEND_SHAPE_PREFIX):
            shapes[channel.attribute[len(BLEND_SHAPE_PREFIX):]] = channel.sample(times)[:, 0] / BLEND_SHAPE_UNIT
    if not moved and not shapes:
        return None
    stage = stages.create(layer_path, kernel_statement.USD, "Clip {0} on {1}".format(name, rig.sources[0] if rig.sources else ""))
    stage.SetTimeCodesPerSecond(rate)
    stage.SetStartTimeCode(0)
    stage.SetEndTimeCode(frames - 1)
    animation = UsdSkel.Animation.Define(stage, "/" + identifier(name))
    stage.SetDefaultPrim(animation.GetPrim())
    animation.GetPrim().CreateAttribute("ruri:sourceName", Sdf.ValueTypeNames.String, custom=True).Set(clip.name)
    animation.GetPrim().CreateAttribute("ruri:sourceKey", Sdf.ValueTypeNames.String, custom=True).Set(clip.key)
    joints = sorted(moved)
    animation.CreateJointsAttr(Vt.TokenArray([rig.tokens[joint] for joint in joints]))
    translations = animation.CreateTranslationsAttr()
    rotations = animation.CreateRotationsAttr()
    scales = animation.CreateScalesAttr()
    columns_t = np.empty((frames, len(joints), 3))
    columns_r = np.empty((frames, len(joints), 4))
    columns_s = np.empty((frames, len(joints), 3))
    for column, joint in enumerate(joints):
        node = hierarchy.nodes[rig.members[joint]]
        curves = moved[joint]
        if "pos" in curves:
            columns_t[:, column] = curves["pos"] @ matrix.T
        else:
            columns_t[:, column] = node.position
        if "rot" in curves:
            quaternion = curves["rot"]
        elif "euler" in curves:
            quaternion = _quaternion_of_euler(curves["euler"])
        else:
            quaternion = None
        if quaternion is not None:
            quaternion = quaternion / np.maximum(np.linalg.norm(quaternion, axis=1, keepdims=True), 1e-12)
            converted = np.concatenate([vector_sign * (quaternion[:, :3] @ matrix.T), quaternion[:, 3:]], axis=1)
            columns_r[:, column] = _continuous(converted)
        else:
            columns_r[:, column] = node.rotation
        columns_s[:, column] = curves["scale"] @ magnitude.T if "scale" in curves else node.scale
    for frame in range(frames):
        code = Usd.TimeCode(frame)
        translations.Set(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(columns_t[frame], dtype=np.float32)), code)
        rotations.Set(Vt.QuatfArray([Gf.Quatf(float(w), Gf.Vec3f(float(x), float(y), float(z)))
                                     for x, y, z, w in columns_r[frame]]), code)
        scales.Set(Vt.Vec3hArray.FromNumpy(np.ascontiguousarray(columns_s[frame], dtype=np.float16)), code)
    if shapes:
        names = Namer()
        tokens = [names.claim("shapes", identifier(shape)) for shape in shapes]
        animation.CreateBlendShapesAttr(Vt.TokenArray(tokens))
        weights = animation.CreateBlendShapeWeightsAttr()
        stacked = np.stack([shapes[shape] for shape in shapes], axis=1)
        for frame in range(frames):
            weights.Set(Vt.FloatArray.FromNumpy(np.ascontiguousarray(stacked[frame], dtype=np.float32)), Usd.TimeCode(frame))
    stage.GetRootLayer().Save()
    return {"joints": len(joints), "frames": frames, "blend_shapes": len(shapes), "rate": rate}
