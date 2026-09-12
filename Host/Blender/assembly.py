"""Join several prefabs into ONE rigged character, with Blender objects.

The generic importer knows how to build ONE prefab. Some games ship a character as
a dozen of them sharing a skeleton, and WHICH dozen -- in what order, on which
bone, with what correction -- is that game's answer
(:attr:`Kernel.app.loading.Packages.parts`). What is left here is the part that can
only happen in this application:

* the piece marked ``rig`` IS the skeleton, and every later part is placed at the
  BONE the game names, so a hair prefab authored relative to the head's hair anchor
  lands on that anchor rather than at the origin;
* a part's own copy of the skeleton is thrown away and its skinning re-pointed at
  the real one, joined purely on bone NAME -- which
  ``armature_builder.graft_armature`` already does, keeping only the bones the part
  genuinely adds (a skirt's, a ponytail's);
* a part's unskinned pieces are parented to that same attachment bone.

The result is ONE armature driving every piece, which is what the game has at
runtime and what an animation clip expects to bind to.

Nothing here names a game: every value it acts on came off the parts it was handed.
"""

from __future__ import annotations

from ...Kernel.app import loading
from ...RuriRipperPyBridge.unity import discovery
from . import armature_builder, coordinate, prefab_importer


def build(context, packages, resolved, options=None):
    """Assemble ``packages.parts`` out of an already-resolved closure."""
    options = dict(prefab_importer.resolve_options(options))
    # A part list only means anything against a skeleton -- the pieces are bound to
    # it by name, and without one they would land as loose meshes.
    options["import_skeleton"] = True
    warnings = []
    missing = []
    parts = list(packages.parts)
    if not parts:
        return loading.Built(warnings=["The game states no pieces for '{0}'.".format(
            packages.label)])

    database = resolved["db"]
    index = discovery.prefab_name_index(database, resolved["roots"])

    # The rig is built apart from the loop: letting the next part quietly become the
    # skeleton would build a character around a hair prefab's bones.
    rig_part = next((part for part in parts if part.get("rig")), parts[0])
    rest = [part for part in parts if part is not rig_part]
    prefab = _prefab_of(database, index, rig_part)
    if prefab is None:
        return loading.Built(missing=[rig_part["asset"]], warnings=[
            "'{0}' is not in the resolved closure, so there is no skeleton to build "
            "on.".format(rig_part["asset"])])
    built = prefab_importer.import_prefab_from_db(
        context, database, prefab, options, name=rig_part["asset"], top_level=True)
    if built.armature is None:
        return loading.Built(warnings=[
            "{0} carries no transform hierarchy to build a skeleton from".format(
                rig_part["asset"])])
    armature = built.armature
    armature.name = packages.label or armature.name
    joined = 1

    for part in rest:
        prefab = _prefab_of(database, index, part)
        if prefab is None:
            missing.append(part["asset"])
            continue
        piece = prefab_importer.import_prefab_from_db(
            context, database, prefab, options,
            name=part.get("label") or part["asset"], top_level=False)
        _attach(context, armature, piece, part)
        joined += 1

    if missing:
        warnings.append("{0} piece(s) are not in the closure: {1}".format(
            len(missing), ", ".join(missing[:6])))
    return loading.Built(armature=armature, manifest=packages.manifest,
                         missing=missing, warnings=warnings, imported=joined)


def _prefab_of(database, index, part):
    """The part's prefab, by the asset name the game states. Exact: the game asks
    the bundle for that name, so anything else would be a different piece."""
    guid = index.get(str(part["asset"]).lower())
    return database.load_guid(guid) if guid else None


def _attach(context, armature, built, part):
    """Place one built part at its attachment bone and fold it into the rig."""
    offset = _attachment_matrix(armature, part) @ _correction(part)
    tops = [built.armature] if built.armature is not None else []
    tops.extend(obj for obj in built.mesh_objects if obj.parent is None)
    for obj in tops:
        obj.matrix_world = offset @ obj.matrix_world

    loose = [obj for obj in built.mesh_objects
             if obj.parent is None and obj is not built.armature]
    if built.armature is not None:
        armature_builder.graft_armature(context, armature, built.armature)
    anchor = _anchor_bone(armature, part)
    for obj in loose:
        if obj.parent is None and anchor:
            _parent_to_bone(obj, armature, anchor)


def _anchor_bone(armature, part):
    """The bone this part hangs on -- the one the game names, which is always a real
    bone. Only a rig that does not have it at all falls back to its first bone, and
    that is a broken import, not a naming rule."""
    bones = armature.data.bones
    name = str(part.get("anchor") or "")
    if name and name in bones:
        return name
    return bones[0].name if len(bones) else ""


def _attachment_matrix(armature, part):
    """Where a part's own space sits in the world.

    A part prefab is authored relative to whatever the game parents it to
    (``SetParent(bone, worldPositionStays: false)`` keeps its local transform), so
    its space is that bone's. Bones live in armature space and the armature object
    carries the once-only root conversion, which is exactly
    ``armature.matrix_world @ bone.matrix_local``."""
    name = str(part.get("anchor") or "")
    bone = armature.data.bones.get(name) if name else None
    if bone is None:
        return armature.matrix_world.copy()
    return armature.matrix_world @ bone.matrix_local


def _correction(part):
    """A piece's own correction, in this application's frame. WHAT it means is
    the game's convention and is stated once (loading.part_correction); this only
    brings the result across the same way every other transform crosses."""
    return coordinate.convert_matrix(loading.part_correction(part))


def _parent_to_bone(obj, armature, bone_name):
    """Hang an unskinned piece off a bone, keeping where it already is -- the
    game's own parenting for an accessory or a static clothing piece."""
    world = obj.matrix_world.copy()
    obj.parent = armature
    obj.parent_type = "BONE"
    obj.parent_bone = bone_name
    obj.matrix_world = world
