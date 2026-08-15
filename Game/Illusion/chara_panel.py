"""The Character tab: build one, then drive her face and her animations.

Three sections, which are the three things this game keeps separate itself:

``Model``  the cast -- every character card the install ships, under the name
           written inside the card -- plus which of her outfits to wear.
``Face``   the head's own blend-shape patterns, and the expressions the game
           names out of them per personality.
``Anime``  the studio's animation catalog, imported onto whichever rig is in the
           scene through the browser's own clip flow. Split again by the kind
           the catalog itself distinguishes: ordinary animations are one per row
           and listed flat, while an H act is a pair -- one animation for each
           partner -- so that kind is drawn as two index-aligned lists instead.

Every list here is a dataset the game's hook publishes (see ``datasets``), drawn
and filtered on the shared engine. Nothing on this side reads a byte of the game.
"""

from __future__ import annotations

import re

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)

from ... import cross_game_retarget, filter_ui, prefab_importer
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import bridge_asset_db, class_registry
from . import chara_importer, datasets, face_importer

MODEL_SECTION = "model"
FACE_SECTION = "face"
ANIME_SECTION = "anime"
# The two kinds of animation the catalog's `family` column already separates,
# drawn as sub-tabs inside the Anime section.
ANIME_NORMAL = "normal"
ANIME_SEX = "sex"

CAST_FILTER_SPEC = filter_ui.register_spec(filter_ui.FilterSpec(
    key="Illusion:cast", fields=(("name", "Name"), ("file", "File"), ("folder", "Folder")),
    state_for=lambda context: context.scene.ruri_kk_chara,
    apply=lambda context: _rebuild_cast(context.scene.ruri_kk_chara)))

ANIME_FILTER_SPEC = filter_ui.register_spec(filter_ui.FilterSpec(
    key="Illusion:anime",
    fields=(("name", "Name"), ("groupName", "Group"), ("categoryName", "Position"),
            ("clip", "Clip"), ("bundle", "Bundle")),
    state_for=lambda context: context.scene.ruri_kk_anime,
    apply=lambda context: _rebuild_anime(context.scene.ruri_kk_anime)))

# The clip classes an animation import serializes.
# ChaFileDefine.CoordinateType, in its own order -- the seven outfits a card carries.
COORDINATES = ("School01", "School02", "Gym", "Swim", "Club", "Plain", "Pajamas")

# Enum item lists Blender's dynamic-items callbacks must not let go of.
_coordinate_items_cache = [("0", COORDINATES[0], "")]
_expression_items_cache = [("", "(none)", "")]

STATUS = "Refresh to read the game's characters."


def _report_exception(operator, prefix, exc):
    import traceback
    traceback.print_exc()
    operator.report({"ERROR"}, "{0}: {1}: {2} (full traceback in console)".format(
        prefix, type(exc).__name__, exc))


def _selected_entry(state):
    if 0 <= state.active_index < len(state.entries):
        entry = state.entries[state.active_index]
        if not entry.is_group:
            return entry
    return None


def _grouped(state, rows, label_key, group_key, key_key, detail_key):
    """Draw a matched row set as a grouped list. The one list-building shape both
    of this tab's lists use."""
    state.entries.clear()
    rows.sort(key=lambda row: (str(row[group_key]), str(row[label_key])))
    counts = {}
    for row in rows:
        counts[str(row[group_key])] = counts.get(str(row[group_key]), 0) + 1

    current = None
    for row in rows:
        group = str(row[group_key])
        if group != current:
            current = group
            header = state.entries.add()
            header.label = "{0}  ({1})".format(group, counts[group])
            header.is_group = True
        entry = state.entries.add()
        entry.label = str(row[label_key])
        entry.key = str(row[key_key])
        entry.detail = str(row[detail_key])
    if state.active_index >= len(state.entries):
        state.active_index = 0


# ── the cast ────────────────────────────────────────────────────────────────

def _rebuild_cast(state):
    global STATUS
    matched, table = datasets.search(datasets.CAST, {},
                                     state.search.strip(), state.filter_rules)
    if table is None:
        state.entries.clear()
        STATUS = datasets.why_empty(datasets.CAST) or "Load a cabmap, then refresh."
        return
    rows = [{name: table.cell(index, name) for name in table.names} for index in matched]
    _grouped(state, rows, "name", "folder", "path", "file")
    STATUS = "{0} of {1} character(s).".format(len(rows), len(table))


def _selected_card(state):
    entry = _selected_entry(state)
    return entry.key if entry else ""


def _plan(state):
    card = _selected_card(state)
    if not card:
        return []
    return datasets.rows(datasets.PLAN, cardPath=card, outfit=int(state.coordinate))


def _coordinate_items(self, context):
    global _coordinate_items_cache
    _coordinate_items_cache = [(str(index), name, "The character's {0} outfit".format(name))
                               for index, name in enumerate(COORDINATES)]
    return _coordinate_items_cache


def _on_cast_edit(self, context):
    _rebuild_cast(self)


# ── the face ────────────────────────────────────────────────────────────────

def _rig(context):
    """The rig to act on: whatever the user has selected. The armature carries its own
    identity (rig/avatar/game stamps), so pointing at it IS the whole instruction -- a
    name field would be a second way to say the same thing."""
    return prefab_importer.find_target_armature(context)


def _expressions(personality):
    """The named expressions one personality has, plus the shared set every
    personality falls back on (the sheets the game files under negative keys)."""
    table = datasets.table(datasets.EXPRESSIONS)
    if table is None:
        return []
    found = []
    for index in range(len(table)):
        own = datasets.number(table, index, "personality")
        if own == personality or own < 0:
            found.append({name: table.cell(index, name) for name in table.names})
    return found


def _expression_items(self, context):
    global _expression_items_cache
    listed = [("", "(none)", "Drive the patterns by hand")]
    for index, row in enumerate(_expressions(self.personality)):
        listed.append((str(index), str(row["name"]), str(row["name"])))
    _expression_items_cache = listed
    return _expression_items_cache


# ── the animation catalog ───────────────────────────────────────────────────

_SIDES = {0: "male", 1: "female"}


def _side_of(row_sex):
    """Which partner a row is for -- the catalog's own ``sex`` column (0 male,
    1 female, -1 unstated), which the hook derives from whatever the game states it
    with. Not re-derived here from a path: a title that files both partners on one
    sheet row has no per-partner path to read."""
    try:
        return _SIDES.get(int(float(row_sex)))
    except (TypeError, ValueError):
        return None


def _rebuild_anime(state):
    matched, table = datasets.search(datasets.ANIMATIONS, {},
                                     state.search.strip(), state.filter_rules)
    if table is None:
        state.entries.clear()
        state.male_entries.clear()
        state.female_entries.clear()
        return
    normal = []
    sides = {"male": {}, "female": {}}
    group_of = {"male": {}, "female": {}}
    order = []
    seen = set()
    for index in matched:
        family = str(table.cell(index, "family"))
        name = table.cell(index, "name")
        category = table.cell(index, "categoryName")
        row = {"name": name,
               "group": "{0} / {1}".format(table.cell(index, "groupName"), category),
               "row": str(index),
               "clip": table.cell(index, "clip")}
        side = _side_of(table.cell(index, "sex"))
        pair = str(table.cell(index, "pair"))
        if family != "h" or side is None or not pair:
            normal.append(row)
            continue
        # WHICH act a row is one partner's half of is the catalog's own ``pair``
        # column, so the two lists are built from ONE key set and stay index-aligned
        # -- row N on the left is row N's partner on the right. Never re-derived from
        # name+clip: a title that spells the clip per sex (hou_m_00 / hou_f_00) then
        # matches nothing, every act becomes two half-empty rows, and the two columns
        # drift a row apart.
        bucket = pair.rpartition("/")[0] or pair
        key = (bucket, pair)
        if key not in sides[side]:
            sides[side][key] = row
            group_of[side].setdefault(bucket, row["group"])
            if key not in seen:
                seen.add(key)
                order.append(key)
    _grouped(state, normal, "name", "group", "row", "clip")
    _grouped_pairs(state, order, sides, group_of)


def _grouped_pairs(state, order, sides, group_of):
    """Fill the male and female lists so their indices correspond.

    Both collections get headers and rows at the same indices and in the same
    order; a position only one partner has gets a blank placeholder on the other
    side rather than shifting every later row out of alignment.

    A header names the group the way that column's own rows are filed -- the two
    partners of one act sit in DIFFERENT groups (男H挿入 vs 女H挿入), and the
    position alone is often just a number, so a shared header would have to drop
    the half that actually reads as a name. Where a column has nothing at all for
    a position it borrows the other one's, since the band is the same act either
    way and a bare position number names nothing."""
    state.male_entries.clear()
    state.female_entries.clear()
    # Within a band, by the act's own id as a NUMBER: it is the order the game lists
    # them in, and sorting the pair string instead puts 10 before 2.
    def _rank(key):
        tail = key[1].rpartition("/")[2]
        return (key[0], 0, int(tail), "") if tail.lstrip("-").isdigit() else (key[0], 1, 0, key[1])

    order.sort(key=_rank)
    counts = {}
    for key in order:
        counts[key[0]] = counts.get(key[0], 0) + 1

    current = None
    for key in order:
        if key[0] != current:
            current = key[0]
            for side, other, entries in (("male", "female", state.male_entries),
                                         ("female", "male", state.female_entries)):
                header = entries.add()
                header.label = "{0}  ({1})".format(
                    group_of[side].get(current) or group_of[other].get(current) or current,
                    counts[current])
                header.is_group = True
        for side, entries in (("male", state.male_entries),
                              ("female", state.female_entries)):
            row = sides[side].get(key)
            entry = entries.add()
            if row is None:
                entry.label = "--"
                entry.key = ""
                entry.detail = ""
            else:
                entry.label = str(row["name"])
                entry.key = str(row["row"])
                entry.detail = str(row["clip"])
    if state.pair_index >= len(state.male_entries):
        state.pair_index = 0


def _on_anime_edit(self, context):
    _rebuild_anime(self)


def _row_of_entry(entry):
    table = datasets.table(datasets.ANIMATIONS)
    if entry is None or table is None or not entry.key:
        return None
    try:
        index = int(entry.key)
    except ValueError:
        return None
    if not 0 <= index < len(table):
        return None
    return {name: table.cell(index, name) for name in table.names}


def _selected_animation(state):
    return _row_of_entry(_selected_entry(state))


def _selected_side(state, side):
    """The catalog row selected in one of the paired lists, or None -- a header
    or a placeholder (the partner this position lacks) selects nothing.

    Both lists share ONE index: an H act is one animation per partner and the
    two collections are index-aligned, so the row chosen on either side IS the
    partner of the row shown on the other."""
    entries = state.male_entries if side == "male" else state.female_entries
    index = state.pair_index
    if not 0 <= index < len(entries):
        return None
    entry = entries[index]
    if entry.is_group:
        return None
    return _row_of_entry(entry)


# ── property groups ─────────────────────────────────────────────────────────

class RURI_PG_kk_entry(bpy.types.PropertyGroup):
    """One drawn line of any of this tab's lists."""
    label: StringProperty()
    key: StringProperty()
    detail: StringProperty()
    is_group: BoolProperty(default=False)


class RURI_PG_kk_chara(filter_ui.FilterStateMixin, bpy.types.PropertyGroup):
    FILTER_SPEC_KEY = "Illusion:cast"

    section: EnumProperty(
        name="Section",
        items=[(MODEL_SECTION, "Model", "Build a character from one of the game's own cards"),
               (FACE_SECTION, "Face", "Drive the head's blend-shape patterns"),
               (ANIME_SECTION, "Anime", "The studio's animation catalog")],
        default=MODEL_SECTION)
    search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"}, update=_on_cast_edit,
                           description="Filter by name, file or folder")
    entries: CollectionProperty(type=RURI_PG_kk_entry)
    active_index: IntProperty()
    coordinate: EnumProperty(name="Outfit", items=_coordinate_items,
                             description="Which of the character's seven outfits to build")
    build_hair: BoolProperty(name="Hair", default=True)
    build_clothes: BoolProperty(name="Clothes", default=True)
    build_accessories: BoolProperty(name="Accessories", default=True)

    personality: IntProperty(name="Personality", default=0, min=-100, max=100,
                             description="Whose named expressions to list -- the character's own "
                                         "personality number, as her card states it")
    expression: EnumProperty(name="Expression", items=_expression_items)
    eyebrow_pattern: IntProperty(name="Eyebrow", default=0, min=0)
    eyes_pattern: IntProperty(name="Eyes", default=0, min=0)
    mouth_pattern: IntProperty(name="Mouth", default=0, min=0)
    eyebrow_open: FloatProperty(name="Eyebrow Open", default=1.0, min=0.0, max=1.0)
    eyes_open: FloatProperty(name="Eyes Open", default=1.0, min=0.0, max=1.0)
    mouth_open: FloatProperty(name="Mouth Open", default=0.0, min=0.0, max=1.0)


class RURI_PG_kk_anime(filter_ui.FilterStateMixin, bpy.types.PropertyGroup):
    FILTER_SPEC_KEY = "Illusion:anime"

    section: EnumProperty(
        name="Kind",
        items=[(ANIME_NORMAL, "Normal", "Poses, locomotion -- everything outside an H act"),
               (ANIME_SEX, "Sex", "H acts -- one animation per partner, side by side")],
        default=ANIME_NORMAL)
    search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"}, update=_on_anime_edit,
                           description="Filter by name, group or bundle")
    entries: CollectionProperty(type=RURI_PG_kk_entry)
    active_index: IntProperty()
    male_entries: CollectionProperty(type=RURI_PG_kk_entry)
    female_entries: CollectionProperty(type=RURI_PG_kk_entry)
    pair_index: IntProperty()


class RURI_UL_kk_list(bpy.types.UIList):
    bl_idname = "RURI_UL_kk_list"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if item.is_group:
            row = layout.row()
            row.enabled = False
            row.label(text=item.label, icon="OUTLINER_COLLECTION")
            return
        row = layout.row(align=True)
        row.label(text=item.label, icon="OUTLINER_OB_ARMATURE")
        detail = row.row()
        detail.enabled = False
        detail.alignment = "RIGHT"
        detail.label(text=item.detail)

    def filter_items(self, context, data, propname):
        return [], []


# ── operators ───────────────────────────────────────────────────────────────

class RURI_OT_kk_refresh(bpy.types.Operator):
    """Read the customization catalog and the cast this install ships."""
    bl_idname = "ruri.kk_chara_refresh"
    bl_label = "Refresh Characters"
    bl_description = "Read the game's customization catalog and its character cards"

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = context.scene.ruri_kk_chara
        try:
            datasets.table(datasets.CAST, refresh=True)
        except Exception as exc:
            _report_exception(self, "Character list failed", exc)
            return {"CANCELLED"}
        _rebuild_cast(state)
        self.report({"INFO"}, STATUS)
        return {"FINISHED"}


class RURI_OT_kk_build(bpy.types.Operator):
    """Assemble the selected character out of the parts her card names."""
    bl_idname = "ruri.kk_chara_build"
    bl_label = "Build Character"
    bl_description = "Resolve every part this character wears and assemble her onto one rig"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None
                and bool(_selected_card(context.scene.ruri_kk_chara)))

    def execute(self, context):
        state = context.scene.ruri_kk_chara
        entry = _selected_entry(state)
        plan = _plan(state)
        if not plan:
            self.report({"WARNING"}, "That card resolves to nothing importable.")
            return {"CANCELLED"}

        wanted = {"armature", "head_armature", "body", "tongue", datasets.HEAD}
        if state.build_hair:
            wanted.add(datasets.HAIR)
        if state.build_clothes:
            wanted.update((datasets.CLOTHES, datasets.SUB_CLOTHES))
        if state.build_accessories:
            wanted.add(datasets.ACCESSORY)
        plan = [part for part in plan if part["slot"] in wanted]

        try:
            report = chara_importer.build(context, plan, context.scene.ruri_cabmap.as_options(),
                                          name=entry.label if entry else "Character")
        except Exception as exc:
            _report_exception(self, "Character build failed", exc)
            return {"CANCELLED"}
        if report.armature is None:
            self.report({"WARNING"}, "Nothing built -- {0}".format(report.summary()))
            return {"CANCELLED"}

        state.personality = int(_personality_of(state))
        for message in report.warnings[:5]:
            self.report({"WARNING"}, message)
        if report.missing:
            self.report({"WARNING"}, "not in the closure: " + ", ".join(report.missing[:6]))
        self.report({"INFO"}, "{0}: {1}".format(entry.label if entry else "", report.summary()))
        return {"FINISHED"}


def _personality_of(state):
    table = datasets.table(datasets.CAST)
    card = _selected_card(state)
    if table is None or not card:
        return 0
    for index in range(len(table)):
        if table.cell(index, "path") == card:
            return datasets.number(table, index, "personality")
    return 0


class RURI_OT_kk_face_apply(bpy.types.Operator):
    """Put the chosen expression on the rig."""
    bl_idname = "ruri.kk_face_apply"
    bl_label = "Apply"
    bl_description = "Set the head's blend-shape patterns to this expression"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(face_importer.head_of(_rig(context))[0])

    def execute(self, context):
        state = context.scene.ruri_kk_chara
        armature = _rig(context)
        table = face_importer.table(armature)
        if not table:
            self.report({"WARNING"}, "No character with a face table is selected.")
            return {"CANCELLED"}

        named = _named_expression(state)
        if named is not None:
            patterns, openness = face_importer.expression_patterns(named)
        else:
            patterns = {"eyebrow": state.eyebrow_pattern, "eyes": state.eyes_pattern,
                        "mouth": state.mouth_pattern}
            openness = {"eyebrow": state.eyebrow_open, "eyes": state.eyes_open,
                        "mouth": state.mouth_open}
        touched, warnings = face_importer.apply(armature, table, patterns, openness)
        for message in warnings[:5]:
            self.report({"WARNING"}, message)
        self.report({"INFO"}, "{0} mesh(es) updated.".format(touched))
        return {"FINISHED"}


class RURI_OT_kk_face_clear(bpy.types.Operator):
    """Put every pattern key back to zero."""
    bl_idname = "ruri.kk_face_clear"
    bl_label = "Clear"
    bl_description = "Zero every blend shape the head's expression system drives"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(face_importer.head_of(_rig(context))[0])

    def execute(self, context):
        armature = _rig(context)
        face_importer.clear(armature, face_importer.table(armature))
        return {"FINISHED"}


def _named_expression(state):
    if not state.expression:
        return None
    listed = _expressions(state.personality)
    try:
        return listed[int(state.expression)]
    except (ValueError, IndexError):
        return None


class RURI_OT_kk_anime_refresh(bpy.types.Operator):
    """Read the studio's animation catalog."""
    bl_idname = "ruri.kk_anime_refresh"
    bl_label = "Refresh Animations"
    bl_description = "Read every animation the studio catalogs, under its own names"

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        try:
            datasets.table(datasets.ANIMATIONS, refresh=True)
        except Exception as exc:
            _report_exception(self, "Animation list failed", exc)
            return {"CANCELLED"}
        _rebuild_anime(context.scene.ruri_kk_anime)
        self.report({"INFO"}, "{0} animation(s).".format(len(datasets.table(datasets.ANIMATIONS) or ())))
        return {"FINISHED"}


class RURI_OT_kk_anime_import(bpy.types.Operator):
    """Import the selected animation onto the character in the scene."""
    bl_idname = "ruri.kk_anime_import"
    bl_label = "Import Animation"
    bl_description = "Build this animation as an action on the selected rig"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None
                and _selected_animation(context.scene.ruri_kk_anime) is not None)

    def execute(self, context):
        armature = _rig(context)
        if armature is None:
            self.report({"WARNING"}, "Select the character's armature first.")
            return {"CANCELLED"}
        return _import_rows(self, context, [_selected_animation(context.scene.ruri_kk_anime)],
                            armature)


class RURI_OT_kk_hanime_import(bpy.types.Operator):
    """Import one partner's selected animation onto the character in the scene."""
    bl_idname = "ruri.kk_hanime_import"
    bl_label = "Import"
    bl_description = "Build this partner's selected animation as an action on the selected rig"
    bl_options = {"REGISTER", "UNDO"}

    side: EnumProperty(
        name="Partner",
        items=[("male", "Male", "The male partner's animation"),
               ("female", "Female", "The female partner's animation")],
        default="male")

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        row = _selected_side(context.scene.ruri_kk_anime, self.side)
        if row is None:
            self.report({"WARNING"}, "Nothing selected on that side.")
            return {"CANCELLED"}
        armature = _rig(context)
        if armature is None:
            self.report({"WARNING"}, "Select the character's armature first.")
            return {"CANCELLED"}
        return _import_rows(self, context, [row], armature)


def _import_rows(operator, context, rows, armature):
    """Build every catalog row in ``rows`` onto ``armature``. The one import body;
    both the flat list and the paired male/female lists call exactly this."""
    options = context.scene.ruri_cabmap.as_options()
    total = 0
    labels = []
    for row in rows:
        if row is None:
            continue
        bundle = str(row.get("overrideBundle") or row["bundle"])
        controller_name = str(row.get("overrideAsset") or row["asset"])
        cabs = datasets.cabs_for([bundle])
        if not cabs:
            operator.report({"WARNING"}, "'{0}' is not in the loaded cabmap.".format(bundle))
            continue
        try:
            clip_keys, family_states = _resolve_state_family(
                cabmap_state.BRIDGE, cabs, controller_name, str(row["clip"]))
            assets, _roots, _seeds, _clips, _scenes = cabmap_state.BRIDGE.import_cabs(
                cabs, export_asset_keys=sorted(clip_keys))
        except LookupError as exc:
            operator.report({"ERROR"}, str(exc))
            continue
        except Exception as exc:
            _report_exception(operator, "Animation import (bridge) failed", exc)
            return {"CANCELLED"}
        db = bridge_asset_db.BridgeAssetDatabase(
            assets, clip_curve_blobs=cabmap_state.BRIDGE.clip_curves_by_guid,
            asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid)

        guid_by_key = cabmap_state.BRIDGE.clip_guid_by_key
        wanted = sorted(guid_by_key.values())
        display_names = {guid_by_key[key]: _catalog_label(row, state_name)
                         for key, state_name in clip_keys.items() if key in guid_by_key}
        try:
            built, warnings = cross_game_retarget.load_clips_onto(
                context, cabmap_state.active_key(), cabs[0], wanted, db, armature,
                None, options, display_names, activate=True)
        except cross_game_retarget.CrossGameRetargetError as exc:
            operator.report({"ERROR"}, str(exc))
            continue
        except Exception as exc:
            _report_exception(operator, "Animation import failed", exc)
            return {"CANCELLED"}
        for message in warnings[:3]:
            operator.report({"WARNING"}, message)
        total += built
        labels.append("{0} ({1})".format(row["name"], ", ".join(family_states)))
    if not total:
        return {"CANCELLED"}
    operator.report({"INFO"}, "{0} action(s): {1}".format(total, " | ".join(labels)))
    return {"FINISHED"}


def _resolve_state_family(bridge, cabs, controller_name, family):
    """(clip asset keys, state names) for one catalog row.

    A row names a position's controller (overrideAsset, or the base asset) and a
    state FAMILY (`clip`): the controller's states are `<prefix>_<family><digit?>`
    variants (L_/M_/S_ camera-intensity tiers in KK's H controllers). Resolution
    is pure topology on the scan graph -- controller -> state machines -> states,
    each family state's motion clips collected through blend trees -- and the
    returned keys materialize exactly those clips, never the 2000-clip bundle."""
    graph = bridge.scan_cabs(cabs)
    controller_id = class_registry.id_for_name("AnimatorController")
    override_id = class_registry.id_for_name("AnimatorOverrideController")
    machine_id = class_registry.id_for_name("AnimatorStateMachine")
    state_id = class_registry.id_for_name("AnimatorState")
    blend_tree_id = class_registry.id_for_name("BlendTree")
    clip_id = class_registry.id_for_name("AnimationClip")

    controllers = graph.find(controller_id, controller_name)
    if len(controllers) != 1:
        present = sorted(graph.name(i) for i in graph.indices_of_class(controller_id))
        raise LookupError(
            "controller {0!r} matches {1} assets in {2} -- controllers present: {3}".format(
                controller_name, len(controllers), ", ".join(cabs), ", ".join(present)))
    states = graph.reachable(controllers[0], {controller_id, override_id, machine_id}, state_id)
    pattern = re.compile(r"^(?:[A-Za-z]+_)?{0}\d*$".format(re.escape(family)))
    family_states = [i for i in states if pattern.match(graph.name(i))]
    if not family_states:
        raise LookupError(
            "no state of {0!r} matches family {1!r} -- states present: {2}".format(
                controller_name, family, ", ".join(sorted(graph.name(i) for i in states))))
    clip_keys = {}
    for state_index in family_states:
        for clip_index in graph.reachable(state_index, {blend_tree_id}, clip_id):
            clip_keys[graph.key(clip_index)] = graph.name(state_index)
    if not clip_keys:
        raise LookupError(
            "family states {0} reference no AnimationClip -- the controller wires "
            "these states to something this resolver does not follow yet.".format(
                sorted(graph.name(i) for i in family_states)))
    return clip_keys, sorted(graph.name(i) for i in family_states)


def _catalog_label(row, state_name):
    """What the panel row says, as the action's name.

    These clips are named after internal controller states (`L_SLoop1`,
    `M_IN_Loop`) which say nothing about what the animation is; the catalog is
    where the readable Japanese identity lives, and it is what the user picked
    from. ``state_name`` only contributes the part that separates one member of
    a state family from another (the L/M/S camera tier and its index), because
    the family as a whole IS the catalog row."""
    variant = ""
    match = re.match(r"^(?:([A-Za-z]+)_)?{0}(\d*)$".format(re.escape(str(row["clip"]))),
                     state_name)
    if match:
        variant = (match.group(1) or "") + (match.group(2) or "")
    parts = [str(row["groupName"]), str(row["categoryName"]), str(row["name"])]
    if variant:
        parts.append(variant)
    return "_".join(part for part in parts if part)


# ── drawing ─────────────────────────────────────────────────────────────────

def _draw_model(layout, context, state):
    filter_ui.draw_search_row(layout, state,
                              extra_operator=(RURI_OT_kk_refresh.bl_idname, "FILE_REFRESH"))
    layout.template_list(RURI_UL_kk_list.bl_idname, "cast", state, "entries",
                         state, "active_index", rows=10)
    layout.label(text=STATUS, icon="INFO")

    card = _selected_card(state)
    options = layout.column(align=True)
    options.enabled = bool(card)
    options.prop(state, "coordinate")
    toggles = options.row(align=True)
    toggles.prop(state, "build_hair", toggle=True)
    toggles.prop(state, "build_clothes", toggle=True)
    toggles.prop(state, "build_accessories", toggle=True)
    if card:
        plan = _plan(state)
        counts = {}
        for part in plan:
            counts[part["slot"]] = counts.get(part["slot"], 0) + 1
        box = options.box()
        box.label(text="{0} part(s) from {1} bundle(s)".format(
            len(plan), len({part["bundle"] for part in plan})))
        box.label(text=", ".join("{0} {1}".format(count, slot)
                                 for slot, count in sorted(counts.items())))
    options.operator(RURI_OT_kk_build.bl_idname, icon="IMPORT")


def _draw_face(layout, context, state):
    armature = _rig(context)
    head = layout.row(align=True)

    table = face_importer.table(armature)
    if not table:
        layout.label(text="Build a character first -- its head carries the pattern table.",
                     icon="INFO")
        return

    named = layout.column(align=True)
    named.prop(state, "personality")
    named.prop(state, "expression")

    manual = layout.column(align=True)
    manual.enabled = not state.expression
    for channel, pattern_prop, open_prop in (("eyebrow", "eyebrow_pattern", "eyebrow_open"),
                                             ("eyes", "eyes_pattern", "eyes_open"),
                                             ("mouth", "mouth_pattern", "mouth_open")):
        row = manual.row(align=True)
        row.prop(state, pattern_prop)
        row.label(text="/ {0}".format(face_importer.pattern_count(table, channel)))
        row.prop(state, open_prop, slider=True, text="")

    actions = layout.row(align=True)
    actions.operator(RURI_OT_kk_face_apply.bl_idname, icon="PLAY")
    actions.operator(RURI_OT_kk_face_clear.bl_idname, icon="LOOP_BACK")


class RURI_MT_kk_anime_filter(bpy.types.Menu):
    bl_idname = "RURI_MT_kk_anime_filter"
    bl_label = "Filter By Selected Row"

    def draw(self, context):
        layout = self.layout
        row = _current_anime_row(context)
        if row is None:
            layout.label(text="No animation selected", icon="INFO")
            return
        filter_ui.draw_quick_filter_menu(layout, ANIME_FILTER_SPEC,
                                         lambda field: str(row.get(field, "")))


def _current_anime_row(context):
    """Whichever row the visible list has selected -- the quick filter builds its
    rules from this, so it must follow the kind the user is looking at."""
    anime = context.scene.ruri_kk_anime
    if anime.section != ANIME_SEX:
        return _selected_animation(anime)
    return _selected_side(anime, "male") or _selected_side(anime, "female")


def _draw_anime(layout, context, state):
    """The animation section: pick a kind, then the list shape that kind needs.

    An ordinary animation is one per row, so those are listed flat. An H act is
    two animations -- one per partner -- so flattening would scatter halves of
    the same act down the list; that kind gets the paired side-by-side view."""
    anime = context.scene.ruri_kk_anime
    layout.row(align=True).prop(anime, "section", expand=True)
    search = filter_ui.draw_search_row(
        layout, anime, extra_operator=(RURI_OT_kk_anime_refresh.bl_idname, "FILE_REFRESH"))
    search.menu(RURI_MT_kk_anime_filter.bl_idname, text="", icon="COLLAPSEMENU")
    if anime.section == ANIME_SEX:
        _draw_anime_sex(layout, anime)
    else:
        _draw_anime_normal(layout, anime)


def _draw_anime_normal(layout, anime):
    layout.template_list(RURI_UL_kk_list.bl_idname, "anime", anime, "entries",
                         anime, "active_index", rows=12)
    row = _selected_animation(anime)
    actions = layout.column(align=True)
    actions.enabled = row is not None
    if row is not None:
        box = actions.box()
        box.label(text="{0}  ->  {1}".format(row["bundle"], row["asset"]))
        box.label(text="clip: {0}".format(row["clip"] or "(every clip in the controller)"))
    actions.operator(RURI_OT_kk_anime_import.bl_idname, icon="ANIM_DATA")


def _draw_anime_sex(layout, anime):
    split = layout.split(factor=0.5)
    for side, title, entries_prop in (
            ("male", "Male", "male_entries"),
            ("female", "Female", "female_entries")):
        row = _selected_side(anime, side)
        column = split.column(align=True)
        column.label(text=title, icon="OUTLINER_OB_ARMATURE")
        column.template_list(RURI_UL_kk_list.bl_idname, "kk_h_" + side,
                             anime, entries_prop, anime, "pair_index", rows=12)
        caption = column.box()
        caption.enabled = row is not None
        caption.label(text=(str(row["name"]) if row is not None else "(nothing selected)"))
        caption.label(text=("clip: {0}".format(row["clip"]) if row is not None else " "))
        button = column.row(align=True)
        button.enabled = row is not None
        button.operator(RURI_OT_kk_hanime_import.bl_idname,
                        text="Import " + title, icon="ANIM_DATA").side = side


_SECTIONS = {MODEL_SECTION: _draw_model, FACE_SECTION: _draw_face,
             ANIME_SECTION: _draw_anime}


def draw_character_tab(layout, context):
    state = context.scene.ruri_kk_chara
    layout.row(align=True).prop(state, "section", expand=True)
    _SECTIONS[state.section](layout, context, state)


_CLASSES = (
    RURI_MT_kk_anime_filter,
    RURI_PG_kk_entry,
    RURI_PG_kk_chara,
    RURI_PG_kk_anime,
    RURI_UL_kk_list,
    RURI_OT_kk_hanime_import,
    RURI_OT_kk_refresh,
    RURI_OT_kk_build,
    RURI_OT_kk_face_apply,
    RURI_OT_kk_face_clear,
    RURI_OT_kk_anime_refresh,
    RURI_OT_kk_anime_import,
)


def register():
    filter_ui.register_spec(CAST_FILTER_SPEC)
    filter_ui.register_spec(ANIME_FILTER_SPEC)
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_kk_chara = PointerProperty(type=RURI_PG_kk_chara)
    bpy.types.Scene.ruri_kk_anime = PointerProperty(type=RURI_PG_kk_anime)


def unregister():
    del bpy.types.Scene.ruri_kk_anime
    del bpy.types.Scene.ruri_kk_chara
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
