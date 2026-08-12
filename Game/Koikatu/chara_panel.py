"""The Character tab: build one, then drive her face and her animations.

Four sections, which are the four things this game keeps separate itself:

``Model``  the cast -- every character card the install ships, under the name
           written inside the card -- plus which of her seven outfits to wear.
``Parts``  the same build from the customization catalog directly: pick an item
           per slot out of the game's own lists.
``Face``   the head's own blend-shape patterns, and the expressions the game
           names out of them per personality.
``Anime``  the studio's animation catalog, imported onto whichever rig is in the
           scene through the browser's own clip flow.

Every list here is a dataset the game's hook publishes (see ``datasets``), drawn
and filtered on the shared engine. Nothing on this side reads a byte of the game.
"""

from __future__ import annotations

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)

from ... import cross_game_retarget, filter_ui, prefab_importer
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import bridge_asset_db, class_registry, discovery
from . import chara_importer, datasets, face_importer

MODEL_SECTION = "model"
PARTS_SECTION = "parts"
FACE_SECTION = "face"
ANIME_SECTION = "anime"

CAST_FILTER_SPEC = filter_ui.register_spec(filter_ui.FilterSpec(
    key="Koikatu:cast", fields=(("name", "Name"), ("file", "File"), ("folder", "Folder")),
    state_for=lambda context: context.scene.ruri_kk_chara,
    apply=lambda context: _rebuild_cast(context.scene.ruri_kk_chara)))

ANIME_FILTER_SPEC = filter_ui.register_spec(filter_ui.FilterSpec(
    key="Koikatu:anime", fields=(("name", "Name"), ("groupName", "Group"), ("bundle", "Bundle")),
    state_for=lambda context: context.scene.ruri_kk_anime,
    apply=lambda context: _rebuild_anime(context.scene.ruri_kk_anime)))

# The clip classes an animation import serializes.
_CLIP_CLASSES = ("AnimationClip", "AnimatorController", "AnimatorOverrideController",
                 "Avatar", "MonoScript")

# ChaFileDefine.CoordinateType, in its own order -- the seven outfits a card carries.
COORDINATES = ("School01", "School02", "Gym", "Swim", "Club", "Plain", "Pajamas")

# Enum item lists Blender's dynamic-items callbacks must not let go of.
_coordinate_items_cache = [("0", COORDINATES[0], "")]
_slot_items_cache = [("0", "(refresh first)", "")]
_item_items_cache = [("0", "(refresh first)", "")]
_expression_items_cache = [("", "(none)", "")]

STATUS = "Refresh to read the game's characters."


def _report_exception(operator, prefix, exc):
    import traceback
    traceback.print_exc()
    operator.report({"ERROR"}, "{0}: {1}: {2} (full traceback in console)".format(
        prefix, type(exc).__name__, exc))


def _class_ids(names):
    resolved = []
    for name in names:
        class_id = class_registry.id_for_name(name)
        if class_id is not None and class_id not in resolved:
            resolved.append(class_id)
    return resolved


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


def _game_root():
    return bpy.context.scene.ruri_cabmap.game_root


# ── the cast ────────────────────────────────────────────────────────────────

def _rebuild_cast(state):
    global STATUS
    matched, table = datasets.search(datasets.CAST, (_game_root(),),
                                     state.search.strip(), state.filter_rules)
    if table is None:
        state.entries.clear()
        STATUS = "Load a cabmap, then refresh."
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
    return datasets.rows(datasets.PLAN, card, int(state.coordinate))


def _coordinate_items(self, context):
    global _coordinate_items_cache
    _coordinate_items_cache = [(str(index), name, "The character's {0} outfit".format(name))
                               for index, name in enumerate(COORDINATES)]
    return _coordinate_items_cache


def _on_cast_edit(self, context):
    _rebuild_cast(self)


# ── the customization catalog ───────────────────────────────────────────────

def _catalog():
    return datasets.table(datasets.CATALOG)


def _slot_items(self, context):
    """Every category the catalog actually carries, under the game's own label."""
    global _slot_items_cache
    table = _catalog()
    listed = []
    if table is not None:
        seen = {}
        for index in range(len(table)):
            number = datasets.text(table, index, "category")
            if number not in seen:
                seen[number] = datasets.text(table, index, "categoryLabel")
        listed = [(number, label, label) for number, label in seen.items()]
    _slot_items_cache = listed or [("0", "(refresh first)", "")]
    return _slot_items_cache


def _item_items(self, context):
    global _item_items_cache
    table = _catalog()
    listed = []
    if table is not None:
        for index in range(len(table)):
            if datasets.text(table, index, "category") != self.slot:
                continue
            item_id = datasets.text(table, index, "id")
            name = datasets.text(table, index, "name") or item_id
            bundle = table.cell(index, "bundle")
            asset = table.cell(index, "asset")
            listed.append((item_id, "{0}  ({1})".format(name, item_id),
                           "{0} -> {1}".format(bundle, asset) if bundle else "empty slot"))
    _item_items_cache = listed or [("0", "(refresh first)", "")]
    return _item_items_cache


def _catalog_row(state):
    table = _catalog()
    if table is None:
        return None
    for index in range(len(table)):
        if (datasets.text(table, index, "category") == state.slot
                and datasets.text(table, index, "id") == state.item):
            return {name: table.cell(index, name) for name in table.names}
    return None


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

def _rebuild_anime(state):
    matched, table = datasets.search(datasets.ANIMATIONS, (),
                                     state.search.strip(), state.filter_rules)
    if table is None:
        state.entries.clear()
        return
    rows = []
    for index in matched:
        rows.append({
            "name": table.cell(index, "name"),
            "group": "{0} / {1}".format(table.cell(index, "groupName"),
                                        table.cell(index, "categoryName")),
            "row": str(index),
            "bundle": table.cell(index, "bundle"),
        })
    _grouped(state, rows, "name", "group", "row", "bundle")


def _on_anime_edit(self, context):
    _rebuild_anime(self)


def _selected_animation(state):
    entry = _selected_entry(state)
    table = datasets.table(datasets.ANIMATIONS)
    if entry is None or table is None:
        return None
    try:
        index = int(entry.key)
    except ValueError:
        return None
    if not 0 <= index < len(table):
        return None
    return {name: table.cell(index, name) for name in table.names}


# ── property groups ─────────────────────────────────────────────────────────

class RURI_PG_kk_entry(bpy.types.PropertyGroup):
    """One drawn line of any of this tab's lists."""
    label: StringProperty()
    key: StringProperty()
    detail: StringProperty()
    is_group: BoolProperty(default=False)


class RURI_PG_kk_chara(filter_ui.FilterStateMixin, bpy.types.PropertyGroup):
    FILTER_SPEC_KEY = "Koikatu:cast"

    section: EnumProperty(
        name="Section",
        items=[(MODEL_SECTION, "Model", "Build a character from one of the game's own cards"),
               (PARTS_SECTION, "Parts", "Build from the customization catalog, slot by slot"),
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

    slot: EnumProperty(name="Slot", items=_slot_items)
    item: EnumProperty(name="Item", items=_item_items)

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
    FILTER_SPEC_KEY = "Koikatu:anime"

    search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"}, update=_on_anime_edit,
                           description="Filter by name, group or bundle")
    entries: CollectionProperty(type=RURI_PG_kk_entry)
    active_index: IntProperty()


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
            datasets.table(datasets.CATALOG, refresh=True)
            datasets.table(datasets.CAST, _game_root(), refresh=True)
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
    table = datasets.table(datasets.CAST, _game_root())
    card = _selected_card(state)
    if table is None or not card:
        return 0
    for index in range(len(table)):
        if table.cell(index, "path") == card:
            return datasets.number(table, index, "personality")
    return 0


class RURI_OT_kk_build_parts(bpy.types.Operator):
    """Build the default body wearing exactly the one catalog item picked."""
    bl_idname = "ruri.kk_chara_build_parts"
    bl_label = "Build This Part"
    bl_description = ("Assemble the base body and the selected catalog item on it -- "
                      "the same build a card drives, with one slot chosen by hand")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None
                and _catalog() is not None)

    def execute(self, context):
        state = context.scene.ruri_kk_chara
        row = _catalog_row(state)
        if row is None or not row["bundle"]:
            self.report({"WARNING"}, "That entry is the empty slot -- it loads nothing.")
            return {"CANCELLED"}

        # The base a part hangs on is the first four rows of any plan, so it is taken
        # from a real one rather than restated here.
        table = datasets.table(datasets.CAST, _game_root())
        if table is None or not len(table):
            self.report({"WARNING"}, "Refresh first -- the base body comes from a card's own plan.")
            return {"CANCELLED"}
        base = [part for part in datasets.rows(datasets.PLAN, table.cell(0, "path"), 0)
                if part["slot"] in ("armature", "head_armature", "body", "tongue")]

        family = str(row["family"])
        base.append({
            "slot": family,
            "label": str(row["name"]) or str(row["asset"]),
            "bundle": str(row["bundle"]),
            "asset": str(row["asset"]),
            "parent": _parent_for(family, row),
            "rebind": chara_importer.REBIND_BODY if family in ("clothes", "sub_clothes") else "",
        })
        try:
            report = chara_importer.build(context, base, context.scene.ruri_cabmap.as_options(),
                                          name=str(row["name"]) or str(row["asset"]))
        except Exception as exc:
            _report_exception(self, "Part build failed", exc)
            return {"CANCELLED"}
        if report.armature is None:
            self.report({"WARNING"}, "Nothing built -- {0}".format(report.summary()))
            return {"CANCELLED"}
        self.report({"INFO"}, "{0}: {1}".format(row["name"], report.summary()))
        return {"FINISHED"}


def _parent_for(family, row):
    """Where the game hangs a part of this family: the bone its own row names for an
    accessory, the head's hair anchor for hair, the head for a head."""
    if family == datasets.ACCESSORY:
        parent = str(row.get("parent") or "")
        return "" if parent in ("", "null", "0") else parent
    if family == datasets.HAIR:
        return "cf_J_FaceUp_ty"
    if family == datasets.HEAD:
        return "cf_s_head"
    return ""


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
        state = context.scene.ruri_kk_chara
        row = _selected_animation(context.scene.ruri_kk_anime)
        armature = _rig(context)
        if armature is None:
            self.report({"WARNING"}, "Select the character's armature first.")
            return {"CANCELLED"}
        cabs = datasets.cabs_for([str(row["bundle"])])
        if not cabs:
            self.report({"WARNING"}, "'{0}' is not in the loaded cabmap.".format(row["bundle"]))
            return {"CANCELLED"}

        options = context.scene.ruri_cabmap.as_options()
        try:
            assets, _roots, _seeds, _clips, _scenes = cabmap_state.BRIDGE.import_cabs(
                cabs, _class_ids(_CLIP_CLASSES))
        except Exception as exc:
            _report_exception(self, "Animation import (bridge) failed", exc)
            return {"CANCELLED"}
        db = bridge_asset_db.BridgeAssetDatabase(
            assets, clip_curve_blobs=cabmap_state.BRIDGE.clip_curves_by_guid,
            asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid)

        wanted = _clip_guids(db, str(row["clip"]))
        if not wanted:
            self.report({"WARNING"}, "No clip named '{0}' in {1}.".format(row["clip"], row["bundle"]))
            return {"CANCELLED"}
        try:
            built, warnings = cross_game_retarget.load_clips_onto(
                context, cabmap_state.active_game(), cabs[0], wanted, db, armature,
                None, options)
        except cross_game_retarget.CrossGameRetargetError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            _report_exception(self, "Animation import failed", exc)
            return {"CANCELLED"}
        for message in warnings[:5]:
            self.report({"WARNING"}, message)
        self.report({"INFO"}, "{0}: {1} action(s).".format(row["name"], built))
        return {"FINISHED"}


def _clip_guids(db, clip_name):
    """The clip the catalog names, by its own m_Name. Exact: a controller bundle
    holds every clip of its family."""
    wanted = clip_name.lower()
    found = []
    for guid in db.all_guids():
        text = db.raw_text(guid)
        if not text:
            continue
        class_name, name = discovery.peek_class_and_name(text)
        if class_name != "AnimationClip":
            continue
        if not wanted or (name or "").lower() == wanted:
            found.append(guid)
    return found


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


def _draw_parts(layout, context, state):
    if _catalog() is None:
        layout.label(text="Refresh to read the game's customization catalog.", icon="INFO")
        layout.operator(RURI_OT_kk_refresh.bl_idname, icon="FILE_REFRESH")
        return
    column = layout.column(align=True)
    column.prop(state, "slot")
    column.prop(state, "item")
    row = _catalog_row(state)
    box = layout.box()
    box.label(text="{0}  ->  {1}".format(
        (row or {}).get("bundle") or "-", (row or {}).get("asset") or "-"))
    layout.operator(RURI_OT_kk_build_parts.bl_idname, icon="IMPORT")


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


def _draw_anime(layout, context, state):
    anime = context.scene.ruri_kk_anime
    filter_ui.draw_search_row(layout, anime,
                              extra_operator=(RURI_OT_kk_anime_refresh.bl_idname, "FILE_REFRESH"))
    layout.template_list(RURI_UL_kk_list.bl_idname, "anime", anime, "entries",
                         anime, "active_index", rows=10)

    row = _selected_animation(anime)
    actions = layout.column(align=True)
    actions.enabled = row is not None
    if row is not None:
        box = actions.box()
        box.label(text="{0}  ->  {1}".format(row["bundle"], row["asset"]))
        box.label(text="clip: {0}".format(row["clip"] or "(every clip in the controller)"))
    actions.operator(RURI_OT_kk_anime_import.bl_idname, icon="ANIM_DATA")


_SECTIONS = {MODEL_SECTION: _draw_model, PARTS_SECTION: _draw_parts,
             FACE_SECTION: _draw_face, ANIME_SECTION: _draw_anime}


def draw_character_tab(layout, context):
    state = context.scene.ruri_kk_chara
    layout.row(align=True).prop(state, "section", expand=True)
    _SECTIONS[state.section](layout, context, state)


_CLASSES = (
    RURI_PG_kk_entry,
    RURI_PG_kk_chara,
    RURI_PG_kk_anime,
    RURI_UL_kk_list,
    RURI_OT_kk_refresh,
    RURI_OT_kk_build,
    RURI_OT_kk_build_parts,
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
