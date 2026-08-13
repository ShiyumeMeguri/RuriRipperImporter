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

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       IntProperty, StringProperty)

from ... import filter_ui
from ...RuriRipperPyBridge.session import cabmap_state
from . import datasets

CHARACTERS = datasets.CHARACTERS
NPCS = datasets.NPCS

# Column labels worth spelling out; anything else reads as its own column name,
# so a column the hook adds to a cast shows up here with no edit.
_FIELD_LABELS = {"key": "Id", "display": "Name", "english": "English", "group": "Profession",
                 "npc": "Npc Id", "template": "Template", "label": "Name", "detail": "Detail",
                 "also": "Also Worn By", "shipped": "Has A Model"}


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
        return (("label", "Name"),)
    names = sorted(table.names, key=lambda name: 0 if name == "label" else 1)
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
    return datasets.language_for_locale(bpy.app.translations.locale)


def _rows(state):
    return _ROWS.get((state.kind, _language(state)))


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
    # A row the game ships no model for gets no Load button, so it is not drawn --
    # offering one would be a lie. Which rows those are is the cast's own column
    # (a cast whose every row is loadable has no such column), and the drop happens
    # here rather than by subsetting the table out from under the search: the row
    # ids come back against the WHOLE table.
    #
    # Ordering reads the two columns it sorts on as WHOLE columns (built once per
    # table, then cached) and materializes cells only for the rows that end up drawn:
    # this cast is every model the game ships, so it is a list in the thousands and a
    # redraw happens per keystroke. The budget is the bundle browser's own.
    labels = table.values("label")
    groups = table.values("group")
    shipped = table.values("shipped") if "shipped" in table.names else None
    order = sorted((int(index) for index in matched if shipped is None or shipped[int(index)]),
                   key=lambda index: (groups[index], labels[index]))
    matched_count = len(order)
    # The whole match set, not a first-N slice: this cast tops out in the low
    # thousands, so it materializes in one go and the list simply SCROLLS. Cutting
    # it at a boundary reads as "the game ships no more of these" -- and cut at 500
    # of 2084 npcs sorted by name, every row on screen was an unnamed id while the
    # named ones sat past the cut.
    rows = [{name: table.cell(index, name) for name in ("key", "label", "detail", "group")}
            for index in order[:cabmap_state.LIST_CAP]]

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
    state.status = "{0} of {1} {2} · {3}{4}".format(
        matched_count, table.row_count, state.kind, _language(state),
        "" if matched_count == len(rows) else
        " · showing {0}, narrow your search to see the rest".format(len(rows)))
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
        language = _language(state)
        state.language = language
        try:
            rows = datasets.cast(state.kind, language)
        except Exception as exc:
            state.status = "{0}: {1}".format(type(exc).__name__, exc)
            _report(self, state.status)
            return {"CANCELLED"}
        _ROWS[(state.kind, language)] = rows
        _rebuild(state)
        return {"FINISHED"}


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
    """``(world_rests, paths, leaf_names)`` for an npc's shared skeleton, read from
    the avatar template the manifest names.

    The skeleton is the Avatar asset the template's own bundle carries: its
    ``m_AvatarSkeleton`` + ``m_AvatarSkeletonPose`` are the whole rig's STANDING
    world rest and its ``m_TOS`` the name/parent table -- the authoritative pose a
    part mesh is bind-baked against, exactly what a shipped rig gets from its
    prefab transform hierarchy. The template MonoBehaviour beside it contributes
    ``bonePathsStr``, the leaf-name vocabulary for any part-specific bone the
    table does not name.

    Both are materialized by their own identity out of a bundle that carries a
    thousand other assets -- never the whole closure."""
    templet = (info.get("avatar_templet") or "").rsplit("/", 1)[-1]
    if not templet:
        return {}, [], []
    stem = "data_npc_avatartemplet_" + templet.lower()
    rows = datasets.named_rows(stem)
    if not rows:
        return {}, [], []
    from ...RuriRipperPyBridge.unity import bridge_asset_db, class_registry
    cab = rows[0]["cab"]
    try:
        graph = cabmap_state.BRIDGE.scan_cabs([cab])
        avatar_id = class_registry.id_for_name("Avatar")
        mono_behaviour_id = class_registry.id_for_name("MonoBehaviour")
        keys = [graph.key(index) for index in graph.indices_of_class(avatar_id)]
        keys += [graph.key(index) for index in graph.find(mono_behaviour_id, stem)]
        if not keys:
            return {}, [], []
        assets, _r, _s, _c, _sc = cabmap_state.BRIDGE.import_cabs(
            [cab], export_asset_keys=sorted(set(keys)))
    except Exception:
        return {}, [], []
    db = bridge_asset_db.BridgeAssetDatabase(
        assets, asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid)

    world_rests, paths, leaves = {}, [], []
    for guid in db.all_guids():
        loaded = db.load_guid(guid)
        if loaded is None:
            continue
        if not world_rests and loaded.first("Avatar") is not None:
            world_rests, paths = _avatar_skeleton(db, loaded)
            continue
        doc = loaded.first("MonoBehaviour")
        if doc is not None and "bonePathsStr" in (doc.data or {}):
            leaves = [str(name) for name in (doc.data.get("bonePathsStr") or [])]
    return world_rests, paths, leaves


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


def _import_part(context, cab, binder, options, wanted_meshes, materials_by_mesh=None):
    """Import exactly the meshes ``wanted_meshes`` names, each baked onto the shared
    rig the binder is growing. One cab's own closure at a time. True when anything
    built.

    The names come from the game's own slot table, so nothing here guesses which
    of a closure's meshes belong to this part or which detail level they are: a
    CAB routinely carries several parts' meshes at every LOD, and picking by name
    pattern imported the wrong ones (or none).

    ``materials_by_mesh`` maps a mesh's own name to the container paths the
    template dresses it in (see _npc_materials); those materials live in their
    own CABs, so they are co-seeded into this part's closure rather than looked
    for inside it."""
    from ... import material_builder, prefab_importer
    from ...RuriRipperPyBridge.unity import bridge_asset_db, discovery
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
    # The raw geometry blobs have to come with it: this game streams/packs its
    # vertex data, so a database without them silently decodes every mesh to zero
    # vertices and the part imports as nothing at all.
    db = bridge_asset_db.BridgeAssetDatabase(
        assets, mesh_blobs=cabmap_state.BRIDGE.mesh_blobs_by_guid,
        asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid)
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



def _model_parts(context, entry, level=0):
    """(manifest, [(cab, [mesh name])], [unresolved part]) for an npc row.

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
        info = datasets.npc_parts(entry.key)
    except Exception:
        return None, [], []
    cab = _avatar_mesh_cab(info)
    if not cab:
        return info, [], list(info["parts"])
    try:
        slots = datasets.npc_meshes([cab])
    except Exception:
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
            hits = datasets.part_rows(model, cast=state.kind)
            if hits:
                return self._import(context, state, entry, hits, model)
            self.report({"WARNING"}, "'{0}' declares model '{1}', which is not in the cabmap.".format(
                entry.label, model))
            return {"CANCELLED"}

        hits = datasets.model_rows(entry.key, state.model_kind, cast=state.kind)
        if not hits:
            # 只报另一个 model family(同样精确名),不做子串扫描——诊断不值得把闭包炸开。
            other = "uimodel" if state.model_kind == "postmodel" else "postmodel"
            alt = datasets.model_rows(entry.key, other, cast=state.kind)
            self.report({"WARNING"},
                        "'{0}' has no {1}_{2} prefab; it does have the {3} one.".format(
                            entry.label, entry.key, state.model_kind, other)
                        if alt else
                        "No prefab named '{0}_{1}' in the loaded cabmap.".format(
                            entry.key, state.model_kind))
            return {"CANCELLED"}

        return self._import(context, state, entry, hits, "")

    def _import(self, context, state, entry, hits, declared):
        """Put the resolved rows in the browser's own selection and run its own
        import. ``declared`` names the prefab the game itself asked for, when one
        did, so the report says where the choice came from."""
        chosen, level = _at_detail_level(hits, state.lod)
        cabmap_state.clear_selection()
        for row in chosen:
            cabmap_state.SELECTED_CABS.add(row["cab"])

        result = bpy.ops.ruri.import_selected()
        if "FINISHED" not in result:
            return {"CANCELLED"}
        # 顶点腿(生成物自带):Fur 壳层位移 + 反壳描边的几何节点 modifier。幂等,全场景扫。
        from ... import material_builder
        try:
            material_builder.apply_vertex_stages()
        except Exception as vertex_error:
            import traceback
            traceback.print_exc()
            # 静默吞掉这里等于整条顶点腿消失而画面毫无痕迹(实锤:按名字点模块的老写法
            # 一改名就 AttributeError 被吞,壳层与描边一起没了还以为是生成器劣化)。
            self.report({"ERROR"}, "顶点腿失败: {0}".format(vertex_error))
        if level != state.lod:
            self.report({"INFO"}, "'{0}' is not authored at LOD{1}; loaded LOD{2}.".format(
                entry.label, state.lod, level))
        elif declared:
            self.report({"INFO"}, "Loaded '{0}' -- the model its own data asset declares.".format(declared))

        if state.load_expressions:
            self._load_expressions(context, entry)
        return {"FINISHED"}

    def _load_npc(self, context, state, entry):
        """An npc is assembled, not shipped: its template's manifest names part
        SLOTS, its avatar-mesh family table says which mesh each slot wears, and
        every one of those meshes is skinned onto the skeleton the avatar template
        carries. The terminal state is ONE armature named after the model
        template, with every mesh parented and skinned onto it."""
        from ... import armature_builder

        info, hits, missing = _model_parts(context, entry, state.lod)
        if info is None:
            self.report({"WARNING"}, "'{0}' ({1}) has no part manifest -- the game ships no "
                                     "assembled model for it.".format(entry.label, entry.key))
            return {"CANCELLED"}
        if not hits:
            self.report({"WARNING"}, "'{0}' resolved none of its {1} part slot(s) to a mesh -- "
                                     "its avatar-mesh table ({2}) is not in the loaded cabmap.".format(
                entry.label, len(info["parts"]), info.get("avatar_mesh") or "unnamed"))
            return {"CANCELLED"}

        options = context.scene.ruri_cabmap.as_options()
        world_rests, paths, leaf_names = _templet_skeleton(info)
        if not world_rests:
            self.report({"WARNING"}, "'{0}' names avatar template '{1}', which is not in the "
                                     "loaded cabmap -- its meshes have no skeleton to bind to.".format(
                entry.label, info.get("avatar_templet") or "none"))
            return {"CANCELLED"}

        # Every mesh a slot names is skinned onto ONE shared skeleton (the template
        # avatar's standing pose): body/hair/tail authored standing, face/ear at
        # their own origin, each aligned onto that skeleton by the binder.
        binder = armature_builder.SkeletonBinder(entry.key, world_rests, paths, leaf_names)
        materials_by_mesh = _npc_materials(context, info, entry.key)
        imported_any = False
        for cab, meshes in hits:
            if _import_part(context, cab, binder, options, meshes, materials_by_mesh):
                imported_any = True
        # The binder builds its rig itself rather than through the prefab
        # importer, so the game identity is stamped at this call site.
        armature_builder.stamp_game(binder.armature, options.get("source_game"))

        if not imported_any:
            return {"CANCELLED"}
        if missing:
            self.report({"WARNING"}, "{0} of {1} part slot(s) resolved to nothing: {2}".format(
                len(missing), len(info["parts"]), ", ".join(missing[:4])))
        else:
            self.report({"INFO"}, "Assembled '{0}' from {1} part slot(s).".format(
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


# 动画锚点、体型组回退与短名规则全在 hook 侧(endfield.character.animations):游戏
# 把动画归在与模型完全不同的一棵树下,按名字搜会被上千条对话表情 morph 淹没,而锚到
# 容器路径则零噪音——这些是这个游戏的目录事实,面板只消费它算出来的锚点。
def _ANIM_RULES(anchor):
    """按钮装进浏览器的 Include 规则集(全部成立才显示 —— 规则编辑器的 AND 语义)。
    搜索框刻意留空:规则是这个按钮的查询,搜索框留给使用者在其上再缩小范围
    (打 "battle" 就只剩战斗动画)。"""
    return [
        {"field": "container", "relation": "contains", "value": anchor, "action": "include"},
        {"field": "type_names", "relation": "contains", "value": "AnimationClip", "action": "include"},
    ]


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

        found = datasets.animation_anchor(entry.key, state.kind)
        if found is None:
            self.report({"WARNING"}, "No animation folder for '{0}' in the loaded cabmap.".format(
                entry.label))
            return {"CANCELLED"}

        # 换成**别人的**动画库时必须说出来,不能静默替换。
        note = ("'{0}' ships no animations of its own; showing the {1} body-type "
                "library it actually plays ({2} rows).".format(entry.label, found["group"], found["hits"])
                if found["group"] else
                "{0}: {1} animation rows.".format(entry.label, found["hits"]))
        self.report({"INFO"}, note)
        return bpy.ops.ruri.cabmap_show_rules(rules=json.dumps(_ANIM_RULES(found["anchor"])))


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
            found = datasets.model_rows(entry.key, state.model_kind, cast=CHARACTERS)
            if found:
                row = found[0]
                return bpy.ops.ruri.cabmap_reveal(query=entry.key, cab=row["cab"],
                                                  folder=row["container"].rpartition("/")[0])
            return bpy.ops.ruri.cabmap_reveal(query=entry.key)

        # An npc's own name reaches no mesh at all (the meshes are named after the
        # art family, not the template), so reveal the first mesh its slot table
        # actually names.
        _info, hits, _missing = _model_parts(context, entry, state.lod)
        if hits:
            cab, meshes = hits[0]
            return bpy.ops.ruri.cabmap_reveal(query=meshes[0], cab=cab)
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
    _CHARACTER_MODELS.clear()
