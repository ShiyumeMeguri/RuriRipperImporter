"""The exposure the post stage multiplies a frame by, stated to the materials that take it back out.

The build's render pipeline writes, every frame, the exposure its auto exposure arrived at into one global every
material reads -- x = E, y = 1/E -- and characters, with the emissives that ignore post exposure, multiply their final
colour by 1/E (their ambient intensity is scaled by E): the exposure lifts the level and leaves them as they are. The
exposure a frame arrives at is adapted, not metered (UberPostPassUtils.AutoExposureUpdateData): the camera's exposure
moves from its previous value toward the target the histogram read back -- already clamped to the level's range -- by the
level's up or down speed times the frame's delta time, so a cut or a change of light is followed over the next frames, not
jumped to. The post stage meters each frame's target in the compositor, after every material has been shaded, so no frame
can read its own; the build reads back an earlier frame's. A render driven here does the same while it renders: once a
frame is written, the next one is stated the exposure adapted from it toward the target it metered, and the post stage
multiplies that frame by the stated exposure, the one its materials read. The state is keyed before Blender starts the
frame because an animation render evaluates each frame for the compositor, whose driver reads the state, before it runs
render_pre, and for the render engine, whose materials read it, after: a key inserted in render_pre is shaded on time but
multiplied by the exposure of the frame before it. It renders as one animation,
because a still render starts the renderer again for every frame and every render job compiles the scene's shaders again.
The state is keys on the scene, so any later render of those frames reads the same state; it is the render's, not the
level's, which is why it lives on the scene and not on the level's world (a material's view-layer attribute finds the
scene first). A final render with no exposure stated is refused by the post stage.
"""

from __future__ import annotations

import math
import os

import OpenImageIO
import bpy
from bpy_extras import anim_utils

from . import material_builder, temporal


def state_global():
    """``(name, default)`` of the one engine global the post exposure states, or None when no registered stack reads it."""
    found = {}
    for provider in material_builder.LEVEL_GLOBALS:
        for name, (statement, base) in provider().items():
            if statement == material_builder.POST_EXPOSURE_STATEMENT:
                found[name] = [float(component) for component in base]
    if len(found) > 1:
        raise RuntimeError("[exposure] the stacks read {0} exposure states; there is one exposure".format(sorted(found)))
    return next(iter(found.items()), None)


def _path(name):
    return '["{0}"]'.format(name)


def _curves(scene, name):
    """The four curves keying the state on ``scene``, or None when it is not keyed."""
    data = scene.animation_data
    channels = anim_utils.action_get_channelbag_for_slot(data.action, data.action_slot) if data and data.action else None
    if channels is None:
        return None
    curves = [channels.fcurves.find(_path(name), index=index) for index in range(4)]
    return curves if all(curve is not None for curve in curves) else None


def _key_at(curve, frame):
    """The key of ``curve`` on ``frame``, or None: the keys stay sorted by frame, so a bisection finds it."""
    points = curve.keyframe_points
    low, high = 0, len(points)
    while low < high:
        middle = (low + high) // 2
        if points[middle].co[0] < frame - 0.5:
            low = middle + 1
        else:
            high = middle
    return points[low] if low < len(points) and abs(points[low].co[0] - frame) < 0.5 else None


def _values(base, stops):
    multiplier = 2.0 ** stops
    return (multiplier - base[0], 1.0 / multiplier - base[1], 0.0, 0.0)


def stated(scene, frame):
    """The exposure, in stops, stated on ``scene`` for ``frame``; None when the scene states none."""
    known = state_global()
    if known is None or known[0] not in scene:
        return None
    name, base = known
    curves = _curves(scene, name)
    value = curves[0].evaluate(frame) if curves is not None else scene[name][0]
    multiplier = float(value) + base[0]
    if multiplier <= 0.0:
        raise RuntimeError("[exposure] scene {0} states an exposure multiplier of {1} at frame {2}".format(
            scene.name, multiplier, frame))
    return math.log2(multiplier)


def state(scene, frame, stops):
    """State ``stops`` on ``scene`` for ``frame``: x = 2^stops, y = its inverse, the rest at their defaults, each written
    as its difference from the stacks' declared default and keyed constant from this frame on. Inserted as a key, which
    a running render reads from the next frame it starts; it starts a frame for the compositor before render_pre, so a
    frame's state is keyed before that frame starts. A key changed in place, even with its action tagged, is not seen
    by that render."""
    known = state_global()
    if known is None:
        raise RuntimeError("[exposure] no registered stack reads a post exposure state")
    name, base = known
    scene[name] = list(_values(base, stops))
    scene.keyframe_insert(_path(name), frame=frame)
    for curve in _curves(scene, name):
        _key_at(curve, frame).interpolation = "CONSTANT"
        curve.update()


def _record_of(stages, scene, frame):
    """The one file the post stages record ``frame``'s metering to."""
    records = {stage.metering_record(scene, frame) for stage in stages} - {None}
    if len(records) != 1:
        raise RuntimeError("[exposure] frame {0} is recorded to {1}; a frame meters one exposure".format(frame, sorted(records)))
    return records.pop()


def _metered(record, frame):
    """The stops the post stage metered on ``frame``, read back from the one-pixel image its compositor recorded. Read
    with the image library itself, not loaded as an image datablock: adding and removing a datablock rebuilds the
    scene's dependencies, which on a level costs more than the frame it was read for."""
    if not os.path.isfile(record):
        raise RuntimeError("[exposure] frame {0} rendered without recording its metering to {1}".format(frame, record))
    reader = OpenImageIO.ImageInput.open(record)
    if reader is None:
        raise RuntimeError("[exposure] frame {0}: {1} cannot be read ({2})".format(frame, record, OpenImageIO.geterror()))
    try:
        value = float(reader.read_image("float").flat[0])
    finally:
        reader.close()
        os.remove(record)
    return value


def _speeds(stages, scene):
    """``(up, down)`` per second: the one adaptation speed the installed post stages read from the level."""
    speeds = {stage.exposure_adaptation(scene) for stage in stages}
    if None in speeds or len(speeds) != 1:
        raise RuntimeError("[exposure] scene {0}'s post stages state the adaptation speeds {1}; the level must state "
                           "one (state its post values again)".format(scene.name, sorted(speeds, key=str)))
    return speeds.pop()


def _adapted(current, target, speeds, delta):
    """One step of the build's exposure update (UberPostPassUtils.AutoExposureUpdateData), in stops: the linear exposure
    moves from ``current`` toward ``target`` by Mathf.Lerp's clamped factor, the up speed when the target is above and
    the down speed otherwise, times the frame's delta time."""
    now, toward = 2.0 ** current, 2.0 ** target
    up, down = speeds
    factor = min(max((up if toward > now else down) * delta, 0.0), 1.0)
    return math.log2(now + (toward - now) * factor)


def _render_range(scene, first, last):
    """One animation render of every frame ``first``..``last``: the renderer starts once for the whole run (a still
    render starts it again for every frame, which costs many times the frame itself), and it overwrites the output a
    frame already has: a frame skipped for having output runs no render_post."""
    scene.frame_start, scene.frame_end, scene.frame_step = first, last, 1
    scene.render.use_overwrite = True
    bpy.ops.render.render(animation=True)


def render_animation(scene, first, last):
    """Render frames ``first``..``last`` of ``scene`` to its output path, each shaded and multiplied by the exposure the
    build adapts to, and leave that exposure stated on the scene. The first frame keeps the exposure the scene states for
    it, or the build's initial exposure of 1 when it states none; each later frame is stated, once the frame before it is
    written and before Blender starts it, the exposure adapted from the frame before it toward the target that frame
    metered, at the speeds the level states. Every frame of the range is rendered, over any output it already has, and
    the scene's own overwrite switch is put back afterwards: a frame Blender skipped would meter nothing, and no frame
    after it would be stated.
    On a scene switched to temporal anti-aliasing (temporal.enabled) the frames resolve it under the state temporal.state
    keys for the range, cleared when the render ends. Returns ``{frame: stops}``."""
    stages = [stage for stage in material_builder.POST_STAGES if stage.installed(scene)]
    if not stages:
        raise RuntimeError("[exposure] scene {0} has no post stage installed: nothing meters its frames".format(scene.name))
    speeds = _speeds(stages, scene)
    delta = scene.render.fps_base / scene.render.fps
    folder = os.path.join(bpy.app.tempdir, "ruri_exposure_{0}".format(scene.name))
    os.makedirs(folder, exist_ok=True)
    start = stated(scene, first)
    exposures = {first: 0.0 if start is None else start}
    # The state exists before recording rebuilds the chain: the chain reads it only if it was there when built.
    state(scene, first, exposures[first])
    for stage in stages:
        stage.record_exposure(scene, folder)
    kept_filter = temporal.state(scene, first, last, os.path.join(bpy.app.tempdir, "ruri_temporal_{0}".format(
        scene.name))) if temporal.enabled(scene) else None
    kept = (scene.frame_start, scene.frame_end, scene.frame_step, scene.frame_current, scene.render.use_overwrite)

    def seed(scene_now, *_arguments):
        frame = scene_now.frame_current
        if frame in exposures and frame + 1 not in exposures and frame < last:
            target = _metered(_record_of(stages, scene_now, frame), frame)
            exposures[frame + 1] = _adapted(exposures[frame], target, speeds, delta)
            state(scene_now, frame + 1, exposures[frame + 1])

    try:
        for frame in range(first, last + 1):
            record = _record_of(stages, scene, frame)
            if os.path.exists(record):
                os.remove(record)
        bpy.app.handlers.render_post.append(seed)
        try:
            _render_range(scene, first, last)
        finally:
            bpy.app.handlers.render_post.remove(seed)
        closing = _record_of(stages, scene, last)
        if not os.path.isfile(closing):
            raise RuntimeError("[exposure] the render stopped before frame {0} recorded its metering".format(last))
        os.remove(closing)
    finally:
        scene.frame_start, scene.frame_end, scene.frame_step = kept[0], kept[1], kept[2]
        scene.render.use_overwrite = kept[4]
        scene.frame_set(kept[3])
        for stage in stages:
            stage.record_exposure(scene, "")
        if kept_filter is not None:
            temporal.clear(scene, kept_filter)
    return exposures
