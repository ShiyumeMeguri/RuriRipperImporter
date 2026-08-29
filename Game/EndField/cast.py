"""Who is in a scene, and what the game loads to put them in it.

Two different things ask this -- the roster tab, which loads one selected cast
member, and the story stage, which loads everybody a unit animates -- and the
game answers it two different ways depending on WHO is being asked about:

``character``   a playable character. Its own data asset declares a model prefab
                (no config table carries one), and that prefab imports whole.
``template``    an npc. The game ships no assembled model for it at all: its
                template names part SLOTS, an avatar-mesh table says which mesh
                each slot wears, and every one of those meshes is skinned onto
                the skeleton the template's avatar carries. It is BUILT, not
                loaded.
``prefab``      neither -- but the timeline binds the track to something, and
                what it binds to is a prefab the cabmap knows by name. A story-
                only walk-on with no roster row is still a model the game ships.

Resolution and building are deliberately separate. Resolving says only what a
member IS and which CABs it lives in, touching no scene; the caller collects
those CABs across the WHOLE cast, crosses the bridge once, and builds everyone
from that one closure. That split is why a unit with fifty performers costs one
read instead of fifty.
"""

from __future__ import annotations

import re

from ...RuriRipperPyBridge.session import cabmap_state
from . import datasets

CHARACTERS = datasets.CHARACTERS
NPCS = datasets.NPCS


# charId -> {"model", "tag", "asset"}, read once per session from the game's own
# character data assets. Module scope for the same reason the tables are.
_CHARACTER_MODELS = {}


def _character_model(character_id):
    """The model prefab the game itself declares for a character, or "" when its
    data asset is not in the loaded cabmap.

    No config table carries a model field (all 693 were swept), so the game's own
    per-character data assets are the only source. Read as one batch: they share a
    handful of bundles, so paying per character would mean re-resolving the same
    closure thirty times."""
    if not _CHARACTER_MODELS:
        cabs = datasets.character_model_cabs()
        if cabs:
            _CHARACTER_MODELS.update(datasets.character_models(cabs))
    return _CHARACTER_MODELS.get(character_id, {}).get("model", "")


def character_model(character_id):
    """The model prefab the game itself declares for a character id, or "" when
    that id has no data asset in the loaded cabmap. The one way any other panel
    asks: the declaration lives in the game's per-character data assets, read
    once per session here."""
    return _character_model(character_id)


# name -> the npc prefab info the game files under it, or None for a name that is
# not an npc template. Read on demand and remembered: it is an install constant.
_NPC_INFO = {}
_BLENDER_SUFFIX = re.compile(r"\.\d{3}$")


def npc_info(name):
    """What the game's own prefab info says about an npc template, or None when
    ``name`` is not one (a playable character's rig, a rig from another tool).

    A rig this add-on assembled is NAMED after its template, so an object's own
    name is a valid key -- minus Blender's uniquifying ``.001`` suffix, which is
    the object's, not the entity's."""
    key = _BLENDER_SUFFIX.sub("", (name or "").strip())
    if not key:
        return None
    if key not in _NPC_INFO:
        try:
            info = datasets.npc_parts(key)
        except Exception:
            info = None
        _NPC_INFO[key] = info if info and info.get("parts") else None
    return _NPC_INFO[key]


def npc_template(name):
    """The npc template ``name`` IS, or "" when it names none. What a panel keys
    an entity's own per-line assets by -- exact, where a name fragment guessed
    off the rig would drag in every sibling that shares a body-type word."""
    return "" if npc_info(name) is None else _BLENDER_SUFFIX.sub("", name.strip())


def declared_face_morph(template_id):
    """The face-morph avatar the game itself assigns an npc template
    (``facialMorphAvatarName``), or "" for a name that is not an npc template.

    The ONE way any panel asks which face tables a rig wears -- the declaration
    lives in the template's own prefab info, and for an npc it names something
    entirely unlike the npc itself (``npc_spl_adaxier_01`` wears ``ardashir``)."""
    info = npc_info(template_id)
    return "" if info is None else info.get("facial_morph", "")


def character_tag(token):
    """The SkeletalMorph tag the game itself assigns a character, matched from a
    rig's name token. Empty when the token names no character in the roster's
    own data, which is what a rig imported from somewhere else looks like."""
    needle = (token or "").strip().lower()
    if not needle:
        return 0
    _character_model("")  # ensures the map is read
    for character_id, declared in _CHARACTER_MODELS.items():
        if needle in character_id.lower() and declared.get("tag"):
            try:
                return int(declared["tag"])
            except ValueError:
                return 0
    return 0


def _avatar_skeleton(db, avatar_file):
    """``(world_rests, paths)`` from an Avatar UnityFile in ``db`` -- the standing
    rest a shared-skeleton part mesh bakes against, plus its transform paths."""
    from ...RuriRipperPyBridge.unity import avatar
    avatar_doc = avatar_file.first("Avatar") if avatar_file is not None else None
    if avatar_doc is None:
        return {}, []
    return avatar.skeleton_world_rests(avatar_doc.data), avatar.transform_paths(avatar_doc.data)


def _templet_skeleton(info):
    """``(world_rests, paths, leaf_names, avatar_data)`` for an npc's shared
    skeleton, read from the avatar template the manifest names.

    The skeleton is the Avatar asset the template's own bundle carries: its
    ``m_AvatarSkeleton`` posed by the pose array ``avatar.py`` selects is the whole
    rig's world rest and its ``m_TOS`` the name/parent table -- the authoritative
    pose a part mesh is bind-baked against, exactly what a shipped rig gets from its
    prefab transform hierarchy. The template MonoBehaviour beside it contributes
    ``bonePathsStr``, the leaf-name vocabulary for any part-specific bone the
    table does not name.

    Both are materialized by their own identity out of a bundle that carries a
    thousand other assets -- never the whole closure."""
    templet = (info.get("avatar_templet") or "").rsplit("/", 1)[-1]
    if not templet:
        return {}, [], [], None
    stem = "data_npc_avatartemplet_" + templet.lower()
    rows = datasets.named_rows(stem)
    if not rows:
        return {}, [], [], None
    from ...RuriRipperPyBridge.unity import bridge_asset_db, class_registry
    cab = rows[0]["cab"]
    try:
        graph = cabmap_state.BRIDGE.scan_cabs([cab])
        avatar_id = class_registry.id_for_name("Avatar")
        mono_behaviour_id = class_registry.id_for_name("MonoBehaviour")
        keys = [graph.key(index) for index in graph.indices_of_class(avatar_id)]
        keys += [graph.key(index) for index in graph.find(mono_behaviour_id, stem)]
        if not keys:
            return {}, [], [], None
        assets, _r, _s, _c, _sc = cabmap_state.BRIDGE.import_cabs(
            [cab], export_asset_keys=sorted(set(keys)))
    except Exception:
        return {}, [], [], None
    db = bridge_asset_db.BridgeAssetDatabase(
        assets, asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid)

    world_rests, paths, leaves, avatar_data = {}, [], [], None
    for guid in db.all_guids():
        loaded = db.load_guid(guid)
        if loaded is None:
            continue
        avatar_doc = loaded.first("Avatar")
        if not world_rests and avatar_doc is not None:
            world_rests, paths = _avatar_skeleton(db, loaded)
            # The whole document travels with the rig, exactly as a shipped
            # character's does: it is what a muscle-encoded clip solves against.
            avatar_data = avatar_doc.data
            continue
        doc = loaded.first("MonoBehaviour")
        if doc is not None and "bonePathsStr" in (doc.data or {}):
            leaves = [str(name) for name in (doc.data.get("bonePathsStr") or [])]
    return world_rests, paths, leaves, avatar_data


def _avatar_mesh_cab(info):
    """The CAB holding this npc's avatar-mesh family table (``data_npc_avatarmesh_
    <leaf>``, named by the manifest's own avatarMeshName). That table is what says
    which mesh each part slot wears and which materials dress it; without it an
    npc cannot be assembled at all."""
    leaf = (info.get("avatar_mesh") or "").rsplit("/", 1)[-1]
    if not leaf:
        return ""
    rows = datasets.named_rows("data_npc_avatarmesh_" + leaf.lower())
    return rows[0]["cab"] if rows else ""


def _npc_materials(context, info, template_id):
    """{mesh name: [material container path]} for one npc template, from the
    game's own assembly table.

    An npc's colours are stated by its TEMPLATE (a material code per renderer),
    never by its parts -- the same part takes different materials under different
    templates, and a part's trailing number is its own index, not a material's.
    So the codes have to be resolved against the family's shared table -- which
    the C# side does, codes and path hashes being binary."""
    cab = _avatar_mesh_cab(info)
    if not cab:
        return {}
    try:
        return datasets.npc_materials(template_id, [cab])
    except Exception:
        return {}


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
    from ... import material_builder, prefab_importer
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
            binder.add_skeleton(*_avatar_skeleton(db, part_file))
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
    the same name the assembly table states. [] when the template dresses this
    mesh in nothing, or a named material is not in the resolved closure."""
    if mat_builder is None or not mesh_name:
        return []
    paths = list((materials_by_mesh or {}).get(mesh_name.lower(), ()))
    named = datasets.ranked(paths)
    materials = []
    for path in paths:
        guid = material_index.get(named.get(path, {}).get("mesh_name", ""))
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



def model_parts(template_id, level=0):
    """(manifest, [(cab, [mesh name])], [unresolved part]) for an npc template.

    A template's ``partNameIdList`` entries are SLOT NAMES in its avatar-mesh
    family table, not asset names -- the mesh a slot wears is stated there and
    nowhere else. Matching the slot name against the cabmap is what used to
    happen here, and it silently fails for every npc whose slot is a postmodel id
    (``npc_8001_deathgirl_postmodel`` wears ``s_npc_major_deathgirl_body_01_lod0``;
    nothing joins those two by name) -- and it quietly imports the WRONG mesh for
    the pedestrians it appears to work for (slot ``P_npc_girl_body_unionscholar_a_02``
    wears the ``_a_01`` mesh).

    So the slot table answers it: slot -> meshes at the wanted detail level, then
    each mesh name resolves to the CAB that actually holds it. Meshes are grouped
    by CAB so one closure resolve covers everything it carries.

    The avatar template is NOT an import row: it is read only for its skeleton
    paths + leaf names (see _templet_skeleton), which the assembler feeds to a
    SkeletonBinder to rebuild the shared skeleton the loose meshes hash against."""
    try:
        info = datasets.npc_parts(template_id)
    except Exception:
        return None, [], []
    cab = _avatar_mesh_cab(info)
    if not cab:
        return info, [], list(info["parts"])
    slots = _slots_of(cab)
    if not slots:
        return info, [], list(info["parts"])

    by_key = {str(name).lower(): levels for name, levels in slots.items()}
    wanted = []
    missing = []
    for part in info["parts"]:
        levels = by_key.get(str(part).lower())
        meshes = _meshes_at_level(levels, level) if levels else []
        if not meshes:
            missing.append(part)
            continue
        wanted.extend(meshes)
    if not wanted:
        return info, [], missing

    # The slot states the addressable path of the mesh it wears, so the CAB it
    # lives in is a lookup, not a search. Paths batch into ONE resolve.
    paths = [mesh["path"] for mesh in wanted if mesh["path"]]
    cabs_by_path = {}
    if paths:
        try:
            for path in paths:
                found = cabmap_state.BRIDGE.resolve_cabs_for_paths([path])
                if found:
                    cabs_by_path[path] = found[0]
        except Exception:
            cabs_by_path = {}

    by_cab = {}
    for mesh in wanted:
        cab_name = cabs_by_path.get(mesh["path"]) if mesh["path"] else None
        if cab_name is None:
            missing.append(mesh["name"])
            continue
        by_cab.setdefault(cab_name, []).append(mesh["name"])
    return info, list(by_cab.items()), missing


def _meshes_at_level(levels, level):
    """One slot's meshes at the requested detail level, or the nearest level the
    game authored it at -- the game states which levels exist per slot, and a
    part simply not authored at LOD2 is normal (see the deathgirl slot, whose
    LOD2 drops the eyeshadow and hairshadow meshes)."""
    if not levels:
        return []
    if level in levels:
        return levels[level]
    nearest = min(levels, key=lambda candidate: (abs(candidate - level), candidate))
    return levels[nearest]


def _at_detail_level(rows, level):
    """The rows at the requested detail level, or -- when the model simply is
    not authored at that level -- the closest one it does have, reported rather
    than silently substituted. The rank is the game's own suffix convention, read
    off the row rather than re-derived here."""
    exact = [row for row in rows if row["lod_rank"] == level]
    if exact:
        return exact, level
    # -1 is "no LOD suffix at all", i.e. a single-detail model: as good as LOD0.
    best = min((row["lod_rank"] for row in rows), key=lambda rank: (rank < 0, abs(rank - level)))
    return [row for row in rows if row["lod_rank"] == best], best


def material_cabs(materials_by_mesh):
    """The CABs holding the materials a template dresses its meshes in.

    They live apart from the meshes, so a read that marks only the part CABs comes
    back with the geometry and none of its colours. Stated as a question the caller
    asks BEFORE the read rather than a seed added during one, which is what lets a
    whole cast resolve in a single closure."""
    material_paths = sorted({path for paths in (materials_by_mesh or {}).values()
                             for path in paths})
    if not material_paths:
        return []
    try:
        return list(dict.fromkeys(cabmap_state.BRIDGE.resolve_cabs_for_paths(material_paths)))
    except Exception:
        return []


# ── one cast, resolved once and read once ───────────────────────────────────

# How a member gets into the scene. Not a type tag for its own sake: the two are
# genuinely different operations, because the game ships one and only describes
# the other.
PREFAB = "prefab"
PARTS = "parts"


class Loadable:
    """One member of a cast: what it is, and every CAB the read has to mark.

    Deliberately inert. Resolving must touch no scene and resolve no closure,
    because the whole point is to know the WHOLE cast's CABs before anything is
    read -- a member that resolved by importing itself could never be batched
    with another.
    """

    __slots__ = ("key", "label", "kind", "cabs", "manifest", "meshes",
                 "missing", "dressing", "paths")

    def __init__(self, key, label, kind, cabs, manifest=None, meshes=(),
                 missing=(), dressing=None, paths=()):
        self.key = key
        self.label = label
        self.kind = kind
        self.cabs = list(cabs)
        self.manifest = manifest
        self.meshes = list(meshes)
        self.missing = list(missing)
        self.dressing = dressing or {}
        self.paths = dict(paths)


class Assembled:
    """What building one member produced, and what it could not."""

    __slots__ = ("armature", "manifest", "missing", "warnings", "imported")

    def __init__(self, armature=None, manifest=None, missing=(), warnings=(), imported=0):
        self.armature = armature
        self.manifest = manifest
        self.missing = list(missing)
        self.warnings = list(warnings)
        self.imported = imported


# Asset name -> the cabmap rows filed under it. A binding path repeats the same
# segments across every track of a unit, and the answer is an install constant.
_NAMED = {}

# avatar-mesh family CAB -> its slot table. One family dresses a whole crowd, so a
# cast of eight npcs asks the same table eight times over -- and each member asks
# twice besides, once for which mesh a slot wears and once for where it lives.
_SLOTS = {}


def _slots_of(cab):
    if cab not in _SLOTS:
        try:
            _SLOTS[cab] = datasets.npc_meshes([cab])
        except Exception:
            _SLOTS[cab] = {}
    return _SLOTS[cab]


def resolve(members, lod=0):
    """{key: Loadable} -- what each member of a cast loads as. Touches no scene.

    A member is keyed in the RESULT by whatever it was asked about, but a Loadable
    is keyed by what it resolved to -- so several spellings of one performer map to
    one shared Loadable, and ``cabs_of`` counts it once.

    ``members`` are dicts stating the game's own identity for one performer:
    ``character``, ``template``, and the timeline ``binding`` that named them.
    The three are asked in the order the game states them most specifically, and
    the first that answers wins:

      the character's own data asset declares a model prefab;
      an npc template describes an assembly;
      whatever the binding turned out to be an instance OF.

    A member none of them answers for is simply absent from the result -- the
    game ships no model for it, which the caller reports rather than works around.
    """
    resolved = {}
    canonical = {}
    for member in members:
        key = member.get("key") or ""
        if not key or key in resolved:
            continue
        found = (_as_character(member) or _as_template(member, lod)
                 or _as_bound_prefab(member))
        if found is None:
            continue
        # Two spellings that resolved to the same thing ARE the same one, and share
        # one Loadable -- so the caller builds it once and lands both spellings'
        # animation on the one rig.
        resolved[key] = canonical.setdefault(found.key, found)
    return resolved


def cabs_of(loadables):
    """Every CAB a cast lives in -- what the single read marks, in one list."""
    return list(dict.fromkeys(cab for loadable in loadables for cab in loadable.cabs if cab))


def _as_character(member):
    """A playable character: the prefab its own data asset declares, else the one
    named after it. Exactly the roster tab's own order -- same question, same
    answer, one implementation."""
    character = member.get("character") or ""
    if not character:
        return None
    declared = character_model(character)
    rows = datasets.part_rows(declared, cast=CHARACTERS) if declared else []
    if not rows:
        rows = datasets.model_rows(character, "postmodel", cast=CHARACTERS)
    if not rows:
        return None
    chosen, _level = _at_detail_level(rows, 0)
    return Loadable(character, member.get("label") or character, PREFAB,
                    [row["cab"] for row in chosen])


def _as_template(member, lod):
    """An npc: the game ships no model, so what resolves is the RECIPE -- which
    meshes, out of which CABs, dressed in which materials."""
    template = member.get("template") or ""
    if not template:
        return None
    manifest, hits, missing = model_parts(template, lod)
    if manifest is None or not hits:
        return None
    dressing = _npc_materials(None, manifest, template)
    return Loadable(template, member.get("label") or template, PARTS,
                    [cab for cab, _meshes in hits] + material_cabs(dressing),
                    manifest=manifest, meshes=hits, missing=missing, dressing=dressing,
                    paths=_mesh_paths(manifest, lod))


def _as_bound_prefab(member):
    """Whatever the timeline bound the track to.

    A binding is a transform path, and its segments are the objects a prefab
    instantiated; the deepest segment the cabmap knows AS an asset is the prefab
    that was instantiated. Asked of the cabmap rather than matched against a name
    pattern, so nothing here has to know how this game spells "a prefab" -- and it
    is what stages a story-only walk-on the roster tables never mention, and what
    stages a dialogue at all (whose clips name a body type, not a person)."""
    binding = member.get("binding") or ""
    for segment in reversed([part for part in binding.split("/") if part]):
        if segment not in _NAMED:
            try:
                _NAMED[segment] = datasets.named_rows(segment)
            except Exception:
                _NAMED[segment] = []
        rows = _NAMED[segment]
        if rows:
            chosen, _level = _at_detail_level(rows, 0)
            return Loadable(segment, member.get("label") or segment, PREFAB,
                            [row["cab"] for row in chosen])
    return None


def _mesh_paths(manifest, lod):
    """{mesh name: its container path} for one template's slots.

    Where the game files a mesh is what says which of a SHARED closure's Avatars
    belongs to this part rather than to somebody else in the same read -- with one
    closure per part that question answered itself, and with one closure per cast
    it has to be asked."""
    cab = _avatar_mesh_cab(manifest)
    slots = _slots_of(cab) if cab else {}
    named = {}
    for levels in slots.values():
        for mesh in _meshes_at_level(levels, lod):
            if mesh.get("path"):
                named[str(mesh["name"]).lower()] = mesh["path"]
    return named


def build(context, loadable, resolved, options=None):
    """Bring one member into the scene off an ALREADY-resolved closure.

    Nothing here reads: the closure covers the whole cast, so a fifty-performer
    unit pays one read rather than fifty."""
    if loadable.kind == PARTS:
        return _build_parts(context, loadable, resolved, options)
    return _build_prefab(context, loadable, resolved, options)


def _build_prefab(context, loadable, resolved, options):
    """The browser's own hierarchy import, restricted to the assets this member
    asked for -- one import path, so a fix there is a fix here.

    Restricted to this member's own CABs on purpose, and by CAB identity rather
    than by name: the closure holds the WHOLE cast, so "every root it exports" would
    give every member all of it. This game's archives are pooled besides, so even a
    single member's closure exports roots that have nothing to do with it."""
    from ... import cabmap_panel
    warnings = []
    rows = [{"cab": cab, "name": loadable.label} for cab in loadable.cabs]
    _ok, imported = cabmap_panel.import_hierarchy_from_closure(
        _Reporter(warnings), context, context.scene.ruri_cabmap, rows, resolved,
        only_seeded=True)
    return Assembled(manifest=None, warnings=warnings, imported=imported)


def _build_parts(context, loadable, resolved, options):
    """Assemble an npc: every mesh its slots name, skinned onto the one skeleton
    its template avatar carries. The terminal state is ONE armature named after
    the template, with every part parented and skinned onto it."""
    from ... import armature_builder
    warnings = []
    world_rests, paths, leaf_names, avatar_data = _templet_skeleton(loadable.manifest)
    if not world_rests:
        return Assembled(manifest=loadable.manifest, missing=loadable.missing, warnings=[
            "'{0}' names avatar template '{1}', which is not in the loaded cabmap -- "
            "its meshes have no skeleton to bind to.".format(
                loadable.label, loadable.manifest.get("avatar_templet") or "none")])
    if options is None:
        options = context.scene.ruri_cabmap.as_options()
    binder = armature_builder.SkeletonBinder(loadable.key, world_rests, paths, leaf_names,
                                             avatar_data)
    built = 0
    for cab, meshes in loadable.meshes:
        scope = [loadable.paths[name] for name in
                 (str(entry).lower() for entry in meshes) if name in loadable.paths]
        if import_part(context, resolved["db"], binder, options, meshes,
                       loadable.dressing, scope):
            built += 1
    # The binder builds its rig itself rather than through the prefab importer, so
    # the game identity is stamped at this call site. The Unity rig identity is NOT:
    # the binder owes that one itself, per growth (see SkeletonBinder._stamp_rig).
    armature_builder.stamp_game(binder.armature, options.get("source_game"))
    if loadable.missing:
        warnings.append("{0}: {1} of {2} part slot(s) resolved to nothing: {3}".format(
            loadable.label, len(loadable.missing), len(loadable.manifest["parts"]),
            ", ".join(loadable.missing[:4])))
    return Assembled(armature=binder.armature if built else None,
                     manifest=loadable.manifest, missing=loadable.missing,
                     warnings=warnings, imported=built)


class _Reporter:
    """A ``.report()`` for a caller that is not an operator -- it collects the
    lines instead of putting them in the status bar, because that caller says them
    in its own summary."""

    __slots__ = ("lines",)

    def __init__(self, lines):
        self.lines = lines

    def report(self, level, message):
        if "ERROR" in level or "WARNING" in level:
            self.lines.append(message)


def assemble(context, template_id, lod=0):
    """Assemble ONE npc, for a caller holding a single selection.

    Deliberately the general path with a cast of one -- resolve, read, build -- so
    the roster tab and the story stage cannot drift into two different npcs."""
    from ... import cabmap_panel
    loadable = _as_template({"key": template_id, "template": template_id}, lod)
    if loadable is None:
        try:
            manifest = datasets.npc_parts(template_id)
        except Exception:
            manifest = None
        return Assembled(manifest=manifest, warnings=[
            "'{0}' has no part manifest, or resolved none of its part slots to a mesh -- "
            "the game ships no assembled model for it.".format(template_id)])
    return _build_parts(context, loadable,
                        cabmap_panel.resolve_import_closure(loadable.cabs), None)


def forget():
    """Drop what one session cached; an install's answers are not the next one's."""
    _CHARACTER_MODELS.clear()
    _NPC_INFO.clear()
    _NAMED.clear()
    _SLOTS.clear()
