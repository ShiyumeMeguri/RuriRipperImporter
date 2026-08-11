"""Browse the game's cast the way the game itself lists it.

The rows come from the game's own config containers (see ``roster``): playable
characters keyed by charId and grouped by the game's own profession, and npcs
collapsed to one row per distinct model prefab. Names are the real localized
names, in whichever language Blender is running in --
``bpy.app.translations.locale`` picks which text container the C# side joins
through, so switching Blender's language switches the roster with no reload of
anything else.

The list behaves like the bundle browser next door: type to filter, click to
select, and one button reveals where the selection lives over in that browser.
"""

from __future__ import annotations

import json
import re

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       IntProperty, StringProperty)

from ... import filter_ui
from ...ruri_pybridge.session import cabmap_state
from . import asset_paths, roster

CHARACTERS = roster.CHARACTERS
NPCS = roster.NPCS

# Column labels worth spelling out; anything else reads as its own column name,
# so a column added to roster.py's declarations shows up here with no edit.
_FIELD_LABELS = {"key": "Id", "display": "Name", "english": "English", "group": "Profession",
                 "npc": "Npc Id", "template": "Template"}


def _filter_fields():
    """The rule vocabulary = the columns the CURRENTLY loaded roster table has.
    Characters and NPCs are different projections, so their filterable fields
    genuinely differ -- read off the table, never tabulated.

    The displayed NAME leads, because the first field is the one a new rule
    starts on (see filter_ui.FilterSpec). Table order would put the row key
    first -- column 0 of any projection is its key -- which defaults the filter
    to an id nobody has memorised."""
    state = getattr(bpy.context.scene, "ruri_roster", None)
    table = _rows(state) if state is not None else None
    if table is None:
        return (("display", "Name"),)
    names = sorted(table.names, key=lambda name: 0 if name == "display" else 1)
    return tuple((name, _FIELD_LABELS.get(name, name.replace("_", " ").title()))
                 for name in names)


ROSTER_FILTER_SPEC = filter_ui.register_spec(filter_ui.FilterSpec(
    key="EndField:character", fields=_filter_fields,
    state_for=lambda context: context.scene.ruri_roster,
    apply=lambda context: _rebuild(context.scene.ruri_roster)))

# Loaded row lists, by (kind, language). Module scope, not scene state: rebuilding
# the drawn list must not cost a re-read, and a plain list is not something
# Blender's property system can hold anyway.
_ROWS = {}


def _report(operator, message, level="WARNING"):
    operator.report({level}, message)


class RURI_PG_roster_entry(bpy.types.PropertyGroup):
    """One drawn line: either a group header or a cast member."""
    label: StringProperty()
    key: StringProperty()
    group: StringProperty()
    detail: StringProperty()
    is_group: BoolProperty(default=False)
    row_index: IntProperty(default=-1)


def _on_filter_edit(self, context):
    _rebuild(self)


def _on_kind_change(self, context):
    """Switching cast only redraws; it never fires the refresh operator. An
    operator called from a property update runs with the UI mid-update, and its
    poll failing there raises rather than reporting."""
    state = context.scene.ruri_roster
    if _rows(state) is None:
        state.entries.clear()
        state.status = "Refresh to read the {0} out of the game's tables.".format(state.kind)
        return
    _rebuild(state)


class RURI_PG_roster(filter_ui.FilterStateMixin, bpy.types.PropertyGroup):
    FILTER_SPEC_KEY = "EndField:character"

    kind: EnumProperty(
        name="Cast",
        items=[(CHARACTERS, "Characters", "Playable characters, grouped by the game's own profession"),
               (NPCS, "NPCs", "Non-playable cast, one row per distinct model prefab")],
        default=CHARACTERS,
        update=_on_kind_change)
    search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"}, update=_on_filter_edit,
                           description="Filter by displayed name, id or group")
    entries: CollectionProperty(type=RURI_PG_roster_entry)
    active_index: IntProperty()
    status: StringProperty(default="Load a cabmap, then refresh the roster.")
    language: StringProperty(default="")

    load_expressions: BoolProperty(
        name="Expressions",
        default=False,
        description="Also load this character's SkeletalMorph expression library. "
                    "Off by default: it is a separate, much larger asset family than the model")
    lod: IntProperty(
        name="LOD",
        default=0, min=0, max=3,
        description="Detail level to load. 0 is the full-detail model")
    model_kind: EnumProperty(
        name="Model",
        items=[("postmodel", "Post", "The in-world actor model"),
               ("uimodel", "UI", "The model menus and portraits pose")],
        default="postmodel",
        description="Which of the game's own model families to import")


def _language(state):
    """The game language this roster is shown in -- Blender's own locale,
    mapped onto the languages the game ships."""
    return roster.language_for_locale(bpy.app.translations.locale)


def _rows(state):
    return _ROWS.get((state.kind, _language(state)))


_BUILDABLE = {}


def context_game_root():
    return bpy.context.scene.ruri_cabmap.game_root


def _buildable(game_root):
    """Cached: the manifest is one small file, but a redraw must not re-read it."""
    if game_root not in _BUILDABLE:
        try:
            _BUILDABLE[game_root] = roster.buildable_templates(cabmap_state.BRIDGE, game_root)
        except Exception:
            _BUILDABLE[game_root] = None
    return _BUILDABLE[game_root]


def _rebuild(state):
    """Rebuild the drawn line list.

    The filter is NOT evaluated here: the search text and the Include/Exclude
    rules go to the same C# engine the bundle browser searches with, over the very
    buffers this table was built from (one ASCII fold per column, then a parallel
    vectorized sweep, then the shared rule evaluator). This side receives row ids
    and reads cells."""
    state.entries.clear()
    table = _rows(state)
    if table is None:
        return
    matched = cabmap_state.BRIDGE.search_data_table(table, state.search.strip(),
                                                    state.filter_rules)
    rows = [roster.row(table, int(index), state.kind) for index in matched]
    if state.kind == NPCS:
        # Row ids index the WHOLE projected table, which is what the C# search
        # returns them against -- so the unbuildable ones are dropped here rather
        # than by subsetting the table out from under the search.
        buildable = _buildable(context_game_root())
        if buildable is not None:
            rows = [row for row in rows if row["key"] in buildable]
    rows.sort(key=lambda row: (row["group"], row["label"]))

    counts = {}
    for row in rows:
        counts[row["group"]] = counts.get(row["group"], 0) + 1

    current_group = None
    for index, row in enumerate(rows):
        if row["group"] and row["group"] != current_group:
            current_group = row["group"]
            header = state.entries.add()
            header.label = "{0}  ({1})".format(current_group, counts[current_group])
            header.group = current_group
            header.is_group = True
        entry = state.entries.add()
        entry.label = row["label"]
        entry.key = row["key"]
        entry.group = row["group"]
        entry.detail = row["detail"]
        entry.row_index = index
    state.status = "{0} of {1} {2} · {3}".format(
        len(rows), table.row_count, state.kind, _language(state))
    if state.active_index >= len(state.entries):
        state.active_index = 0


def _selected(state):
    if 0 <= state.active_index < len(state.entries):
        entry = state.entries[state.active_index]
        if not entry.is_group:
            return entry
    return None


class RURI_UL_roster(bpy.types.UIList):
    bl_idname = "RURI_UL_roster"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if item.is_group:
            row = layout.row()
            row.enabled = False
            row.label(text=item.label, icon="OUTLINER_COLLECTION")
            return
        row = layout.row(align=True)
        row.label(text=item.label, icon="OUTLINER_OB_ARMATURE")
        # The game's own id, dimmed: with several rows sharing a display name it
        # is the only thing that tells them apart. Skipped when the name already
        # IS the id, so nothing is printed twice.
        identifier = row.row()
        identifier.enabled = False
        identifier.label(text="" if item.key == item.label else "({0})".format(item.key))
        sub = row.row()
        sub.alignment = "RIGHT"
        sub.label(text=item.detail)

    def filter_items(self, context, data, propname):
        # Filtering already happened in _rebuild, against the game's own fields
        # rather than the drawn string -- leave the list untouched.
        return [], []


class RURI_OT_roster_refresh(bpy.types.Operator):
    """Read the cast out of the game's own config containers."""
    bl_idname = "ruri.roster_refresh"
    bl_label = "Refresh Roster"
    bl_description = "Read the character/npc roster out of the game's own data tables"

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = context.scene.ruri_roster
        game_root = context.scene.ruri_cabmap.game_root
        language = _language(state)
        state.language = language
        try:
            rows = roster.load(cabmap_state.BRIDGE, game_root, language, state.kind)
        except Exception as exc:
            state.status = "{0}: {1}".format(type(exc).__name__, exc)
            _report(self, state.status)
            return {"CANCELLED"}
        _ROWS[(state.kind, language)] = rows
        _rebuild(state)
        return {"FINISHED"}


def _prefab_rows(query, model_kind):
    """The cabmap rows for the ONE prefab named ``<query>_<model_kind>``.

    ``query`` is the id the GAME keys the row by, ``model_kind`` one of the
    game's own model families ("postmodel" = in-world actor, "uimodel" = the one
    menus pose). Matching is EXACT on the file stem, never a substring:

    a substring match reads as "one character" but resolves to every asset whose
    path happens to carry the word -- "chr_0004_pelica" also lives inside
    "abilityentity_chr_0004_pelica_ultimate_skill_postmodel". Each extra hit
    drags its own dependency closure into the load, and the closures are what
    make an import eat the whole game instead of one character. Exact stem or
    nothing; a caller with no ``model_kind`` has no business importing.

    One row per distinct path: the same prefab is listed by every bundle that
    carries it, and importing it thirty times is thirty times the work for the
    same result. Returns [(row index, container path)]."""
    if not model_kind:
        raise ValueError("_prefab_rows needs a model_kind -- substring import was removed")
    cabmap_state.apply_filter(query)
    wanted = "{0}_{1}".format(query.lower(), model_kind)
    by_path = {}
    for row in cabmap_state.VISIBLE:
        for path_index in range(cabmap_state.ROWS.container_path_count(row)):
            path = cabmap_state.ROWS.container_path(row, path_index)
            low = path.lower()
            if not low.endswith(".prefab") or low in by_path:
                continue
            if low.rsplit("/", 1)[-1][:-len(".prefab")] == wanted:
                by_path[low] = (row, path)
    return sorted(by_path.values(), key=lambda hit: hit[1])


# charId -> {"model", "tag", "asset"}, read once per session from the game's own
# character data assets. Module scope for the same reason the tables are.
_CHARACTER_MODELS = {}


def _character_model(character_id):
    """The model prefab the game itself declares for a character, or "" when its
    data asset is not in the loaded cabmap.

    No config table carries a model field (all 693 were swept), so
    ``gamedata/characterdata/data_chr_*.asset`` is the only source. Read as one
    batch: they share a handful of bundles, so paying per character would mean
    re-resolving the same closure thirty times."""
    if not _CHARACTER_MODELS:
        cabs = set()
        cabmap_state.apply_filter("gamedata/characterdata/data_chr_")
        for row in cabmap_state.VISIBLE:
            for path_index in range(cabmap_state.ROWS.container_path_count(row)):
                path = cabmap_state.ROWS.container_path(row, path_index).lower()
                if "gamedata/characterdata/data_chr_" in path and path.endswith(".asset"):
                    cabs.add(cabmap_state.ROWS.cab(row))
        if cabs:
            _CHARACTER_MODELS.update(cabmap_state.BRIDGE.read_character_models(sorted(cabs)))
    return _CHARACTER_MODELS.get(character_id, {}).get("model", "")


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


def _prefab_named(name):
    """The importable rows for one part of an assembled npc.

    A part manifest names the part, not the asset that holds it, and the game
    ships parts in two shapes: some as a generated prefab
    (``.../generated/.../prefabs/p_<part>_static.prefab``), the rest only as the
    authored skinned mesh (``arts/entity/npc/.../models/sk_<part>.fbx``). Both
    are the same part; which one exists is the game's choice, so both are
    accepted and prefabs win when a part has both.

    The leading kind letter is the game's own asset-kind prefix (``p_`` prefab,
    ``sk_`` skinned mesh, ``m_`` material, ``t_`` texture), so the part id is the
    stem underneath it -- matched exactly, never as a substring."""
    core = name.lower()
    if core.startswith("p_"):
        core = core[2:]
    cabmap_state.apply_filter(core)
    prefabs = {}
    meshes = {}
    for row in cabmap_state.VISIBLE:
        for path_index in range(cabmap_state.ROWS.container_path_count(row)):
            path = cabmap_state.ROWS.container_path(row, path_index)
            low = path.lower()
            leaf = low.rsplit("/", 1)[-1]
            stem, _, extension = leaf.rpartition(".")
            if extension == "prefab" and (
                    stem == core or stem == "p_" + core
                    or stem.startswith(core + "_") or stem.startswith("p_" + core + "_")):
                prefabs[low] = (row, path)
            elif extension == "fbx" and stem in (core, "sk_" + core):
                meshes[low] = (row, path)
    found = prefabs or meshes
    if found:
        return sorted(found.values(), key=lambda hit: hit[1])

    # The trailing number on a part id picks the MATERIAL variant, not the mesh:
    # "..._body_rfg_a_04" is shipped as m_..._rfg_a_04.mat over whichever mesh the
    # family has (sk_..._rfg_a_01.fbx / _02.fbx -- there is no _04 mesh). So when
    # the exact index has no asset, the part is that family's mesh.
    family = re.sub(r"_\d+$", "", core)
    if family == core:
        return []
    cabmap_state.apply_filter(family)
    siblings = {}
    for row in cabmap_state.VISIBLE:
        for path_index in range(cabmap_state.ROWS.container_path_count(row)):
            path = cabmap_state.ROWS.container_path(row, path_index)
            low = path.lower()
            leaf = low.rsplit("/", 1)[-1]
            stem, _, extension = leaf.rpartition(".")
            if extension == "fbx" and re.fullmatch(re.escape("sk_" + family) + r"_\d+", stem):
                siblings[low] = (row, path)
    return sorted(siblings.values(), key=lambda hit: hit[1])[:1]


def _asset_named(stem):
    """Cabmap rows whose asset leaf IS this name, any extension."""
    cabmap_state.apply_filter(stem)
    wanted = stem.lower()
    found = {}
    for row in cabmap_state.VISIBLE:
        for path_index in range(cabmap_state.ROWS.container_path_count(row)):
            path = cabmap_state.ROWS.container_path(row, path_index)
            low = path.lower()
            if low.rsplit("/", 1)[-1].rsplit(".", 1)[0] == wanted:
                found[low] = (row, path)
    return sorted(found.values(), key=lambda hit: hit[1])



def _avatar_skeleton(db, avatar_file):
    """``(world_rests, paths)`` from an Avatar UnityFile in ``db`` -- the standing
    rest a shared-skeleton part mesh bakes against, plus its transform paths."""
    from ...ruri_pybridge.unity import avatar
    avatar_doc = avatar_file.first("Avatar") if avatar_file is not None else None
    if avatar_doc is None:
        return {}, []
    return avatar.skeleton_world_rests(avatar_doc.data), avatar.transform_paths(avatar_doc.data)


def _templet_skeleton(info):
    """``(world_rests, paths, leaf_names)`` for an npc's shared skeleton, read from
    the avatar template the manifest names.

    The template's ``sizeAvatar`` carries the whole skeleton's STANDING world rest
    (m_AvatarSkeleton + m_AvatarSkeletonPose) AND its ``m_TOS`` name/parent table
    -- the authoritative pose a part mesh is bind-baked against, exactly what a
    shipped rig gets from its prefab transform hierarchy. ``bonePathsStr`` is kept
    as leaf-name vocabulary for any part-specific bone the table does not name."""
    templet = (info.get("avatar_templet") or "").rsplit("/", 1)[-1]
    if not templet:
        return {}, [], []
    rows = _asset_named("data_npc_avatartemplet_" + templet.lower())
    if not rows:
        return {}, [], []
    from ...ruri_pybridge.unity import bridge_asset_db
    try:
        assets, _r, _s, _c, _sc = cabmap_state.BRIDGE.import_cabs(
            [cabmap_state.ROWS.cab(rows[0][0])])
    except Exception:
        return {}, [], []
    db = bridge_asset_db.BridgeAssetDatabase(
        assets, asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid)
    for guid in db.all_guids():
        mono = db.load_guid(guid)
        doc = mono.first("MonoBehaviour") if mono is not None else None
        if doc is None or "bonePathsStr" not in (doc.data or {}):
            continue
        leaves = [str(name) for name in (doc.data.get("bonePathsStr") or [])]
        size_ref = doc.data.get("sizeAvatar")
        world_rests, paths = {}, []
        if isinstance(size_ref, dict) and size_ref.get("guid"):
            world_rests, paths = _avatar_skeleton(db, db.load_guid(size_ref["guid"]))
        return world_rests, paths, leaves
    return {}, [], []


def _npc_materials(context, info, template_id):
    """{mesh name: [material container path]} for one npc template, from the
    game's own assembly table.

    An npc's colours are stated by its TEMPLATE (a material code per renderer),
    never by its parts -- the same part takes different materials under different
    templates, and a part's trailing number is its own index, not a material's.
    So the codes have to be resolved against the family's shared table
    (``data_npc_avatarmesh_<leaf>``, named by the manifest's avatarMeshName) --
    which the C# side does, codes and path hashes being binary."""
    leaf = (info.get("avatar_mesh") or "").rsplit("/", 1)[-1]
    if not leaf:
        return {}
    rows = _asset_named("data_npc_avatarmesh_" + leaf.lower())
    if not rows:
        return {}
    try:
        assigned = cabmap_state.BRIDGE.npc_materials(
            [cabmap_state.ROWS.cab(rows[0][0])],
            roster.vfs_roots(context.scene.ruri_cabmap.game_root), template_id)
    except Exception:
        return {}
    return {row["mesh"].lower(): row["materials"] for row in assigned}


def _import_part(context, cab, binder, options, level, materials_by_mesh=None):
    """Import one part's best-LOD meshes, each baked onto the shared rig the binder
    is growing. Works for every part kind -- a body/hair/tail fbx or a face/ear
    _variant prefab -- since each is a skinned mesh with a bind pose and its own
    avatar. One cab's own closure at a time. True when anything built.

    ``materials_by_mesh`` maps a mesh's own name to the container paths the
    template dresses it in (see _npc_materials); those materials live in their
    own CABs, so they are co-seeded into this part's closure rather than looked
    for inside it."""
    from ... import material_builder, prefab_importer
    from ...ruri_pybridge.unity import bridge_asset_db, discovery
    material_paths = sorted({path for paths in (materials_by_mesh or {}).values() for path in paths})
    seeds = [cab]
    if material_paths:
        try:
            seeds.extend(c for c in cabmap_state.BRIDGE.resolve_cabs_for_paths(material_paths)
                         if c != cab)
        except Exception:
            pass
    try:
        assets, _r, _s, _c, _sc = cabmap_state.BRIDGE.import_cabs(seeds)
    except Exception:
        return False
    db = bridge_asset_db.BridgeAssetDatabase(
        assets, asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid)
    # This part's own Avatar supplies the standing rest for any bone it alone
    # introduces (the base skeleton already came from the template avatar).
    for guid in db.all_guids():
        part_file = db.load_guid(guid)
        if part_file is not None and part_file.first("Avatar") is not None:
            binder.add_skeleton(*_avatar_skeleton(db, part_file))
            break

    mat_builder = (material_builder.MaterialBuilder(db, options)
                   if materials_by_mesh and options["import_materials"] else None)
    material_index = discovery.material_name_index(db) if mat_builder is not None else {}

    imported = False
    for guid in _loose_part_mesh_guids(db, level):
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
    materials = []
    for path in (materials_by_mesh or {}).get(mesh_name.lower(), ()):
        guid = material_index.get(asset_paths.expected_mesh_name(path))
        if guid is None:
            continue
        material = mat_builder.build_from_ref({"guid": guid})
        if material is not None:
            materials.append(material)
    return materials


def _loose_part_mesh_guids(db, level):
    """The Mesh guids to import from a loose part's closure: the best-ranked LOD
    per mesh family, so one detail level lands rather than the lod0..lod3 the
    closure ships all stacked at the origin."""
    from ...ruri_pybridge.unity import discovery
    families = {}
    for guid in db.all_guids():
        text = db.raw_text(guid)
        if not text:
            continue
        class_name, name = discovery.peek_class_and_name(text)
        if class_name != "Mesh":
            continue
        families.setdefault(asset_paths.lod_family_stem(name or guid), []).append((guid, name or guid))
    chosen = []
    for members in families.values():
        ranked = [(asset_paths.lod_rank(name), guid) for guid, name in members]
        exact = [guid for rank, guid in ranked if rank == level]
        if exact:
            chosen.extend(exact)
            continue
        best = min((rank for rank, _guid in ranked), key=lambda rank: (rank < 0, abs(rank - level)))
        chosen.extend(guid for rank, guid in ranked if rank == best)
    return chosen



def _model_parts(context, entry):
    """(manifest, [(part, row, path)], [unresolved part]) for an npc row.

    A part is taken as ONE asset: the game publishes some parts as a generated
    prefab and the rest only as the authored skinned mesh, and where both a
    ``_variant`` and a ``_static`` prefab exist the variant is the one skinned
    onto the shared skeleton -- the static one is a standalone posed copy, which
    is what lands unparented with no armature modifier.

    The avatar template is NOT an import row: it is read only for its skeleton
    paths + leaf names (see _templet_skeleton), which the assembler feeds to a
    SkeletonBinder to rebuild the shared skeleton the loose meshes hash against."""
    roots = roster.vfs_roots(context.scene.ruri_cabmap.game_root)
    try:
        info = cabmap_state.BRIDGE.npc_prefab_parts(roots, entry.key)
    except Exception:
        return None, [], []
    hits = []
    missing = []
    for part in info["parts"]:
        candidates = _prefab_named(part)
        if not candidates:
            missing.append(part)
            continue
        skinned = [hit for hit in candidates if "_variant." in hit[1].lower()]
        row, path = (skinned or candidates)[0]
        hits.append((part, row, path))
    return info, hits, missing



def _for_cast(hits, kind):
    """When the same model is published under more than one folder, take the one
    belonging to the cast being browsed -- the game files a character's actor
    model under ``.../characters/`` and its npc-usage copy under ``.../npc/``,
    and importing both is the same geometry twice."""
    folder = "/characters/" if kind == CHARACTERS else "/npc/"
    preferred = [hit for hit in hits if folder in hit[1].lower()]
    return preferred or hits


def _at_detail_level(hits, level):
    """The hits at the requested detail level, or -- when the model simply is
    not authored at that level -- the closest one it does have, reported rather
    than silently substituted. ``asset_paths.lod_rank`` is the game's own
    suffix convention, already used by the scene importer."""
    ranked = [(asset_paths.lod_rank(path), row, path) for row, path in hits]
    exact = [(row, path) for rank, row, path in ranked if rank == level]
    if exact:
        return exact, level
    # -1 is "no LOD suffix at all", i.e. a single-detail model: as good as LOD0.
    best = min((rank for rank, _, _ in ranked), key=lambda rank: (rank < 0, abs(rank - level)))
    return [(row, path) for rank, row, path in ranked if rank == best], best


class RURI_OT_roster_load(bpy.types.Operator):
    """Import the selected cast member's model prefab.

    Deliberately not its own importer: it resolves the row to prefab CABs, puts
    them in the browser's own selection, and runs the browser's own import. One
    import path, so a fix there is a fix here."""
    bl_idname = "ruri.roster_load"
    bl_label = "Load Model"
    bl_description = "Import this one's model prefab, exactly as the bundle browser would"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.scene.ruri_cabmap.loaded
                and cabmap_state.BRIDGE is not None
                and _selected(context.scene.ruri_roster) is not None)

    def execute(self, context):
        state = context.scene.ruri_roster
        entry = _selected(state)
        if entry is None:
            return {"CANCELLED"}

        if state.kind == NPCS:
            return self._load_npc(context, state, entry)

        # The game states a character's model prefab in its own data asset; only
        # the UI model is named by convention, because no asset declares one.
        model = _character_model(entry.key)
        if model and state.model_kind == "postmodel":
            hits = _prefab_named(model)
            if hits:
                return self._import(context, state, entry, _for_cast(hits, state.kind), model)
            self.report({"WARNING"}, "'{0}' declares model '{1}', which is not in the cabmap.".format(
                entry.label, model))
            return {"CANCELLED"}

        hits = _prefab_rows(entry.key, state.model_kind)
        if not hits:
            # 只报另一个 model family(同样精确名),不做子串扫描——诊断不值得把闭包炸开。
            other = "uimodel" if state.model_kind == "postmodel" else "postmodel"
            alt = _prefab_rows(entry.key, other)
            self.report({"WARNING"},
                        "'{0}' has no {1}_{2} prefab; it does have the {3} one.".format(
                            entry.label, entry.key, state.model_kind, other)
                        if alt else
                        "No prefab named '{0}_{1}' in the loaded cabmap.".format(
                            entry.key, state.model_kind))
            return {"CANCELLED"}

        return self._import(context, state, entry, _for_cast(hits, state.kind), "")

    def _import(self, context, state, entry, hits, declared):
        """Put the resolved rows in the browser's own selection and run its own
        import. ``declared`` names the prefab the game itself asked for, when one
        did, so the report says where the choice came from."""
        chosen, level = _at_detail_level(hits, state.lod)
        cabmap_state.clear_selection()
        for row, _path in chosen:
            cabmap_state.SELECTED_CABS.add(cabmap_state.ROWS.cab(row))

        result = bpy.ops.ruri.import_selected()
        if "FINISHED" not in result:
            return {"CANCELLED"}
        if level != state.lod:
            self.report({"INFO"}, "'{0}' is not authored at LOD{1}; loaded LOD{2}.".format(
                entry.label, state.lod, level))
        elif declared:
            self.report({"INFO"}, "Loaded '{0}' -- the model its own data asset declares.".format(declared))

        if state.load_expressions:
            self._load_expressions(context, entry)
        return {"FINISHED"}

    def _load_npc(self, context, state, entry):
        """An npc is assembled, not shipped: its template's manifest lists the
        part prefabs (body, face, hair, ear, tail), and a named actor's manifest
        lists one whole prefab instead. The terminal state is ONE armature named
        after the model template, with every part parented and skinned onto it."""
        from ... import armature_builder

        info, hits, missing = _model_parts(context, entry)
        if info is None:
            self.report({"WARNING"}, "'{0}' ({1}) has no part manifest -- the game ships no "
                                     "assembled model for it.".format(entry.label, entry.key))
            return {"CANCELLED"}
        if not hits:
            self.report({"WARNING"}, "None of {0}'s {1} part(s) are in the loaded cabmap.".format(
                entry.label, len(info["parts"])))
            return {"CANCELLED"}

        options = context.scene.ruri_cabmap.as_options()
        # A named actor (manifest carries correspondingCharId) ships one complete
        # standing prefab and takes the prefab path; a generic npc is assembled
        # from parts that share the template skeleton and takes the binder path.
        named_actor = bool(info.get("character_id"))
        world_rests, paths, leaf_names = ({}, [], []) if named_actor else _templet_skeleton(info)
        if world_rests:
            # Assembled npc: every part is a skinned mesh sharing one skeleton
            # (the template avatar's standing pose). Each part -- body/hair/tail
            # authored standing, face/ear authored at their own origin -- is baked
            # onto ONE binder rig, its own bones aligned onto the shared skeleton.
            binder = armature_builder.SkeletonBinder(entry.key, world_rests, paths, leaf_names)
            materials_by_mesh = _npc_materials(context, info, entry.key)
            imported_any = False
            for _part, row, _path in hits:
                if _import_part(context, cabmap_state.ROWS.cab(row), binder, options, state.lod,
                                materials_by_mesh):
                    imported_any = True
        else:
            # Named actor: one complete standing prefab (no shared-skeleton
            # template) -- the browser's own hierarchy import, grafting any extra
            # roots into the first so it ends on one armature.
            imported_any = False
            before = {o for o in context.scene.objects if o.type == "ARMATURE"}
            for _part, row, _path in hits:
                cabmap_state.clear_selection()
                cabmap_state.SELECTED_CABS.add(cabmap_state.ROWS.cab(row))
                if "FINISHED" in bpy.ops.ruri.import_selected():
                    imported_any = True
            new_arms = [o for o in context.scene.objects
                        if o.type == "ARMATURE" and o not in before]
            for arm in new_arms[1:]:
                armature_builder.graft_armature(context, new_arms[0], arm)

        if not imported_any:
            return {"CANCELLED"}
        if missing:
            self.report({"WARNING"}, "{0} of {1} part(s) are not in the cabmap: {2}".format(
                len(missing), len(info["parts"]), ", ".join(missing)))
        else:
            self.report({"INFO"}, "Assembled '{0}' from {1} part(s).".format(
                entry.label, len(info["parts"])))
        if state.load_expressions:
            self._load_expressions(context, entry)
        return {"FINISHED"}

    def _load_expressions(self, context, entry):
        """The face library is a separate asset family, so it is a separate
        import -- the existing Character-tab flow, driven rather than copied."""
        if bpy.ops.ruri.character_scan.poll():
            bpy.ops.ruri.character_scan()
        if bpy.ops.ruri.character_load_library.poll():
            bpy.ops.ruri.character_load_library()
        else:
            self.report({"WARNING"},
                        "Model loaded, but no rig was found to bind '{0}'s expressions to.".format(entry.label))


# ── 动画:锚点是容器路径,不是名字 ────────────────────────────────────────────
# 游戏把动画归在与模型完全不同的一棵树下(模型 prefab 在
# dynamicassets/gameplay/actors/postmodels/,动画在 arts/entity/actor/),所以
# reveal 那套"取模型所在目录"在这里无效,得另立锚点。EndField_1.4.4 cabmap 实测:
#   角色本体      arts/entity/actor/<体型组>/<短名>/animations/<类别>/
#                 类别 = dialog / 3c / battle / customized / ui / interact
#   行人 NPC      自己没有 animations 目录,跑体型组共享库
#                 arts/entity/actor/<体型组>/common/animations/
#   物件/动物 NPC arts/entity/npc/<类>/<模板>/animations/
# 按名字搜会被对话表情 morph 淹没 —— pelica 名下 2403 条 AnimationClip 里绝大多数是
# dialog/timeline/dlgtl_*/morphanim/*_npc_chr_0004_pelica_N.anim(嘴型/表情,不是身体
# 动画);锚到 "<标识>/animations/" 则 431 行命中 431 行是本体动画,零噪音。
_ANIM_ANCHOR = "{0}/animations/"
_SHARED_ANIM_ANCHOR = "actor/{0}/common/animations/"


def _ANIM_RULES(anchor):
    """按钮装进浏览器的 Include 规则集(全部成立才显示 —— 规则编辑器的 AND 语义)。
    搜索框刻意留空:规则是这个按钮的查询,搜索框留给使用者在其上再缩小范围
    (打 "battle" 就只剩战斗动画)。"""
    return [
        {"field": "container", "relation": "contains", "value": anchor, "action": "include"},
        {"field": "type_names", "relation": "contains", "value": "AnimationClip", "action": "include"},
    ]
_ACTOR_TREE = "/arts/entity/actor/"
_PEDESTRIAN_TREE = "/pedestrain/"


def _short_name(key):
    """角色 id → 动画树用的短名:chr_0004_pelica -> pelica。目录段与片段名都用它,
    完整 id 只出现在 postmodel prefab 那棵树里。"""
    return re.sub(r"^chr_\d+_", "", key.lower())


def _anim_hits(anchor):
    """锚点在 cabmap 里的命中行数(走 C# 快搜引擎,全表向量化)。"""
    cabmap_state.apply_filter(anchor)
    return len(cabmap_state.VISIBLE)


def _segment_after(marker, needle, want_index):
    """在命中 needle 的行里找出 marker 之后的第 want_index 段 —— 体型组是从这一位
    自己的资产路径上读出来的,不是从名字猜的(模板名与目录会不一致:npc_fattym_* 的
    prefab 就住在 pedestrain/fatty/ 下)。"""
    cabmap_state.apply_filter(needle)
    for row in cabmap_state.VISIBLE:
        for path_index in range(cabmap_state.ROWS.container_path_count(row)):
            low = cabmap_state.ROWS.container_path(row, path_index).lower()
            _head, found, tail = low.partition(marker)
            if not found:
                continue
            segments = tail.split("/")
            if len(segments) > want_index:
                return segments[want_index]
    return ""


def _body_group(kind, key, short):
    """这一位的体型组(girl/lady/gentleman/…),即共享动画库那一段。"""
    if kind == CHARACTERS:
        # arts/entity/actor/<组>/<短名>/... —— 只认第二段真是这个角色的那条路径。
        cabmap_state.apply_filter(short)
        for row in cabmap_state.VISIBLE:
            for path_index in range(cabmap_state.ROWS.container_path_count(row)):
                low = cabmap_state.ROWS.container_path(row, path_index).lower()
                _head, found, tail = low.partition(_ACTOR_TREE)
                if not found:
                    continue
                segments = tail.split("/")
                if len(segments) > 1 and segments[1] == short:
                    return segments[0]
        return ""
    group = _segment_after(_PEDESTRIAN_TREE, key, 0)
    if group:
        return group
    parts = key.lower().split("_")
    return parts[1] if len(parts) > 1 else ""


class RURI_OT_roster_animations(bpy.types.Operator):
    """List this one's animation clips over in the bundle browser.

    Anchored on the container path, not the name: the id also keys thousands of
    per-line dialogue morph clips, and a name search buries the body animation
    library under them. Falls back to the body-type group's shared library when
    this one ships no animation folder of its own -- said out loud in the report
    rather than substituted silently, because "these are not hers" matters."""
    bl_idname = "ruri.roster_animations"
    bl_label = "Find Animations"
    bl_description = ("Switch to the bundle browser and list this one's animation clips "
                      "(body animations, not the per-line dialogue morphs)")

    @classmethod
    def poll(cls, context):
        return (context.scene.ruri_cabmap.loaded
                and cabmap_state.BRIDGE is not None
                and _selected(context.scene.ruri_roster) is not None)

    def execute(self, context):
        state = context.scene.ruri_roster
        entry = _selected(state)
        if entry is None:
            return {"CANCELLED"}

        short = _short_name(entry.key) if state.kind == CHARACTERS else entry.key.lower()
        anchor = _ANIM_ANCHOR.format(short)
        hits = _anim_hits(anchor)
        note = "{0}: {1} animation rows.".format(entry.label, hits)

        if not hits:
            group = _body_group(state.kind, entry.key, short)
            anchor = _SHARED_ANIM_ANCHOR.format(group) if group else ""
            hits = _anim_hits(anchor) if anchor else 0
            if not hits:
                self.report({"WARNING"}, "No animation folder for '{0}' in the loaded cabmap.".format(
                    entry.label))
                return {"CANCELLED"}
            # 换的是**别人的**动画库,必须说出来,不能静默替换。
            note = ("'{0}' ships no animations of its own; showing the {1} body-type "
                    "library it actually plays ({2} rows).".format(entry.label, group, hits))

        self.report({"INFO"}, note)
        return bpy.ops.ruri.cabmap_show_rules(rules=json.dumps(_ANIM_RULES(anchor)))


class RURI_OT_roster_reveal(bpy.types.Operator):
    """Open where the selected cast member lives, over in the bundle browser.

    The query is the id the game itself keys that row by -- no path convention
    of ours is involved."""
    bl_idname = "ruri.roster_reveal"
    bl_label = "Open Containing Folder"
    bl_description = "Switch to the bundle browser and open where this one's assets live"

    @classmethod
    def poll(cls, context):
        return _selected(context.scene.ruri_roster) is not None

    def execute(self, context):
        state = context.scene.ruri_roster
        entry = _selected(state)
        if entry is None:
            return {"CANCELLED"}
        # The folder of the model this row actually loads -- not a text search,
        # which lands on every asset whose name merely contains the id.
        if state.kind == CHARACTERS:
            found = [(row, path) for row, path in
                     _for_cast(_prefab_rows(entry.key, state.model_kind), CHARACTERS)]
        else:
            found = [(row, path) for _part, row, path in _model_parts(context, entry)[1]]
        if found:
            row, path = found[0]
            folder = path.rpartition("/")[0]
            return bpy.ops.ruri.cabmap_reveal(query=entry.key, folder=folder,
                                              cab=cabmap_state.ROWS.cab(row))
        return bpy.ops.ruri.cabmap_reveal(query=entry.key)


def draw_roster(layout, context):
    state = context.scene.ruri_roster

    head = layout.row(align=True)
    head.prop(state, "kind", expand=True)
    head.operator(RURI_OT_roster_refresh.bl_idname, text="", icon="FILE_REFRESH")

    filter_ui.draw_search_row(layout, state)
    layout.template_list(RURI_UL_roster.bl_idname, "", state, "entries",
                         state, "active_index", rows=10)
    layout.label(text=state.status, icon="INFO")

    entry = _selected(state)
    options = layout.column(align=True)
    options.enabled = entry is not None
    options.row(align=True).prop(state, "model_kind", expand=True)
    row = options.row(align=True)
    row.prop(state, "lod")
    row.prop(state, "load_expressions", toggle=True, icon="SHAPEKEY_DATA")
    options.operator(RURI_OT_roster_load.bl_idname, icon="IMPORT")
    options.operator(RURI_OT_roster_reveal.bl_idname, icon="FILE_FOLDER")
    options.operator(RURI_OT_roster_animations.bl_idname, icon="ANIM_DATA")


_CLASSES = (
    RURI_PG_roster_entry,
    RURI_PG_roster,
    RURI_UL_roster,
    RURI_OT_roster_refresh,
    RURI_OT_roster_load,
    RURI_OT_roster_animations,
    RURI_OT_roster_reveal,
)


def register():
    filter_ui.register_spec(ROSTER_FILTER_SPEC)
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_roster = bpy.props.PointerProperty(type=RURI_PG_roster)


def unregister():
    del bpy.types.Scene.ruri_roster
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _ROWS.clear()
    _BUILDABLE.clear()
    _CHARACTER_MODELS.clear()
