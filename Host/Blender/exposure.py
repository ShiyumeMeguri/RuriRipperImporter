"""The exposure the post stage multiplies a frame by, stated to the materials that take it back out.

The build's render pipeline writes, every frame, the exposure its auto exposure arrived at into one global every
material reads -- x = E, y = 1/E -- and characters, with the emissives that ignore post exposure, multiply their final
colour by 1/E (their ambient intensity is scaled by E): the exposure lifts the level and leaves them as they are. The post
stage meters each frame in the compositor, after every material has been shaded, so no material can read the E of its own
frame; the build reads back the previous frame's, and so does this host. A render driven here states each frame's exposure
on the scene before the frame is shaded -- what the frame before metered -- and where the frame meters off it by more than
half the post stage's tolerance (the first frame, a cut) it shades the frame again under the exposure the passes so far
point to, until the frame meters what it was shaded under: the fixed point the build's adaptation settles on. The state is
keys on the scene, so any later render of those frames reads the same state; it is the render's, not the level's, which
is why it lives on the scene and not on the level's world (a material's view-layer attribute finds the scene first). A
final render of any frame whose state is off what it meters by more than that tolerance is refused by the post stage.
"""

from __future__ import annotations

import math
import os

import bpy
from bpy_extras import anim_utils

from . import material_builder

#: What the stacks call the statement a post stage's exposure makes (the recipe's engine global source).
STATEMENT = "post_exposure"
#: How many times one frame may be shaded while its exposure is sought before the render stops with what it found.
PASSES = 4


def state_global():
    """``(name, default)`` of the one engine global the post exposure states, or None when no registered stack reads it."""
    found = {}
    for provider in material_builder.LEVEL_GLOBALS:
        for name, (statement, base) in provider().items():
            if statement == STATEMENT:
                found[name] = [float(component) for component in base]
    if len(found) > 1:
        raise RuntimeError("[exposure] the stacks read {0} exposure states; there is one exposure".format(sorted(found)))
    return next(iter(found.items()), None)


def _path(name):
    return '["{0}"]'.format(name)


def stated(scene, frame):
    """The exposure, in stops, stated on ``scene`` for ``frame``; None when the scene states none."""
    known = state_global()
    if known is None or known[0] not in scene:
        return None
    name, base = known
    data = scene.animation_data
    channels = anim_utils.action_get_channelbag_for_slot(data.action, data.action_slot) if data and data.action else None
    curve = channels.fcurves.find(_path(name), index=0) if channels else None
    value = curve.evaluate(frame) if curve is not None else scene[name][0]
    multiplier = float(value) + base[0]
    if multiplier <= 0.0:
        raise RuntimeError("[exposure] scene {0} states an exposure multiplier of {1} at frame {2}".format(
            scene.name, multiplier, frame))
    return math.log2(multiplier)


def state(scene, frame, stops):
    """State ``stops`` on ``scene`` for ``frame``: x = 2^stops, y = its inverse, the rest at their defaults, each written
    as its difference from the stacks' declared default and keyed constant from this frame on."""
    known = state_global()
    if known is None:
        raise RuntimeError("[exposure] no registered stack reads a post exposure state")
    name, base = known
    multiplier = 2.0 ** stops
    scene[name] = [multiplier - base[0], 1.0 / multiplier - base[1], 0.0, 0.0]
    scene.keyframe_insert(_path(name), frame=frame)
    data = scene.animation_data
    channels = anim_utils.action_get_channelbag_for_slot(data.action, data.action_slot)
    for index in range(4):
        curve = channels.fcurves.find(_path(name), index=index)
        for point in curve.keyframe_points:
            if abs(point.co[0] - frame) < 0.5:
                point.interpolation = "CONSTANT"
        curve.update()


def _record_of(stages, scene, frame):
    """The one file the post stages record ``frame``'s metering to."""
    records = {stage.metering_record(scene, frame) for stage in stages} - {None}
    if len(records) != 1:
        raise RuntimeError("[exposure] frame {0} is recorded to {1}; a frame meters one exposure".format(frame, sorted(records)))
    return records.pop()


def _metered(record, frame):
    """The stops the post stage metered on ``frame``, read back from the one-pixel image its compositor recorded."""
    if not os.path.isfile(record):
        raise RuntimeError("[exposure] frame {0} rendered without recording its metering to {1}".format(frame, record))
    image = bpy.data.images.load(record, check_existing=False)
    try:
        value = float(image.pixels[0])
    finally:
        bpy.data.images.remove(image)
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


def render_frames(scene, frames, path_of):
    """Render ``frames`` of ``scene`` to ``path_of(frame)``, each shaded under the exposure the post stage multiplies it
    by, and leave that exposure stated on the scene. Returns ``{frame: (stops, shaded_times)}``."""
    stages = [stage for stage in material_builder.POST_STAGES if stage.installed(scene)]
    if not stages:
        raise RuntimeError("[exposure] scene {0} has no post stage installed: nothing meters its frames".format(scene.name))
    tolerance = min(stage.EXPOSURE_TOLERANCE_STOPS for stage in stages) / 2.0
    folder = os.path.join(bpy.app.tempdir, "ruri_exposure_{0}".format(scene.name))
    os.makedirs(folder, exist_ok=True)
    # The state exists before recording rebuilds the chain: the stage's guard reads it only if it was there when built.
    start = stated(scene, frames[0])
    state(scene, frames[0], 0.0 if start is None else start)
    for stage in stages:
        stage.record_exposure(scene, folder)
    done = {}
    previous = None
    try:
        for frame in frames:
            scene.frame_set(frame)
            record = _record_of(stages, scene, frame)
            stops = previous if previous is not None else stated(scene, frame)
            stops = 0.0 if stops is None else stops
            passes = []
            while True:
                state(scene, frame, stops)
                scene.frame_set(frame)
                scene.render.filepath = path_of(frame)
                if os.path.exists(record):
                    os.remove(record)
                bpy.ops.render.render(write_still=True)
                metered = _metered(record, frame)
                passes.append((stops, metered))
                if abs(metered - stops) <= tolerance:
                    break
                if len(passes) == PASSES:
                    raise RuntimeError("[exposure] frame {0} found no exposure it meters within {1} stops in {2} passes: {3}"
                                       .format(frame, tolerance, PASSES, passes))
                stops = _next_guess(passes)
            done[frame] = (stops, len(passes))
            previous = metered
            print("[exposure] frame {0}: shaded under {1:+.3f} stops, metered {2:+.3f}, {3} pass(es)".format(
                frame, stops, metered, len(passes)), flush=True)
    finally:
        for stage in stages:
            stage.record_exposure(scene, "")
    return done
