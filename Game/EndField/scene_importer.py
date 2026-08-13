"""Build a whole Endfield map into the scene from discovered placements.

The generic importer knows how to build ONE prefab or ONE mesh out of a resolved
closure; what a placement is, how its asset path resolves to a named mesh
sub-object, and which of its LOD siblings to keep are all facts about this game
-- stated by the hook (``endfield.asset.rank``, ``scene_state``), read here as
columns -- so the pass that walks placements lives here rather than in that
importer.
"""

from __future__ import annotations

import bpy

from ... import coordinate, material_builder, prefab_importer
from ...RuriRipperPyBridge.unity import discovery
from . import datasets


def _scene_materials_for(material_index, mat_builder, material_asset_paths, named):
    """Real materials for a scene-placed mesh, resolved directly from its own
    material_asset_paths -- the entity's actual material hash(es), resolved
    through the same StringPathHash LUT as its mesh. [] when the entity
    carries no material, none resolved, or import_materials is off."""
    if mat_builder is None or not material_asset_paths:
        return []
    materials = []
    for path in material_asset_paths:
        guid = material_index.get(named.get(path, {}).get("mesh_name", ""))
        if guid is None:
            continue
        mat = mat_builder.build_from_ref({"guid": guid})
        if mat is not None:
            materials.append(mat)
    return materials


def _duplicate_hierarchy(context, anchor):
    """Deep-copies an anchor Empty and every descendant object beneath it
    (sharing mesh/material/armature DATA -- only object-level transform and
    parent differ), returning the new anchor. The prefab-placement
    equivalent of the mesh-instancing pattern import_scene_placements
    already uses for Streaming meshes -- a repeated DynamicScene prop
    (a satellite dish, a decoration) shares one import instead of a second
    full prefab rebuild."""
    def _walk(obj):
        new_obj = obj.copy()
        if obj.data is not None:
            new_obj.data = obj.data
        context.collection.objects.link(new_obj)
        for child in obj.children:
            new_child = _walk(child)
            new_child.parent = new_obj
        return new_obj
    return _walk(anchor)


def _place_prefab_report(context, report, placement):
    """Wraps every top-level object import_prefab_from_db produced (mesh
    objects with no parent, plus the armature if any -- static/environmental
    DynamicScene prefabs are not expected to have one, but this stays
    correct either way) under a new anchor Empty, then moves that anchor to
    the placement's resolved world transform. Only objects with no parent
    need the placement applied -- Blender already propagates parent-to-child
    transforms for anything nested under an armature or another mesh."""
    top_level = [obj for obj in report.root_objects if obj.parent is None]
    if not top_level:
        return None

    anchor = bpy.data.objects.new(f"{top_level[0].name}_placement", None)
    context.collection.objects.link(anchor)
    for obj in top_level:
        obj.parent = anchor

    unity_matrix = coordinate.unity_trs(
        {"x": placement["px"], "y": placement["py"], "z": placement["pz"]},
        {"x": placement["qx"], "y": placement["qy"], "z": placement["qz"], "w": placement["qw"]},
        {"x": placement["sx"], "y": placement["sy"], "z": placement["sz"]})
    # The placement anchor is the top-level object here: the prefab under it was
    # imported top_level=False (pure C), so R is applied once, at this world matrix.
    anchor.matrix_world = coordinate.convert_root_matrix(unity_matrix)
    return anchor


def import_scene_placements(context, db, placements, roots=(), options=None):
    """Import a batch of scene placements (see scene_state.py) into the
    current scene, against an already-resolved closure db covering every CAB
    those placements need. Covers BOTH scene-data families in one pass:

    - Streaming family (raw FBX mesh sub-assets, e.g.
      '...models/s_x.fbx##subname'): resolves the expected mesh sub-object
      by name (discovery.mesh_name_index), materials from the
      placement's own material_asset_paths (discovery.material_name_index
      -- FBPropertyAssetData AssetType==1, same hashLut as the mesh, no
      naming-convention guess), and builds via import_mesh_from_db.
    - DynamicScene family (Model/Effect/Tree, resolved to REAL .prefab
      paths -- the rank table's isPrefab): resolves the prefab by name against
      `roots` (discovery.prefab_name_index) and builds via
      import_prefab_from_db, which already brings real Renderer + Materials
      (no separate material-hash lookup needed for these).

    Either way, imports each DISTINCT asset exactly once; every further
    placement of the same asset becomes a linked-data duplicate (mesh path:
    .copy() sharing mesh data; prefab path: _duplicate_hierarchy sharing the
    whole object graph's data) instead of a second full import -- a real map
    can place the same prop hundreds of times, and re-decoding identical
    bytes that many times would be exactly the kind of eagerly-repeated cost
    the animation browser fix (see cabmap_state.py) already had to solve for
    a similar reason.
    Returns (imported_count, placed_count, unresolved_count)."""
    options = prefab_importer.resolve_options(options)
    name_index = discovery.mesh_name_index(db)
    prefab_index = discovery.prefab_name_index(db, roots)
    mat_builder = material_builder.MaterialBuilder(db, options) if options["import_materials"] else None
    material_index = discovery.material_name_index(db) if mat_builder is not None else {}
    obj_by_guid = {}
    anchor_by_guid = {}
    imported = 0
    placed = 0
    unresolved = 0

    # The game's naming conventions for every path this pass will touch, read once:
    # a map places the same prop hundreds of times, and asking per placement would
    # be a round trip per placement.
    wanted = {placement["asset_path"] for placement in placements}
    for placement in placements:
        wanted.update(placement.get("material_asset_paths") or ())
    named = datasets.ranked(sorted(wanted))

    for placement in placements:
        asset_path = placement["asset_path"]
        convention = named.get(asset_path, {})

        if convention.get("is_prefab"):
            guid = prefab_index.get(convention.get("stem", ""))
            if guid is None:
                unresolved += 1
                continue

            base_anchor = anchor_by_guid.get(guid)
            if base_anchor is None:
                prefab_file = db.load_guid(guid)
                if prefab_file is None:
                    unresolved += 1
                    continue
                report = prefab_importer.import_prefab_from_db(context, db, prefab_file, options,
                                                               top_level=False)
                anchor = _place_prefab_report(context, report, placement)
                if anchor is None:
                    unresolved += 1
                    continue
                anchor_by_guid[guid] = anchor
                imported += 1
                placed += 1
                continue

            target = _duplicate_hierarchy(context, base_anchor)
        else:
            expected_name = convention.get("mesh_name", "")
            guid = name_index.get(expected_name)
            if guid is None:
                unresolved += 1
                continue

            base_obj = obj_by_guid.get(guid)
            if base_obj is None:
                mesh_file = db.load_guid(guid)
                if mesh_file is None:
                    unresolved += 1
                    continue
                materials = _scene_materials_for(material_index, mat_builder,
                                                 placement.get("material_asset_paths") or [], named)
                report = prefab_importer.import_mesh_from_db(context, db, mesh_file, options, materials)
                if not report.mesh_objects:
                    unresolved += 1
                    continue
                base_obj = report.mesh_objects[0]
                obj_by_guid[guid] = base_obj
                imported += 1
                target = base_obj
            else:
                target = base_obj.copy()
                target.data = base_obj.data
                context.collection.objects.link(target)

        unity_matrix = coordinate.unity_trs(
            {"x": placement["px"], "y": placement["py"], "z": placement["pz"]},
            {"x": placement["qx"], "y": placement["qy"], "z": placement["qz"], "w": placement["qw"]},
            {"x": placement["sx"], "y": placement["sy"], "z": placement["sz"]})
        # A loose streaming mesh is placed as its own top-level object (its verts
        # came through pure C in build_mesh_object), so the root yaw folds in here.
        target.matrix_world = coordinate.convert_root_matrix(unity_matrix)
        placed += 1

    return imported, placed, unresolved
