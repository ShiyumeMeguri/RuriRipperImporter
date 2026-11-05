"""What a stated light carries into the shading stacks beyond what the host's own light hands a light loop, stamped
once on the Blender light a statement made -- its content, like its colour.

Four things, all LIGHT attributes the stacks' one light template reads:

* the source's camera-distance fade, as four coefficients under :data:`material_builder.LIGHT_FADE_PROPERTY` -- every
  stack's template scales the light by it, the way the source's light list does for every one of its shaders;
* how far the source's range exceeds the radius the host culls the light at, under
  :data:`material_builder.LIGHT_RANGE_EXCESS_PROPERTY` (:func:`cull`);
* the source's own per-light parameter vectors (culling box, tube length, cookie rect, ...) for a stack that lights
  through the source's own light model, under the names that stack declares
  (:data:`material_builder.LIGHT_PARAMETERS`), in the order the statement states them;
* the light's world orientation under :data:`material_builder.LIGHT_FRAME_PROPERTY`, which the source's tube, fog
  and cookie lighting turn on and the host's light loop does not hand over -- stamped as placed, like the culling box
  beside it, so turning a light after the import does not turn its tube or its cookie.

A vector of zeros is not stamped: an attribute a light does not carry reads zero anyway (the stacks encode a plain
light as zeros), and every property on a light lengthens the search each of its attribute reads makes.
"""

from __future__ import annotations

import math

from . import material_builder

#: EEVEE's light loop skips a light at a point where its mask times its influence there falls under this
#: (``LIGHT_ATTENUATION_THRESHOLD``, eevee_light_lib.bsl.hh); the influence at distance d is (1 - (d / r)^4)^2 on the
#: cull radius r (``light_influence_attenuation``), and a spot's mask is the smoothstep of its cone.
LOOP_THRESHOLD = 1e-6
#: How many times more of a spot's cone edge the light loop may skip at the far end of the light's reach than it skips
#: there when culled at the range. A cone's smoothstep goes to zero at its edge, so every cull radius short of the
#: range skips a little more of it; this keeps what it skips under 1e-5 of the light's radiance there.
SPOT_EDGE_TOLERANCE = 10.0


def attribute_names():
    """The parameter attribute names in statement order, as every loaded stack that reads parameters states them.
    Stacks reading none are not counted; two stacks reading different layouts cannot share one light and are
    refused."""
    layouts = {tuple(provider()) for provider in material_builder.LIGHT_PARAMETERS}
    layouts.discard(())
    if len(layouts) > 1:
        raise RuntimeError(
            "[light-parameters] the loaded shading stacks read different light parameters {0}; they come from "
            "different generator builds -- redeploy the stale ones".format(sorted(layouts)))
    return list(next(iter(layouts))) if layouts else []


def stamp(light, stated):
    """Stamp a stated light's fade coefficients and parameter vectors onto its Blender light data ``light``."""
    fade = [float(value) for value in stated["fade"]]
    if any(fade):
        light[material_builder.LIGHT_FADE_PROPERTY] = fade
    vectors = stated["parameters"]
    if len(vectors) == 0:
        return
    names = attribute_names()
    if len(vectors) != len(names):
        raise ValueError("[light-parameters] light {0} states {1} parameter vectors; the stacks read {2}".format(
            light.name, len(vectors), len(names)))
    for name, vector in zip(names, vectors):
        values = [float(value) for value in vector]
        if any(values):
            light[name] = values


def cull(light, stated):
    """Cull a stated point or spot light, its Blender light data ``light``, where the source's shading stops lighting
    anything: the light's cutoff distance is EEVEE's cull radius, and the source states its range and its reach (a
    culling box can stop its shading short of the range). The radius is the reach widened just enough that the
    influence there stays over the loop's threshold -- for a spot, whose cone edge multiplies in, over the range cull's
    influence there divided by :data:`SPOT_EDGE_TOLERANCE` -- so nothing the source lights is culled. What the range
    exceeds it by is stamped beside it for the stacks (:func:`source_range`)."""
    reach, stated_range = float(stated["reach"]), float(stated["range"])
    light.use_custom_distance = True
    if reach >= stated_range:
        light.cutoff_distance = stated_range
        return
    floor = LOOP_THRESHOLD
    if light.type == "SPOT":
        floor = max(floor, _influence(reach, stated_range) / SPOT_EDGE_TOLERANCE)
    light.cutoff_distance = min(stated_range, reach / (1.0 - math.sqrt(floor)) ** 0.25)
    excess = stated_range - light.cutoff_distance
    if excess > 0.0:
        light[material_builder.LIGHT_RANGE_EXCESS_PROPERTY] = [excess] * 4


def source_range(light):
    """The source's range of a light :func:`cull` culled: its cutoff distance and the excess stamped beside it."""
    excess = light.get(material_builder.LIGHT_RANGE_EXCESS_PROPERTY)
    return light.cutoff_distance + (float(excess[0]) if excess is not None else 0.0)


def _influence(distance, radius):
    factor = (distance / radius) ** 2
    return max(0.0, 1.0 - factor * factor) ** 2


def stamp_frame(obj, world):
    """Stamp a stated light's world orientation onto its Blender object ``obj`` placed at the world matrix ``world``:
    the (x, y, z) of the world rotation quaternion with w >= 0. An unturned light carries none."""
    rotation = world.to_3x3().normalized().to_quaternion()
    if rotation.w < 0.0:
        rotation.negate()
    frame = [rotation.x, rotation.y, rotation.z]
    if any(frame):
        obj[material_builder.LIGHT_FRAME_PROPERTY] = frame
