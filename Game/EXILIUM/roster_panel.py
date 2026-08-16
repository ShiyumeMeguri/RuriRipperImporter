"""Browse the game's cast the way the game itself lists it.

Two panes over one dataset: ``Characters`` are the units the game lets you field,
named through whichever text package Blender's own locale reads
(``bpy.app.translations.locale`` picks it, so switching Blender's language switches
the roster with no reload of anything else); ``Models`` is every model the config
declares -- a character's outfits, the enemies, the summons -- each already
resolved to the address the catalog knows it by.

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
MODELS = datasets.MODELS

# Column labels worth spelling out; anything else reads as its own column name, so
# a column the hook adds to a cast shows up here with no edit.
_FIELD_LABELS = {"key": "Id", "label": "Name", "display": "English", "group": "Role",
                 "detail": "Detail", "character": "Character", "address": "Address",
                 "container": "Asset", "archives": "Archives", "models": "Outfits",
                 "shipped": "Downloaded"}

_ROWS = {}


def _filter_fields():
    """The rule vocabulary = the columns the CURRENTLY loaded cast has. The two
    casts are different projections, so their filterable fields genuinely differ --
    read off the table, never tabulated."""
    state = getattr(bpy.context.scene, "ruri_exilium_roster", None)
    table = _rows(state) if state is not None else None
    if table is None:
        return (("label", "Name"),)
    names = sorted(table.names, key=lambda name: 0 if name == "label" else 1)
    return tuple((name, _FIELD_LABELS.get(name, name.replace("_", " ").title()))
                 for name in names)


ROSTER_FILTER_SPEC = filter_ui.register_spec(filter_ui.FilterSpec(
    key="EXILIUM:character", fields=_filter_fields,
    state_for=lambda context: context.scene.ruri_exilium_roster,
    apply=lambda context: _rebuild(context.scene.ruri_exilium_roster)))


class RURI_PG_exilium_roster_entry(bpy.types.PropertyGroup):
    """One drawn line: either a group header or a cast member."""
    label: StringProperty()
    key: StringProperty()
    group: StringProperty()
    detail: StringProperty()
    address: StringProperty()
    shipped: BoolProperty(default=False)
    is_group: BoolProperty(default=False)


def _on_filter_edit(self, context):
    _rebuild(self)


def _on_kind_change(self, context):
    """Switching cast only redraws; it never fires the refresh operator. An
    operator called from a property update runs with the UI mid-update, and its
    poll failing there raises rather than reporting."""
    state = context.scene.ruri_exilium_roster
    if _rows(state) is None:
        state.entries.clear()
        state.status = "Refresh to read the {0} out of the game's tables.".format(state.kind)
        return
    _rebuild(state)


class RURI_PG_exilium_roster(filter_ui.FilterStateMixin, bpy.types.PropertyGroup):
    FILTER_SPEC_KEY = "EXILIUM:character"

    kind: EnumProperty(
        name="Cast",
        items=[(CHARACTERS, "Characters", "The units the game lets you field"),
               (MODELS, "Models", "Every model the game's own config declares")],
        default=CHARACTERS,
        update=_on_kind_change)
    search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"}, update=_on_filter_edit,
                           description="Filter by displayed name, id or group")
    entries: CollectionProperty(type=RURI_PG_exilium_roster_entry)
    active_index: IntProperty()
    status: StringProperty(default="Load a cabmap, then refresh the roster.")
    language: StringProperty(default="")
    downloaded_only: BoolProperty(
        name="Downloaded",
        default=True,
        description="Hide what the catalog names but this install never downloaded. "
                    "Those rows have nothing to import; showing them offers a Load button that lies")


def _language(state):
    return datasets.language_for_locale(bpy.app.translations.locale)


def _rows(state):
    return _ROWS.get((state.kind, _language(state)))


def _rebuild(state):
    """Rebuild the drawn line list.

    The filter is NOT evaluated here: the search text and the Include/Exclude rules
    go to the same C# engine the bundle browser searches with, over the very buffers
    this table was built from. This side receives row ids and reads cells."""
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
    matched = cabmap_state.BRIDGE.search_data_table(table, state.search.strip(), state.filter_rules)
    labels = table.values("label")
    groups = table.values("group")
    shipped = table.values("shipped")
    order = sorted((int(index) for index in matched
                    if not state.downloaded_only or shipped[int(index)]),
                   key=lambda index: (groups[index], labels[index]))
    matched_count = len(order)
    rows = [{name: table.cell(index, name)
             for name in ("key", "label", "detail", "group", "address", "shipped")}
            for index in order[:cabmap_state.LIST_CAP]]

    counts = {}
    for row in rows:
        counts[row["group"]] = counts.get(row["group"], 0) + 1

    current_group = None
    for row in rows:
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
        entry.address = row["address"]
        entry.shipped = bool(float(row["shipped"] or 0))
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


class RURI_UL_exilium_roster(bpy.types.UIList):
    bl_idname = "RURI_UL_exilium_roster"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if item.is_group:
            row = layout.row()
            row.enabled = False
            row.label(text=item.label, icon="OUTLINER_COLLECTION")
            return
        row = layout.row(align=True)
        row.label(text=item.label,
                  icon="OUTLINER_OB_ARMATURE" if item.shipped else "LIBRARY_DATA_BROKEN")
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


class RURI_OT_exilium_roster_refresh(bpy.types.Operator):
    """Read the cast out of the game's own config tables."""
    bl_idname = "ruri.exilium_roster_refresh"
    bl_label = "Refresh Roster"
    bl_description = "Read the cast out of the game's own config tables"

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = context.scene.ruri_exilium_roster
        language = _language(state)
        state.language = language
        try:
            table = datasets.cast(state.kind, language)
        except Exception as exc:
            state.status = "{0}: {1}".format(type(exc).__name__, exc)
            self.report({"WARNING"}, state.status)
            return {"CANCELLED"}
        _ROWS[(state.kind, language)] = table
        _rebuild(state)
        return {"FINISHED"}


class RURI_OT_exilium_roster_load(bpy.types.Operator):
    """Import the selected one's model.

    Deliberately not its own importer: it resolves the row's own address to the
    CABs the loaded map holds, puts them in the browser's own selection, and runs
    the browser's own import. One import path, so a fix there is a fix here."""
    bl_idname = "ruri.exilium_roster_load"
    bl_label = "Load Model"
    bl_description = "Import this one's model, exactly as the bundle browser would"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.scene.ruri_cabmap.loaded
                and cabmap_state.BRIDGE is not None
                and _selected(context.scene.ruri_exilium_roster) is not None)

    def execute(self, context):
        entry = _selected(context.scene.ruri_exilium_roster)
        if entry is None:
            return {"CANCELLED"}
        return load_address(self, entry.address, entry.label)


def load_address(operator, address, label):
    """Put whatever one address resolves to in the browser's own selection and run
    its own import. Shared by both tabs, because "load this one thing" is the same
    act whether the thing is a model or a scene."""
    if not address:
        operator.report({"WARNING"}, "'{0}' has no address in the game's own catalog.".format(label))
        return {"CANCELLED"}
    rows = datasets.cabs_for([address])
    cabs = [row["cab"] for row in rows if row["cab"]]
    if not cabs:
        known = any(row["container"] for row in rows)
        operator.report({"WARNING"},
                        "'{0}' is in the catalog but this install carries no archive for it -- "
                        "download it in the game first.".format(label) if known else
                        "'{0}' is not in this install's catalog.".format(label))
        return {"CANCELLED"}
    cabmap_state.clear_selection()
    for cab in cabs:
        cabmap_state.SELECTED_CABS.add(cab)
    result = bpy.ops.ruri.import_selected()
    if "FINISHED" not in result:
        return {"CANCELLED"}
    operator.report({"INFO"}, "Loaded '{0}' from {1} cab(s).".format(label, len(cabs)))
    return {"FINISHED"}


class RURI_OT_exilium_roster_reveal(bpy.types.Operator):
    """Open where the selection lives, over in the bundle browser."""
    bl_idname = "ruri.exilium_roster_reveal"
    bl_label = "Open Containing Folder"
    bl_description = "Switch to the bundle browser and open where this one's assets live"

    @classmethod
    def poll(cls, context):
        return _selected(context.scene.ruri_exilium_roster) is not None

    def execute(self, context):
        entry = _selected(context.scene.ruri_exilium_roster)
        if entry is None:
            return {"CANCELLED"}
        return reveal_address(entry.address, entry.key)


def reveal_address(address, fallback):
    """Reveal what one address resolves to. The query is the asset the GAME's own
    catalog named, never a path this add-on invented."""
    rows = datasets.cabs_for([address]) if address else []
    for row in rows:
        if row["cab"]:
            return bpy.ops.ruri.cabmap_reveal(query=row["container"], cab=row["cab"])
    return bpy.ops.ruri.cabmap_reveal(query=fallback)


class RURI_OT_exilium_roster_outfits(bpy.types.Operator):
    """List the selected character's own models, in the Models pane."""
    bl_idname = "ruri.exilium_roster_outfits"
    bl_label = "Show Outfits"
    bl_description = "Switch to the Models pane and list only the models this one wears"

    @classmethod
    def poll(cls, context):
        state = context.scene.ruri_exilium_roster
        return state.kind == CHARACTERS and _selected(state) is not None

    def execute(self, context):
        state = context.scene.ruri_exilium_roster
        entry = _selected(state)
        if entry is None:
            return {"CANCELLED"}
        wanted = entry.key
        state.kind = MODELS
        if _rows(state) is None:
            bpy.ops.ruri.exilium_roster_refresh()
        state.filter_rules.clear()
        rule = state.filter_rules.add()
        rule.field = "character"
        rule.relation = "is"
        rule.value = wanted
        rule.action = "include"
        _rebuild(state)
        return {"FINISHED"}


def draw_roster(layout, context):
    state = context.scene.ruri_exilium_roster

    head = layout.row(align=True)
    head.prop(state, "kind", expand=True)
    head.operator(RURI_OT_exilium_roster_refresh.bl_idname, text="", icon="FILE_REFRESH")

    filter_ui.draw_search_row(layout, state)
    layout.template_list(RURI_UL_exilium_roster.bl_idname, "", state, "entries",
                         state, "active_index", rows=10)
    layout.label(text=state.status, icon="INFO")

    entry = _selected(state)
    options = layout.column(align=True)
    options.prop(state, "downloaded_only", toggle=True, icon="IMPORT")
    actions = options.column(align=True)
    actions.enabled = entry is not None
    actions.operator(RURI_OT_exilium_roster_load.bl_idname, icon="IMPORT")
    actions.operator(RURI_OT_exilium_roster_reveal.bl_idname, icon="FILE_FOLDER")
    if state.kind == CHARACTERS:
        actions.operator(RURI_OT_exilium_roster_outfits.bl_idname, icon="MOD_CLOTH")


_CLASSES = (
    RURI_PG_exilium_roster_entry,
    RURI_PG_exilium_roster,
    RURI_UL_exilium_roster,
    RURI_OT_exilium_roster_refresh,
    RURI_OT_exilium_roster_load,
    RURI_OT_exilium_roster_reveal,
    RURI_OT_exilium_roster_outfits,
)


def register():
    filter_ui.register_spec(ROSTER_FILTER_SPEC)
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_exilium_roster = bpy.props.PointerProperty(type=RURI_PG_exilium_roster)


def unregister():
    del bpy.types.Scene.ruri_exilium_roster
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _ROWS.clear()
