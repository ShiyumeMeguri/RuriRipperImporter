"""Put one of the game's UI display stages into the Blender scene.

Three things arrive, each straight out of the stage's own assets and none of them
invented here:

``HGEnvironmentPhase``   the sun -- direction, colour temperature, intensity --
                         plus the sky's own ambient SH as the world colour, and
                         the stage's own frame EXPOSURE, which every radiance
                         above is written through (see ``pre_exposure``). The
                         exposure is not optional decoration: the game displays
                         ``radiance * preExposure`` and this add-on reproduces
                         the game's tone mapper, so radiance handed over raw
                         arrives thousands of times too bright and clips white
                         before any grade can see it.
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

from ... import coordinate, light_units, material_builder, prefab_importer
from . import datasets, ui_scene_state

STAGE_COLLECTION = "Endfield UI Stage"

MAIN_CAMERA_TAG = "MainCamera"

# Which field of which asset drives which host target is the game's own answer
# (``endfield.ui.bindings``); what is left here is the writing -- a Blender light,
# a world colour, one input of a shader group.
LIGHT_DIRECTION = "light.direction"
LIGHT_ENERGY = "light.energy"
LIGHT_PRE_EXPOSURE = "light.preExposure"
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


def pre_exposure(env_data):
    """The stage's own frame exposure, or 1.0 when it states none.

    The game multiplies ALL scene radiance by this before tone mapping, and this
    add-on ports the tone mapper itself into the compositor -- so the exposure
    has to be in the values the compositor RECEIVES. A stage that carries no
    usable number (zero, absent, negative) states no exposure rather than a
    black scene, and the caller says so out loud instead of silently rendering
    something thousands of times too bright."""
    for binding in datasets.ui_bindings():
        if binding["target"] != LIGHT_PRE_EXPOSURE:
            continue
        value = _resolve(env_data, binding["source"])
        if value is None:
            continue
        scalar = _scalar(value)
        return scalar if scalar > 0.0 else 1.0
    return 1.0


def apply_environment(context, env_data, name="Endfield Sun"):
    """Build/replace the stage's sun and world from its own environment asset,
    one published binding at a time. Nothing here knows which field of the
    asset means what -- only how to write a Blender light and a world.

    Every radiance the stage states (the sun's energy, the sky's ambient) is
    written PRE-EXPOSED, by the stage's own exposure -- see ``pre_exposure``.
    The bindings are collected before anything is written because energy and
    exposure are one product: applying them as they arrive would make the
    result depend on the row order of a data table."""
    exposure = pre_exposure(env_data)
    values = {}
    for binding in datasets.ui_bindings():
        target = binding["target"]
        if not (target.startswith("light.") or target.startswith("world.")):
            continue
        value = _resolve(env_data, binding["source"])
        if value is not None:
            values[target] = value

    if WORLD_COLOR in values:
        _world(context, values.pop(WORLD_COLOR), exposure)
    values.pop(LIGHT_PRE_EXPOSURE, None)
    if not values:
        return None
    sun = _sun(context, name)
    for target, value in values.items():
        _write_light(sun, target, value, exposure)
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


def _write_light(obj, target, value, exposure):
    """One binding onto the sun.

    A direction is the direction the light TRAVELS, already resolved by the game
    from its pitch/yaw; Blender's sun shines down its own -Z, so the object
    simply tracks that vector. The intensity crosses as a directional
    irradiance, which is 1:1 (both sides divide by pi for the Lambert term --
    the game even stores the quotient, ``directIntensityDividePi``), times the
    stage's own pre-exposure -- see light_units for why the exposure belongs in
    the value rather than in view_settings. A colour temperature travels as
    Kelvin, which Blender carries on the light itself."""
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
        data.energy = light_units.directional_energy(_scalar(value), exposure)
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


def _world(context, ambient, exposure):
    """The stage's ambient, as the DC term of its own baked sky SH.

    Only the constant term is used: the character shader takes its ambient from
    the character volume rather than the world, so what the world owes the scene
    is the backdrop level, and that is sh[0] per channel. Pre-exposed by the same
    factor as the sun -- an exposure that reached one and not the other would
    change the balance between them, which is worse than applying neither."""
    if not isinstance(ambient, dict):
        return None
    channels = [float(ambient.get("sh[{0:2d}]".format(base), 0.0)) * exposure
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


def apply_post(context):
    """Put the game's own post-processing onto the scene's compositor.

    It belongs there and not in the materials: the game runs its post chain once
    on the composited frame, so a material-side copy would run again for every
    overlapping transparent surface. The stage itself is a generated module that
    registered its own installer -- nothing here knows what the chain does."""
    return material_builder.apply_post_stages(context.scene)


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
