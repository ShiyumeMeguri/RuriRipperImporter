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
from . import records, stages
from .names import Namer, file_stem, identifier

#: The rate a clip is sampled at when it states none.
DEFAULT_RATE = 30.0

#: How blend shape curves are named in a clip, and what their weight is measured in against the target's.
BLEND_SHAPE_PREFIX = "blendShape."
BLEND_SHAPE_UNIT = 100.0

#: The class id of a humanoid animator: its float curves are muscles, which the statement solves into bone curves.
ANIMATOR_CLASS_ID = 95

#: The child of a clip's prim that states the curves driving a property of a component or an object.
PROPERTY_CURVES = "propertyCurves"

#: The child of a clip's prim that states the animation events the source puts on the clip.
EVENTS = "events"

#: The class id of a Transform, and the property each kind of transform curve drives on it, with the axes of its components, under
#: the names the source's own bindings give them. A transform curve no joint of the rig takes is stated as one scalar curve per axis of
#: these properties.
TRANSFORM_CLASS_ID = 4
TRANSFORM_PROPERTIES = {"pos": ("m_LocalPosition", "xyz"), "rot": ("m_LocalRotation", "xyzw"),
                        "euler": ("m_LocalEulerAnglesRaw", "xyz"), "scale": ("m_LocalScale", "xyz")}


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
    """Write ``clip`` as the performance of ``rig`` to ``layer_path``. Returns ``{"written", "unplaced", ...}``: whether a
    layer was written, how many of the clip's curves the layer cannot carry by kind (counted here rather than dropped silently),
    and when written the counts of what it moves. A transform curve that lands on no joint of the rig -- an object outside the rig,
    or the rig's own root -- is not one of these: it is stated among the property curves (``transform_curves`` counts its axes)."""
    matrix, vector_sign = _reflection()
    magnitude = np.abs(matrix)
    rate, duration, times = _sampling(clip)
    frames = len(times)
    joint_of_source = {source: joint for joint, source in enumerate(rig.sources)}
    moved = {}
    unplaced = {}
    loose = []
    for kind in ("pos", "rot", "euler", "scale"):
        for channel in clip.of_kind(kind):
            joint = joint_of_source.get(channel.path)
            if joint is not None:
                moved.setdefault(joint, {})[kind] = channel.sample(times)
            else:
                loose.append(channel)
    shapes = {}
    for channel in clip.of_kind("float"):
        if not channel.attribute.startswith(BLEND_SHAPE_PREFIX):
            if channel.class_id == ANIMATOR_CLASS_ID:
                unplaced["float:{0}".format(channel.class_id)] = unplaced.get("float:{0}".format(channel.class_id), 0) + 1
        elif channel.path in joint_of_source:
            shapes[(channel.path, channel.attribute[len(BLEND_SHAPE_PREFIX):])] = (
                channel.sample(times)[:, 0] / BLEND_SHAPE_UNIT)
        else:
            reason = "blend_shape:self" if not channel.path else "blend_shape:unmatched"
            unplaced[reason] = unplaced.get(reason, 0) + 1
    if not moved and not shapes:
        return {"written": False, "unplaced": unplaced, "transform_curves": 0}
    stage = stages.create(layer_path, kernel_statement.USD, "Clip {0} on {1}".format(name, rig.sources[0] if rig.sources else ""))
    stage.SetTimeCodesPerSecond(rate)
    stage.SetStartTimeCode(0)
    stage.SetEndTimeCode(frames - 1)
    animation = UsdSkel.Animation.Define(stage, "/" + identifier(name))
    stage.SetDefaultPrim(animation.GetPrim())
    animation.GetPrim().CreateAttribute("ruri:sourceName", Sdf.ValueTypeNames.String, custom=True).Set(clip.name)
    animation.GetPrim().CreateAttribute("ruri:sourceKey", Sdf.ValueTypeNames.String, custom=True).Set(clip.key)
    animation.GetPrim().CreateAttribute("ruri:clipLoop", Sdf.ValueTypeNames.Bool, custom=True).Set(bool(clip.loop))
    animation.GetPrim().CreateAttribute("ruri:clipDuration", Sdf.ValueTypeNames.Double, custom=True).Set(float(duration))
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
    _write_shapes(animation, shapes, frames)
    properties, transforms = _write_property_curves(stage, animation, clip, loose)
    events = write_events(stage, animation.GetPrim(), clip.events)
    stage.GetRootLayer().Save()
    return {"written": True, "unplaced": unplaced, "joints": len(joints), "frames": frames, "blend_shapes": len(shapes),
            "rate": rate, "property_curves": properties, "transform_curves": transforms, "events": events}


def _sampling(clip):
    """``(rate, duration, times)``: the rate the clip is sampled at, its length and the sample times, the last being its end."""
    rate = clip.sample_rate or DEFAULT_RATE
    duration = clip.duration
    times = (np.minimum(np.arange(int(np.ceil(duration * rate)) + 1) / rate, duration) if duration > 0
             else np.zeros(1))
    return rate, duration, times


def _write_shapes(animation, shapes, frames):
    """The blend shape weights of ``shapes`` (``{(renderer path, shape name): weights per frame}``) on ``animation``: the
    USD arrays by unique token, and beside them the renderer path and the source's own name of each, which USD has no
    place for."""
    if not shapes:
        return
    names = Namer()
    keys = list(shapes)
    tokens = [names.claim("shapes", identifier(shape)) for _path, shape in keys]
    animation.CreateBlendShapesAttr(Vt.TokenArray(tokens))
    animation.GetPrim().CreateAttribute("ruri:blendShapePaths", Sdf.ValueTypeNames.StringArray, custom=True).Set(
        Vt.StringArray([path for path, _shape in keys]))
    animation.GetPrim().CreateAttribute("ruri:blendShapeSourceNames", Sdf.ValueTypeNames.StringArray, custom=True).Set(
        Vt.StringArray([shape for _path, shape in keys]))
    weights = animation.CreateBlendShapeWeightsAttr()
    stacked = np.stack([shapes[key] for key in keys], axis=1)
    for frame in range(frames):
        weights.Set(Vt.FloatArray.FromNumpy(np.ascontiguousarray(stacked[frame], dtype=np.float32)), Usd.TimeCode(frame))


def _scalar_curve(channel, column, curve):
    """The keys of one axis of ``channel`` as the members of ``curve``: times, values and both tangents of a cubic Hermite curve,
    unsampled, each key's in weight, out weight and weighted mode, and the curve's pre and post wrap mode, where the source
    states them."""
    if channel.pre_wrap_mode is not None:
        curve["preWrapMode"] = channel.pre_wrap_mode
    if channel.post_wrap_mode is not None:
        curve["postWrapMode"] = channel.post_wrap_mode
    curve["times"] = [float(value) for value in channel.times]
    curve["values"] = [float(value) for value in channel.values[:, column]]
    curve["inSlopes"] = [float(value) for value in channel.in_slopes[:, column]]
    curve["outSlopes"] = [float(value) for value in channel.out_slopes[:, column]]
    if channel.in_weights is not None:
        curve["inWeights"] = [float(value) for value in channel.in_weights[:, column]]
        curve["outWeights"] = [float(value) for value in channel.out_weights[:, column]]
        curve["weightedModes"] = [int(value) for value in channel.weighted_modes]
    return curve


def _transform_curves(channels):
    """The transform curves ``channels`` (each a vector across its axes) as one scalar curve per axis, driving the property of a
    Transform the source's own binding names (``TRANSFORM_PROPERTIES``) at the path the clip states, in the source's own space and
    units."""
    curves = []
    for channel in channels:
        if not len(channel.times):
            continue
        base, axes = TRANSFORM_PROPERTIES[channel.kind]
        for column, axis in enumerate(axes):
            curves.append(_scalar_curve(channel, column, {
                "classId": TRANSFORM_CLASS_ID, "className": "Transform", "path": channel.path,
                "attribute": "{0}.{1}".format(base, axis)}))
    return curves


def _write_property_curves(stage, animation, clip, loose=()):
    """The curves of ``clip`` that drive a property of a component or an object, as the child ``propertyCurves`` of the clip's
    prim: the float curves, and the ``loose`` transform curves (the ones no joint takes). For each, the class of the component
    it drives, the path of the object it sits on and the property's name as the source states them (the class name of a native
    class, the script a script component runs), the curve's pre and post wrap mode, and the keys exactly as the source holds them
    -- times, values and both tangents of a cubic Hermite curve, unsampled, and each key's in weight, out weight and weighted
    mode. A blend shape weight is the layer's own, and a humanoid muscle is solved into bone curves, so neither is one. Returns
    ``(how many curves there are, how many of them are axes of a transform)``."""
    curves = []
    for channel in clip.of_kind("float"):
        if channel.attribute.startswith(BLEND_SHAPE_PREFIX) or channel.class_id == ANIMATOR_CLASS_ID or not len(channel.times):
            continue
        curve = {"classId": channel.class_id, "path": channel.path, "attribute": channel.attribute}
        if channel.class_name:
            curve["className"] = channel.class_name
        if channel.script_class:
            curve["scriptAssembly"] = channel.script_assembly
            curve["scriptNamespace"] = channel.script_namespace
            curve["scriptClass"] = channel.script_class
        curves.append(_scalar_curve(channel, 0, curve))
    transforms = _transform_curves(loose)
    curves.extend(transforms)
    if curves:
        records.attach(stage, animation.GetPrim(), PROPERTY_CURVES, {"curves": curves}, {"prims": 0, "attributes": 0})
    return len(curves), len(transforms)


def write_events(stage, prim, events):
    """The animation ``events`` of a clip (``Clip.events``) as the child ``events`` of the clip's ``prim``: a list ``events`` of
    objects, in the order the source holds them, each with ``time`` (seconds), ``functionName``, ``stringParameter``,
    ``floatParameter``, ``intParameter``, ``messageOptions`` and, for an event that passes an object,
    ``objectReferenceParameter`` (the asset reference the script records use). Returns how many there are."""
    if not events:
        return 0
    listing = []
    for event in events:
        item = {"time": float(event["time"]), "functionName": event["functionName"],
                "stringParameter": event["stringParameter"], "floatParameter": float(event["floatParameter"]),
                "intParameter": int(event["intParameter"]), "messageOptions": int(event["messageOptions"])}
        if event.get("objectReference"):
            item["objectReferenceParameter"] = event["objectReference"]
        listing.append(item)
    records.attach(stage, prim, EVENTS, {"events": listing}, {"prims": 0, "attributes": 0})
    return len(listing)


def author_carrier(layer_path, name, clip):
    """Write ``clip`` once, on no rig, for a clip that no rig could place anything of: what it says without a skeleton --
    its identity, loop flag, length, blend shape curves with the renderer paths it authored them against, and its curves as the
    property curves they are (a transform curve as one scalar curve per axis) -- and, as ``ruri:unplaced``, how many curves it has
    that this layer cannot carry (``kind=count``). A clip with no curves at all is a pose of nothing and still has an identity a
    controller refers to."""
    rate, duration, times = _sampling(clip)
    frames = len(times)
    unplaced = {}
    loose = [channel for kind in ("pos", "rot", "euler", "scale") for channel in clip.of_kind(kind)]
    shapes = {}
    for channel in clip.of_kind("float"):
        if channel.attribute.startswith(BLEND_SHAPE_PREFIX):
            shapes[(channel.path, channel.attribute[len(BLEND_SHAPE_PREFIX):])] = (
                channel.sample(times)[:, 0] / BLEND_SHAPE_UNIT)
        elif channel.class_id == ANIMATOR_CLASS_ID:
            unplaced["float:{0}".format(channel.class_id)] = unplaced.get("float:{0}".format(channel.class_id), 0) + 1
    stage = stages.create(layer_path, kernel_statement.USD, "Clip {0} on no rig".format(name))
    stage.SetTimeCodesPerSecond(rate)
    stage.SetStartTimeCode(0)
    stage.SetEndTimeCode(frames - 1)
    animation = UsdSkel.Animation.Define(stage, "/" + identifier(name))
    stage.SetDefaultPrim(animation.GetPrim())
    prim = animation.GetPrim()
    prim.CreateAttribute("ruri:sourceName", Sdf.ValueTypeNames.String, custom=True).Set(clip.name)
    prim.CreateAttribute("ruri:sourceKey", Sdf.ValueTypeNames.String, custom=True).Set(clip.key)
    prim.CreateAttribute("ruri:clipLoop", Sdf.ValueTypeNames.Bool, custom=True).Set(bool(clip.loop))
    prim.CreateAttribute("ruri:clipDuration", Sdf.ValueTypeNames.Double, custom=True).Set(float(duration))
    animation.CreateJointsAttr(Vt.TokenArray([]))
    _write_shapes(animation, shapes, frames)
    properties, transforms = _write_property_curves(stage, animation, clip, loose)
    events = write_events(stage, prim, clip.events)
    if unplaced:
        prim.CreateAttribute("ruri:unplaced", Sdf.ValueTypeNames.StringArray, custom=True).Set(
            Vt.StringArray(["{0}={1}".format(reason, unplaced[reason]) for reason in sorted(unplaced)]))
    stage.GetRootLayer().Save()
    return {"written": True, "unplaced": unplaced, "joints": 0, "frames": frames, "blend_shapes": len(shapes), "rate": rate,
            "property_curves": properties, "transform_curves": transforms, "events": events}


def layer_stem(clip):
    """The file name of ``clip``'s layer: its name for whoever reads the folder, and its key -- the one thing that tells
    two clips of the same name apart -- for everything else."""
    return "{0}__{1}".format(file_stem(clip.name), identifier(clip.key))
