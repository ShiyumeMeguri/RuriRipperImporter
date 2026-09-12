"""Build what a game resolved, with Blender objects.

A panel that loads a character says WHAT the game states it is -- the CABs to
read, the meshes to keep, the materials to put on them, the skeleton to bind them
to (:class:`Kernel.app.loading.Packages`) -- and this turns that into a scene.
Nothing here knows which game asked; every value it acts on came off the packages
it was handed.

Two shapes, because games state two: a PREFAB, which is the browser's own
hierarchy import restricted to that member's own CABs, and PARTS, which is a set
of meshes skinned onto one skeleton the game named separately. The terminal state
of the second is ONE armature with every part parented and skinned onto it.
"""

from __future__ import annotations

from ...Kernel.app import loading
from ...RuriRipperPyBridge.session import cabmap_state
from . import (armature_builder, assembly, cabmap_panel, material_builder,
               prefab_importer, scene_window, unreal_importer)

#: THE clip build, named here because this is the module the port reaches for.
build_clips = cabmap_panel.build_clips


def materialise(context, packages, resolved, options=None, report=None):
    """Bring one resolved thing into the scene off an ALREADY-resolved closure.

    Nothing here reads: the closure covers the whole cast, so a fifty-performer
    unit pays one read rather than fifty."""
    if packages.kind == loading.PARTS:
        return _build_parts(context, packages, resolved, options)
    if packages.kind == loading.ASSEMBLY:
        return assembly.build(context, packages, resolved, options)
    if packages.kind == loading.PLACEMENTS:
        return unreal_importer.materialise(context, packages, options)
    if packages.kind == loading.SCENE_WINDOW:
        return _build_window(context, packages, options)
    return _build_prefab(context, packages, resolved, options)


def _build_window(context, packages, options):
    """A whole scene window: every placement a real, selectable object, one build
    per distinct asset and a linked duplicate per further placement.

    The report it produces is far richer than :class:`Built` carries -- what
    resolved, what the game itself ships with no geometry, what each phase cost --
    and every line of it goes to the console the way it always has. What crosses
    back is what every caller of this entry takes."""
    report = scene_window.import_scene_window(
        context, cabmap_state.BRIDGE, packages.window, options)
    for line in scene_window.console_lines(report, set(packages.cabs)):
        print("[RuriRipper] scene: " + line)
    return loading.Built(warnings=scene_window.lost_content(report),
                         imported=report.placed)


def _build_prefab(context, packages, resolved, options):
    """THE hierarchy import, over whatever the packages asked for.

    ``seeded_only`` is what a caller sharing one closure with other callers
    needs, and by CAB identity rather than by name: the closure holds a WHOLE
    cast, so "every root it exports" would give every member all of it. The
    browser asks for the opposite -- a bundled row routinely pulls a second
    top-level asset that belongs with it -- and says so the same way.

    A row's NAME is not carried across: the cabmap already states what each CAB
    holds, so this reads it there instead of taking a copy that could drift."""
    warnings = []
    named = cabmap_state.rows_by_cab()
    rows = [{"cab": cab,
             "name": (named.get(cab) or {}).get("name") or packages.label}
            for cab in packages.cabs]
    _ok, imported = cabmap_panel.import_hierarchy_from_closure(
        Reporter(warnings), context, context.scene.ruri_cabmap, rows, resolved,
        only_root_names=packages.named_roots,
        populate_browser=len(rows) == 1,
        only_seeded=packages.seeded_only, options=options)
    return loading.Built(manifest=None, warnings=warnings, imported=imported)


def _build_parts(context, packages, resolved, options):
    """Assemble an npc: every mesh its slots name, skinned onto the one skeleton
    its template avatar carries. The terminal state is ONE armature named after
    the template, with every part parented and skinned onto it."""
    warnings = []
    world_rests, paths, leaf_names, avatar_data = (
        packages.skeleton() if packages.skeleton is not None else ({}, [], [], None))
    if not world_rests:
        return loading.Built(manifest=packages.manifest, missing=packages.missing, warnings=[
            "'{0}' names avatar template '{1}', which is not in the loaded cabmap -- "
            "its meshes have no skeleton to bind to.".format(
                packages.label, packages.manifest.get("avatar_templet") or "none")])
    if options is None:
        options = context.scene.ruri_cabmap.as_options()
    binder = armature_builder.SkeletonBinder(packages.key, world_rests, paths, leaf_names,
                                             avatar_data)
    built = 0
    for cab, meshes in packages.meshes:
        scope = [packages.paths[name] for name in
                 (str(entry).lower() for entry in meshes) if name in packages.paths]
        if import_part(context, resolved["db"], binder, options, meshes,
                       packages.dressing, scope):
            built += 1
    # The binder builds its rig itself rather than through the prefab importer, so
    # the game identity is stamped at this call site. The Unity rig identity is NOT:
    # the binder owes that one itself, per growth (see SkeletonBinder._stamp_rig).
    armature_builder.stamp_game(binder.armature, options.get("source_game"))
    if packages.missing:
        warnings.append("{0}: {1} of {2} part slot(s) resolved to nothing: {3}".format(
            packages.label, len(packages.missing), len(packages.manifest["parts"]),
            ", ".join(packages.missing[:4])))
    return loading.Built(armature=binder.armature if built else None,
                     manifest=packages.manifest, missing=packages.missing,
                     warnings=warnings, imported=built)


class Reporter:
    """A ``.report()`` for a caller that is not an operator -- it collects the
    lines instead of putting them in the status bar, because that caller says them
    in its own summary."""

    __slots__ = ("lines",)

    def __init__(self, lines):
        self.lines = lines

    def report(self, level, message):
        if "ERROR" in level or "WARNING" in level:
            self.lines.append(message)


def import_part(context, db, binder, options, wanted_meshes, materials_by_mesh=None,
                scope=()):
    """Import exactly the meshes ``wanted_meshes`` names, each baked onto the shared
    rig the binder is growing, out of a closure the caller already resolved. True
    when anything built.

    The names come from the game's own slot table, so nothing here guesses which
    of a closure's meshes belong to this part or which detail level they are: a
    CAB routinely carries several parts' meshes at every LOD, and picking by name
    pattern imported the wrong ones (or none).

    ``materials_by_mesh`` maps a mesh's own name to the container paths the
    template dresses it in (see _npc_materials); those materials live in their
    own CABs, so they are co-seeded into this part's closure rather than looked
    for inside it."""
    from ...RuriRipperPyBridge.unity import discovery
    # A part's own Avatar supplies the standing rest for any bone IT alone introduces
    # (the template avatar already gave the base skeleton). With one closure covering
    # a whole cast, "the first Avatar in the closure" would be somebody else's -- so
    # the search is scoped to where the game itself files this part, by the container
    # paths the slot table stated for its meshes.
    folders = {path.rsplit("/", 1)[0].lower() for path in scope if "/" in path}
    paths = cabmap_state.BRIDGE.asset_paths_by_guid if folders else {}
    for guid in db.all_guids():
        where = str(paths.get(guid, "")).lower()
        if not where or where.rsplit("/", 1)[0] not in folders:
            continue
        part_file = db.load_guid(guid)
        if part_file is not None and part_file.first("Avatar") is not None:
            binder.add_skeleton(*loading.avatar_skeleton(db, part_file))
            break

    mat_builder = (material_builder.MaterialBuilder(db, options)
                   if materials_by_mesh and options["import_materials"] else None)
    material_index = discovery.material_name_index(db) if mat_builder is not None else {}

    imported = False
    for guid in _named_mesh_guids(db, wanted_meshes):
        mesh_file = db.load_guid(guid)
        if mesh_file is None:
            continue
        mesh_doc = mesh_file.first("Mesh")
        mesh_name = str((mesh_doc.data.get("m_Name") if mesh_doc is not None else "") or "")
        report = prefab_importer.import_mesh_from_db(
            context, db, mesh_file, options,
            materials=_materials_for(mesh_name, materials_by_mesh, material_index, mat_builder),
            skeleton=binder)
        if report.mesh_objects:
            imported = True
    return imported


def _materials_for(mesh_name, materials_by_mesh, material_index, mat_builder):
    """The built materials one submesh wears, joined on the Mesh's own name --
    the same name the assembly table states. [] when the game dresses this mesh
    in nothing, or a named material is not in the resolved closure.

    ``materials_by_mesh`` already holds MATERIAL NAMES: turning whatever a
    game's table states into the name an asset carries is that game's own
    convention, resolved where the packages were stated."""
    if mat_builder is None or not mesh_name:
        return []
    materials = []
    for name in (materials_by_mesh or {}).get(mesh_name.lower(), ()):
        guid = material_index.get(name)
        if guid is None:
            continue
        material = mat_builder.build_from_ref({"guid": guid})
        if material is not None:
            materials.append(material)
    return materials


def _named_mesh_guids(db, wanted_meshes):
    """The guids of exactly the Meshes the slot table named, in that order.

    Matched on the Mesh's own m_Name (case-insensitively -- the slot table writes
    ``S_npc_...`` while the addressable path is lowercase), read off a bounded
    prefix peek rather than a parse, so scanning a closure costs one sniff per
    document instead of decoding every mesh in it."""
    from ...RuriRipperPyBridge.unity import discovery
    wanted = {str(name).lower(): position for position, name in enumerate(wanted_meshes)}
    if not wanted:
        return []
    found = []
    for guid in db.all_guids():
        text = db.raw_text(guid)
        if not text:
            continue
        class_name, name = discovery.peek_class_and_name(text)
        if class_name != "Mesh" or name is None:
            continue
        position = wanted.get(name.lower())
        if position is not None:
            found.append((position, guid))
    found.sort()
    return [guid for _position, guid in found]

