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

import re

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       IntProperty, StringProperty)

from ...ruri_pybridge.session import cabmap_state
from . import asset_paths, roster

SKELETON_PART = "<skeleton>"

CHARACTERS = roster.CHARACTERS
NPCS = roster.NPCS

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


class RURI_PG_roster(bpy.types.PropertyGroup):
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

    The filter is NOT evaluated here: the search text goes to the same C# engine
    the bundle browser searches with, over the very buffers this table was built
    from (one ASCII fold per column, then a parallel vectorized sweep). This side
    receives row ids and reads cells."""
    state.entries.clear()
    table = _rows(state)
    if table is None:
        return
    matched = cabmap_state.BRIDGE.search_data_table(table, state.search.strip())
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


def _prefab_rows(query, model_kind=""):
    """Every cabmap row whose container path is a .prefab carrying ``query``.

    ``query`` is the id the GAME keys the row by, matched against the game's own
    addressable index -- no path convention of ours is involved. ``model_kind``
    narrows to one of the game's own model families ("postmodel" is the in-world
    actor, "uimodel" the one menus pose), by the suffix the game names them with.

    One row per distinct path: the same prefab is listed by every bundle that
    carries it, and importing it thirty times is thirty times the work for the
    same result. Returns [(row index, container path)]."""
    cabmap_state.apply_filter(query)
    needle = query.lower()
    # The whole leaf must BE the id (plus the family suffix), not merely contain
    # it: "chr_0004_pelica" is also a substring of the ability-entity effect
    # prefab "abilityentity_chr_0004_pelica_ultimate_skill_postmodel", which is
    # not the character and must not be imported as one.
    wanted = "{0}_{1}".format(needle, model_kind) if model_kind else None
    by_path = {}
    for row in cabmap_state.VISIBLE:
        for path_index in range(cabmap_state.ROWS.container_path_count(row)):
            path = cabmap_state.ROWS.container_path(row, path_index)
            low = path.lower()
            if not low.endswith(".prefab") or low in by_path:
                continue
            stem = low.rsplit("/", 1)[-1][:-len(".prefab")]
            if stem == wanted if wanted else needle in low:
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



def _register_skeleton_bone_names(hits):
    """Hand the importer the bone names the game's own avatar templet declares.

    A part mesh identifies its bones by hash, so a rebuilt shared skeleton would
    otherwise carry hash names and nothing keyed by bone name could drive an npc
    the way it drives a character. ``bonePathsStr`` on the templet is that list."""
    from ... import prefab_importer
    prefab_importer.set_shared_bone_names([])
    templet = next((row for part, row, _p in hits if part == SKELETON_PART), None)
    if templet is None:
        return
    try:
        assets, _r, _s, _c, _sc = cabmap_state.BRIDGE.import_cabs(
            [cabmap_state.ROWS.cab(templet)])
        from ...ruri_pybridge.unity import bridge_asset_db
        db = bridge_asset_db.BridgeAssetDatabase(assets)
        for guid in db.all_guids():
            text = db.raw_text(guid) or ""
            if "bonePathsStr" not in text:
                continue
            names = []
            collecting = False
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("bonePathsStr"):
                    collecting = True
                    continue
                if not collecting:
                    continue
                if stripped.startswith("-"):
                    names.append(stripped[1:].strip())
                elif stripped:
                    break
            if names:
                prefab_importer.set_shared_bone_names(names)
                prefab_importer.set_shared_leaf_names(names)
                return
    except Exception:
        return



def _model_parts(context, entry):
    """(manifest, [(part, row, path)], [unresolved part]) for an npc row.

    A part is taken as ONE asset: the game publishes some parts as a generated
    prefab and the rest only as the authored skinned mesh, and where both a
    ``_variant`` and a ``_static`` prefab exist the variant is the one skinned
    onto the shared skeleton -- the static one is a standalone posed copy, which
    is what lands unparented with no armature modifier."""
    roots = roster.vfs_roots(context.scene.ruri_cabmap.game_root)
    try:
        info = cabmap_state.BRIDGE.npc_prefab_parts(roots, entry.key)
    except Exception:
        return None, [], []
    hits = []
    missing = []
    # The shared skeleton. A part published only as its authored mesh brings an
    # Avatar with no bones in it -- the bones live in the template the manifest
    # names, so it has to come along or those parts have nothing to skin to.
    templet = (info.get("avatar_templet") or "").rsplit("/", 1)[-1]
    for part in info["parts"]:
        candidates = _prefab_named(part)
        if not candidates:
            missing.append(part)
            continue
        skinned = [hit for hit in candidates if "_variant." in hit[1].lower()]
        row, path = (skinned or candidates)[0]
        hits.append((part, row, path))

    # Only when a part actually needs it. A named actor ships one fully rigged
    # postmodel prefab; adding the template there imports the same meshes a
    # second time, unparented, on top of the good ones.
    if templet and any("GameObject" not in cabmap_state.ROWS[row]["type_names"]
                       for _part, row, _path in hits):
        for row, path in _asset_named("data_npc_avatartemplet_" + templet.lower()):
            hits.append((SKELETON_PART, row, path))
            break
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
            any_prefab = _prefab_rows(entry.key)
            self.report({"WARNING"}, "'{0}' has no _{1} prefab; it does have {2}.".format(
                entry.label, state.model_kind, ", ".join(path for _row, path in any_prefab[:3]))
                if any_prefab else
                "No prefab in the loaded cabmap is named after '{0}'.".format(entry.key))
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
        lists one whole prefab instead."""
        info, hits, missing = _model_parts(context, entry)
        if info is None:
            self.report({"WARNING"}, "'{0}' ({1}) has no part manifest -- the game ships no "
                                     "assembled model for it.".format(entry.label, entry.key))
            return {"CANCELLED"}
        if not hits:
            self.report({"WARNING"}, "None of {0}'s {1} part(s) are in the loaded cabmap.".format(
                entry.label, len(info["parts"])))
            return {"CANCELLED"}

        # Two passes, and it has to be two. A part published as a prefab carries a
        # GameObject; a part published only as its authored mesh carries Mesh +
        # Avatar and no GameObject at all. The browser's loose-asset import only
        # runs for a closure with NO root in it, so mixing both kinds into one
        # selection means the mesh-only parts are silently dropped -- which is
        # exactly how an npc arrived as a floating head.
        _register_skeleton_bone_names(hits)
        skeleton, rooted, loose = [], [], []
        for part, row, _path in hits:
            if part == SKELETON_PART:
                skeleton.append(row)
            elif "GameObject" in cabmap_state.ROWS[row]["type_names"]:
                rooted.append(row)
            else:
                loose.append(row)

        # Skeleton first, and on its own: a loose skinned mesh binds to whatever
        # rig is already in the scene, so importing it alongside its own skeleton
        # is a race it loses.
        imported_any = False
        # Rigged parts first: they arrive with named bone hierarchies, and every
        # later part identifies its bones by the hash of that same path -- so the
        # names have to be in the scene before anything that needs them.
        for group, one_at_a_time in ((rooted, False), (skeleton, False), (loose, True)):
            if not group:
                continue
            # Root-less rows go ONE AT A TIME. The browser only falls back to its
            # loose-asset import when the whole resolved closure has no root in it,
            # and batching a root-less row together with anything that drags in a
            # rooted dependency hides it behind that root -- which is exactly how
            # a body/hair/tail mesh got resolved, selected, and then silently
            # never built.
            batches = [[row] for row in group] if one_at_a_time else [group]
            for batch in batches:
                cabmap_state.clear_selection()
                for row in batch:
                    cabmap_state.SELECTED_CABS.add(cabmap_state.ROWS.cab(row))
                if "FINISHED" in bpy.ops.ruri.import_selected():
                    imported_any = True
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

    layout.prop(state, "search", icon="VIEWZOOM", text="")
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


_CLASSES = (
    RURI_PG_roster_entry,
    RURI_PG_roster,
    RURI_UL_roster,
    RURI_OT_roster_refresh,
    RURI_OT_roster_load,
    RURI_OT_roster_reveal,
)


def register():
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
