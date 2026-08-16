"""Put one of the game's UI display stages into the Blender scene.

Three things arrive, each straight out of the stage's own assets and none of them
invented here:

``HGEnvironmentPhase``   the sun -- direction, colour temperature, and the
                         PRE-DIVIDED intensity its own shaders are handed
                         (``directIntensityDividePi``; see ``_write_light``),
                         plus the sky's own ambient SH as the world colour.
``HGCharacterVolume``    the character-lighting overrides, pushed onto every
                         Endfield uber material in the scene as its
                         ``_CharacterParams*`` inputs (see BINDINGS -- which
                         volume field lands in which slot is read off how the
                         game's own shader uses that component).
``stage prefab``         the stage itself, imported by the shared prefab
                         importer: floor, sky sphere, cameras, hierarchy 1:1.

Only parameters the volume actually OVERRIDES are pushed: an unticked row in the
game's inspector contributes nothing, and writing its m_Value anyway would dress
a stale default up as a setting.
"""

from __future__ import annotations

import bpy
import numpy
from mathutils import Vector

from ... import coordinate, prefab_importer
from . import datasets, ui_scene_state

STAGE_COLLECTION = "Endfield UI Stage"

MAIN_CAMERA_TAG = "MainCamera"

# Which field of which asset drives which host target is the game's own answer
# (``endfield.ui.bindings``); what is left here is the writing -- a Blender light,
# a world colour, one input of a shader group.
LIGHT_DIRECTION = "light.direction"
LIGHT_ENERGY = "light.energy"
LIGHT_ANGLE = "light.angle"
LIGHT_COLOR = "light.color"
LIGHT_TEMPERATURE = "light.temperature"
LIGHT_USE_TEMPERATURE = "light.useTemperature"
WORLD_COLOR = "world.color"
CHARACTER_PARAMS = "material.characterParams"

_XYZ = {"x": 0, "y": 1, "z": 2}


def _resolve(data, source):
    """One binding's source value, addressed the way the binding states it
    (``lightConfig.forwardDirect``). None when the asset does not carry it."""
    value = data
    for step in source.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(step)
    return value


def _vector(value, keys="xyz"):
    """A {x,y,z}/{r,g,b} dict or a scalar as a list of floats."""
    if isinstance(value, dict):
        pick = keys if keys[0] in value else ("rgba" if "r" in value else keys)
        return [float(value.get(k, 0.0)) for k in pick]
    return [float(value)]


def _scalar(value):
    if isinstance(value, dict):
        return float(value.get("x", value.get("r", 0.0)))
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    return float(value)


def apply_environment(context, env_data, name="Endfield Sun", exposure_ev=0.0):
    """Build/replace the stage's sun and world from its own environment asset,
    one published binding at a time. Nothing here knows which field of the
    asset means what -- only how to write a Blender light and a world.

    ``exposure_ev`` is stops, applied as ONE factor to every radiance the stage
    states -- the sun's energy and the sky's ambient alike -- so the balance
    between them stays exactly as authored. 0 writes the asset's own numbers.
    It is a control rather than a read value because the game's absolute level
    is produced at runtime by metering the rendered frame (HGAutoExposure); the
    asset fixes the RATIOS and nothing else, and inventing a factor to stand in
    for the missing level would be a number this add-on made up."""
    scale = 2.0 ** float(exposure_ev)
    sun = None
    for binding in datasets.ui_bindings():
        target = binding["target"]
        if not (target.startswith("light.") or target.startswith("world.")):
            continue
        value = _resolve(env_data, binding["source"])
        if value is None:
            continue
        if target == WORLD_COLOR:
            _world(context, value, scale)
            continue
        sun = sun or _sun(context, name)
        _write_light(sun, target, value, scale)
    return sun


def _sun(context, name):
    """The stage's directional light, built once and reused."""
    data = bpy.data.lights.get(name)
    if data is None or data.type != "SUN":
        data = bpy.data.lights.new(name, type="SUN")
        data.name = name
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

    A colour temperature travels as Kelvin, which Blender carries on the light
    itself."""
    data = obj.data
    if target == LIGHT_DIRECTION:
        if not isinstance(value, dict):
            return
        unity = numpy.array([[float(value.get("x", 0.0)), float(value.get("y", 0.0)),
                              float(value.get("z", 0.0))]], dtype=numpy.float32)
        converted = coordinate.convert_points(unity)[0]
        direction = coordinate.root_matrix().to_3x3() @ Vector(
            (float(converted[0]), float(converted[1]), float(converted[2])))
        if direction.length > 1e-6:
            obj.rotation_mode = "QUATERNION"
            obj.rotation_quaternion = direction.to_track_quat("-Z", "Y")
    elif target == LIGHT_ENERGY:
        data.energy = _scalar(value) * scale
    elif target == LIGHT_ANGLE:
        data.angle = 2.0 * _scalar(value)
    elif target == LIGHT_COLOR:
        components = _vector(value, "rgb")
        if len(components) >= 3:
            data.color = (components[0], components[1], components[2])
    elif target == LIGHT_USE_TEMPERATURE:
        if hasattr(data, "use_temperature"):
            data.use_temperature = bool(_scalar(value))
    elif target == LIGHT_TEMPERATURE:
        if _scalar(value) and hasattr(data, "temperature"):
            data.temperature = _scalar(value)


def _world(context, ambient, scale):
    """The stage's ambient, as the DC term of its own baked sky SH.

    Only the constant term is used: the character shader takes its ambient from
    the character volume rather than the world, so what the world owes the scene
    is the backdrop level, and that is sh[0] per channel. Scaled by the same
    stops as the sun -- an exposure that moved one and not the other would
    change the balance the asset authored."""
    if not isinstance(ambient, dict):
        return None
    channels = [float(ambient.get("sh[{0:2d}]".format(base), 0.0)) * scale
                for base in (0, 9, 18)]
    world = context.scene.world
    if world is None:
        world = bpy.data.worlds.new("Endfield UI Stage")
        context.scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs[0].default_value = (channels[0], channels[1], channels[2], 1.0)
    return world


def apply_character_params(char_volume_data, materials=None):
    """Push the stage's HGCharacterVolume onto every Endfield uber material.

    Returns (materials touched, parameters written)."""
    pushed = []
    for binding in datasets.ui_bindings():
        if binding["target"] != CHARACTER_PARAMS:
            continue
        value = (ui_scene_state.overridden(char_volume_data, binding["source"])
                 if binding["gate"] == "override"
                 else char_volume_data.get(binding["source"]))
        if value is not None:
            pushed.append((binding["slot"], binding["components"], value))
    if not pushed:
        return 0, 0

    touched = written = 0
    for material in (materials if materials is not None else bpy.data.materials):
        if not material.use_nodes or material.node_tree is None:
            continue
        hits = 0
        for node in material.node_tree.nodes:
            if node.bl_idname != "ShaderNodeGroup" or node.node_tree is None:
                continue
            for slot, comps, value in pushed:
                hits += _write_slot(node, slot, comps, value)
        if hits:
            touched += 1
            written += hits
    return touched, written


def _write_slot(node, slot, comps, value):
    """Write one volume value into one CP slot of a uber group node. The xyz of
    a slot is one vector socket and its w a second float socket -- the emitter
    splits them, so a write does too."""
    vector_socket = node.inputs.get("_CharacterParams{0}".format(slot))
    w_socket = node.inputs.get("_CharacterParams{0}_w".format(slot))
    written = 0
    if comps in ("rgb", "xyzw"):
        components = _vector(value)
        if vector_socket is not None and len(components) >= 3:
            current = list(vector_socket.default_value)
            for index in range(3):
                current[index] = components[index]
            vector_socket.default_value = current
            written += 1
        if comps == "xyzw" and w_socket is not None and len(components) >= 4:
            w_socket.default_value = components[3]
            written += 1
        return written
    if comps == "w":
        if w_socket is not None:
            w_socket.default_value = _scalar(value)
            written += 1
        return written
    index = _XYZ.get(comps)
    if index is not None and vector_socket is not None:
        current = list(vector_socket.default_value)
        current[index] = _scalar(value)
        vector_socket.default_value = current
        written += 1
    return written


def import_stage(context, db, roots, options):
    """Build the stage prefab exactly as any other prefab is built -- the shared
    importer turns its hierarchy into objects, its renderers into meshes and its
    cameras into cameras -- inside its own collection, so it can be hidden or
    deleted without touching whatever character is being looked at.

    Returns (report, ...) per root built."""
    collection = bpy.data.collections.get(STAGE_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(STAGE_COLLECTION)
    if collection.name not in context.scene.collection.children:
        context.scene.collection.children.link(collection)

    previous = context.view_layer.active_layer_collection
    layer = _layer_for(context.view_layer.layer_collection, collection)
    if layer is not None:
        context.view_layer.active_layer_collection = layer
    reports = []
    try:
        for guid in roots:
            prefab_file = db.load_guid(guid)
            if prefab_file is None:
                continue
            reports.append(prefab_importer.import_prefab_from_db(context, db, prefab_file, options))
    finally:
        if previous is not None:
            context.view_layer.active_layer_collection = previous
    return reports


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
    tagged = [obj for obj in cameras if obj.get(prefab_importer.UNITY_TAG) == MAIN_CAMERA_TAG]
    visible = [obj for obj in cameras if not obj.hide_viewport] or list(cameras)
    chosen = tagged[0] if tagged else visible[0]
    context.scene.camera = chosen
    render = context.scene.render
    if render.resolution_x * 9 != render.resolution_y * 16:
        render.resolution_x, render.resolution_y = 1920, 1080
    return chosen


def _layer_for(layer_collection, collection):
    if layer_collection.collection is collection:
        return layer_collection
    for child in layer_collection.children:
        found = _layer_for(child, collection)
        if found is not None:
            return found
    return None
