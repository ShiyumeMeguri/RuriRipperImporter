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

from ...Kernel import host as host_port
from ...Kernel.app import command, loading
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import prefab as prefab_scan
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
        assets, asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid,
        texture_srgb=cabmap_state.BRIDGE.texture_srgb_by_guid)

    world_rests, paths, leaves, avatar_data = {}, [], [], None
    for guid in db.all_guids():
        loaded = db.load_guid(guid)
        if loaded is None:
            continue
        avatar_doc = loaded.first("Avatar")
        if not world_rests and avatar_doc is not None:
            world_rests, paths = loading.avatar_skeleton(db, loaded)
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
        assigned = datasets.npc_materials(template_id, [cab])
    except Exception:
        return {}
    # The table states a container PATH; what a closure holds is an asset NAME,
    # and only this game knows how one becomes the other. Resolved once for the
    # whole batch, so the host is handed names and joins on them.
    paths = [path for paths in assigned.values() for path in paths]
    named = datasets.ranked(paths) if paths else {}
    return {mesh: [named[path]["mesh_name"] for path in paths
                   if path in named and named[path]["mesh_name"]]
            for mesh, paths in assigned.items()}


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
    # Two different -1s meet here: the OPTION's means "build every level", a ROW's
    # means "this one carries no LOD suffix". Reading the option's as a rank would
    # quietly select only the suffix-less models and call it "all of them".
    if level == prefab_scan.EVERY_LEVEL:
        return list(rows), level
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
PREFAB = loading.PREFAB
PARTS = loading.PARTS


#: What one member loads as -- the kernel's neutral shape, so a host can
#: materialise it without importing this game. Deliberately inert: resolving must
#: touch no scene and resolve no closure, because the whole point is to know the
#: WHOLE cast's CABs before anything is read.
Loadable = loading.Packages


#: What building one member produced, and what it could not.
Assembled = loading.Built


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
        found = (_as_character(member, lod) or _as_template(member, lod)
                 or _as_bound_prefab(member, lod))
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


def _as_character(member, lod):
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
    chosen, _level = _at_detail_level(rows, lod)
    # A cast resolves ONE closure for everybody, so this member takes exactly the
    # roots ITS OWN CABs seeded -- "every root the closure exports" would give each
    # member the whole cast, and this game's archives are pooled besides.
    return Loadable(character, member.get("label") or character, PREFAB,
                    [row["cab"] for row in chosen], seeded_only=True)


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
                    paths=_mesh_paths(manifest, lod),
                    skeleton=lambda info=manifest: _templet_skeleton(info))


def _as_bound_prefab(member, lod):
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
            chosen, _level = _at_detail_level(rows, lod)
            return Loadable(segment, member.get("label") or segment, PREFAB,
                            [row["cab"] for row in chosen], seeded_only=True)
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
    """Hand one resolved member to the host to materialise.

    What building IS differs completely per host -- a skeleton and node materials
    in Blender, a mesh file and texture sets in Painter -- and none of it is this
    game's business. This game's business ended at saying what the member is made
    of."""
    return host_port.current().import_packages(context, loadable, options,
                                               resolved=resolved)


def assemble(context, template_id, lod=0):
    """Assemble ONE npc, for a caller holding a single selection.

    Deliberately the general path with a cast of one -- resolve, read, build -- so
    the roster tab and the story stage cannot drift into two different npcs."""
    loadable = _as_template({"key": template_id, "template": template_id}, lod)
    if loadable is None:
        try:
            manifest = datasets.npc_parts(template_id)
        except Exception:
            manifest = None
        return Assembled(manifest=manifest, warnings=[
            "'{0}' has no part manifest, or resolved none of its part slots to a mesh -- "
            "the game ships no assembled model for it.".format(template_id)])
    return build(context, loadable, loading.resolve_closure(loadable.cabs))


def load_steps(context, member, level=0):
    """Bring ONE cast member into the scene, as steps a driver can pace.

    The same three moves a whole cast makes -- resolve what it is, read its
    closure, build it -- so a tab loading one person and a unit loading fifty run
    the identical path and cannot drift. Written as steps so the reads happen off
    the main thread: what a member IS costs table queries, and what it is MADE of
    costs a bundle closure, and neither has any business freezing the window."""
    loadable = yield command.Read(
        lambda: resolve([member], level).get(member.get("key") or ""), 0.3)
    if loadable is None:
        return Assembled(warnings=[
            "The game states no model for '{0}'.".format(
                member.get("label") or member.get("key") or "?")])
    resolved = yield command.Read(
        lambda: loading.resolve_closure(loadable.cabs), 0.75)
    yield command.Mark(0.85)
    return build(context, loadable, resolved)


def forget():
    """Drop what one session cached; an install's answers are not the next one's."""
    _CHARACTER_MODELS.clear()
    _NPC_INFO.clear()
    _NAMED.clear()
    _SLOTS.clear()
