"""A whole scene window into one Painter project.

The Blender materialiser next door turns the same ``Packages`` of kind
SCENE_WINDOW into one real object per placement, sharing a mesh datablock between
placements of the same asset. This writes the identical statement as one glTF
file, and shares geometry the same way: a glTF node REFERENCES a mesh, so a window
that stamps one rock into four hundred rows writes those vertices once and points
at them four hundred times.

Neither is a lesser version of the other, and the difference is not in what was
read: the closure crossing, which renderers a prefab actually draws (LOD levels,
shadow proxies, disabled objects, static-batch windows) and how a skinned mesh is
baked are the SHARED rules both hosts run. What differs is the last step, which is
exactly the step a host is for.

What does not cross is a light or a camera: there is nothing in a Painter project
for either to be, and a placement that is only one of those simply writes no node.
"""

from __future__ import annotations

import numpy as np

from . import gltf_writer, importer, model_builder, settings
from ...Kernel.app import loading
from ...RuriRipperPyBridge.math3d import coordinate as _coordinate
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import (bridge_asset_db, builtin_meshes,
                                         hierarchy as unity_hierarchy,
                                         prefab as prefab_scan, skinning)

coordinate = _coordinate.GLTF

#: 引擎内置 primitive 住在 `unity default resources` 里,AssetRipper 按设计不导出它们
#: (真 Unity 工程本来就有),所以它们永远 join 不到导出 guid。按引擎自己的规格重建 ——
#: 与 Blender 那半读的是同一份规格,不是第二份。
_BUILTIN_MARKER = "renderpipelineresources/mesh/"


class _Report:
    """What one window import produced, in the terms both hosts report."""

    def __init__(self):
        self.sources = 0
        self.placed = 0
        #: A piece whose GameObject was off in Unity. Counted apart from ``placed``
        #: for the same reason the other host hides it rather than dropping it: it
        #: is a surface you may well want to paint, but it is not what the game
        #: draws, and one number for both would make the two hosts' reports say
        #: different things.
        self.hidden_placed = 0
        self.unresolved = 0
        self.unresolved_paths = []
        self.no_geometry = 0
        self.no_geometry_assets = []
        self.unresolved_materials = []
        self.builtin = 0
        self.warnings = []

    def summary(self):
        return ("{0} distinct asset(s), {1} node(s)".format(self.sources, self.placed)
                + (", {0} hidden".format(self.hidden_placed) if self.hidden_placed else "")
                + (", {0} collision-only placement(s) the game ships with no geometry".format(
                    self.no_geometry) if self.no_geometry else "")
                + (", {0} engine built-in primitive(s) rebuilt to spec".format(
                    self.builtin) if self.builtin else "")
                + (", !! {0} UNRESOLVED".format(self.unresolved) if self.unresolved else ""))


def materialise(context, packages, options=None, lines=None):
    """Write every placement of one window into one project."""
    lines = lines if lines is not None else []
    window = packages.window
    table = window["table"]
    if table is None or len(table) == 0:
        return loading.Built(warnings=["Nothing discovered -- run Read on a selection first."])

    bridge = cabmap_state.BRIDGE
    assets, _roots, _seed_roots, _clips, _scenes = bridge.import_reachable(
        window["seeds"], loading.WINDOW_EXCLUDED_CLASSES)
    database = bridge_asset_db.BridgeAssetDatabase(
        assets, clip_curve_blobs=bridge.clip_curves_by_guid,
        mesh_blobs=bridge.mesh_blobs_by_guid, asset_paths=bridge.asset_paths_by_guid,
        texture_srgb=bridge.texture_srgb_by_guid)

    job = importer.ImportJob(packages.label, database, [], settings.import_options())
    if options:
        job.options = dict(job.options, **{key: value for key, value in options.items()
                                           if key in job.options})
    report = _Report()
    job.build = _write(window, database, bridge.seed_asset_guids_by_path, job,
                       job.options, report)
    lines.append("scene window: " + report.summary())
    lines.extend(report.warnings[:8])
    if report.unresolved:
        lines.append("!! {0} placement(s) of {1} asset(s) did NOT resolve and are "
                     "MISSING: {2}".format(report.unresolved, len(report.unresolved_paths),
                                           ", ".join(report.unresolved_paths[:5])))
    if job.build.glb_path is None:
        return loading.Built(warnings=[line[3:] if line.startswith("!! ") else line
                                       for line in lines])

    importer.finish_textures(job)
    importer.launch(job, reuse_open_project=False)
    return loading.Built(warnings=[line[3:] for line in lines if line.startswith("!! ")],
                         imported=report.placed)


def _write(window, database, guids, job, options, report):
    """One .glb out of the whole window, one glTF mesh per distinct drawable."""
    options = model_builder.resolve_options(options)
    result = model_builder.BuildResult(window["label"])
    builder = gltf_writer.GlbBuilder()
    table = window["table"]
    named = window["named"]
    materials_by_row = window["materials_by_row"]
    paths = table.values("assetPath")
    # One batched conversion for every placement, never one per row: a real
    # window's row count runs into the hundreds of thousands, which is the whole
    # reason the table reached here columnar.
    unity = _placement_matrices(table)
    stats = prefab_scan.SkipStats()

    # Distinct keying, the same rule the other host uses: a loose mesh is (its
    # path, ITS placement's material set) -- the same mesh placed with different
    # materials is a different drawable -- while a prefab keys on its path alone,
    # its materials being its renderers' own.
    pieces_of = {}
    for row in range(len(table)):
        path = paths[row]
        is_prefab = bool(named.get(path, {}).get("is_prefab"))
        key = path if is_prefab else (path, materials_by_row.get(row, ()))
        pieces = pieces_of.get(key)
        if pieces is None:
            pieces = pieces_of[key] = _pieces_for(
                builder, result, report, database, guids, options, stats,
                path, is_prefab, named.get(path, {}), materials_by_row.get(row, ()))
        if pieces is None or not pieces:
            continue
        placement = unity[row]
        for mesh_index, local, disabled in pieces:
            matrix = placement if local is None else placement @ local
            builder.node(path.rsplit("/", 1)[-1], mesh_index,
                         coordinate.convert_matrix(matrix), None)
            if disabled:
                report.hidden_placed += 1
            else:
                report.placed += 1

    result.skipped_lod += stats.lod
    result.skipped_shadow += stats.shadow
    result.skipped_inactive += stats.inactive
    result.inactive_included += stats.inactive_included
    result.finish()
    if builder.is_empty():
        result.warnings.append(
            "'{0}' places {1} row(s) but none of them draws geometry this host can "
            "write.".format(window["label"], len(table)))
        return result
    model_builder._write(builder, job.glb_path(), result)
    return result


def _pieces_for(builder, result, report, database, guids, options, stats,
                path, is_prefab, convention, material_paths):
    """[(glTF mesh index, its own in-prefab matrix or None, was it off in Unity)]
    for one distinct asset, written into the file exactly once. [] when it resolves
    to nothing."""
    guid = guids.get(path)
    builtin = _builtin_name(path) if guid is None else None
    if builtin is not None:
        pieces = _builtin_pieces(builder, result, report, database, guids, options,
                                 builtin, path, material_paths)
    elif guid is None:
        report.unresolved += 1
        if path not in report.unresolved_paths:
            report.unresolved_paths.append(path)
        return []
    elif is_prefab:
        prefab = database.load_guid(guid)
        if prefab is None:
            report.unresolved += 1
            if path not in report.unresolved_paths:
                report.unresolved_paths.append(path)
            return []
        pieces = _prefab_pieces(builder, result, database, options, stats, prefab)
    else:
        pieces = _mesh_pieces(builder, result, report, database, guids, options,
                              guid, convention, path, material_paths)
    if pieces:
        report.sources += 1
    return pieces


def _prefab_pieces(builder, result, database, options, stats, prefab):
    """A prefab flattened to its drawing renderers -- the SHARED rules, so this
    host and the other cannot disagree about what a prefab contains."""
    nodes, _roots = unity_hierarchy.build_hierarchy(prefab)
    go_to_node = {node.go_id: node for node in nodes.values()}
    world = unity_hierarchy.world_matrices(nodes)

    pieces = []
    for renderer in prefab_scan.iter_renderers(prefab, go_to_node, options, stats):
        decoded = model_builder._decode_mesh_ref(database, renderer.mesh_ref, result,
                                                 renderer.name)
        if decoded is None:
            continue
        entries = [model_builder._register_material(result, database, ref, renderer.name)
                   for ref in renderer.material_refs]
        baked = renderer.is_skinned and skinning.bake_bind_pose(decoded, renderer.bones, world)
        mesh_index = _mesh(builder, result, renderer.name, decoded, entries, options,
                           renderer.submesh_range)
        if mesh_index is None:
            continue
        # A baked skinned mesh is already in the prefab's own world space, so its
        # node must not move it again.
        local = None if (baked or renderer.node is None) else np.asarray(
            renderer.node.world, dtype=np.float64)
        pieces.append((mesh_index, local, bool(renderer.disabled)))
    return pieces


def _mesh_pieces(builder, result, report, database, guids, options, guid, convention,
                 path, material_paths):
    """A loose mesh asset placed with the materials its ROW names."""
    loaded = prefab_scan.load_mesh(database, {"guid": guid}, path)
    if not loaded.ok:
        if loaded.problem == "empty":
            report.no_geometry += 1
            if path not in report.no_geometry_assets:
                report.no_geometry_assets.append(path)
        else:
            report.unresolved += 1
            if path not in report.unresolved_paths:
                report.unresolved_paths.append(path)
        return []
    name = convention.get("mesh_name") or path.rsplit("/", 1)[-1]
    entries = _row_materials(result, report, database, guids, material_paths, name)
    mesh_index = _mesh(builder, result, name, loaded.decoded, entries, options, None)
    return [] if mesh_index is None else [(mesh_index, None, False)]


def _builtin_pieces(builder, result, report, database, guids, options, builtin,
                    path, material_paths):
    """An engine primitive rebuilt to the engine's own spec -- not data loss and
    not an ordinary import, so it is counted on its own."""
    decoded = builtin_meshes.build(builtin)
    if decoded is None:
        return []
    name = path.split("##")[-1] or builtin
    entries = _row_materials(result, report, database, guids, material_paths, name)
    mesh_index = _mesh(builder, result, name, decoded, entries, options, None)
    if mesh_index is None:
        return []
    report.builtin += 1
    return [(mesh_index, None, False)]


def _row_materials(result, report, database, guids, material_paths, owner):
    """The Texture Set entries a ROW's own material paths resolve to. A path the
    closure did not hand back leaves that slot empty, which is a visible
    difference and therefore counted."""
    entries = []
    for material_path in material_paths or ():
        guid = guids.get(material_path) if material_path else None
        if guid is None:
            if material_path and material_path not in report.unresolved_materials:
                report.unresolved_materials.append(material_path)
            # Positional: _mesh reads entry i as sub-mesh i's Texture Set and
            # falls back on None, so an unresolved one empties ITS slot rather
            # than pulling every later sub-mesh onto the wrong material.
            entries.append(None)
            continue
        entries.append(model_builder._register_material(result, database,
                                                        {"guid": guid}, owner))
    return entries


def _mesh(builder, result, name, decoded, entries, options, submesh_range):
    """The mesh's primitives, one per submesh, each naming its Texture Set."""
    indices = []
    for entry in entries:
        indices.append(model_builder._fallback_material(builder, result, name)
                       if entry is None else builder.material(entry.guid, entry.name))
    wanted = submesh_range[1] if submesh_range else max(len(decoded.submeshes), 1)
    while len(indices) < max(wanted, 1):
        indices.append(model_builder._fallback_material(builder, result, name))
    primitives = model_builder._primitives_from_decoded(decoded, indices, options,
                                                        submesh_range)
    if not primitives:
        return None
    result.mesh_count += 1
    result.vertex_count += int(sum(len(one["positions"]) for one in primitives))
    result.triangle_count += int(sum(len(one["indices"]) for one in primitives))
    return builder.mesh(name, primitives)


def _builtin_name(path):
    """这个 seed 是引擎内置 primitive 吗 -> 它的名字,否则 None。"""
    lowered = path.lower()
    if _BUILTIN_MARKER not in lowered:
        return None
    stem = lowered.split("##")[0].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return stem.title() if stem == "quad" else None


def _placement_matrices(table):
    """(n, 4, 4) Unity-space world matrices for every placement row, one batch."""
    positions = np.stack([np.asarray(table.values(c), dtype=np.float64)
                          for c in ("px", "py", "pz")], axis=1)
    quaternions = np.stack([np.asarray(table.values(c), dtype=np.float64)
                            for c in ("qx", "qy", "qz", "qw")], axis=1)
    scales = np.stack([np.asarray(table.values(c), dtype=np.float64)
                       for c in ("sx", "sy", "sz")], axis=1)
    return _coordinate.unity_trs_batch(positions, quaternions, scales)
