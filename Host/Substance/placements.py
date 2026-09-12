"""A decoded placement tree into a Painter project.

The Blender materialiser next door turns the same
:class:`Kernel.app.loading.Packages` of kind PLACEMENTS into objects parented to
each other, sharing one mesh datablock between rows that draw the same thing.
This writes the identical statement as one glTF file -- and shares geometry the
same way, because a glTF node REFERENCES a mesh: a package that stamps one mesh
into hundreds of rows writes those vertices once and points at them from every
row, which is what the engine does with them too.

What does not cross is the rig: Painter has no skeleton, so a skinned mesh is
written at its bind pose, which is the pose to texture on anyway. A light is not
written at all -- there is nothing in a Painter project for it to be.
"""

from __future__ import annotations

from ...Kernel.app import loading
from ...RuriRipperPyBridge.math3d import coordinate as _coordinate
from . import importer, model_builder, settings

#: Unity -> glTF. The Unreal decoder states its transforms in the same convention
#: every other reader here does, so this is the same conversion, not a second one.
coordinate = _coordinate.GLTF


def materialise(context, packages, options=None, lines=None):
    """Write every placement into one project."""
    lines = lines if lines is not None else []
    rows = list(packages.placements)
    if not rows:
        return loading.Built(warnings=["'{0}' places nothing.".format(packages.label)])

    job = importer.ImportJob(packages.label, packages.textures, [], settings.import_options())
    if options:
        job.options = dict(job.options, **{key: value for key, value in options.items()
                                           if key in job.options})
    job.build = _write(packages, job, job.options)
    lines.append("placements: {0} row(s), {1}".format(len(rows), job.build.summary()))
    for warning in job.build.warnings:
        lines.append("!! " + warning)
    if job.build.glb_path is None:
        return loading.Built(warnings=[line[3:] for line in lines if line.startswith("!! ")])

    # From here it is the ordinary road: bake the channels every Texture Set
    # names, create the project on the written mesh, wire the shader.
    importer.finish_textures(job)
    importer.launch(job, reuse_open_project=False)
    return loading.Built(manifest=packages.manifest,
                         warnings=[line[3:] for line in lines if line.startswith("!! ")],
                         imported=job.build.mesh_count)


def _write(packages, job, options):
    """One .glb out of the placement tree, sharing a mesh between rows that draw
    the same one with the same slots."""
    from . import gltf_writer

    result = model_builder.BuildResult(packages.label)
    builder = gltf_writer.GlbBuilder()
    rows = list(packages.placements)
    nodes = [None] * len(rows)
    shared = {}

    for index, row in enumerate(rows):
        entry = packages.library.get(row["mesh"])
        parent = int(row["parent"])
        under = nodes[parent] if 0 <= parent < len(nodes) else None
        matrix = coordinate.convert_matrix(_local(row))
        if entry is None:
            # A transform other rows hang under, or a light: Painter has nowhere to
            # put a light, but the transform still has to exist or its children move.
            nodes[index] = builder.node(row["name"], None, matrix, under)
            continue
        slots = loading.slot_paths(row, packages.library)
        key = (row["mesh"], tuple(slots))
        existing = shared.get(key)
        if existing is not None:
            # The same drawable again: one more node over the SAME mesh.
            nodes[index] = builder.node(row["name"], existing, matrix, under)
            result.mesh_count += 1
            continue
        entries = [_material(result, packages, path, row["name"]) for path in slots]
        mesh_index = _mesh(builder, result, row["name"], entry[0], entries, options)
        if mesh_index is None:
            nodes[index] = builder.node(row["name"], None, matrix, under)
            continue
        shared[key] = mesh_index
        nodes[index] = builder.node(row["name"], mesh_index, matrix, under)
        result.mesh_count += 1

    result.finish()
    if builder.is_empty():
        result.warnings.append(
            "'{0}' places {1} row(s) but none of them draws geometry this host can "
            "write.".format(packages.label, len(rows)))
        return result
    model_builder._write(builder, job.glb_path(), result)
    return result


def _local(row):
    """A row's own transform, in the engine's space."""
    return _coordinate.unity_trs(
        {"x": row["px"], "y": row["py"], "z": row["pz"]},
        {"x": row["qx"], "y": row["qy"], "z": row["qz"], "w": row["qw"]},
        {"x": row["sx"], "y": row["sy"], "z": row["sz"]})


def _material(result, packages, path, drawn_by):
    """One material as a Texture Set entry, keyed by the path the engine names it
    by -- the same role a Unity guid plays on the other road."""
    if not path:
        return None
    props = packages.materials.get(path)
    name = (getattr(props, "name", "") or path.rsplit(".", 1)[-1] or path)
    entry = result.materials.get(name)
    if entry is None:
        entry = model_builder.MaterialEntry(path, name, props)
        result.materials[name] = entry
    if drawn_by not in entry.renderers:
        entry.renderers.append(drawn_by)
    return entry


def _mesh(builder, result, name, decoded, entries, options):
    """The mesh's primitives, one per submesh, each naming its Texture Set."""
    indices = []
    for entry in entries:
        indices.append(model_builder._fallback_material(builder, result, name)
                       if entry is None else builder.material(entry.guid, entry.name))
    while len(indices) < max(len(decoded.submeshes), 1):
        indices.append(model_builder._fallback_material(builder, result, name))
    primitives = model_builder._primitives_from_decoded(decoded, indices, options, None)
    if not primitives:
        return None
    result.vertex_count += int(sum(len(one["positions"]) for one in primitives))
    result.triangle_count += int(sum(len(one["indices"]) for one in primitives))
    return builder.mesh(name, primitives)
