"""Where a model's authored secondary-motion settings land in the cloth add-on.

The two solvers are the same algorithm, so the parameters are the same parameters
under two spellings: the game writes ``inertiaConstraint.worldInertia`` where the
add-on writes ``inertia.world_inertia``. This module is the one place that says
which spelling is which, and nothing else here knows a field name -- the applier
walks the table, so a parameter that turns up later is a row and not code.

Bone names are the OTHER vocabulary, and this module does not own that one: the
retarget presets already map one skeleton's names onto another's, and a second
table beside them would be a second answer to the same question.
"""

from __future__ import annotations

import json
import pathlib

from ...RuriRipperPyBridge.session import cabmap_state

CONFIGS = "endfield.cloth.configs"
VALUES = "endfield.cloth.values"
CURVES = "endfield.cloth.curves"
BONES = "endfield.cloth.bones"
COLLIDERS = "endfield.cloth.colliders"
CONFIG_COLLIDERS = "endfield.cloth.config_colliders"
ATTRIBUTES = "endfield.cloth.attributes"

CURVE_SAMPLES = 16

ROOT_ROLE = "rootBones"
IGNORE_ROLE = "ignoreFromRootBones"
SKINNING_ROLE = "skinningBones"
COLLISION_ROLE = "collisionBones"

# game enum ordinal -> the add-on's identifier for the same choice
CLOTH_TYPES = {1: 'BONE_CLOTH', 2: 'BONE_SPRING'}
CONNECTION_MODES = {0: 'LINE', 1: 'AUTOMATIC_MESH', 2: 'SEQUENTIAL_LOOP_MESH',
                    3: 'SEQUENTIAL_NON_LOOP_MESH'}
NORMAL_AXES = {0: 'RIGHT', 1: 'UP', 2: 'FORWARD',
               3: 'INVERSE_RIGHT', 4: 'INVERSE_UP', 5: 'INVERSE_FORWARD'}
ALIGNMENT_MODES = {0: 'NONE', 1: 'BOUNDING_BOX_CENTER', 2: 'TRANSFORM'}
TELEPORT_MODES = {0: 'NONE', 1: 'RESET', 2: 'KEEP'}
COLLISION_MODES = {0: 'NONE', 1: 'POINT', 2: 'EDGE'}
SELF_MODES = {0: 'NONE', 1: 'FULL_MESH'}

# The per-particle attribute the game paints, in the add-on's own words.
ATTRIBUTE_KINDS = {0: 'IGNORE', 1: 'FIXED', 2: 'MOVE'}

FLOAT = "float"
BOOLEAN = "boolean"
ENUM = "enum"

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
    ("gravityDirection.x", "gravity_direction.0", FLOAT, None),
    ("gravityDirection.y", "gravity_direction.1", FLOAT, None),
    ("gravityDirection.z", "gravity_direction.2", FLOAT, None),
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

# What the game states and this add-on has no counterpart for. Named rather than
# ignored: a parameter that silently goes nowhere is the same defect whether it
# was forgotten or decided against, and only a list tells the two apart.
UNMAPPED = {
    "updateMode": "which update loop the game ticks the solver on; the host has its own",
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
    "cullingSettings.cameraCullingMode": "the source states a mask this add-on has no equivalent for",
    "cullingSettings.cameraCullingMethod": "the source states a mask this add-on has no equivalent for",
}

VALUE_BY_SOURCE = {row[0]: row for row in VALUE_TABLE}
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


def read(texts):
    """Everything one prefab states about its secondary motion, as plain rows."""
    configs = _rows(CONFIGS, texts)
    return {
        "configs": [{"index": _int(row["index"]), "name": row["name"]} for row in configs],
        "values": [{"config": _int(row["config"]), "path": row["path"],
                    "value": float(row["value"])} for row in _rows(VALUES, texts)],
        "curves": [{"config": _int(row["config"]), "path": row["path"],
                    "use": bool(_int(row["use"])), "value": float(row["value"]),
                    "samples": [float(row["sample%d" % index]) for index in range(CURVE_SAMPLES)]}
                   for row in _rows(CURVES, texts)],
        "bones": [{"config": _int(row["config"]), "role": row["role"], "bone": row["bone"]}
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
                                          float(row["localRotationZ"]), float(row["localRotationW"]))}
                      for row in _rows(COLLIDERS, texts)],
        "config_colliders": [{"config": _int(row["config"]), "collider": _int(row["collider"])}
                             for row in _rows(CONFIG_COLLIDERS, texts)],
        "attributes": [{"config": _int(row["config"]), "bone": row["bone"],
                        "attribute": _int(row["attribute"]), "error": float(row["error"])}
                       for row in _rows(ATTRIBUTES, texts)],
    }


class BoneNames:
    """One skeleton's names read as another's, out of a retarget preset.

    A name the preset does not carry is reported rather than guessed: a bone that
    silently keeps its source name would look like an ordinary missing bone later,
    at a point where nothing remembers it came from a preset that had no row for it.
    """

    def __init__(self, mapping, source):
        self._mapping = dict(mapping)
        self.source = source
        self.missing = set()

    @classmethod
    def from_preset(cls, path):
        document = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        pairs = {}
        for entry in document.get("mappings", []):
            name = str(entry.get("source", ""))
            target = str(entry.get("dest", ""))
            if name and target:
                pairs[name] = target
        return cls(pairs, str(path))

    @classmethod
    def identity(cls):
        return cls({}, "")

    def __len__(self):
        return len(self._mapping)

    def of(self, name):
        if not self._mapping:
            return name
        found = self._mapping.get(name)
        if found is None:
            self.missing.add(name)
            return ""
        return found
