"""Build a whole Endfield map window into the scene.

Every placement is a REAL, selectable, editable object. Picking one rock out of a
hillside and moving it is the point of importing a map at all, so this pass does
not produce GPU instances or a merged blob -- it produced them once, and that cost
0.05s of a 36s import while taking away the only interaction the result exists
for. What placements of the same asset DO share is the data-block: one mesh, one
material set, N cheap object headers pointing at them -- Blender's own linked
duplicate, which costs a copy of the object struct rather than a re-decode.

The expensive parts of the old pass are gone structurally, and none of them was
editability:

* the join is ``bridge.import_reachable``'s seed_asset_guids_by_path -- the C#
  side resolves every placement path ("a.fbx##sub" included) to its exported
  guid while it still holds the loaded closure, and exports ONLY what the seeds
  reach (no clips, no controllers, no co-hosted junk);
* each DISTINCT (asset, material set) decodes and builds ONE source object;
* placement transforms are built and converted in ONE batched numpy pass
  (math3d.unity_trs_batch -> BLENDER.convert_root_matrices), never per row;
* a DynamicScene prefab flattens ONCE into its renderer pieces (the SAME shared
  prefab_scan rules every importer uses -- LOD groups, shadow proxies, static
  batching, disabled state). A single-piece prop places as that one object; a
  multi-piece one gets an anchor Empty per placement so the whole prop still
  moves as a unit, with each piece's own local transform living on the piece and
  therefore copied for free. No armatures -- a massed environment placement is
  geometry at rest pose by definition; the character pipeline next door is where
  rigs live.

Everything is filed under a ``RuriScene`` collection with one sub-collection per
distinct asset, so the outliner stays navigable instead of being one flat wall of
names -- and so the imported assets are actually VISIBLE there, which an unlinked
sources collection was not.

What a placement is, and which columns state it, are this game's facts -- which
is why this pass lives here; the closure crossing it rides (import_reachable)
knows no game at all.
"""

from __future__ import annotations

import math
import time

import numpy as np

import bpy
from mathutils import Matrix

from ... import light_units, material_builder, mesh_builder, prefab_importer
from ...RuriRipperPyBridge.math3d import coordinate as math3d
from ...RuriRipperPyBridge.unity import (bridge_asset_db, hierarchy as unity_hierarchy,
                                         prefab as prefab_scan, skinning)
from . import datasets, scene_state

# Classes a massed environment window never renders: animation state (the whole
# reason the old path paid a per-prefab closure scan) and streamed media.
EXCLUDED_CLASSES = ("AnimationClip", "AnimatorController", "AnimatorOverrideController",
                    "Avatar", "AudioClip", "VideoClip")

ROOT_COLLECTION = "RuriScene"

# Cameras and lights aim down -Z in Blender but +Z forward in Unity -- the same
# once-per-object aim the prefab importer applies.
_AIM = Matrix.Rotation(math.pi / 2.0, 4, "X")

_BLENDER = math3d.BLENDER


class SceneReport:
    def __init__(self):
        self.sources = 0
        self.placed = 0
        self.hidden_placed = 0
        self.lights = 0
        self.cameras = 0
        # Two DIFFERENT things, deliberately not one counter: `unresolved` is a
        # placement whose asset this importer could not find -- a real gap, and
        # the only honest reading of it is data loss. `no_geometry` is a
        # placement whose asset the GAME ITSELF ships with zero vertices (every
        # instance measured is a collision proxy: .../models/collisions/*, or a
        # *_COL1_UM01 / *_UCX01 sub-mesh whose Unity Mesh was emptied because
        # physics uses cooked data instead). Nothing renders for those in game
        # either, so they are not loss -- but counting them as unresolved read
        # as losing 57 of 407 placements when 54 of those were never geometry.
        self.unresolved = 0
        self.unresolved_paths = []
        self.no_geometry = 0
        self.no_geometry_assets = []
        # A material path a placement names that the closure did not hand back.
        # The mesh still builds, with one slot fewer -- which is a silent visual
        # difference unless it is counted here.
        self.unresolved_materials = []
        self.materials = 0
        self.textures = 0
        self.warnings = []
        self.seconds = 0.0
        # Wall seconds per stage. Without this the only reading an import gives is
        # one total, and every attempt to make it faster is a guess -- the C# side
        # has printed its own phase split all along.
        self.phases = {}

    def summary(self):
        text = ("{0} distinct asset(s), {1} object(s)".format(self.sources, self.placed)
                + (", {0} hidden".format(self.hidden_placed) if self.hidden_placed else "")
                + (", {0} light(s)".format(self.lights) if self.lights else "")
                + (", {0} camera(s)".format(self.cameras) if self.cameras else "")
                + (", {0} collision-only placement(s) the game ships with no geometry".format(
                    self.no_geometry) if self.no_geometry else "")
                + (", !! {0} UNRESOLVED".format(self.unresolved) if self.unresolved else "")
                + ", {0:.1f}s".format(self.seconds))
        return text

    def phase_summary(self):
        return " ".join("{0}={1:.2f}s".format(name, seconds)
                        for name, seconds in self.phases.items())


def _placement_matrices(table):
    """(n, 4, 4) Unity-space world matrices for every placement row, one batch."""
    positions = np.stack([np.asarray(table.values(c), dtype=np.float64)
                          for c in ("px", "py", "pz")], axis=1)
    quaternions = np.stack([np.asarray(table.values(c), dtype=np.float64)
                            for c in ("qx", "qy", "qz", "qw")], axis=1)
    scales = np.stack([np.asarray(table.values(c), dtype=np.float64)
                       for c in ("sx", "sy", "sz")], axis=1)
    return math3d.unity_trs_batch(positions, quaternions, scales)


def _timed_material(mat_builder, ref, report):
    """Materials (and the textures they pull) are built from inside the source
    loop, so their cost hides inside it -- charge it to its own phase."""
    if mat_builder is None:
        return None
    started = time.time()
    material = mat_builder.build_from_ref(ref)
    report.phases["materials"] = report.phases.get("materials", 0.0) + (time.time() - started)
    return material


def _asset_collection(root, name):
    """One collection per distinct asset, under the scene's RuriScene root. The
    root IS linked into the scene: an unlinked collection keeps its contents out
    of the outliner entirely, which reads as "the import produced nothing"."""
    collection = bpy.data.collections.new(name)
    root.children.link(collection)
    return collection


def _adopt(obj, collection):
    """Move an object the mesh builder linked into the working collection over to
    its asset's own collection -- one membership, never two."""
    for holder in list(obj.users_collection):
        holder.objects.unlink(obj)
    collection.objects.link(obj)


def _place_copies(collection, source, matrices):
    """Place ONE source at N world matrices. The first placement IS the source
    object (already built, already linked); every further one is
    ``source.copy()`` -- a linked duplicate sharing the mesh, its material slots
    and every attribute, so N placements cost N object headers and one decode."""
    link = collection.objects.link
    for index, matrix in enumerate(matrices.tolist()):
        obj = source
        if index:
            obj = source.copy()
            link(obj)
        obj.matrix_world = Matrix(matrix)


def _place_anchored(collection, parts, matrices, name):
    """Place a multi-piece prefab at N world matrices: one anchor Empty per
    placement carrying the placement transform, with a copy of each piece
    parented under it -- so the whole prop selects and moves as a unit while each
    piece stays individually reachable.

    Each piece already holds its OWN in-prefab local transform as its basis (set
    once, before this runs), so a copy needs no matrix work at all: Blender
    composes anchor world x piece basis, which is exactly R.C.P.C . C.L.C =
    R.C.P.L.C -- the same product the single-piece path folds up front."""
    link = collection.objects.link
    for index, matrix in enumerate(matrices.tolist()):
        anchor = bpy.data.objects.new(name, None)
        link(anchor)
        anchor.matrix_world = Matrix(matrix)
        for part in parts:
            obj = part
            if index:
                obj = part.copy()
                link(obj)
            obj.parent = anchor


def _build_mesh_source(context, db, guid, name, materials, options, report):
    """(object, problem). ``problem`` is "empty" when the asset resolved but the
    game ships it with no vertices (a collision proxy -- see SceneReport), any
    other truthy value when the asset itself could not be read."""
    loaded = prefab_scan.load_mesh(db, {"guid": guid}, name)
    if not loaded.ok:
        if loaded.problem == "empty":
            return None, "empty"
        report.warnings.append("{0}: {1}".format(name, loaded.detail or "no geometry"))
        return None, loaded.problem or "unreadable"
    obj = mesh_builder.build_mesh_object(context, loaded.decoded, loaded.name or name,
                                         None, [], {}, materials, options)
    return obj, None


def _flatten_prefab(context, db, prefab_file, mat_builder, options, report):
    """One DynamicScene prefab as flat pieces: (source object, Unity world 4x4
    within the prefab, hidden) per surviving renderer, plus its cameras/lights
    as (data-block factory results, node) for per-placement instantiation.
    The renderer rules are the shared prefab_scan ones -- stated once for every
    importer. A piece that drew nothing in Unity is hidden on the SOURCE, so
    every linked duplicate of it inherits that for free."""
    nodes, _roots = unity_hierarchy.build_hierarchy(prefab_file)
    go_to_node = {node.go_id: node for node in nodes.values()}
    world = unity_hierarchy.world_matrices(nodes)

    pieces = []
    stats = prefab_scan.SkipStats()
    for renderer in prefab_scan.iter_renderers(prefab_file, go_to_node, options, stats):
        loaded = prefab_scan.load_mesh(db, renderer.mesh_ref, renderer.name)
        if not loaded.ok:
            if loaded.problem != "no_ref":
                report.warnings.append("{0}: {1}".format(renderer.name, loaded.detail))
            continue
        decoded = loaded.decoded
        materials = [_timed_material(mat_builder, ref, report)
                     for ref in renderer.material_refs] if mat_builder is not None else []
        baked = renderer.is_skinned and skinning.bake_bind_pose(decoded, renderer.bones, world)
        obj = mesh_builder.build_mesh_object(context, decoded, renderer.name,
                                             None, [], {}, materials, options)
        local = np.eye(4, dtype=np.float64) if baked or renderer.node is None \
            else renderer.node.world
        if renderer.disabled:
            obj.hide_viewport = obj.hide_render = True
        pieces.append((obj, local, bool(renderer.disabled)))

    cameras = [(camera, camera.node.world) for camera in
               prefab_scan.iter_cameras(prefab_file, go_to_node, options)]
    lights = [(light, light.node.world) for light in
              prefab_scan.iter_lights(prefab_file, go_to_node, options)]
    return pieces, cameras, lights


def _light_data(light, options):
    kind = light_units.blender_type(light.type)
    data = bpy.data.lights.new(light.name, kind)
    data.color = light.color
    data.energy = light_units.energy_for(light.type, light.intensity, light.area_size,
                                         options["pre_exposure"])
    if kind == "SPOT":
        data.spot_size = math.radians(light.spot_angle)
        if light.spot_angle > 0.0:
            data.spot_blend = 1.0 - min(light.inner_spot_angle / light.spot_angle, 1.0)
    elif kind == "AREA":
        data.shape = "RECTANGLE"
        data.size, data.size_y = light.area_size
    if kind != "SUN":
        data.use_custom_distance = True
        data.cutoff_distance = light.range
    return data


def _camera_data(camera):
    data = bpy.data.cameras.new(camera.name)
    data.clip_start = camera.near
    data.clip_end = camera.far
    if camera.orthographic:
        data.type = "ORTHO"
        data.ortho_scale = camera.orthographic_size * 2.0
    else:
        data.sensor_fit = "VERTICAL"
        data.angle_y = math.radians(camera.fov)
    return data


def _place_aimed(collection, data, name, world_blender, disabled):
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    obj.matrix_world = Matrix([list(row) for row in world_blender]) @ _AIM
    if disabled:
        obj.hide_viewport = obj.hide_render = True
    return obj


def import_scene_window(context, bridge, options=None):
    """Import the CURRENT discovery (scene_state.TABLE et al.).

    One closure crossing, one build per distinct asset, a linked duplicate per
    further placement -- see the module docstring for why each stage has the
    shape it has."""
    start = time.time()
    report = SceneReport()

    table = scene_state.TABLE
    if table is None or len(table) == 0:
        raise RuntimeError("Nothing discovered -- run Read on a selection first.")
    options = prefab_importer.resolve_options(options)

    crossing = time.time()
    bridge_assets, _roots, _seed_roots, _clips, _scenes = bridge.import_reachable(
        scene_state.SEED_PATHS, EXCLUDED_CLASSES)
    report.phases["bridge"] = time.time() - crossing
    guids = bridge.seed_asset_guids_by_path
    db = bridge_asset_db.BridgeAssetDatabase(
        bridge_assets, clip_curve_blobs=bridge.clip_curves_by_guid,
        mesh_blobs=bridge.mesh_blobs_by_guid, asset_paths=bridge.asset_paths_by_guid)
    mat_builder = material_builder.MaterialBuilder(db, options) \
        if options["import_materials"] else None

    columns = time.time()
    paths = table.values("assetPath")
    materials_by_row = scene_state.MATERIALS_BY_ROW
    named = datasets.ranked(sorted({*paths, *(path for mats in materials_by_row.values()
                                              for path in mats)}))
    unity = _placement_matrices(table)
    report.phases["columns"] = time.time() - columns
    sources_started = time.time()

    # Distinct keying: a loose mesh is (its path, ITS placement material set) --
    # the same mesh placed with different materials is a different drawable and
    # must not inherit the first placement's look. A prefab keys on its path
    # alone; its materials are its renderers' own.
    root = bpy.data.collections.new(ROOT_COLLECTION)
    context.scene.collection.children.link(root)

    sources = []
    key_to_entry = {}         # key -> ("mesh", src, coll) | ("prefab", [(src, local, hidden)], cams, lights, coll)
    row_entries = []
    for row in range(len(table)):
        path = paths[row]
        convention = named.get(path, {})
        is_prefab = bool(convention.get("is_prefab"))
        key = path if is_prefab else (path, materials_by_row.get(row, ()))
        entry = key_to_entry.get(key)
        if entry is None:
            guid = guids.get(path)
            if guid is None:
                key_to_entry[key] = entry = ("missing", path)
            elif is_prefab:
                prefab_file = db.load_guid(guid)
                if prefab_file is None:
                    key_to_entry[key] = entry = ("missing", path)
                else:
                    pieces, cameras, lights = _flatten_prefab(
                        context, db, prefab_file, mat_builder, options, report)
                    collection = _asset_collection(
                        root, convention.get("stem") or path.rsplit("/", 1)[-1])
                    indexed = []
                    for obj, local, hidden in pieces:
                        _adopt(obj, collection)
                        sources.append(obj)
                        indexed.append((len(sources) - 1, local, hidden))
                    key_to_entry[key] = entry = ("prefab", indexed, cameras, lights,
                                                 collection)
            else:
                materials = []
                if mat_builder is not None:
                    for material_path in materials_by_row.get(row, ()):
                        material_guid = guids.get(material_path)
                        if material_guid is None:
                            if material_path not in report.unresolved_materials:
                                report.unresolved_materials.append(material_path)
                            continue
                        material = _timed_material(mat_builder, {"guid": material_guid}, report)
                        if material is not None:
                            materials.append(material)
                display = convention.get("mesh_name") or path.rsplit("/", 1)[-1]
                obj, problem = _build_mesh_source(context, db, guid, display, materials,
                                                  options, report)
                if problem == "empty":
                    key_to_entry[key] = entry = ("empty", path)
                elif obj is None:
                    key_to_entry[key] = entry = ("missing", path)
                else:
                    collection = _asset_collection(root, display)
                    _adopt(obj, collection)
                    sources.append(obj)
                    key_to_entry[key] = entry = ("mesh", len(sources) - 1, collection)
        if entry[0] == "missing":
            report.unresolved += 1
            if entry[1] not in report.unresolved_paths:
                report.unresolved_paths.append(entry[1])
            continue
        if entry[0] == "empty":
            report.no_geometry += 1
            if entry[1] not in report.no_geometry_assets:
                report.no_geometry_assets.append(entry[1])
            continue
        row_entries.append((row, entry))

    report.sources = len(sources)
    # Conservation: every discovered placement is BUILT, or has no geometry to
    # build, or is lost -- there is no fourth outcome, and a future edit that
    # quietly drops rows fails here instead of shipping a thinner scene.
    accounted = len(row_entries) + report.no_geometry + report.unresolved
    if accounted != len(table):
        raise AssertionError(
            "placement accounting lost {0} row(s): {1} built + {2} no-geometry + "
            "{3} unresolved != {4} discovered".format(
                len(table) - accounted, len(row_entries), report.no_geometry,
                report.unresolved, len(table)))
    report.phases["sources"] = time.time() - sources_started
    placing = time.time()

    # Placement. Rows are grouped by the asset they place so each group's
    # matrices convert in ONE batched pass; within a group every row is a linked
    # duplicate of that asset's already-built source.
    mesh_groups = {}      # source index -> (entry, [table rows])
    prefab_groups = {}    # id(entry) -> (entry, [table rows])
    for row, entry in row_entries:
        if entry[0] == "mesh":
            mesh_groups.setdefault(entry[1], (entry, []))[1].append(row)
        else:
            prefab_groups.setdefault(id(entry), (entry, []))[1].append(row)

    for entry, rows in mesh_groups.values():
        _place_copies(entry[2], sources[entry[1]],
                      _BLENDER.convert_root_matrices(unity[rows]))
        report.placed += len(rows)

    for entry, rows in prefab_groups.values():
        _kind, indexed, cameras, lights, collection = entry
        placement_unity = unity[rows]                       # (r, 4, 4)
        if len(indexed) == 1:
            # A single-piece prop needs no grouping node: fold its own local
            # transform into the placement and place the piece itself.
            source_index, local, _hidden = indexed[0]
            _place_copies(collection, sources[source_index],
                          _BLENDER.convert_root_matrices(
                              placement_unity @ np.asarray(local, dtype=np.float64)[None]))
        else:
            for source_index, local, _hidden in indexed:
                sources[source_index].matrix_basis = Matrix(
                    _BLENDER.convert_matrix(local).tolist())
            _place_anchored(collection, [sources[i] for i, _l, _h in indexed],
                            _BLENDER.convert_root_matrices(placement_unity),
                            collection.name)
        hidden_pieces = sum(1 for _i, _l, hidden in indexed if hidden)
        report.placed += len(rows) * (len(indexed) - hidden_pieces)
        report.hidden_placed += len(rows) * hidden_pieces

        for camera, world in cameras:
            data = _camera_data(camera)
            for blender_world in _BLENDER.convert_root_matrices(
                    placement_unity @ np.asarray(world, dtype=np.float64)[None]):
                _place_aimed(collection, data, camera.name, blender_world, camera.disabled)
                report.cameras += 1
        for light, world in lights:
            data = _light_data(light, options)
            for blender_world in _BLENDER.convert_root_matrices(
                    placement_unity @ np.asarray(world, dtype=np.float64)[None]):
                _place_aimed(collection, data, light.name, blender_world, light.disabled)
                report.lights += 1

    report.phases["placing"] = time.time() - placing
    if mat_builder is not None:
        report.materials = len(mat_builder._content_cache)
        report.textures = len(mat_builder._image_cache)
    report.seconds = time.time() - start
    return report
