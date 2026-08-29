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
from ...RuriRipperPyBridge.session import cabmap_state
from . import cast
from . import cloth_panel
from . import datasets

def detail_level(context):
    """Which detail level to load, as the ONE place that states it.

    Not a setting of this tab: the same number decides what the bundle browser
    imports and what a story unit stages, and three copies of it meant picking a
    level here changed nothing there."""
    return context.scene.ruri_cabmap.detail_level


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
    key="Endfield:character", fields=_filter_fields,
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


CAST_PANE = "cast"
STORY_PANE = "story"


def _on_pane_change(self, context):
    """三格一行里前两格选的是 cast,第三格根本不是 cast —— 所以 pane 是显示面、
    kind 仍是「哪个 cast」。pane 单向写 kind(反向永不发生),两者语义各自完整。"""
    state = context.scene.ruri_roster
    if state.pane in (CHARACTERS, NPCS) and state.kind != state.pane:
        state.kind = state.pane


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
    FILTER_SPEC_KEY = "Endfield:character"

    pane: EnumProperty(
        name="Pane",
        items=[(CHARACTERS, "Characters", "Playable characters, grouped by the game's own profession"),
               (NPCS, "NPCs", "Non-playable cast, one row per distinct model prefab"),
               (STORY_PANE, "Story", "The animations the game plays during story, under its own filing")],
        default=CHARACTERS,
        update=_on_pane_change)
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
    with filter_ui.rebuilding():
        _fill(state)


def _fill(state):
    # The selection is the cast member, not the row number: refilling this list
    # (a keystroke, a rule edit) must not hand the Load button a different one.
    chosen = filter_ui.selected_key(state)
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
    # The ones the game actually names come FIRST. A label falls back to the row's
    # own id when the game names it nothing, and ids are ASCII while the names are
    # not -- so plain alphabetical order pushed every named npc past 1600 ids, and
    # the list read as "this game has no localized names at all".
    named = table.values("display") if "display" in table.names else None
    order = sorted((int(index) for index in matched if shipped is None or shipped[int(index)]),
                   key=lambda index: (0 if named is not None and named[index] else 1,
                                      groups[index], labels[index]))
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
    filter_ui.restore_selection(state, chosen)


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


# What the game loads for one cast member is not a panel's business and never was
# -- the story stage needs the same answers. It lives in ``cast``; these are the
# names this module has always exposed, so every caller is untouched.
from .cast import (_at_detail_level, _avatar_skeleton, _character_model,  # noqa: F401
                   _materials_for, _named_mesh_guids, _npc_materials, _templet_skeleton,
                   character_model, character_tag, declared_face_morph, import_part,
                   material_cabs, model_parts, npc_info, npc_template)


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
        wanted = detail_level(context)
        chosen, level = _at_detail_level(hits, wanted)
        cabmap_state.clear_selection()
        for row in chosen:
            cabmap_state.SELECTED_CABS.add(row["cab"])

        result = bpy.ops.ruri.import_selected()
        if "FINISHED" not in result:
            return {"CANCELLED"}
        if level != wanted:
            self.report({"INFO"}, "'{0}' is not authored at LOD{1}; loaded LOD{2}.".format(
                entry.label, wanted, level))
        elif declared:
            self.report({"INFO"}, "Loaded '{0}' -- the model its own data asset declares.".format(declared))

        if state.load_expressions:
            self._load_expressions(context, entry, declared_face_morph(entry.key))
        return {"FINISHED"}

    def _load_npc(self, context, state, entry):
        """An npc is assembled, not shipped: its template's manifest names part
        SLOTS, its avatar-mesh family table says which mesh each slot wears, and
        every one of those meshes is skinned onto the skeleton the avatar template
        carries. The terminal state is ONE armature named after the model
        template, with every mesh parented and skinned onto it."""
        built = cast.assemble(context, entry.key, detail_level(context))
        for warning in built.warnings:
            self.report({"WARNING"}, warning)
        info = built.manifest
        missing = built.missing
        imported_any = built.armature is not None
        if info is None:
            return {"CANCELLED"}
        if not imported_any:
            return {"CANCELLED"}
        if missing:
            self.report({"WARNING"}, "{0} of {1} part slot(s) resolved to nothing: {2}".format(
                len(missing), len(info["parts"]), ", ".join(missing[:4])))
        else:
            self.report({"INFO"}, "Assembled '{0}' from {1} part slot(s).".format(
                entry.label, len(info["parts"])))
        if state.load_expressions:
            self._load_expressions(context, entry, info.get("facial_morph", ""))
        return {"FINISHED"}

    def _load_expressions(self, context, entry, declared=""):
        """The face library is a separate asset family, so it is a separate
        import -- the existing Character-tab flow, driven rather than copied.

        ``declared`` is the face-morph avatar this entity's own data names, when
        the caller already read it. Handing it over is what lets an npc bind at
        all: its face tables are named after something else entirely, so the
        Character tab could never have found them from the rig."""
        if declared:
            context.scene.ruri_character.face_morph = declared
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
        _info, hits, _missing = model_parts(entry.key, detail_level(context))
        if hits:
            cab, meshes = hits[0]
            return bpy.ops.ruri.cabmap_reveal(query=meshes[0], cab=cab)
        return bpy.ops.ruri.cabmap_reveal(query=entry.key)


def draw_roster(layout, context):
    state = context.scene.ruri_roster

    head = layout.row(align=True)
    head.prop(state, "pane", expand=True)
    head.operator(RURI_OT_roster_refresh.bl_idname, text="", icon="FILE_REFRESH")

    if state.pane == STORY_PANE:
        return STORY_PANE

    filter_ui.draw_search_row(layout, state)
    layout.template_list(RURI_UL_roster.bl_idname, "", state, "entries",
                         state, "active_index", rows=10)
    layout.label(text=state.status, icon="INFO")

    entry = _selected(state)
    options = layout.column(align=True)
    options.enabled = entry is not None
    options.row(align=True).prop(state, "model_kind", expand=True)
    # 与场景导入同一份选项(都读 ruri_cabmap.as_options),画在按 Load 的地方。
    cabmap = context.scene.ruri_cabmap
    row = options.row(align=True)
    row.prop(context.scene.ruri_cabmap, "detail_level")
    row.prop(state, "load_expressions", toggle=True, icon="SHAPEKEY_DATA")
    # 布料和表情一样是"这个模型自己带的第二份东西",所以并排;它就是浏览器那一个开关,
    # 不是这里的第二份状态 —— Load 走的本来就是浏览器自己的导入。
    if cloth_panel.addon_present():
        row.prop(cabmap, "import_secondary_motion", toggle=True, icon="MOD_CLOTH")
    shading = options.row(align=True)
    shading.prop(cabmap, "import_materials")
    game = shading.row(align=True)
    game.enabled = cabmap.import_materials
    game.prop(cabmap, "character_shaders")
    options.operator(RURI_OT_roster_load.bl_idname, icon="IMPORT")
    options.operator(RURI_OT_roster_reveal.bl_idname, icon="FILE_FOLDER")
    options.operator(RURI_OT_roster_animations.bl_idname, icon="ANIM_DATA")
    return state.pane


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
    cast.forget()
