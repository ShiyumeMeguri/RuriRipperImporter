"""What ONE game states about its own secondary motion, and what that means.

This game tunes its hair, cloth and accessory chains ON the model prefab itself,
which is the whole reason those settings can travel with an import at all. This
reads them and states them in the SOLVER's own terms (:mod:`Kernel.app.rigging`
carries the shape; the names below are the cloth solver's).

BOTH halves of the join live here, and that is the point. Which fields this game
writes, and what its enum ordinals mean, is this game's knowledge -- a second
title states different fields under different names, and it brings its own table
rather than editing a shared one. What the other side calls each parameter is the
solver's, and a path the solver does not carry fails at the assignment where the
truth is, not against a copy kept somewhere else.

A parameter the solver has no counterpart for is listed in ``UNMAPPED`` with why:
a parameter that silently goes nowhere is the same defect whether it was forgotten
or decided against, and only a list tells the two apart.
"""

from __future__ import annotations

from ...Kernel.app import rigging
from ...RuriRipperPyBridge.session import cabmap_state

CONFIGS = "endfield.cloth.configs"
VALUES = "endfield.cloth.values"
CURVES = "endfield.cloth.curves"
BONES = "endfield.cloth.bones"
COLLIDERS = "endfield.cloth.colliders"
CONFIG_COLLIDERS = "endfield.cloth.config_colliders"
ATTRIBUTES = "endfield.cloth.attributes"

#: How many points the decoder samples a curve at. Published as the LENGTH of each
#: row's sample list rather than as a number the writer also has to know.
CURVE_SAMPLES = 16

FLOAT = "float"
BOOLEAN = "boolean"
ENUM = "enum"

#: This game's word for each bone role -> the shared one.
ROLES = {
    "rootBones": rigging.ROOT_ROLE,
    "ignoreFromRootBones": rigging.IGNORE_ROLE,
    "skinningBones": rigging.SKINNING_ROLE,
    "collisionBones": rigging.COLLISION_ROLE,
}

#: This game's per-particle attribute ordinal -> the shared one.
ATTRIBUTE_KINDS = {0: rigging.IGNORE_ATTRIBUTE, 1: rigging.FIXED_ATTRIBUTE,
                   2: rigging.MOVE_ATTRIBUTE}

# This game's enum ordinal -> the identifier the solver names that choice by.
CLOTH_TYPES = {1: 'BONE_CLOTH', 2: 'BONE_SPRING'}
CONNECTION_MODES = {0: 'LINE', 1: 'AUTOMATIC_MESH', 2: 'SEQUENTIAL_LOOP_MESH',
                    3: 'SEQUENTIAL_NON_LOOP_MESH'}
NORMAL_AXES = {0: 'RIGHT', 1: 'UP', 2: 'FORWARD',
               3: 'INVERSE_RIGHT', 4: 'INVERSE_UP', 5: 'INVERSE_FORWARD'}
ALIGNMENT_MODES = {0: 'NONE', 1: 'BOUNDING_BOX_CENTER', 2: 'TRANSFORM'}
TELEPORT_MODES = {0: 'NONE', 1: 'RESET', 2: 'KEEP'}
COLLISION_MODES = {0: 'NONE', 1: 'POINT', 2: 'EDGE'}
SELF_MODES = {0: 'NONE', 1: 'FULL_MESH'}

#: (this game's field path, the solver's path, kind, that kind's choices).
VALUE_TABLE = (
    ("clothType", "cloth_type", ENUM, CLOTH_TYPES),
    ("connectionMode", "connection_mode", ENUM, CONNECTION_MODES),
    ("rotationalInterpolation", "rotational_interpolation", FLOAT, None),
    ("rootRotation", "root_rotation", FLOAT, None),
    ("animationPoseRatio", "animation_pose_ratio", FLOAT, None),
    ("blendWeight", "blend_weight", FLOAT, None),
    ("stablizationTimeAfterReset", "stablization_time", FLOAT, None),
    ("normalAxis", "normal_axis", ENUM, NORMAL_AXES),
    ("gravity", "gravity", FLOAT, None),
    ("gravityFalloff", "gravity_falloff", FLOAT, None),
    ("customSkinningSetting.enable", "custom_skinning_enable", BOOLEAN, None),
    ("normalAlignmentSetting.alignmentMode", "normal_alignment_mode", ENUM, ALIGNMENT_MODES),
    ("cullingSettings.distanceCullingLength.value",
     "culling.distance_culling_length.value", FLOAT, None),
    ("cullingSettings.distanceCullingLength.use",
     "culling.distance_culling_length.use", BOOLEAN, None),
    ("cullingSettings.distanceCullingFadeRatio", "culling.distance_culling_fade_ratio", FLOAT, None),
    ("inertiaConstraint.anchorInertia", "inertia.anchor_inertia", FLOAT, None),
    ("inertiaConstraint.worldInertia", "inertia.world_inertia", FLOAT, None),
    ("inertiaConstraint.movementInertiaSmoothing", "inertia.movement_inertia_smoothing", FLOAT, None),
    ("inertiaConstraint.movementSpeedLimit.value", "inertia.movement_speed_limit.value", FLOAT, None),
    ("inertiaConstraint.movementSpeedLimit.use", "inertia.movement_speed_limit.use", BOOLEAN, None),
    ("inertiaConstraint.rotationSpeedLimit.value", "inertia.rotation_speed_limit.value", FLOAT, None),
    ("inertiaConstraint.rotationSpeedLimit.use", "inertia.rotation_speed_limit.use", BOOLEAN, None),
    ("inertiaConstraint.localInertia", "inertia.local_inertia", FLOAT, None),
    ("inertiaConstraint.localMovementSpeedLimit.value",
     "inertia.local_movement_speed_limit.value", FLOAT, None),
    ("inertiaConstraint.localMovementSpeedLimit.use",
     "inertia.local_movement_speed_limit.use", BOOLEAN, None),
    ("inertiaConstraint.localRotationSpeedLimit.value",
     "inertia.local_rotation_speed_limit.value", FLOAT, None),
    ("inertiaConstraint.localRotationSpeedLimit.use",
     "inertia.local_rotation_speed_limit.use", BOOLEAN, None),
    ("inertiaConstraint.depthInertia", "inertia.depth_inertia", FLOAT, None),
    ("inertiaConstraint.centrifualAcceleration", "inertia.centrifugal_acceleration", FLOAT, None),
    ("inertiaConstraint.particleSpeedLimit.value", "inertia.particle_speed_limit.value", FLOAT, None),
    ("inertiaConstraint.particleSpeedLimit.use", "inertia.particle_speed_limit.use", BOOLEAN, None),
    ("inertiaConstraint.teleportMode", "inertia.teleport_mode", ENUM, TELEPORT_MODES),
    ("inertiaConstraint.teleportDistance", "inertia.teleport_distance", FLOAT, None),
    ("inertiaConstraint.teleportRotation", "inertia.teleport_rotation", FLOAT, None),
    ("tetherConstraint.distanceCompression", "tether.distance_compression", FLOAT, None),
    ("triangleBendingConstraint.stiffness", "triangle_bending.stiffness", FLOAT, None),
    ("angleRestorationConstraint.useAngleRestoration", "angle_restoration.use", BOOLEAN, None),
    ("angleRestorationConstraint.velocityAttenuation",
     "angle_restoration.velocity_attenuation", FLOAT, None),
    ("angleRestorationConstraint.gravityFalloff", "angle_restoration.gravity_falloff", FLOAT, None),
    ("angleLimitConstraint.useAngleLimit", "angle_limit.use", BOOLEAN, None),
    ("angleLimitConstraint.stiffness", "angle_limit.stiffness", FLOAT, None),
    ("motionConstraint.useMaxDistance", "motion.use_max_distance", BOOLEAN, None),
    ("motionConstraint.useBackstop", "motion.use_backstop", BOOLEAN, None),
    ("motionConstraint.backstopRadius", "motion.backstop_radius", FLOAT, None),
    ("motionConstraint.stiffness", "motion.stiffness", FLOAT, None),
    ("colliderCollisionConstraint.mode", "collider_collision.mode", ENUM, COLLISION_MODES),
    ("colliderCollisionConstraint.friction", "collider_collision.friction", FLOAT, None),
    ("selfCollisionConstraint.selfMode", "self_collision.self_mode", ENUM, SELF_MODES),
    ("selfCollisionConstraint.syncMode", "self_collision.sync_mode", ENUM, SELF_MODES),
    ("selfCollisionConstraint.clothMass", "self_collision.cloth_mass", FLOAT, None),
    ("wind.influence", "wind.influence", FLOAT, None),
    ("wind.frequency", "wind.frequency", FLOAT, None),
    ("wind.turbulence", "wind.turbulence", FLOAT, None),
    ("wind.blend", "wind.blend", FLOAT, None),
    ("wind.synchronization", "wind.synchronization", FLOAT, None),
    ("wind.depthWeight", "wind.depth_weight", FLOAT, None),
    ("wind.movingWind", "wind.moving_wind", FLOAT, None),
    ("springConstraint.useSpring", "spring.use_spring", BOOLEAN, None),
    ("springConstraint.springPower", "spring.spring_power", FLOAT, None),
    ("springConstraint.limitDistance", "spring.limit_distance", FLOAT, None),
    ("springConstraint.normalLimitRatio", "spring.normal_limit_ratio", FLOAT, None),
    ("springConstraint.springNoise", "spring.noise", FLOAT, None),
)

#: A direction is stated in this game's own world, and that world does not stand
#: the same way up as any host's, so the three components are gathered back into
#: ONE row and the host converts the vector as a whole.
DIRECTION_TABLE = (("gravityDirection", "gravity_direction"),)

DIRECTION_COMPONENTS = ("x", "y", "z")

CURVE_TABLE = (
    ("damping", "damping"),
    ("radius", "radius"),
    ("distanceConstraint.stiffness", "distance.stiffness"),
    ("angleRestorationConstraint.stiffness", "angle_restoration.stiffness"),
    ("angleLimitConstraint.limitAngle", "angle_limit.limit_angle"),
    ("motionConstraint.maxDistance", "motion.max_distance"),
    ("motionConstraint.backstopDistance", "motion.backstop_distance"),
    ("colliderCollisionConstraint.limitDistance", "collider_collision.limit_distance"),
    ("selfCollisionConstraint.surfaceThickness", "self_collision.surface_thickness"),
)

#: What this game states and no solver here has a counterpart for, with why.
UNMAPPED = {
    "updateMode": "which update loop the game ticks the solver on; a host has its own",
    "meshWriteMode": "the source is bones here, so there is no mesh to write back",
    "paintMode": "an authoring-time tool state, not a simulation parameter",
    "reductionSetting.simpleDistance": "mesh reduction, which bone chains do not go through",
    "reductionSetting.shapeDistance": "mesh reduction, which bone chains do not go through",
    "clothAnimatorAbilityLODThreshold": "a runtime level-of-detail budget with no viewport meaning",
    "clothAnimatorLODThreshold": "a runtime level-of-detail budget with no viewport meaning",
    "clothLodFadeTime": "a runtime level-of-detail budget with no viewport meaning",
    "clothSimulateWeight": "driven per frame by the game, not authored",
    "resetSimulationToAnimationPoseWhenWeightLow": "follows the weight the game drives",
    "resetSimulationToAnimationPoseWeightThreshold": "follows the weight the game drives",
    "cullingSettings.cameraCullingMode": "the source states a mask no solver here has an equivalent for",
    "cullingSettings.cameraCullingMethod": "the source states a mask no solver here has an equivalent for",
}

VALUE_BY_SOURCE = {row[0]: row for row in VALUE_TABLE}
DIRECTION_BY_SOURCE = dict(DIRECTION_TABLE)
CURVE_BY_SOURCE = dict(CURVE_TABLE)


def _table(dataset_id, texts):
    return cabmap_state.BRIDGE.game_data(dataset_id, assetText=list(texts))


def _rows(dataset_id, texts):
    table = _table(dataset_id, texts)
    return [{name: table.cell(index, name) for name in table.names}
            for index in range(len(table))]


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _direction_of(path):
    """(the solver's direction name, component index) for a leaf that is part of
    one, else (None, -1)."""
    head, _, component = path.rpartition(".")
    if head in DIRECTION_BY_SOURCE and component in DIRECTION_COMPONENTS:
        return DIRECTION_BY_SOURCE[head], DIRECTION_COMPONENTS.index(component)
    return None, -1


def _coerce(kind, choices, number, path, unknown):
    """One scalar in the solver's own terms: a bool, a choice identifier, or a
    float. An ordinal outside the choices this game states is reported, never
    guessed at."""
    if kind == BOOLEAN:
        return number != 0.0
    if kind == ENUM:
        identifier = choices.get(int(round(number)))
        if identifier is None:
            unknown[path] = number
        return identifier
    return number


def _values(rows, unknown):
    """Every scalar and every direction, in the solver's own names."""
    found = []
    directions = {}
    for row in rows:
        config, path, number = _int(row["config"]), row["path"], float(row["value"])
        name, component = _direction_of(path)
        if name is not None:
            directions.setdefault((config, name), [0.0, 0.0, 0.0])[component] = number
            continue
        entry = VALUE_BY_SOURCE.get(path)
        if entry is None:
            if path not in UNMAPPED:
                unknown[path] = number
            continue
        value = _coerce(entry[2], entry[3], number, path, unknown)
        if value is not None:
            found.append({"config": config, "path": entry[1], "value": value,
                          "kind": rigging.VALUE})
    for (config, name), components in directions.items():
        found.append({"config": config, "path": name, "value": tuple(components),
                      "kind": rigging.DIRECTION})
    return found


def _curves(rows, unknown):
    found = []
    for row in rows:
        name = CURVE_BY_SOURCE.get(row["path"])
        if name is None:
            if row["path"] not in UNMAPPED:
                unknown[row["path"]] = float(row["value"])
            continue
        found.append({"config": _int(row["config"]), "path": name,
                      "use": bool(_int(row["use"])), "value": float(row["value"]),
                      "samples": [float(row["sample%d" % index])
                                  for index in range(CURVE_SAMPLES)]})
    return found


def read(texts):
    """Everything one prefab states about its secondary motion, as the statement
    :mod:`Kernel.app.rigging` describes."""
    unknown = {}
    reading = {
        "configs": [{"index": _int(row["index"]), "name": row["name"]}
                    for row in _rows(CONFIGS, texts)],
        "values": _values(_rows(VALUES, texts), unknown),
        "curves": _curves(_rows(CURVES, texts), unknown),
        "bones": [{"config": _int(row["config"]),
                   "role": ROLES.get(row["role"], row["role"]), "bone": row["bone"]}
                  for row in _rows(BONES, texts)],
        "colliders": [{"index": _int(row["index"]), "bone": row["bone"], "kind": row["kind"],
                       "center": (float(row["centerX"]), float(row["centerY"]), float(row["centerZ"])),
                       "size": (float(row["sizeX"]), float(row["sizeY"]), float(row["sizeZ"])),
                       "direction": _int(row["direction"]),
                       "aligned_on_center": bool(_int(row["alignedOnCenter"])),
                       "radius_separation": bool(_int(row["radiusSeparation"])),
                       "reverse_direction": bool(_int(row["reverseDirection"])),
                       "local_position": (float(row["localX"]), float(row["localY"]),
                                          float(row["localZ"])),
                       "local_rotation": (float(row["localRotationX"]), float(row["localRotationY"]),
                                          float(row["localRotationZ"]), float(row["localRotationW"])),
                       "bone_position": (float(row["boneX"]), float(row["boneY"]), float(row["boneZ"])),
                       "bone_rotation": (float(row["boneRotationX"]), float(row["boneRotationY"]),
                                         float(row["boneRotationZ"]), float(row["boneRotationW"]))}
                      for row in _rows(COLLIDERS, texts)],
        "config_colliders": [{"config": _int(row["config"]), "collider": _int(row["collider"])}
                             for row in _rows(CONFIG_COLLIDERS, texts)],
        "attributes": [{"config": _int(row["config"]), "bone": row["bone"],
                        "attribute": ATTRIBUTE_KINDS.get(_int(row["attribute"]),
                                                         rigging.MOVE_ATTRIBUTE),
                        "error": float(row["error"])}
                       for row in _rows(ATTRIBUTES, texts)],
    }
    reading["unknown"] = unknown
    return reading
