"""Browse the game's cast the way the game itself lists it.

The rows come from the game's own config containers (see ``roster``): playable
characters keyed by charId, npcs keyed by npcId and grouped by the game's own
npcGroupId. Names are the real localized names, in whichever language Blender
is running in -- ``bpy.app.translations.locale`` picks which text container the
C# side joins through, so switching Blender's language switches the roster with
no reload of anything else.

The list behaves like the bundle browser next door: type to filter, click to
select, and one button reveals where the selection lives over in that browser.
"""

from __future__ import annotations

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       IntProperty, StringProperty)

from ...ruri_pybridge.session import cabmap_state
from . import roster

CHARACTERS = "characters"
NPCS = "npcs"

# Loaded tables, by (kind, language). Module scope, not scene state: these are
# columnar buffers, not something Blender's property system can hold.
_TABLES = {}


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
    if _table(state) is None:
        state.entries.clear()
        state.status = "Refresh to read the {0} out of the game's tables.".format(state.kind)
        return
    _rebuild(state)


class RURI_PG_roster(bpy.types.PropertyGroup):
    kind: EnumProperty(
        name="Cast",
        items=[(CHARACTERS, "Characters", "Playable characters, grouped by the game's own profession"),
               (NPCS, "NPCs", "Non-playable cast, grouped by the game's own npc group")],
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


def _language(state):
    """The game language this roster is shown in -- Blender's own locale,
    mapped onto the languages the game ships."""
    return roster.language_for_locale(bpy.app.translations.locale)


def _table(state):
    return _TABLES.get((state.kind, _language(state)))


def _rebuild(state):
    """Rebuild the drawn line list from the loaded table. Group headers are the
    game's own grouping field; a filter that empties a group drops the header
    with it."""
    state.entries.clear()
    table = _table(state)
    if table is None:
        return
    needle = state.search.strip().lower()
    detail_column = "english" if state.kind == CHARACTERS else "title"
    details = table.values(detail_column)

    total = 0
    for group, members in roster.grouped(table):
        kept = [member for member in members
                if not needle
                or needle in member[1].lower()
                or needle in member[2].lower()
                or needle in group.lower()]
        if not kept:
            continue
        header = state.entries.add()
        header.label = "{0}  ({1})".format(group or "(ungrouped)", len(kept))
        header.group = group
        header.is_group = True
        for row_index, label, key in kept:
            entry = state.entries.add()
            entry.label = label
            entry.key = key
            entry.group = group
            entry.detail = details[row_index]
            entry.row_index = row_index
            total += 1
    state.status = "{0} of {1} {2} · {3}".format(
        total, table.row_count, state.kind, _language(state))
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
            if state.kind == CHARACTERS:
                table = roster.load_characters(cabmap_state.BRIDGE, game_root, language)
            else:
                table = roster.load_npcs(cabmap_state.BRIDGE, game_root, language)
        except Exception as exc:
            state.status = "{0}: {1}".format(type(exc).__name__, exc)
            _report(self, state.status)
            return {"CANCELLED"}
        _TABLES[(state.kind, language)] = table
        _rebuild(state)
        return {"FINISHED"}


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
        entry = _selected(context.scene.ruri_roster)
        if entry is None:
            return {"CANCELLED"}
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
    row = options.row(align=True)
    row.prop(state, "lod")
    row.prop(state, "load_expressions", toggle=True, icon="SHAPEKEY_DATA")
    options.operator(RURI_OT_roster_reveal.bl_idname, icon="FILE_FOLDER")


_CLASSES = (
    RURI_PG_roster_entry,
    RURI_PG_roster,
    RURI_UL_roster,
    RURI_OT_roster_refresh,
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
    _TABLES.clear()
