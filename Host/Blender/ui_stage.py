"""Put one of the game's UI display stages into the Blender scene.

Four things arrive, each straight out of the stage's own assets and none of them
invented here:

``HGEnvironmentPhase``   the sun -- direction, colour temperature, and the
                         PRE-DIVIDED intensity its own shaders are handed
                         (``directIntensityDividePi``; see ``_write_light``),
                         plus the sky's own ambient SH as the world colour.
``volume``               the engine globals the stage's own volumes blend to --
                         the post-processing switches and the character lighting,
                         packed the way the game's render pipeline packs them --
                         written onto the scene's world the way a level's are, so
                         the shading stacks read them live.
``post``                 the post-processing the stage's camera renders under --
                         the grading and the exposure of its phase, the bloom and
                         the screen-space reflection its volumes blend to -- as
                         the post chain's own inputs, written the way a level's
                         grading is.
``stage prefab``         the stage itself, loaded through the kernel's one load:
                         floor, sky sphere, cameras, hierarchy 1:1.
"""

from __future__ import annotations

import bpy
import numpy
from mathutils import Vector

from . import coordinate, derived_state, material_builder, materialise
from ...Kernel.app import loading, staging

MAIN_CAMERA_TAG = "MainCamera"

#: Where each target the game resolved gets written. The names are the shared
#: ones (``Kernel.app.staging``); which FIELD of which asset produced a value is
#: the game's answer and never reaches here.
LIGHT_DIRECTION = staging.LIGHT_DIRECTION
LIGHT_ENERGY = staging.LIGHT_ENERGY
LIGHT_COLOR = staging.LIGHT_COLOR
LIGHT_SHADOWS = staging.LIGHT_SHADOWS
LIGHT_VOLUME = staging.LIGHT_VOLUME
WORLD_COLOR = staging.WORLD_COLOR

def apply_environment(context, pairs, name="Endfield Sun", exposure_ev=0.0):
    """Build/replace the stage's sun and world from the pairs the game resolved,
    one target at a time. Nothing here knows which field of which asset meant
    what -- only how to write a Blender light and a world.

    ``exposure_ev`` is stops, applied as ONE factor to every radiance the stage
    states -- the sun's energy and the sky's ambient alike -- so the balance
    between them stays exactly as authored. 0 writes the asset's own numbers.
    It is a control rather than a read value because the game's absolute level
    is produced at runtime by metering the rendered frame (HGAutoExposure); the
    asset fixes the RATIOS and nothing else, and inventing a factor to stand in
    for the missing level would be a number this add-on made up."""
    scale = 2.0 ** float(exposure_ev)
    sun = None
    for target, value in pairs:
        if target == WORLD_COLOR:
            _world(context, value, scale)
            continue
        sun = sun or _sun(context, name)
        _write_light(sun, target, value, scale)
    if sun is not None:
        derived_state.announce(sun)
    return sun


def _sun(context, name):
    """The stage's directional light, built once and reused. A point source: the game's shadow is a filter of
    one fixed radius over its shadow map, and an angular size here would widen it with every metre to the
    occluder, a canopy's shadow gone to noise where the game's stays a leaf."""
    data = bpy.data.lights.get(name)
    if data is None or data.type != "SUN":
        data = bpy.data.lights.new(name, type="SUN")
        data.name = name
    data.angle = 0.0
    obj = bpy.data.objects.get(name)
    if obj is None or obj.data is not data:
        obj = bpy.data.objects.new(name, data)
        context.collection.objects.link(obj)
    elif obj.name not in context.scene.objects:
        context.collection.objects.link(obj)
    return obj


def _write_light(obj, target, value, scale):
    """One binding onto the sun.

    A direction is the direction the light TRAVELS, already resolved by the game
    from its pitch/yaw; Blender's sun shines down its own -Z, so the object
    simply tracks that vector.

    Energy goes in unchanged, and what arrives is the game's own
    ``directIntensityDividePi`` rather than its ``directIntensity`` -- because
    what READS this light is not Blender's own shading but the ported game
    shader (its ``mainLight.color`` capability is fulfilled with
    ``data.color * data.energy``), and the quantity that shader is handed in
    game is ``directColor * directIntensityDividePi``. Feeding it the undivided
    intensity is pi times too much light, which on a tone-mapped stage reads as
    a blown-white frame. The Lambert 1/pi is therefore accounted for exactly
    once, on the side that authored it.

    The colour arrives as the game emits it, linear and per unit of that energy:
    a temperature is the game's own curve, folded in before it gets here, never
    Blender's blackbody."""
    data = obj.data
    if target == LIGHT_DIRECTION:
        unity = numpy.array([value], dtype=numpy.float32)
        converted = coordinate.convert_points(unity)[0]
        direction = coordinate.root_matrix().to_3x3() @ Vector(
            (float(converted[0]), float(converted[1]), float(converted[2])))
        if direction.length > 1e-6:
            obj.rotation_mode = "QUATERNION"
            obj.rotation_quaternion = direction.to_track_quat("-Z", "Y")
    elif target == LIGHT_ENERGY:
        data.energy = value * scale
    elif target == LIGHT_COLOR:
        data.color = value
    elif target == LIGHT_SHADOWS:
        data.use_shadow = value > 0.5
    elif target == LIGHT_VOLUME:
        data.volume_factor = value


def _world(context, ambient, scale):
    """The stage's ambient: the DC term of its own baked sky SH per channel, as the game
    resolved it. Scaled by the same stops as the sun -- an exposure that moved one and not
    the other would change the balance the asset authored."""
    channels = [channel * scale for channel in ambient]
    world = context.scene.world
    if world is None:
        world = bpy.data.worlds.new("Endfield UI Stage")
        context.scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs[0].default_value = (channels[0], channels[1], channels[2], 1.0)
    return world


def adopt_camera(context, cameras):
    """Make the stage's own camera the scene camera, so numpad-0 looks through
    what the game looks through. The render aspect follows: a vertical FOV only
    frames the same picture at the same aspect ratio.

    Which of a stage's cameras that is comes off Unity's own ``MainCamera`` tag,
    which the prefab states on exactly the one the game renders through; the
    rest are cinemachine rigs and per-body-type framing helpers. Falling back to
    "the first visible one" would be a guess, so it only happens when the prefab
    tags nothing."""
    if not cameras:
        return None
    tagged = [obj for obj in cameras if obj.get(materialise.TAG) == MAIN_CAMERA_TAG]
    visible = [obj for obj in cameras if not obj.hide_viewport] or list(cameras)
    chosen = tagged[0] if tagged else visible[0]
    context.scene.camera = chosen
    render = context.scene.render
    if render.resolution_x * 9 != render.resolution_y * 16:
        render.resolution_x, render.resolution_y = 1920, 1080
    return chosen


# ---------------------------------------------------------------------------
# The one entry the port names
# ---------------------------------------------------------------------------
def load(context, stage, options):
    """Write one stated display stage into this scene, and say what was written.

    The halves are independent on purpose: a stage whose art is not wanted
    still lights the character, and a stage the install ships no prefab for still
    has a sun. What each of them IS came off the statement; none of it is read
    here."""
    done = []
    if stage["environment"]:
        sun = apply_environment(context, stage["environment"], stage["label"] + " Sun",
                                stage["exposure"])
        done.append("sun " + (sun.name if sun else "(no direction in the asset)"))
    if stage["volume"]:
        written, unclaimed = material_builder.apply_level_resources(context.scene, stage["volume"], [])
        done.append("{0} volume global(s){1}".format(
            len(written), " ({0} no stack reads)".format(len(unclaimed)) if unclaimed else ""))
    if stage["post"]:
        done.append("{0} post input(s)".format(material_builder.apply_post_inputs(context.scene, stage["post"])))
    if stage["prefabs"]:
        done.extend(_load_art(context, stage["prefabs"], options))
    return done


def _load_art(context, prefabs, options):
    """The stage's own prefab, loaded the ordinary way: its paths are its seeds."""
    seeds = [path for path in prefabs if path]
    if not seeds:
        return ["this stage names no art to build"]
    before = set(bpy.data.objects)
    built = loading.load(context, seeds, options)
    placed = [obj for obj in bpy.data.objects if obj not in before]
    meshes = len([obj for obj in placed if obj.type == "MESH"])
    cameras = [obj for obj in placed if obj.type == "CAMERA"]
    done = ["{0} mesh(es), {1} camera(s)".format(meshes, len(cameras))] + built.warnings[:3]
    chosen = adopt_camera(context, cameras)
    if chosen is not None:
        done.append("looking through " + chosen.name)
    return done
