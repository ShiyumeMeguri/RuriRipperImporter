"""The state the post stage's temporal anti-aliasing reads, stated frame by frame before a render shades them.

The game's TAAU resolves each frame against the history of the frames before it: the camera is jittered by a sub-pixel
offset that cycles through eight phases (HGCamera: Halton (2, 3) of taaFrameIndex + 1, minus a half, taaJitterPhaseCount 8
at rendering scale 1), the dilation reprojects this frame's nearest depth into the previous frame with the previous
frame's non-jittered view projection times the inverse of this one (_ReprojectionMatrix), the first frame has no history
(historyWeight 0, firstFrame 1) and a camera cut fast-converges it (CameraManager._OnVCamCut sets taauFastConverge). A
render driven here states all of it as keys on the scene, constant per frame, before it starts: the post stage's drivers
read them, and EEVEE takes the jitter as its one sample's offset (eevee_sampling.cc reads the scene's
override_pixel_jitter_sample) with the film's reconstruction filter off, so each pixel holds the one shading at its
jittered position the way the game's render target does. The state is the render's, not the level's: it is cleared when
the render ends. It is stated only on a scene switched to it (``enabled``); otherwise a frame is the scene's own samples
through its film filter and the chain is not built.
"""

from __future__ import annotations

import os

import bpy
from bpy_extras import anim_utils

from . import material_builder

#: The scene property EEVEE reads, when a frame starts rendering, as its one sample's offset in render pixels.
JITTER_PROPERTY = "override_pixel_jitter_sample"

#: How many jitter phases the game cycles at rendering scale 1 (HGCamera.taaJitterPhaseCount).
PHASES = 8

#: The scene property that switches the host's animation render to the post stage's temporal anti-aliasing; absent is
#: off. Every sample of a frame shares the frame's one jitter, so a scene switched on renders one sample per frame.
ENABLED_KEY = "ruri_temporal_anti_aliasing"


def available(scene):
    """Whether a post stage installed on ``scene`` has a chain that keeps history, so the switch means anything."""
    return any(stage.keeps_history() for stage in material_builder.POST_STAGES if stage.installed(scene))


def enabled(scene):
    """Whether the host's animation render resolves ``scene``'s frames through the temporal anti-aliasing."""
    return bool(scene.get(ENABLED_KEY))


def set_enabled(scene, value):
    """Switch it on or off; the switch is the scene's, saved with the file."""
    if value:
        scene[ENABLED_KEY] = 1
    elif ENABLED_KEY in scene:
        del scene[ENABLED_KEY]


def _radical_inverse(index, base):
    """The digit loop HGCamera builds its jitter from: index written in base, its digits mirrored past the point."""
    result = 0.0
    fraction = 1.0 / base
    while index > 0:
        result += (index % base) * fraction
        index //= base
        fraction /= base
    return result


def jitter(frame):
    """The game's jitter for a frame, in render pixels: Halton (2, 3) of taaFrameIndex + 1, minus a half. taaFrameIndex
    cycles through PHASES; it is taken as the frame number modulo PHASES so a frame rendered again gets the same offset."""
    index = frame % PHASES + 1
    return (_radical_inverse(index, 2) - 0.5, _radical_inverse(index, 3) - 0.5)


def _stages(scene):
    stages = [stage for stage in material_builder.POST_STAGES if stage.installed(scene)]
    if not stages:
        raise RuntimeError("[temporal] scene {0} has no post stage installed: nothing resolves its frames".format(scene.name))
    names = {stage.TEMPORAL_STATE_NAME for stage in stages}
    if len(names) != 1:
        raise RuntimeError("[temporal] the post stages read {0}; a frame has one temporal state".format(sorted(names)))
    return stages, names.pop(), stages[0].TEMPORAL_STATE_LENGTH


def _view_projection(scene, depsgraph):
    """The active camera's view projection the way the post stage reads it, and the camera: Blender's projection at the
    output size with its depth row rewritten to the reversed Z of the game's device depth (row 3 ← -0.5·row 3 + 0.5·row 4),
    y not flipped, times the inverse of the camera's unscaled world matrix."""
    render = scene.render
    width = max(1, render.resolution_x * render.resolution_percentage // 100)
    height = max(1, render.resolution_y * render.resolution_percentage // 100)
    camera = scene.camera.evaluated_get(depsgraph)
    projection = camera.calc_matrix_camera(depsgraph, x=width, y=height, scale_x=render.pixel_aspect_x,
                                           scale_y=render.pixel_aspect_y)
    rewritten = projection.copy()
    for column in range(4):
        rewritten[2][column] = -0.5 * projection[2][column] + 0.5 * projection[3][column]
    return rewritten @ camera.matrix_world.normalized().inverted(), scene.camera


def _curves(scene, name, length):
    data = scene.animation_data
    channels = anim_utils.action_get_channelbag_for_slot(data.action, data.action_slot) if data and data.action else None
    if channels is None:
        return channels, []
    path = '["{0}"]'.format(name)
    return channels, [channels.fcurves.find(path, index=index) for index in range(length)]


def _key(scene, name, rows):
    """Key {frame: values} on the scene's custom property name, constant from each frame on, in one write per curve."""
    frames = sorted(rows)
    length = len(rows[frames[0]])
    scene[name] = [float(value) for value in rows[frames[0]]]
    scene.keyframe_insert('["{0}"]'.format(name), frame=frames[0])
    _channels, curves = _curves(scene, name, length)
    for index, curve in enumerate(curves):
        points = curve.keyframe_points
        points.clear()
        points.add(len(frames))
        points.foreach_set("co", [value for frame in frames for value in (float(frame), float(rows[frame][index]))])
        points.foreach_set("interpolation", [0] * len(frames))
        curve.update()


def state(scene, first, last, folder):
    """State, on every frame first..last, the jitter, the first-frame and fast-converge flags and the reprojection the post
    stage resolves the frame under, the jitter to EEVEE, and the history folder the stage writes each frame's history to
    and reads the previous frame's from (emptied of a previous render's history first). Returns what clear() restores."""
    stages, name, length = _stages(scene)
    if not any(stage.keeps_history() for stage in stages):
        raise RuntimeError("[temporal] no post stage on scene {0} keeps history: nothing would resolve its jittered "
                           "frames".format(scene.name))
    os.makedirs(folder, exist_ok=True)
    records = tuple("_{0}.exr".format(record) for record, _mode in stages[0].TEMPORAL_RECORDS)
    for entry in os.listdir(folder):
        if entry.endswith(records):
            os.remove(os.path.join(folder, entry))
    depsgraph = bpy.context.evaluated_depsgraph_get()
    kept_frame = scene.frame_current
    views = {}
    for frame in range(first, last + 1):
        scene.frame_set(frame)
        views[frame] = _view_projection(scene, depsgraph)
    scene.frame_set(kept_frame)
    rows = {}
    for frame in range(first, last + 1):
        current, camera = views[frame]
        previous, previous_camera = views[frame - 1] if frame > first else views[frame]
        reprojection = previous @ current.inverted()
        offset = jitter(frame)
        rows[frame] = [offset[0], offset[1], 1.0 if frame == first else 0.0,
                       1.0 if previous_camera != camera else 0.0] + [
            reprojection[row][column] for column in range(4) for row in range(4)]
        if len(rows[frame]) != length:
            raise RuntimeError("[temporal] the post stage reads {0} state values, a frame states {1}".format(
                length, len(rows[frame])))
    _key(scene, name, rows)
    _key(scene, JITTER_PROPERTY, {frame: row[:2] for frame, row in rows.items()})
    kept_filter = scene.render.filter_size
    scene.render.filter_size = 0.0
    for stage in stages:
        stage.record_history(scene, folder)
    return kept_filter


def clear(scene, kept_filter):
    """End the temporal state of a render: the stage stops recording history, the keyed state and EEVEE's jitter leave
    the scene, the film filter comes back."""
    stages, name, length = _stages(scene)
    for stage in stages:
        stage.record_history(scene, "")
    for property_name, property_length in ((name, length), (JITTER_PROPERTY, 2)):
        channels, curves = _curves(scene, property_name, property_length)
        for curve in curves:
            if curve is not None:
                channels.fcurves.remove(curve)
        if property_name in scene:
            del scene[property_name]
    scene.render.filter_size = kept_filter
