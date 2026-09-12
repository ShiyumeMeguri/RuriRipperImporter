"""An Unreal package into Blender, with no other engine in between.

The other road turns an Unreal package into Unity assets, runs the AssetRipper pipeline over
them, serialises a project and hands the text back to be parsed; every step of it exists to
reach facts the engine already stated. This asks the decoder for those facts directly -- what a
level places, the buffers of every mesh, the reference skeleton a skinned one indexes, the
parameters of every material, the pixels of every texture -- and builds from them.

What it does NOT do is build differently. The mesh goes through ``mesh_builder``, the material
through ``material_builder``, the armature through ``armature_builder``: the same three the
Unity path uses, reached through the same normalised forms (``DecodedMesh``,
``MaterialProperties``, reference-skeleton nodes). This module is the assembly -- what is asked
for, in what order, and which object hangs under which -- and nothing else.
"""

from __future__ import annotations

import bpy
from mathutils import Quaternion, Vector

from . import (animation_builder, armature_builder, coordinate, derived_state,
               material_builder, mesh_builder, prefab_importer)
from ...Kernel.app import loading
from ...RuriRipperPyBridge.unreal import direct

# Blender's own name for each light kind the decoder states.
LIGHT_KINDS = {"spot": "SPOT", "directional": "SUN", "point": "POINT", "area": "AREA"}

_DEGREES = 0.017453292519943295


def materialise(context, packages, options=None):
    """Build everything one package places, off placements the module that claims
    this engine already read.

    Nothing here asks the decoder anything: which rows, which meshes, which
    material properties and where the pixels come from is
    :class:`Kernel.app.loading.Packages` of kind PLACEMENTS. What is left is the
    assembly -- which object hangs under which, and what a row becomes."""
    options = prefab_importer.resolve_options(options)
    rows = list(packages.placements)
    if not rows:
        return loading.Built(warnings=["'{0}' places nothing.".format(packages.label)])
    meshes = packages.library
    materials = _build_materials(packages, options)
    built = []
    shared = {}
    for row in rows:
        built.append(_place(context, row, built, meshes, materials, shared, options))
    made = [obj for obj in built if obj is not None]
    return loading.Built(armature=next((obj for obj in made if obj.type == "ARMATURE"), None),
                         imported=len(made))


def import_animations(context, bridge, package, options):
    """A package that places nothing may still carry animation. Only reachable on
    a host with an animation surface -- the caller checks, not this."""
    return _import_animations(context, bridge, package, options)


def _build_materials(packages, options):
    """Every material any placement draws with, built once each -- from the
    properties the decoder stated, through the SAME builder a Unity material goes
    through."""
    if not options.get("import_materials", True) or not packages.materials:
        return {}
    builder = material_builder.MaterialBuilder(packages.textures, options)
    return {path: builder.build_from_props(props, path)
            for path, props in packages.materials.items()}


def _import_animations(context, bridge, package, options):
    """Every sequence in the package as an action on the armature the user is working on.

    The clips reach the same builder a Unity clip reaches, through the same clip form, and bind
    through the rig identity the armature carries -- so a rig this add-on built in any session,
    under any names its bones have since been given, is a valid target.
    """
    if not options.get("import_animations", True):
        return []
    clips = direct.animations(bridge, package)
    if not clips:
        return []
    armature = prefab_importer.find_target_armature(context)
    if armature is None:
        raise RuntimeError(
            "{0} sequence(s) read, but no armature is selected to play them on: import the "
            "character first, then select its armature.".format(len(clips)))
    maps = prefab_importer.maps_from_stamped_armature(armature)
    if maps is None:
        raise RuntimeError(
            "'{0}' carries no rig identity, so a clip cannot be bound to its bones.".format(armature.name))
    first = None
    for name in sorted(clips):
        action, slot, _frames = animation_builder.build_action(
            clips[name], armature, maps, options=options, display_name=name)
        if first is None:
            first = (action, slot)
    if first is not None:
        animation_builder.adopt_action(armature, first[0], first[1], scene=context.scene)
    return [armature]


def _skeleton_nodes(rows):
    """One skinned mesh's reference skeleton in the shape the armature builder takes.
    The decoder's rows are plain numbers; this is where they become vectors."""
    return [(bone["name"], bone["parent"], Vector(bone["position"]),
             Quaternion((bone["rotation"][3], bone["rotation"][0],
                         bone["rotation"][1], bone["rotation"][2])), bone["path"])
            for bone in rows]


def _place(context, row, built, meshes, materials, shared, options):
    """One row as a Blender object: a mesh where it renders one, a light where it lights, an
    empty otherwise -- so a transform other rows hang under never disappears.

    The object returned is the one that CARRIES the placement: a skinned mesh hangs under the
    armature its weights index, so the armature is the placement and the mesh rides it.
    """
    entry = meshes.get(row["mesh"])
    if entry is not None:
        obj = _mesh_object(context, row, entry, materials, meshes, shared, options)
    elif row["light"]:
        obj = _light_object(row)
        context.collection.objects.link(obj)
    else:
        obj = bpy.data.objects.new(row["name"], None)
        context.collection.objects.link(obj)
    parent = int(row["parent"])
    top = not (0 <= parent < len(built) and built[parent] is not None)
    if not top:
        obj.parent = built[parent]
    convert = coordinate.convert_root_matrix if top else coordinate.convert_matrix
    obj.matrix_basis = convert(coordinate.unity_trs(
        {"x": row["px"], "y": row["py"], "z": row["pz"]},
        {"x": row["qx"], "y": row["qy"], "z": row["qz"], "w": row["qw"]},
        {"x": row["sx"], "y": row["sy"], "z": row["sz"]}))
    if row["active"] != "1":
        obj.hide_viewport = obj.hide_render = True
    return obj


def _mesh_object(context, row, entry, materials, meshes, shared, options):
    """A placement that renders a mesh: the mesh with its slots, and -- when it is skinned --
    the armature its weights index, with the mesh parented to it.

    A level stamps the same mesh into hundreds of placements: this one measures 1533 of them
    over 56 distinct meshes, so building a fresh mesh per placement writes the same 215k
    vertices 5.47 MILLION times over. A placement that renders the same mesh with the same
    materials as one already built gets an object over the SAME mesh datablock -- Blender's own
    linked duplicate, which is what the engine does with them too. Skinned placements are not
    shared: their weights live in vertex groups on the object, and their armature is their own.

    Returns whichever of the two the placement's own transform belongs on.
    """
    decoded, _own, bones, skeleton = entry
    nodes = _skeleton_nodes(skeleton) if skeleton else []
    paths = loading.slot_paths(row, meshes)
    slots = [materials.get(path) for path in paths]
    skinned = bool(nodes) and options.get("import_skeleton", True)
    if not skinned:
        key = (row["mesh"], tuple(paths))
        existing = shared.get(key)
        if existing is not None:
            obj = bpy.data.objects.new(row["name"], existing)
            context.collection.objects.link(obj)
            derived_state.announce(obj)
            return obj
    armature = None
    if skinned:
        armature, names = armature_builder.build_armature_from_nodes(
            context, nodes, row["name"] + "_Armature")
        bones = [names.get(index, bone) for index, bone in enumerate(bones)]
    mesh = mesh_builder.build_mesh_object(
        context, decoded, row["name"], armature,
        [{"fileID": bone} for bone in bones],
        {bone: bone for bone in bones},
        slots, options)
    if not skinned:
        shared[(row["mesh"], tuple(paths))] = mesh.data
    return armature if armature is not None else mesh


def _light_object(row):
    """A light component as a Blender light: its kind, colour, energy and shape as stated."""
    data = bpy.data.lights.new(row["name"], LIGHT_KINDS.get(row["light"], "POINT"))
    data.color = (row["lr"], row["lg"], row["lb"])
    data.energy = row["intensity"]
    if data.type in ("POINT", "SPOT", "AREA") and row["range"]:
        data.use_custom_distance = True
        data.cutoff_distance = row["range"]
    if data.type == "SPOT":
        data.spot_size = row["outer"] * _DEGREES
        data.spot_blend = max(0.0, 1.0 - (row["inner"] / row["outer"])) if row["outer"] else 0.0
    if data.type == "AREA":
        data.shape = "RECTANGLE"
        data.size = row["width"]
        data.size_y = row["height"]
    return bpy.data.objects.new(row["name"], data)
