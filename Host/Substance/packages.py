"""Build what a game resolved, with a Painter project.

The Blender materialiser next door turns the same :class:`Kernel.app.loading.Packages`
into a skeleton, meshes and node materials. Painter's answer to the identical
description is one self-contained mesh file plus a wired Texture Set per
material, because that is what Painter's entry point is -- ``project.create``
takes a path, and there is no in-memory geometry API.

The difference is real, not a shortfall on either side, and it is stated exactly
here: the panel that asked said WHAT the thing is and nothing about how to build
one.

WHAT A PARTS RECIPE MEANS HERE. A game that ships a recipe rather than a prefab
names meshes, materials and a skeleton to bind them to. Painter has no skeleton,
so the binding is the part that does not cross -- what does is every mesh the
recipe names, in one project, each material its own Texture Set. That is the
honest equivalent: the same surfaces to paint, minus a rig there is nowhere to
put.
"""

from __future__ import annotations

import numpy as np

from ...Kernel.app import loading
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import discovery, hierarchy
from . import importer, model_builder, placements, scene_window, settings


def materialise(context, packages, options=None, report=None, resolved=None):
    """Import one resolved thing into a Painter project."""
    lines = report if report is not None else []
    if cabmap_state.BRIDGE is None:
        return loading.Built(warnings=["No cabmap session is open."])
    # Kinds that carry their own decoded content are answered before the CAB
    # closure road, which is what a Unity build needs and pure overhead to them.
    if packages.kind == loading.ASSEMBLY:
        return _build_assembly(context, packages, options, lines, resolved)
    if packages.kind == loading.PLACEMENTS:
        return placements.materialise(context, packages, options, lines)
    if packages.kind == loading.SCENE_WINDOW:
        return scene_window.materialise(context, packages, options, lines)
    if not packages.cabs:
        return loading.Built(warnings=[
            "The game states no packages for '{0}'.".format(packages.label)])

    # The closure the caller already read on a worker thread, or -- for a caller
    # with no step loader behind it -- the same one function, read here.
    if resolved is None:
        resolved = loading.resolve_closure(packages.cabs)
    jobs = importer.jobs_from_closure(packages.cabs, resolved)
    if not jobs:
        return loading.Built(warnings=[
            "'{0}' resolved to {1} CAB(s), none of which exported anything "
            "importable.".format(packages.label, len(packages.cabs))])

    # A recipe naming its own meshes keeps only those; a prefab keeps whatever it
    # exports. Which it is came off the packages, not off a branch here.
    wanted = _wanted_meshes(packages)
    # EVERY root, in one project: what was asked for is one thing to look at, and
    # a glTF file holds a whole hierarchy. Taking the first and calling the rest
    # "reachable from the browser" was this host importing less than it was told.
    job = importer.one_job(jobs, packages.label)
    if len(jobs) > 1:
        lines.append("'{0}': {1} roots in one project.".format(
            packages.label, len(jobs)))
    if options:
        job.options = dict(job.options, **{key: value for key, value in options.items()
                                           if key in job.options})
    importer.prepare(job)
    lines.extend(job.report)
    if job.build is None or job.build.glb_path is None:
        return loading.Built(warnings=list(lines))

    importer.launch(job, reuse_open_project=False)
    return loading.Built(manifest=packages.manifest, missing=packages.missing,
                         warnings=[line[3:] for line in lines if line.startswith("!! ")],
                         imported=job.build.mesh_count)


def _build_assembly(context, packages, options, lines, resolved):
    """Several prefabs that are one character, in one project.

    THE BONE IS A NODE. Blender hangs each piece off a bone of the armature it
    built; there is no armature here and none is needed -- the bone the game names
    is a Transform of the rig prefab, and this host reads that prefab's node tree
    anyway. So the same statement produces the same placement, and the pieces land
    on the head instead of at the origin.

    What does NOT cross is the rig itself: the skinning is baked at its bind pose,
    which is the pose to texture on."""
    if resolved is None:
        resolved = loading.resolve_closure(
            packages.cabs, export_class_ids=list(packages.export_class_ids) or None)
    database = resolved["db"]
    index = discovery.prefab_name_index(database, resolved["roots"])
    parts = list(packages.parts)
    if not parts:
        return loading.Built(warnings=["The game states no pieces for '{0}'.".format(
            packages.label)])

    rig_part = next((part for part in parts if part.get("rig")), parts[0])
    rig_prefab = _prefab_of(database, index, rig_part)
    if rig_prefab is None:
        return loading.Built(missing=[rig_part["asset"]], warnings=[
            "'{0}' is not in the resolved closure, so there is nothing to hang the "
            "pieces on.".format(rig_part["asset"])])
    anchors = _anchor_matrices(rig_prefab)

    prefabs, placed, missing = [rig_prefab], [None], []
    for part in parts:
        if part is rig_part:
            continue
        prefab = _prefab_of(database, index, part)
        if prefab is None:
            missing.append(part["asset"])
            continue
        prefabs.append(prefab)
        placed.append(_placement(anchors, part))

    job = importer.ImportJob(packages.label or rig_part["asset"], database, prefabs,
                             settings.import_options())
    if options:
        job.options = dict(job.options, **{key: value for key, value in options.items()
                                           if key in job.options})
    job.placements = placed
    importer.prepare(job)
    lines.extend(job.report)
    lines.append("assembly: {0} of {1} piece(s)".format(len(prefabs), len(parts)))
    if missing:
        lines.append("!! {0} piece(s) are not in the closure: {1}".format(
            len(missing), ", ".join(missing[:6])))
    if job.build is None or job.build.glb_path is None:
        return loading.Built(missing=missing,
                             warnings=[line[3:] for line in lines if line.startswith("!! ")])

    importer.launch(job, reuse_open_project=False)
    return loading.Built(manifest=packages.manifest, missing=missing,
                         warnings=[line[3:] for line in lines if line.startswith("!! ")],
                         imported=job.build.mesh_count)


def _prefab_of(database, index, part):
    guid = index.get(str(part["asset"]).lower())
    return database.load_guid(guid) if guid else None


def _anchor_matrices(rig_prefab):
    """{transform name: Unity-space world 4x4} for the rig prefab's own tree --
    which is what a "bone" is before anything turns it into one."""
    nodes, _roots = hierarchy.build_hierarchy(rig_prefab)
    found = {}
    for node in nodes.values():
        found.setdefault(node.name, node.world)
    return found


def _placement(anchors, part):
    """Where one piece's own space sits: the bone the game names, times the
    correction the game states. Both are the GAME's space; the writer converts."""
    where = anchors.get(str(part.get("anchor") or ""))
    correction = loading.part_correction(part)
    if where is None:
        return correction
    return np.asarray(where, dtype=np.float64) @ correction


def _wanted_meshes(packages):
    """The mesh names a recipe named, flattened out of its (cab, meshes) pairs.
    Empty for a prefab, which states its content by being one."""
    if packages.kind != loading.PARTS:
        return ()
    names = []
    for _cab, meshes in packages.meshes:
        names.extend(str(name) for name in meshes)
    return tuple(names)


def resolution():
    return int(settings.get("texture_resolution", 2048))
