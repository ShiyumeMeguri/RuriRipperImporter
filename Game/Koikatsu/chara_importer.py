"""Assemble one character into the scene from the plan the game's hook produced.

The generic importer knows how to build ONE prefab. A Koikatsu character is a
dozen of them sharing a skeleton, and WHICH dozen -- in what order, on which
bone, rebound how -- is the hook's answer (``koikatsu.chara.plan``). What is left
here is the part that can only happen in Blender:

* the first row IS the skeleton, and every later part is placed at the BONE the
  plan names, so a hair prefab authored relative to the head's hair anchor lands
  on that anchor rather than at the origin;
* a part's own copy of the skeleton is thrown away and its skinning re-pointed at
  the real one, joined purely on bone NAME -- which
  ``armature_builder.graft_armature`` already does, keeping only the bones the
  part genuinely adds (a skirt's, a ponytail's);
* a part's unskinned pieces are parented to that same attachment bone.

The result is ONE armature driving every piece, which is what the game has at
runtime and what an animation clip expects to bind to.
"""

from __future__ import annotations

from math import radians

import bpy
from mathutils import Euler, Matrix

from ... import armature_builder, coordinate, prefab_importer
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import bridge_asset_db, class_registry, discovery
from . import datasets, face_importer

# What a character part contributes, by class NAME. The exclusions are the
# deciding optimisation: one head bundle's closure carries the game's whole
# eye/eyebrow/nose PATTERN LIBRARY as several hundred textures a build never
# looks at.
_GEOMETRY = ("GameObject", "Transform", "Mesh", "SkinnedMeshRenderer", "MeshRenderer",
             "MeshFilter", "MonoBehaviour", "MonoScript")
_MATERIALS = ("Material", "Shader")
_TEXTURES = ("Texture2D",)

# The plan's own vocabulary for how a part binds, and the rig's root.
REBIND_BODY = "body"
ROOT_BONE = "cf_j_root"


class BuildReport:
    __slots__ = ("armature", "parts", "missing", "warnings")

    def __init__(self):
        self.armature = None
        self.parts = 0
        self.missing = []
        self.warnings = []

    def summary(self):
        text = "{0} part(s)".format(self.parts)
        if self.missing:
            text += ", {0} not in the closure".format(len(self.missing))
        return text


def _class_ids(options):
    names = list(_GEOMETRY)
    if options.get("import_materials", True):
        names.extend(_MATERIALS)
        if options.get("import_textures", True):
            names.extend(_TEXTURES)
    resolved = []
    for name in names:
        class_id = class_registry.id_for_name(name)
        if class_id is not None and class_id not in resolved:
            resolved.append(class_id)
    return resolved


def build(context, plan, options=None, name="Character"):
    """Build a plan (the rows of ``koikatsu.chara.plan``). Returns a BuildReport;
    the armature it names is the one every later flow binds to."""
    options = dict(prefab_importer.resolve_options(options))
    # A part list only means anything against a skeleton -- the pieces are bound to
    # it by name, and without one they would land as loose meshes.
    options["import_skeleton"] = True
    report = BuildReport()
    if not plan:
        return report

    bundles = []
    for part in plan:
        if part["bundle"] not in bundles:
            bundles.append(part["bundle"])
    cabs = datasets.cabs_for(bundles)
    if not cabs:
        report.warnings.append("none of the {0} bundle(s) are in the loaded cabmap".format(len(bundles)))
        return report

    # One import for the whole plan: the parts share bundles heavily, and a closure
    # resolved once is the difference between a character and a re-read per slot.
    assets, roots, _seeds, _clips, _scenes = cabmap_state.BRIDGE.import_cabs(cabs, _class_ids(options))
    db = bridge_asset_db.BridgeAssetDatabase(
        assets, clip_curve_blobs=cabmap_state.BRIDGE.clip_curves_by_guid,
        mesh_blobs=cabmap_state.BRIDGE.mesh_blobs_by_guid,
        asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid)
    index = discovery.prefab_name_index(db, roots)

    # Row 0 is the skeleton every later part binds onto. Built apart from the loop:
    # letting the next part quietly become the rig would build a character around a
    # hair prefab's bones.
    skeleton, rest = plan[0], plan[1:]
    prefab = _prefab_of(db, index, skeleton)
    if prefab is None:
        report.missing.append("{0} ({1})".format(skeleton["asset"], skeleton["bundle"]))
        return report
    built = prefab_importer.import_prefab_from_db(context, db, prefab, options,
                                                  name=skeleton["asset"], top_level=True)
    if built.armature is None:
        report.warnings.append("{0} carries no transform hierarchy to build a skeleton from".format(
            skeleton["asset"]))
        return report
    report.armature = built.armature
    report.armature.name = name
    report.parts = 1

    for part in rest:
        prefab = _prefab_of(db, index, part)
        if prefab is None:
            report.missing.append("{0} ({1})".format(part["asset"], part["bundle"]))
            continue
        built = prefab_importer.import_prefab_from_db(
            context, db, prefab, options, name=part["label"], top_level=False)
        _attach(context, report, built, part)
        report.parts += 1
        if part["slot"] == "head":
            # The head IS the expression system; remember WHICH head so the face can
            # still be driven in a later session (its pattern table is the hook's).
            face_importer.stamp(report.armature, part["bundle"], part["asset"])

    return report


def _prefab_of(db, index, part):
    """The part's prefab, by the asset name the plan states. Exact: the game asks
    the bundle for that name, so anything else would be a different piece."""
    guid = index.get(str(part["asset"]).lower())
    return db.load_guid(guid) if guid else None


def _attach(context, report, built, part):
    """Place one built part at its attachment bone and fold it into the rig."""
    offset = _attachment_matrix(report.armature, part) @ _correction(part)
    tops = [built.armature] if built.armature is not None else []
    tops.extend(obj for obj in built.mesh_objects if obj.parent is None)
    for obj in tops:
        obj.matrix_world = offset @ obj.matrix_world

    loose = [obj for obj in built.mesh_objects
             if obj.parent is None and obj is not built.armature]
    if built.armature is not None:
        armature_builder.graft_armature(context, report.armature, built.armature)
    anchor = _anchor_bone(report.armature, part)
    for obj in loose:
        if obj.parent is None and anchor:
            _parent_to_bone(obj, report.armature, anchor)


def _anchor_bone(armature, part):
    """The bone this part hangs on: the one the plan names, or the rig's root for a
    part the game parents to the character itself."""
    bones = armature.data.bones
    name = str(part.get("parent") or "")
    if name and name in bones:
        return name
    if ROOT_BONE in bones:
        return ROOT_BONE
    return bones[0].name if len(bones) else ""


def _attachment_matrix(armature, part):
    """Where a part's own space sits in the world.

    A part prefab is authored relative to whatever the game parents it to
    (``SetParent(bone, worldPositionStays: false)`` keeps its local transform), so
    its space is that bone's. Bones live in armature space and the armature object
    carries the once-only root conversion, which is exactly
    ``armature.matrix_world @ bone.matrix_local``."""
    name = str(part.get("parent") or "")
    bone = armature.data.bones.get(name) if name else None
    if bone is None:
        return armature.matrix_world.copy()
    return armature.matrix_world @ bone.matrix_local


def _correction(part):
    """An accessory's per-slot correction, which the plan already composed. Unity's
    left-handed metres and ZXY euler degrees become Blender's frame the same way
    every other transform on this path does."""
    def value(column, fallback=0.0):
        try:
            return float(part.get(column, fallback))
        except (TypeError, ValueError):
            return fallback

    position = (value("movePosX"), value("movePosY"), value("movePosZ"))
    rotation = (value("moveRotX"), value("moveRotY"), value("moveRotZ"))
    scale = (value("moveSclX", 1.0), value("moveSclY", 1.0), value("moveSclZ", 1.0))
    if position == (0.0, 0.0, 0.0) and rotation == (0.0, 0.0, 0.0) and scale == (1.0, 1.0, 1.0):
        return Matrix.Identity(4)

    quaternion = Euler((radians(rotation[0]), radians(rotation[1]), radians(rotation[2])),
                       "ZXY").to_quaternion()
    unity = coordinate.unity_trs(
        {"x": position[0], "y": position[1], "z": position[2]},
        {"x": quaternion.x, "y": quaternion.y, "z": quaternion.z, "w": quaternion.w},
        {"x": scale[0], "y": scale[1], "z": scale[2]})
    return coordinate.convert_matrix(unity)


def _parent_to_bone(obj, armature, bone_name):
    """Hang an unskinned piece off a bone, keeping where it already is -- the
    game's own parenting for an accessory or a static clothing piece."""
    world = obj.matrix_world.copy()
    obj.parent = armature
    obj.parent_type = "BONE"
    obj.parent_bone = bone_name
    obj.matrix_world = world


def find_armature(context, name=""):
    """The rig a later flow should act on: the named one, else the active object's,
    else the only one in the scene."""
    if name:
        found = bpy.data.objects.get(name)
        if found is not None and found.type == "ARMATURE":
            return found
    active = context.view_layer.objects.active if context.view_layer else None
    if active is not None:
        if active.type == "ARMATURE":
            return active
        if active.parent is not None and active.parent.type == "ARMATURE":
            return active.parent
    armatures = [obj for obj in context.scene.objects if obj.type == "ARMATURE"]
    return armatures[0] if len(armatures) == 1 else None


def mesh_children(armature):
    """Every mesh the rig drives, skinned or hung off a bone."""
    if armature is None:
        return []
    found = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        if obj.parent is armature or any(
                modifier.type == "ARMATURE" and modifier.object is armature
                for modifier in obj.modifiers):
            found.append(obj)
    return found
