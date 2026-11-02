"""The exposure the post stage multiplies a frame by, stated to the materials that take it back out.

The build's render pipeline writes, every frame, the exposure its auto exposure arrived at into one global every
material reads -- x = E, y = 1/E -- and characters, with the emissives that ignore post exposure, multiply their final
colour by 1/E (their ambient intensity is scaled by E): the exposure lifts the level and leaves them as they are. The post
stage meters each frame in the compositor, after every material has been shaded, so no material can read the E of its own
frame; the build reads back the previous frame's. A render driven here does the same while it renders -- each frame is
stated, before it is shaded, the exposure the frame before it metered -- and then renders the frames again where they
meter off their state by more than half the post stage's tolerance (a cut, a fast change), under the exposure the passes
so far point to, until each frame meters what it was shaded under: the fixed point the build's adaptation settles on. It
renders as animations -- the main pass, then one render over the frames still off that skips the rest -- because a still
render starts the renderer again for every frame, and every render job compiles the scene's shaders again. The state is keys on the scene, so any later render of those frames reads the same state;
it is the render's, not the level's, which is why it lives on the scene and not on the level's world (a material's
view-layer attribute finds the scene first). A final render of any frame whose state is off what it meters by more than
that tolerance is refused by the post stage.
"""

from __future__ import annotations

import math
import os

import OpenImageIO
import bpy
from bpy_extras import anim_utils

from . import material_builder

#: How many times one frame may be shaded while its exposure is sought before the render stops with what it found.
PASSES = 4


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
    as its difference from the stacks' declared default and keyed constant from this frame on. A frame already keyed has
    its keys' values changed in place: writing the property or inserting a key makes the whole scene evaluate again, a
    large share of a frame while a render runs."""
    known = state_global()
    if known is None:
        raise RuntimeError("[exposure] no registered stack reads a post exposure state")
    name, base = known
    values = _values(base, stops)
    curves = _curves(scene, name)
    keys = [_key_at(curve, frame) for curve in curves] if curves is not None else None
    if keys is not None and all(key is not None for key in keys):
        for curve, key, value in zip(curves, keys, values):
            if key.co[1] != value:
                key.co[1] = value
                curve.update()
        return
    scene[name] = list(values)
    scene.keyframe_insert(_path(name), frame=frame)
    for curve in _curves(scene, name):
        _key_at(curve, frame).interpolation = "CONSTANT"
        curve.update()


def _key_frames(scene, frames, stops):
    """Key ``stops`` constant on every one of ``frames`` that has no key yet, in one go: a render that states its frames
    as it goes then only changes values."""
    state(scene, frames[0], stops)
    name, base = state_global()
    for curve, value in zip(_curves(scene, name), _values(base, stops)):
        keyed = {round(point.co[0]) for point in curve.keyframe_points}
        missing = [frame for frame in frames if frame not in keyed]
        if not missing:
            continue
        start = len(curve.keyframe_points)
        curve.keyframe_points.add(len(missing))
        for offset, frame in enumerate(missing):
            point = curve.keyframe_points[start + offset]
            point.co = (frame, value)
            point.interpolation = "CONSTANT"
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


def _next_guess(passes):
    """The next exposure to shade under, from ``(stated, metered)`` of the passes so far: the frame meters a smooth function
    of what it was shaded under (the materials that take the exposure back out darken as it rises), so the fixed point is the
    root of metered - stated -- one pass steps to what it metered, two or more take the secant through the last two."""
    stated_now, metered_now = passes[-1]
    if len(passes) == 1:
        return metered_now
    stated_before, metered_before = passes[-2]
    slope = (metered_now - stated_now) - (metered_before - stated_before)
    if slope == 0.0:
        return metered_now
    return stated_now - (metered_now - stated_now) * (stated_now - stated_before) / slope


def _render_range(scene, first, last):
    """One animation render of frames ``first``..``last``: the renderer starts once for the whole run. A still render
    starts it again for every frame, which costs many times the frame itself."""
    scene.frame_start, scene.frame_end, scene.frame_step = first, last, 1
    bpy.ops.render.render(animation=True)


def _render_again(scene, first, last):
    """One animation render over ``first``..``last`` that shades only the frames whose output is missing: the renderer
    skips a frame whose file exists when it may not overwrite. Every render job compiles the scene's shaders again, so the
    frames still off go in one job, not one job per run."""
    render = scene.render
    overwrite = render.use_overwrite
    render.use_overwrite = False
    try:
        _render_range(scene, first, last)
    finally:
        render.use_overwrite = overwrite


def render_animation(scene, first, last):
    """Render frames ``first``..``last`` of ``scene`` to its output path, each shaded under the exposure the post stage
    multiplies it by, and leave that exposure stated on the scene. The main pass renders the whole range once, stating each
    frame, as it is about to be shaded, the exposure the frame before it metered; frames still metering off their state by
    more than half the post stage's tolerance (a cut, a fast change) are stated the next guess and rendered again in one
    render that skips the rest, up to PASSES renders each. Returns ``{frame: (stops, renders)}``."""
    stages = [stage for stage in material_builder.POST_STAGES if stage.installed(scene)]
    if not stages:
        raise RuntimeError("[exposure] scene {0} has no post stage installed: nothing meters its frames".format(scene.name))
    tolerance = min(stage.EXPOSURE_TOLERANCE_STOPS for stage in stages) / 2.0
    folder = os.path.join(bpy.app.tempdir, "ruri_exposure_{0}".format(scene.name))
    os.makedirs(folder, exist_ok=True)
    frames = list(range(first, last + 1))
    # The state exists before recording rebuilds the chain: the stage's guard reads it only if it was there when built.
    start = stated(scene, first)
    _key_frames(scene, frames, 0.0 if start is None else start)
    for stage in stages:
        stage.record_exposure(scene, folder)
    kept = (scene.frame_start, scene.frame_end, scene.frame_step, scene.frame_current)
    history = {frame: [] for frame in frames}

    def meter(frame):
        history[frame].append((stated(scene, frame), _metered(_record_of(stages, scene, frame), frame)))

    def seed(scene_now, *_arguments):
        frame = scene_now.frame_current
        if frame - 1 in history and not history[frame - 1]:
            meter(frame - 1)
            state(scene_now, frame, history[frame - 1][-1][1])

    try:
        for frame in frames:
            record = _record_of(stages, scene, frame)
            if os.path.exists(record):
                os.remove(record)
        bpy.app.handlers.render_pre.append(seed)
        try:
            _render_range(scene, first, last)
        finally:
            bpy.app.handlers.render_pre.remove(seed)
        meter(last)
        unsettled = [frame for frame in frames if abs(history[frame][-1][1] - history[frame][-1][0]) > tolerance]
        while unsettled:
            print("[exposure] rendered {0} frame(s); {1} still off their state by more than {2} stops".format(
                sum(len(history[frame]) for frame in frames), len(unsettled), tolerance), flush=True)
            stuck = [frame for frame in unsettled if len(history[frame]) == PASSES]
            if stuck:
                raise RuntimeError("[exposure] frame(s) {0} found no exposure they meter within {1} stops in {2} renders: {3}"
                                   .format(stuck, tolerance, PASSES, {frame: history[frame] for frame in stuck[:3]}))
            for frame in unsettled:
                state(scene, frame, _next_guess(history[frame]))
                written = scene.render.frame_path(frame=frame)
                if os.path.exists(written):
                    os.remove(written)
            _render_again(scene, unsettled[0], unsettled[-1])
            for frame in unsettled:
                meter(frame)
            unsettled = [frame for frame in unsettled if abs(history[frame][-1][1] - history[frame][-1][0]) > tolerance]
    finally:
        scene.frame_start, scene.frame_end, scene.frame_step = kept[0], kept[1], kept[2]
        scene.frame_set(kept[3])
        for stage in stages:
            stage.record_exposure(scene, "")
    return {frame: (history[frame][-1][0], len(history[frame])) for frame in frames if history[frame]}
