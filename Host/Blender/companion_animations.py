"""Host-only action transactions and sample-rate alignment; no asset matching."""
import math

import bpy
import numpy as np

from . import animation_builder, rig_identity


class Session(animation_builder.PerformanceSession):
    def __init__(self, context, rig, binding):
        self.context, self.rig = context, rig
        self.owner = rig.parent
        while self.owner is not None and rig_identity.seed_of(self.owner) != binding["actor_seed"]:
            self.owner = self.owner.parent
        if self.owner is None:
            raise ValueError("The mounted rig has no matching owner.")
        super().__init__(context, (rig, self.owner))
        self.can_pair = not any(obj.animation_data and any(not track.mute for track in obj.animation_data.nla_tracks)
                                for obj in (rig, self.owner))

    def synchronize(self, primary, companion):
        scene = self.context.scene
        pairs = [(self.rig, primary[0])]
        if companion:
            pairs.append((self.owner, companion[0]))
        rate = scene.render.fps / scene.render.fps_base
        for obj, action in pairs:
            source = action[animation_builder.SAMPLE_RATE_KEY]
            factor = rate / source
            for layer in action.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        for curve in bag.fcurves:
                            for field in ("co", "handle_left", "handle_right"):
                                values = np.empty(len(curve.keyframe_points) * 2, dtype=np.float32)
                                curve.keyframe_points.foreach_get(field, values)
                                values[::2] *= factor
                                curve.keyframe_points.foreach_set(field, values)
                            curve.update()
            action[animation_builder.SAMPLE_RATE_KEY] = rate
            animation_builder.adopt_action(obj, action, action.slots[0], scene, False)
        animation_builder.set_frame_range(scene, min(a.frame_range[0] for _, a in pairs),
                                          math.ceil(max(a.frame_range[1] for _, a in pairs)), False)
        scene.frame_set(self.time[2], subframe=self.time[3])

def transaction(context, rig, binding):
    return Session(context, rig, binding)
