"""Place a reader's animation spans on Blender's NLA surface."""

from __future__ import annotations

import math

from . import animation_builder


def free_track(animation, name, start, end):
    layer = 0
    while True:
        label = name if layer == 0 else "{0}.{1}".format(name, layer + 1)
        track = next((one for one in animation.nla_tracks if one.name == label), None)
        if track is None:
            track = animation.nla_tracks.new()
            track.name = label
            return track
        if all(strip.frame_end <= start + 1e-3 or strip.frame_start >= end - 1e-3
               for strip in track.strips):
            return track
        layer += 1


def place_strip(track, action, slot, start, end, action_start, action_end,
                blend_in, blend_out, clip_end=None, loop=False):
    parking = max(int(round(start)),
                  int(max((strip.frame_end for strip in track.strips), default=0.0)) + 1)
    strip = track.strips.new(action.name, parking, action)
    if slot is not None:
        strip.action_slot = slot
    strip.action_frame_start = action_start if clip_end is None else 0.0
    strip.action_frame_end = action_end if clip_end is None else clip_end
    strip.scale = (end - start) / (action_end - action_start)
    strip.frame_start_ui = start
    strip.frame_end = end
    strip.use_auto_blend = False
    strip.blend_type = "REPLACE"
    strip.blend_in = blend_in
    strip.blend_out = blend_out
    strip.extrapolation = "NOTHING"
    if clip_end is not None:
        strip.use_animated_time = True
        strip.use_animated_time_cyclic = loop
        times = [(start, action_start), (end, action_end)] if loop else [
            (start, min(action_start, clip_end)), (end, min(action_end, clip_end))]
        if not loop and action_start < clip_end < action_end:
            reached = start + (end - start) * (clip_end - action_start) / (action_end - action_start)
            times.insert(1, (reached, clip_end))
        for frame, value in times:
            strip.strip_time = value
            strip.keyframe_insert(data_path="strip_time", frame=frame)
        for curve in strip.fcurves:
            if curve.data_path == "strip_time":
                for key in curve.keyframe_points:
                    key.interpolation = "LINEAR"
    return strip


def _spans(performances, sequence):
    if not sequence:
        raise ValueError("The animation sequence contains no spans.")
    spans = []
    for row in sequence:
        key = row["clip"]
        if key not in performances:
            raise ValueError("The animation sequence names an unbuilt clip: {0}.".format(key))
        action, slot = performances[key]
        at, until, clip_in, speed, blend_in, blend_out = (
            float(row[name]) for name in ("at", "until", "clip_in", "speed", "blend_in", "blend_out"))
        loop = row["loop"]
        length = float(row["clip_length"])
        if type(loop) is not bool:
            raise ValueError("The animation sequence's loop flag must be Boolean.")
        if animation_builder.SAMPLE_RATE_KEY not in action:
            raise ValueError("{0} carries no performance sample rate.".format(action.name))
        sample = float(action[animation_builder.SAMPLE_RATE_KEY])
        values = (at, until, clip_in, speed, blend_in, blend_out, sample, length)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("The animation sequence contains a non-finite time or rate.")
        if at < 0 or until <= at or clip_in < 0 or speed <= 0 or sample <= 0 or length <= 0:
            raise ValueError("The animation sequence contains an invalid span or rate.")
        duration = until - at
        blending = blend_in + blend_out
        if blend_in < 0 or blend_out < 0 or blending > duration and not math.isclose(blending, duration):
            raise ValueError("The animation sequence's blend exceeds its span.")
        source_start = clip_in * sample
        source_end = source_start + duration * speed * sample
        frame_start, frame_end = action.frame_range
        clip_end = length * sample if clip_in + duration * speed > length else None
        if clip_end is not None:
            if frame_start > 0 or round(clip_end) > frame_end:
                raise ValueError("The animation phase domain reads outside {0}'s keyed span.".format(action.name))
            if loop:
                source_start %= clip_end
                source_end = source_start + duration * speed * sample
        else:
            if source_start < frame_start or round(source_end) > frame_end:
                raise ValueError("The animation sequence reads outside {0}'s keyed span.".format(action.name))
            source_end = min(source_end, frame_end)
        if source_end <= source_start:
            raise ValueError("The animation sequence consumes no keyed interval.")
        if not spans and blend_in:
            raise ValueError("The first span has no preceding pose to blend from.")
        if spans:
            previous = spans[-1]
            if at < previous[0] or at > previous[1]:
                raise ValueError("Animation sequence spans must be ordered and continuous.")
            overlap = previous[1] - at
            if not math.isclose(overlap, blend_in):
                raise ValueError("The overlap must equal the incoming clip's blend-in.")
            if previous[7] and not math.isclose(previous[7], overlap):
                raise ValueError("The outgoing blend must describe the same overlap.")
            if len(spans) > 1 and at < spans[-2][1]:
                raise ValueError("The animation sequence overlaps more than two clips.")
        spans.append((at, until, action, slot, source_start, source_end, blend_in, blend_out, clip_end, loop))
    return spans


def _overlaps(track, start, end):
    strips = list(track.strips)
    for index, strip in enumerate(strips):
        if strip.mute:
            continue
        first = -math.inf if index == 0 and strip.extrapolation == "HOLD" else strip.frame_start
        last = strip.frame_end
        if strip.extrapolation != "NOTHING":
            last = strips[index + 1].frame_start if index + 1 < len(strips) else math.inf
        if first < end and last > start:
            return True
    return False


def arrange(context, rig, performances, sequence):
    spans = _spans(performances, sequence)
    scene = context.scene
    fps = scene.render.fps / scene.render.fps_base
    origin = scene.frame_current + scene.frame_subframe
    first = origin + spans[0][0] * fps
    last = origin + spans[-1][1] * fps
    animation = rig.animation_data
    if animation is not None:
        if animation.use_tweak_mode:
            raise ValueError("Leave NLA tweak mode before importing an animation sequence.")
        for track in animation.nla_tracks:
            if track.is_solo:
                raise ValueError("Leave NLA solo mode before importing an animation sequence.")
            if not track.mute and _overlaps(track, first, last):
                raise ValueError("An existing NLA strip overlaps the animation sequence.")
    else:
        animation = rig.animation_data_create()
    previous = animation.action
    was_enabled = animation.use_nla
    animation.action = None
    animation.use_nla = True
    for index, (at, until, action, slot, source_start, source_end, blend_in, blend_out, clip_end, loop) in enumerate(spans):
        track = animation.nla_tracks.new()
        track.name = "Ruri Sequence {0}".format(index + 1)
        outgoing = 0.0 if index + 1 < len(spans) else blend_out
        place_strip(track, action, slot, origin + at * fps, origin + until * fps,
                    source_start, source_end, blend_in * fps, outgoing * fps, clip_end, loop)
    animation_builder.set_frame_range(scene, math.ceil(first), math.floor(last))
    lines = ["{0} NLA span(s) placed at frame {1:g}.".format(len(spans), origin)]
    if previous is not None:
        if not previous.users:
            previous.use_fake_user = True
        lines.append("Previous active action {0} is retained as an action datablock.".format(previous.name))
    if not was_enabled:
        lines.append("NLA playback enabled.")
    return lines
