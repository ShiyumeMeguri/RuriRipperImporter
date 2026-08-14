"""Build a whole Endfield map window into the scene as INSTANCES.

The shape of the problem: a window places 10^5 entities drawn from a few hundred
distinct assets. The old pass imported per placement -- one Blender object each,
a full-closure name-index scan to join placements to assets, a full-closure
export behind it, and a per-prefab animation discovery sweep. Every one of those
costs is gone structurally, not tuned:

* the join is ``bridge.import_reachable``'s seed_asset_guids_by_path -- the C#
  side resolves every placement path ("a.fbx##sub" included) to its exported
  guid while it still holds the loaded closure, and exports ONLY what the seeds
  reach (no clips, no controllers, no co-hosted junk);
* each DISTINCT (asset, material set) builds ONE source object; every placement
  is one row of the scene_instancer point cloud -- two foreach_set writes total;
* placement transforms are built and converted in ONE batched numpy pass
  (math3d.unity_trs_batch -> BLENDER.convert_root_matrices), never per row;
* a DynamicScene prefab flattens ONCE into its renderer pieces (the SAME shared
  prefab_scan rules every importer uses -- LOD groups, shadow proxies, static
  batching, disabled state), and a placement of it becomes one instance row per
  piece: placement matrix x piece world matrix, again batched. Its cameras and
  lights (rare) become real objects per placement. No armatures, no empties --
  a massed environment placement is geometry at rest pose by definition; the
  character pipeline next door is where rigs live.

What a placement is, and which columns state it, are this game's facts -- which
is why this pass lives here; the instancing kernel it feeds (scene_instancer)
and the closure crossing it rides (import_reachable) know no game at all.
"""

from __future__ import annotations

import math
import time

import numpy as np

import bpy
from mathutils import Matrix

from ... import material_builder, mesh_builder, prefab_importer, scene_instancer
from ...RuriRipperPyBridge.math3d import coordinate as math3d
from ...RuriRipperPyBridge.unity import (bridge_asset_db, hierarchy as unity_hierarchy,
                                         prefab as prefab_scan, skinning)
from . import datasets, scene_state

# Classes a massed environment window never renders: animation state (the whole
# reason the old path paid a per-prefab closure scan) and streamed media.
EXCLUDED_CLASSES = ("AnimationClip", "AnimatorController", "AnimatorOverrideController",
                    "Avatar", "AudioClip", "VideoClip")

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
        text = ("{0} distinct source(s), {1} instance(s)".format(self.sources, self.placed)
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
    importer, applied here to a prefab that will only ever be instanced."""
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
        pieces.append((obj, local, bool(renderer.disabled)))

    cameras = [(camera, camera.node.world) for camera in
               prefab_scan.iter_cameras(prefab_file, go_to_node, options)]
    lights = [(light, light.node.world) for light in
              prefab_scan.iter_lights(prefab_file, go_to_node, options)]
    return pieces, cameras, lights


def _light_data(light):
    kind = {0: "SPOT", 1: "SUN", 2: "POINT"}.get(light.type, "AREA")
    data = bpy.data.lights.new(light.name, kind)
    data.color = light.color
    data.energy = light.intensity
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


def _place_aimed(context, data, name, world_blender, disabled):
    obj = bpy.data.objects.new(name, data)
    context.collection.objects.link(obj)
    obj.matrix_world = Matrix([list(row) for row in world_blender]) @ _AIM
    if disabled:
        obj.hide_viewport = obj.hide_render = True
    return obj


def import_scene_window(context, bridge, options=None):
    """Import the CURRENT discovery (scene_state.TABLE et al.) as instances.

    One closure crossing, K source builds, two attribute writes -- see the
    module docstring for why each stage has the shape it has."""
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
    sources = []
    pieces_by_source = {}     # prefab source index -> extra pieces [(idx, local)]
    key_to_entry = {}         # key -> ("mesh", src) | ("prefab", [(src, local, hidden)], cams, lights)
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
                    indexed = []
                    for obj, local, hidden in pieces:
                        sources.append(obj)
                        indexed.append((len(sources) - 1, local, hidden))
                    key_to_entry[key] = entry = ("prefab", indexed, cameras, lights)
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
                    sources.append(obj)
                    key_to_entry[key] = entry = ("mesh", len(sources) - 1)
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
    instancing = time.time()

    # Instance rows. Mesh rows bind the placement matrix directly; prefab rows
    # fan out to one row per piece with the piece's own world folded in -- all
    # of it batched per group, nothing per placement.
    visible_indices = []
    visible_matrices = []
    hidden_indices = []
    hidden_matrices = []

    mesh_rows = {}        # source index -> [table rows]
    prefab_rows = {}      # id(entry) -> (entry, [table rows])
    for row, entry in row_entries:
        if entry[0] == "mesh":
            mesh_rows.setdefault(entry[1], []).append(row)
        else:
            prefab_rows.setdefault(id(entry), (entry, []))[1].append(row)

    for source_index, rows in mesh_rows.items():
        matrices = _BLENDER.convert_root_matrices(unity[rows])
        visible_indices.append(np.full(len(rows), source_index, dtype=np.int32))
        visible_matrices.append(matrices)
        report.placed += len(rows)

    for entry, rows in prefab_rows.values():
        _kind, indexed, cameras, lights = entry
        placement_unity = unity[rows]                       # (r, 4, 4)
        for source_index, local, hidden in indexed:
            combined = placement_unity @ np.asarray(local, dtype=np.float64)[None]
            matrices = _BLENDER.convert_root_matrices(combined)
            target_i = hidden_indices if hidden else visible_indices
            target_m = hidden_matrices if hidden else visible_matrices
            target_i.append(np.full(len(rows), source_index, dtype=np.int32))
            target_m.append(matrices)
            if hidden:
                report.hidden_placed += len(rows)
            else:
                report.placed += len(rows)
        for camera, world in cameras:
            data = _camera_data(camera)
            for blender_world in _BLENDER.convert_root_matrices(
                    placement_unity @ np.asarray(world, dtype=np.float64)[None]):
                _place_aimed(context, data, camera.name, blender_world, camera.disabled)
                report.cameras += 1
        for light, world in lights:
            data = _light_data(light)
            for blender_world in _BLENDER.convert_root_matrices(
                    placement_unity @ np.asarray(world, dtype=np.float64)[None]):
                _place_aimed(context, data, light.name, blender_world, light.disabled)
                report.lights += 1

    sources_collection = scene_instancer.build_sources_collection(
        "RuriScene Sources", sources)
    if visible_indices:
        scene_instancer.build_instancer(
            context, "RuriScene", sources_collection,
            np.concatenate(visible_indices), np.concatenate(visible_matrices))
    if hidden_indices:
        scene_instancer.build_instancer(
            context, "RuriScene Hidden", sources_collection,
            np.concatenate(hidden_indices), np.concatenate(hidden_matrices), hidden=True)

    report.phases["instancing"] = time.time() - instancing
    if mat_builder is not None:
        report.materials = len(mat_builder._content_cache)
        report.textures = len(mat_builder._image_cache)
    report.seconds = time.time() - start
    return report
