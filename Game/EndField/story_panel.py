"""Browse the animations the game plays during story, filed the way it files them.

The game plays story through two things, and keeps each one's animations in its
own folder: a CUTSCENE splits into shots (``animations/sc001/``) and names every
file after what it moves (``a_actor_pelica_01_cs_e0m2_2_sc001``), and a DIALOGUE
TIMELINE keeps one clip per spoken line next to the morph asset it drives. That
filing IS the classification -- unit, then shot, then kind and actor -- so this
tab reads it off the game's own container paths (``endfield.story.units`` /
``endfield.story.clips``) instead of matching names here.

Nothing is decoded to browse: both datasets are pure cabmap reads, so opening a
unit and looking at its 400 clips costs a table lookup. A clip is only ever built
when it is checked and imported, and importing routes into the bundle browser's
own clip import -- one import path, so a fix there is a fix here.

The two lists split the shared filter the way their sizes ask for: the unit list
carries the full search box + Include/Exclude rule editor (509 units across both
channels), and the clip list carries a plain search box. Both run the same
vectorized C# engine over the very table the rows came from; neither matches
anything in Python.
"""

from __future__ import annotations

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       IntProperty, StringProperty)

from ... import filter_ui
from ...RuriRipperPyBridge.session import cabmap_state
from . import datasets

STORY_SPEC_KEY = "EndField:story"

# Column labels worth spelling out; any other column the hook adds to the table
# shows up under its own name with no edit here.
_FIELD_LABELS = {"unit": "Story Unit", "group": "Group", "shots": "Shots", "clips": "Clips",
                 "assets": "Assets", "actors": "Actors", "variants": "Variants",
                 "anchor": "Folder", "channel": "Channel"}

# What each row of the clip list moves, as the game's own leaf-name vocabulary.
# Only the icon is ours; an unlisted kind simply draws with the default one.
_KIND_ICONS = {"actor": "OUTLINER_OB_ARMATURE", "npc": "OUTLINER_OB_ARMATURE",
               "monster": "GHOST_ENABLED", "cam": "VIEW_CAMERA", "item": "MESH_DATA",
               "wpn": "TOOL_SETTINGS", "prop": "MESH_CUBE", "cprop": "MESH_CUBE",
               "cbuild": "HOME", "crock": "MESH_ICOSPHERE", "cfoliage": "OUTLINER_OB_POINTCLOUD",
               "morphanim": "SHAPEKEY_DATA", "morphanimso": "PRESET"}
_DEFAULT_KIND_ICON = "DOT"

# Loaded tables, by channel. Module scope, not scene state: a redraw must not
# cost a re-read, and a ColumnTable is not something Blender's RNA can hold.
_UNITS = {}
_CLIPS = {}

# What is checked, per (channel, unit), as {container path: cab}. The drawn rows
# are a WINDOW onto a filtered, capped view of the unit's table, so the checked
# set cannot live on them: narrowing the filter after checking twenty clips would
# quietly import whatever happened to still be on screen.
_CHECKED = {}


def _units_table(state):
    return _UNITS.get(state.channel)


def _clips_table(state):
    return _CLIPS.get((state.channel, state.unit))


def _filter_fields():
    """The rule vocabulary = the columns the loaded unit table actually has, so a
    column the hook adds is filterable the day it appears. The unit name leads:
    the first field is what a new rule starts on."""
    state = getattr(bpy.context.scene, "ruri_story", None)
    table = _units_table(state) if state is not None else None
    if table is None:
        return (("unit", "Story Unit"),)
    names = sorted(table.names, key=lambda name: 0 if name == "unit" else 1)
    return tuple((name, _FIELD_LABELS.get(name, name.replace("_", " ").title()))
                 for name in names)


def _quick_relation(field):
    return "is" if field in ("shots", "clips", "assets", "actors") else "contains"


STORY_FILTER_SPEC = filter_ui.register_spec(filter_ui.FilterSpec(
    key=STORY_SPEC_KEY, fields=_filter_fields,
    state_for=lambda context: context.scene.ruri_story,
    apply=lambda context: _rebuild_units(context.scene.ruri_story),
    quick_relation_for=_quick_relation))


def _on_filter_edit(self, context):
    _rebuild_units(self)


def _on_clip_filter_edit(self, context):
    _rebuild_clips(self)


def _on_channel_change(self, context):
    """Switching channel only redraws. It never calls the refresh operator: an
    operator invoked from a property update runs with the UI mid-update, and a
    failing poll there raises instead of reporting."""
    self.unit = ""
    self.clips.clear()
    if _units_table(self) is None:
        self.entries.clear()
        self.status = "Refresh to read the {0} units out of the loaded cabmap.".format(self.channel)
        return
    _rebuild_units(self)


def _on_unit_pick(self, context):
    """Clicking a unit opens it. The clips dataset is a cabmap read cached by
    (id, args) on the C# side, so walking the list with the arrow keys re-reads
    nothing already seen."""
    entry = _selected_unit(self)
    if entry is None or entry.key == self.unit:
        return
    _open_unit(self, entry.key)


def _on_clip_check(self, context):
    """A tick writes straight into the unit's own checked set, so it survives the
    list being rebuilt by a filter edit or a redraw."""
    state = context.scene.ruri_story
    checked = _CHECKED.setdefault((state.channel, state.unit), {})
    if self.selected and self.clip:
        checked[self.container] = self.cab
    else:
        checked.pop(self.container, None)


class RURI_PG_story_entry(bpy.types.PropertyGroup):
    """One drawn line of the unit list: a group header or a story unit."""
    label: StringProperty()
    key: StringProperty()
    group: StringProperty()
    detail: StringProperty()
    is_group: BoolProperty(default=False)


class RURI_PG_story_clip(bpy.types.PropertyGroup):
    """One drawn line of the clip list: a shot/kind header, or one animation the
    unit plays. ``clip`` is false for a row the game files next to the animations
    without it being one (a dialogue timeline's morph asset), which is why the
    import button counts them separately instead of failing on them."""
    label: StringProperty()
    shot: StringProperty()
    kind: StringProperty()
    actor: StringProperty()
    name: StringProperty()
    container: StringProperty()
    cab: StringProperty()
    clip: BoolProperty(default=True)
    is_group: BoolProperty(default=False)
    selected: BoolProperty(default=False, update=_on_clip_check)


class RURI_PG_story(filter_ui.FilterStateMixin, bpy.types.PropertyGroup):
    FILTER_SPEC_KEY = STORY_SPEC_KEY

    channel: EnumProperty(
        name="Channel",
        items=[(datasets.CUTSCENE, "Cutscene",
                "The game's cutscenes: body, camera and prop animation, split into the shots "
                "the game splits them into"),
               (datasets.DIALOG, "Dialogue",
                "The game's dialogue timelines: one facial animation per spoken line, next to "
                "the morph asset it drives")],
        default=datasets.CUTSCENE,
        update=_on_channel_change)
    search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"}, update=_on_filter_edit,
                           description="Filter the units by any column the table has")
    entries: CollectionProperty(type=RURI_PG_story_entry)
    active_index: IntProperty(update=_on_unit_pick)
    status: StringProperty(default="Load a cabmap, then refresh.")

    unit: StringProperty()
    unit_detail: StringProperty()
    clip_search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"},
                                update=_on_clip_filter_edit,
                                description="Filter this unit's animations by shot, kind, actor or name")
    clips: CollectionProperty(type=RURI_PG_story_clip)
    clips_active_index: IntProperty()
    clip_status: StringProperty(default="")


def _rebuild_units(state):
    """Rebuild the drawn unit list. The search text and the Include/Exclude rules
    go to the same C# engine the bundle browser searches with, over the very
    buffers this table was built from; this side receives row ids and reads
    cells."""
    state.entries.clear()
    table = _units_table(state)
    if table is None:
        return
    matched = cabmap_state.BRIDGE.search_data_table(table, state.search.strip(), state.filter_rules)
    groups = table.values("group")
    units = table.values("unit")
    order = sorted((int(index) for index in matched),
                   key=lambda index: (groups[index], units[index]))
    rows = [{name: table.cell(index, name)
             for name in ("unit", "group", "shots", "clips", "actors", "variants")}
            for index in order[:cabmap_state.DISPLAY_CAP]]

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
        entry.label = row["unit"]
        entry.key = row["unit"]
        entry.group = row["group"]
        entry.detail = _unit_detail(row)
    state.status = "{0} of {1} {2} unit(s){3}".format(
        len(order), len(table), state.channel,
        "" if len(order) == len(rows) else
        " · showing {0}, narrow the filter to see the rest".format(len(rows)))
    if state.active_index >= len(state.entries):
        state.active_index = 0


def _unit_detail(row):
    """The one-line summary of a unit: what the game's own folder holds. A shot
    count of zero is a unit whose animations the game does not ship separately,
    which is worth reading rather than hiding."""
    shots = int(row["shots"] or 0)
    parts = ["{0} shot(s)".format(shots)] if shots else []
    parts.append("{0} clip(s)".format(int(row["clips"] or 0)))
    actors = int(row["actors"] or 0)
    if actors:
        parts.append("{0} actor(s)".format(actors))
    if row["variants"]:
        parts.append(row["variants"])
    return " · ".join(parts)


def _open_unit(state, unit):
    """Read one unit's animations and draw them grouped by the game's own split:
    a cutscene by shot, a dialogue timeline by what the asset drives."""
    state.unit = unit
    state.clips.clear()
    state.clip_status = ""
    if not unit or cabmap_state.BRIDGE is None:
        return
    try:
        table = datasets.story_clips(state.channel, unit)
    except Exception as exc:
        state.clip_status = "{0}: {1}".format(type(exc).__name__, exc)
        return
    _CLIPS[(state.channel, unit)] = table
    _rebuild_clips(state)


def _rebuild_clips(state):
    table = _clips_table(state)
    state.clips.clear()
    if table is None:
        return
    checked = _CHECKED.get((state.channel, state.unit), {})
    matched = cabmap_state.BRIDGE.search_data_table(table, state.clip_search.strip(), None)
    order = sorted(int(index) for index in matched)
    rows = [{name: table.cell(index, name)
             for name in ("shot", "kind", "actor", "name", "container", "cab", "clip")}
            for index in order[:cabmap_state.DISPLAY_CAP]]

    counts = {}
    for row in rows:
        counts[_bucket(row)] = counts.get(_bucket(row), 0) + 1

    current = None
    for row in rows:
        bucket = _bucket(row)
        if bucket != current:
            current = bucket
            header = state.clips.add()
            header.label = "{0}  ({1})".format(bucket, counts[bucket])
            header.is_group = True
        item = state.clips.add()
        item.shot = row["shot"]
        item.kind = row["kind"]
        item.actor = row["actor"]
        item.name = row["name"]
        item.container = row["container"]
        item.cab = row["cab"]
        item.clip = bool(int(row["clip"] or 0))
        item.selected = row["container"] in checked
        item.label = row["actor"] or row["name"]
    state.clip_status = "{0} of {1} row(s){2}{3}".format(
        len(order), len(table),
        "" if len(order) == len(rows) else " · showing {0}".format(len(rows)),
        " · {0} checked".format(len(checked)) if checked else "")
    if state.clips_active_index >= len(state.clips):
        state.clips_active_index = 0


def _bucket(row):
    """The group a clip row is drawn under -- the game's own split: the shot for a
    cutscene, and what the asset drives for a dialogue timeline (which files no
    shots)."""
    return row["shot"] or row["kind"] or "(ungrouped)"


def _selected_unit(state):
    if 0 <= state.active_index < len(state.entries):
        entry = state.entries[state.active_index]
        if not entry.is_group:
            return entry
    return None


def _checked(state):
    """{container path: cab} for the open unit -- the WHOLE checked set, including
    rows the current filter is hiding."""
    return _CHECKED.get((state.channel, state.unit), {})


class RURI_UL_story_units(bpy.types.UIList):
    bl_idname = "RURI_UL_story_units"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if item.is_group:
            row = layout.row()
            row.enabled = False
            row.label(text=item.label, icon="OUTLINER_COLLECTION")
            return
        row = layout.row(align=True)
        row.label(text=item.label, icon="SEQ_STRIP_DUPLICATE")
        detail = row.row()
        detail.enabled = False
        detail.alignment = "RIGHT"
        detail.label(text=item.detail)

    def filter_items(self, context, data, propname):
        # Filtering already happened against the game's own columns in
        # _rebuild_units, not against the drawn string -- leave the list alone.
        return [], []


class RURI_UL_story_clips(bpy.types.UIList):
    bl_idname = "RURI_UL_story_clips"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if item.is_group:
            row = layout.row()
            row.enabled = False
            row.label(text=item.label, icon="SEQUENCE")
            return
        row = layout.row(align=True)
        checkbox = row.row()
        checkbox.enabled = item.clip
        checkbox.prop(item, "selected", text="")
        row.label(text=item.kind, icon=_KIND_ICONS.get(item.kind, _DEFAULT_KIND_ICON))
        row.label(text=item.label)
        if not item.clip:
            note = row.row()
            note.enabled = False
            note.alignment = "RIGHT"
            note.label(text="not a clip")

    def filter_items(self, context, data, propname):
        return [], []


class RURI_OT_story_refresh(bpy.types.Operator):
    """Read the story units out of the loaded cabmap."""
    bl_idname = "ruri.story_refresh"
    bl_label = "Refresh Story Units"
    bl_description = "List the cutscenes / dialogue timelines the game ships animations for"

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = context.scene.ruri_story
        try:
            table = datasets.story_units(state.channel)
        except Exception as exc:
            state.status = "{0}: {1}".format(type(exc).__name__, exc)
            self.report({"WARNING"}, state.status)
            return {"CANCELLED"}
        _UNITS[state.channel] = table
        _rebuild_units(state)
        return {"FINISHED"}


class RURI_OT_story_select(bpy.types.Operator):
    """Check or uncheck this unit's animations -- everything, nothing, or just
    the ones moving one kind of thing (the actors, say, and not the cameras and
    props alongside them)."""
    bl_idname = "ruri.story_select"
    bl_label = "Check Animations"
    bl_description = "Check or uncheck the listed animations"
    bl_options = {"REGISTER", "UNDO"}
    mode: StringProperty(default="ALL")
    kind: StringProperty(default="")

    @classmethod
    def poll(cls, context):
        return len(context.scene.ruri_story.clips) > 0

    def execute(self, context):
        state = context.scene.ruri_story
        if self.mode == "NONE":
            # The whole set, not just what is drawn: unchecking has to be able to
            # undo a check made under a different filter.
            _CHECKED.pop((state.channel, state.unit), None)
        for item in state.clips:
            if item.is_group or not item.clip:
                continue
            if self.mode == "ALL":
                item.selected = True
            elif self.mode == "NONE":
                item.selected = False
            elif self.mode == "KIND":
                item.selected = item.kind == self.kind
        return {"FINISHED"}


class RURI_OT_story_import(bpy.types.Operator):
    """Build the checked animations as actions on the rig in the scene.

    Deliberately not its own importer: it puts the checked rows' CABs in the
    browser's own selection and runs the browser's own import, which resolves one
    shared closure for the whole batch and attaches the clips to the armature the
    user has selected. One import path, so a fix there is a fix here."""
    bl_idname = "ruri.story_import"
    bl_label = "Import Checked Animations"
    bl_description = ("Build the checked animations onto the selected rig -- select the actor's "
                      "armature in the viewport first")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None
                and bool(_checked(context.scene.ruri_story)))

    def execute(self, context):
        state = context.scene.ruri_story
        checked = _checked(state)
        if not checked:
            self.report({"WARNING"}, "Nothing checked.")
            return {"CANCELLED"}

        cabmap_state.clear_selection()
        cabmap_state.SELECTED_CABS.update(checked.values())
        return bpy.ops.ruri.import_selected()


class RURI_OT_story_reveal(bpy.types.Operator):
    """Open where this unit's animations live, over in the bundle browser.

    The folder is the one the hook read off the game's own filing (the unit's
    anchor), never a path this add-on invented."""
    bl_idname = "ruri.story_reveal"
    bl_label = "Open Containing Folder"
    bl_description = "Switch to the bundle browser and open this unit's own animation folder"

    @classmethod
    def poll(cls, context):
        state = context.scene.ruri_story
        return context.scene.ruri_cabmap.loaded and bool(state.unit)

    def execute(self, context):
        state = context.scene.ruri_story
        checked = _checked(state)
        if checked:
            container, cab = next(iter(checked.items()))
        else:
            drawn = [item for item in state.clips if not item.is_group]
            if not drawn:
                return bpy.ops.ruri.cabmap_reveal(query=state.unit)
            container, cab = drawn[0].container, drawn[0].cab
        return bpy.ops.ruri.cabmap_reveal(query=state.unit,
                                          folder=container.rpartition("/")[0], cab=cab)


def _kinds_in(state):
    """The kinds this unit's own rows carry, in first-seen order -- the buttons
    that make sense for THIS unit rather than a tabulated list of every kind the
    game has."""
    kinds = []
    for item in state.clips:
        if not item.is_group and item.clip and item.kind and item.kind not in kinds:
            kinds.append(item.kind)
    return kinds


def draw_story_tab(layout, context):
    state = context.scene.ruri_story

    head = layout.row(align=True)
    head.prop(state, "channel", expand=True)
    filter_ui.draw_search_row(layout, state,
                              extra_operator=(RURI_OT_story_refresh.bl_idname, "FILE_REFRESH"))
    layout.template_list(RURI_UL_story_units.bl_idname, "", state, "entries",
                         state, "active_index", rows=8)
    layout.label(text=state.status, icon="INFO")

    if not state.unit:
        layout.label(text="Pick a unit to see the animations it plays.", icon="ANIM_DATA")
        return

    layout.separator()
    box = layout.box()
    box.label(text=state.unit, icon="SEQ_STRIP_DUPLICATE")
    box.prop(state, "clip_search", icon="VIEWZOOM", text="")

    row = box.row(align=True)
    op = row.operator(RURI_OT_story_select.bl_idname, text="All")
    op.mode = "ALL"
    op = row.operator(RURI_OT_story_select.bl_idname, text="None")
    op.mode = "NONE"
    for kind in _kinds_in(state)[:4]:
        op = row.operator(RURI_OT_story_select.bl_idname, text=kind.title())
        op.mode = "KIND"
        op.kind = kind

    box.template_list(RURI_UL_story_clips.bl_idname, "", state, "clips",
                      state, "clips_active_index", rows=10)

    checked = _checked(state)
    box.label(text=state.clip_status, icon="INFO")
    actions = box.column(align=True)
    actions.operator(RURI_OT_story_import.bl_idname, icon="IMPORT",
                     text="Import {0} Checked Animation(s)".format(len(checked)) if checked
                     else "Import Checked Animations")
    actions.operator(RURI_OT_story_reveal.bl_idname, icon="FILE_FOLDER")


_CLASSES = (
    RURI_PG_story_entry,
    RURI_PG_story_clip,
    RURI_PG_story,
    RURI_UL_story_units,
    RURI_UL_story_clips,
    RURI_OT_story_refresh,
    RURI_OT_story_select,
    RURI_OT_story_import,
    RURI_OT_story_reveal,
)


def register():
    filter_ui.register_spec(STORY_FILTER_SPEC)
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_story = bpy.props.PointerProperty(type=RURI_PG_story)


def unregister():
    del bpy.types.Scene.ruri_story
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _UNITS.clear()
    _CLIPS.clear()
    _CHECKED.clear()
