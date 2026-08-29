"""Browse the animations the game plays during story, filed the way it files them.

The game plays story through two things, and keeps each one's animations in its
own folder: a CUTSCENE splits into shots (``animations/sc001/``) and names every
file after what it moves (``a_actor_pelica_01_cs_e0m2_2_sc001``), and a DIALOGUE
TIMELINE keeps one clip per spoken line next to the morph asset it drives. That
filing IS the classification -- unit, then shot, then kind and actor -- so this
tab reads it off the game's own container paths (``endfield.story.units`` /
``endfield.story.clips``) instead of matching names here.

The same library answers a second question the folders alone make painful: BY
ACTOR. "Everything the story animates this one through" is scattered across every
cutscene they appear in, plus their dialogue lines, plus their own animation
library -- so the actor index (``endfield.story.actors``) is a first-class list
here, and picking one collects all three (``story_clips(actor=...)``).

Nothing is decoded to browse: both datasets are pure cabmap reads, so opening a
unit and looking at its 400 clips costs a table lookup. A clip is only ever built
when it is checked and imported, and importing routes into the bundle browser's
own clip import -- one import path, so a fix there is a fix here.

The two lists split the shared filter the way their sizes ask for: the unit list
carries the full search box + Include/Exclude rule editor (509 units across both
channels), and the clip list carries a plain search box. Both run the same
vectorized C# engine over the very table the rows came from; neither matches
anything in Python.

WHAT A UNIT IS is a second question the folders cannot answer, and it is the one
a folder id like ``cutscene_c31m3_1`` is worst at: it is the cutscene of 拳心, a
side mission of chapter two that happens in 景玉谷 and belongs to 弭弗. That
belongs to the mission the game plays the unit from, so the unit list is asked
for a LANGUAGE and every row carries its mission's own name, kind, chapter and
place -- which also means the search box searches those, and typing a mission's
name finds everything it plays. Picking a unit opens the rest of it: what the
mission is about, its quest graph in the words the player reads, and every line
the unit speaks with the speaker and the emotion the face is driven to.
"""

from __future__ import annotations

import re

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       IntProperty, StringProperty)

from ... import filter_ui
from ...RuriRipperPyBridge.session import cabmap_state
from . import datasets, roster_panel

STORY_SPEC_KEY = "Endfield:story"

# Column labels worth spelling out; any other column the hook adds to the table
# shows up under its own name with no edit here.
_FIELD_LABELS = {"unit": "Story Unit", "group": "Group", "shots": "Shots", "clips": "Clips",
                 "assets": "Assets", "actors": "Actors", "variants": "Variants",
                 "anchor": "Folder", "channel": "Channel", "actor": "Actor",
                 "character": "Character Id", "kinds": "Kinds", "units": "Units",
                 "cutscenes": "Cutscene Clips", "dialogs": "Dialogue Clips",
                 "mission": "Mission Id", "title": "Mission", "kind": "Mission Kind",
                 "chapter": "Chapter", "level": "Level Id", "place": "Place",
                 "lines": "Spoken Lines", "summary": "Recap", "relation": "Attributed By"}

# The two ways to come at the same library: by the演出 the game plays, or by the
# one it animates. Both are the game's own filing -- the second is the reason this
# mode exists at all, since "everything this character is animated through" is
# otherwise spread over every cutscene they appear in.
BY_STORY = "story"
BY_ACTOR = "actor"

# What each row of the clip list moves, as the game's own leaf-name vocabulary.
# Only the icon is ours; an unlisted kind simply draws with the default one.
_KIND_ICONS = {"actor": "OUTLINER_OB_ARMATURE", "npc": "OUTLINER_OB_ARMATURE",
               "monster": "GHOST_ENABLED", "cam": "VIEW_CAMERA", "item": "MESH_DATA",
               "wpn": "TOOL_SETTINGS", "prop": "MESH_CUBE", "cprop": "MESH_CUBE",
               "cbuild": "HOME", "crock": "MESH_ICOSPHERE", "cfoliage": "OUTLINER_OB_POINTCLOUD",
               "morphanim": "SHAPEKEY_DATA", "morphanimso": "PRESET"}
_DEFAULT_KIND_ICON = "DOT"

# What the panel draws its two halves from: the ANIMATIONS a unit plays, or the
# SCRIPT it plays them to. Both are the same unit; which one is on top is the
# question the user is asking about it right now.
BY_ANIMATION = "animation"
BY_SCRIPT = "script"

# Loaded tables, by channel. Module scope, not scene state: a redraw must not
# cost a re-read, and a ColumnTable is not something Blender's RNA can hold.
_UNITS = {}
_CLIPS = {}
_LINES = {}
_QUESTS = {}

# What is checked, per (channel, unit), as {container path: cab}. The drawn rows
# are a WINDOW onto a filtered, capped view of the unit's table, so the checked
# set cannot live on them: narrowing the filter after checking twenty clips would
# quietly import whatever happened to still be on screen.
_CHECKED = {}

# actor token -> the armature object this session built for it. Filled by the
# whole-unit load, read by the checked-clip import so a clip lands on the rig of
# the one it animates instead of on "whatever is selected".
_ACTOR_RIGS = {}

# Which kinds of row name something the game ships a MODEL for. The others a
# cutscene moves (cameras, items, props, buildings) are animations on scene
# objects the story spawns, not on a rig that can be loaded on its own.
_MODEL_KINDS = ("actor", "npc", "monster")

# The Timeline track classes that place an animation. The others a cutscene
# carries (activation, control, audio) are its wiring, not its motion.
_ANIMATION_TRACKS = ("AnimationTrack",)

# The rest of the game's own track vocabulary, by what a host has to DO with it:
# switch something on for a span, or mark when something the scene cannot play
# back (a spoken line, an audio event) happens.
_ACTIVATION_TRACK = "ActivationTrack"
_MARKER_TRACKS = ("SubtitleTrack", "AudioTrack", "BigLogoTrack", "CommonMaskTrack")
_CAMERA_KIND = "cam"


def _language():
    """The game language this tab is shown in -- Blender's own locale, mapped onto
    the languages the game ships. Same rule as every other list in this add-on, so
    switching Blender's language switches the story with it."""
    return datasets.language_for_locale(bpy.app.translations.locale)


def _top_table(state):
    """The list on top: the story units of the current channel, or the actor index.
    Cached per (mode, channel) -- both are cabmap reads, and a redraw must not
    re-ask for one."""
    return _UNITS.get((state.mode, state.channel if state.mode == BY_STORY else ""))


def _clips_table(state):
    return _CLIPS.get(_selection_key(state))


def _lines_table(state):
    return _LINES.get(_selection_key(state))


def _quests_table(state):
    return _QUESTS.get(state.mission)


def _selection_key(state):
    """What the open clip list belongs to. The actor view deliberately spans every
    channel, so the channel is not part of its key."""
    return (BY_STORY, state.channel, state.unit) if state.mode == BY_STORY \
        else (BY_ACTOR, "", state.actor)


def _key_column(state):
    return "unit" if state.mode == BY_STORY else "actor"


def _filter_fields():
    """The rule vocabulary = the columns the loaded list actually has, so a column
    the hook adds is filterable the day it appears, and the two modes offer their
    own genuinely different fields. The list's own key leads: the first field is
    what a new rule starts on."""
    state = getattr(bpy.context.scene, "ruri_story", None)
    table = _top_table(state) if state is not None else None
    if table is None:
        return (("unit", "Story Unit"),)
    leading = _key_column(state)
    names = sorted(table.names, key=lambda name: 0 if name == leading else 1)
    return tuple((name, _FIELD_LABELS.get(name, name.replace("_", " ").title()))
                 for name in names)


def _quick_relation(field):
    return "is" if field in ("shots", "clips", "assets", "actors", "units",
                             "cutscenes", "dialogs") else "contains"


STORY_FILTER_SPEC = filter_ui.register_spec(filter_ui.FilterSpec(
    key=STORY_SPEC_KEY, fields=_filter_fields,
    state_for=lambda context: context.scene.ruri_story,
    apply=lambda context: _rebuild_top(context.scene.ruri_story),
    quick_relation_for=_quick_relation))


def _on_filter_edit(self, context):
    _rebuild_top(self)


def _on_clip_filter_edit(self, context):
    _rebuild_clips(self)


def _on_line_filter_edit(self, context):
    _rebuild_lines(self)


def _on_view_change(self, context):
    """Switching mode or channel only redraws. It never calls the refresh
    operator: an operator invoked from a property update runs with the UI
    mid-update, and a failing poll there raises instead of reporting."""
    self.unit = ""
    self.actor = ""
    self.clips.clear()
    self.clip_status = ""
    _forget_context(self)
    if _top_table(self) is None:
        self.entries.clear()
        self.status = "Refresh to read the {0} out of the loaded cabmap.".format(
            "actor index" if self.mode == BY_ACTOR else self.channel + " units")
        return
    _rebuild_top(self)


def _on_entry_pick(self, context):
    """Clicking a row opens it. Both clip reads are cabmap reads cached by
    (id, args) on the C# side, so walking the list with the arrow keys re-reads
    nothing already seen."""
    if filter_ui.is_rebuilding():
        return
    entry = _selected_entry(self)
    if entry is None or entry.key == (self.unit if self.mode == BY_STORY else self.actor):
        return
    _open_entry(self, entry.key)


def _on_clip_check(self, context):
    """A tick writes straight into the open selection's checked set, so it
    survives the list being rebuilt by a filter edit or a redraw."""
    state = context.scene.ruri_story
    checked = _CHECKED.setdefault(_selection_key(state), {})
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
    channel: StringProperty()
    unit: StringProperty()
    shot: StringProperty()
    kind: StringProperty()
    actor: StringProperty()
    name: StringProperty()
    container: StringProperty()
    cab: StringProperty()
    clip: BoolProperty(default=True)
    is_group: BoolProperty(default=False)
    selected: BoolProperty(default=False, update=_on_clip_check)


class RURI_PG_story_line(bpy.types.PropertyGroup):
    """One drawn line of what a unit says: a spoken line, a reply the player is
    offered, or a cutscene subtitle. ``who`` is the speaker's display name, which
    a cutscene subtitle does not have -- the game files those without a speaker
    row, so the row is drawn as the narration it is rather than blamed on nobody."""
    label: StringProperty()
    kind: StringProperty()
    speaker: StringProperty()
    who: StringProperty()
    text: StringProperty()
    emotion: StringProperty()
    order: IntProperty()
    is_group: BoolProperty(default=False)


class RURI_PG_story_quest(bpy.types.PropertyGroup):
    """One objective of the mission that plays the open unit, in the words the
    player reads. ``dialog`` and ``cutscene`` are what the game itself says this
    objective waits on, which is the one attribution that is stated rather than
    read off a file name."""
    label: StringProperty()
    quest: StringProperty()
    description: StringProperty()
    waits: StringProperty()
    place: StringProperty()
    dialog: StringProperty()
    cutscene: StringProperty()
    main_path: IntProperty(default=-1)
    is_group: BoolProperty(default=False)


class RURI_PG_story(filter_ui.FilterStateMixin, bpy.types.PropertyGroup):
    FILTER_SPEC_KEY = STORY_SPEC_KEY

    mode: EnumProperty(
        name="Browse",
        items=[(BY_STORY, "By Story",
                "One cutscene or dialogue timeline at a time, split into the shots the game "
                "splits it into"),
               (BY_ACTOR, "By Actor",
                "One character at a time: every animation the story plays them through, across "
                "every cutscene and dialogue, plus their own animation library")],
        default=BY_STORY,
        update=_on_view_change)
    channel: EnumProperty(
        name="Channel",
        items=[(datasets.CUTSCENE, "Cutscene",
                "The game's cutscenes: body, camera and prop animation, split into the shots "
                "the game splits them into"),
               (datasets.DIALOG, "Dialogue",
                "The game's dialogue timelines: one facial animation per spoken line, next to "
                "the morph asset it drives")],
        default=datasets.CUTSCENE,
        update=_on_view_change)
    search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"}, update=_on_filter_edit,
                           description="Filter the list by any column the table has")
    entries: CollectionProperty(type=RURI_PG_story_entry)
    active_index: IntProperty(update=_on_entry_pick)
    status: StringProperty(default="Load a cabmap, then refresh.")

    unit: StringProperty()
    actor: StringProperty()
    clip_search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"},
                                update=_on_clip_filter_edit,
                                description="Filter these animations by unit, shot, kind, actor or name")
    clips: CollectionProperty(type=RURI_PG_story_clip)
    clips_active_index: IntProperty()
    clip_status: StringProperty(default="")

    content: EnumProperty(
        name="Show",
        items=[(BY_ANIMATION, "Animation",
                "The animations this unit plays, grouped the way the game splits them"),
               (BY_SCRIPT, "Script",
                "What this unit says and what the mission playing it asks the player to do")],
        default=BY_ANIMATION)

    # What the open unit IS, as the mission that plays it states it. Flat strings
    # rather than a re-read: the panel redraws constantly and this is what it draws.
    mission: StringProperty()
    mission_title: StringProperty()
    mission_kind: StringProperty()
    mission_chapter: StringProperty()
    mission_level: StringProperty()
    mission_place: StringProperty()
    mission_description: StringProperty()
    mission_character: StringProperty()
    unit_summary: StringProperty()
    unit_relation: StringProperty()

    line_search: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"},
                                update=_on_line_filter_edit,
                                description="Filter what is said by speaker, text or emotion")
    lines: CollectionProperty(type=RURI_PG_story_line)
    lines_active_index: IntProperty()
    line_status: StringProperty(default="")

    quests: CollectionProperty(type=RURI_PG_story_quest)
    quests_active_index: IntProperty()
    quest_status: StringProperty(default="")

    # Who the open ACTOR is, and which stories they are in. Separate from the
    # mission fields above on purpose: the two views open different things, and one
    # set of strings meaning two things is how a panel starts drawing the wrong one.
    actor_who: StringProperty()
    actor_title: StringProperty()
    actor_named: StringProperty()
    actor_character: StringProperty()
    actor_stories: StringProperty()
    actor_places: StringProperty()


def _rebuild_top(state):
    """Rebuild the drawn list on top -- units or actors, whichever mode is on. The
    search text and the Include/Exclude rules go to the same C# engine the bundle
    browser searches with, over the very buffers this table was built from; this
    side receives row ids and reads cells.

    What is OPEN survives this: the selection's identity is its key, not its
    position, so the highlight is re-pointed at the same unit/actor afterwards --
    and a filter that hides it leaves it open rather than swapping it for
    whichever row inherited the index."""
    with filter_ui.rebuilding():
        _fill_top(state)


def _fill_top(state):
    state.entries.clear()
    table = _top_table(state)
    if table is None:
        return
    matched = cabmap_state.BRIDGE.search_data_table(table, state.search.strip(), state.filter_rules)
    by_story = state.mode == BY_STORY
    key_column = _key_column(state)
    keys = table.values(key_column)
    if by_story:
        # Units of one mission belong together, and the mission is what the game
        # plays them from -- so the folder prefix only orders the units nothing
        # attributed, which is exactly the set it is still the best answer for.
        missions = table.values("mission")
        groups = table.values("group")
        order = sorted((int(index) for index in matched),
                       key=lambda index: (missions[index] or groups[index], keys[index]))
    else:
        # Named first, characters ahead of the rest of the named cast, and inside
        # each the most-animated first -- which puts the cast the story revolves
        # around on top and leaves the nameless cameras and props at the bottom.
        # WHO one is, and whether the game names them at all, is the hook's join.
        named = table.values("named")
        clips = table.values("clips")
        order = sorted((int(index) for index in matched),
                       key=lambda index: (_ACTOR_RANK.get(named[index], len(_ACTOR_RANK)),
                                          -float(clips[index])))
    columns = ("unit", "group", "shots", "clips", "actors", "variants",
               "mission", "title", "kind", "chapter", "place", "lines") if by_story \
        else ("actor", "who", "title", "named", "character", "kinds", "units",
              "cutscenes", "dialogs", "clips", "stories", "missions", "places")
    rows = [{name: table.cell(index, name) for name in columns}
            for index in order[:cabmap_state.LIST_CAP]]
    for row in rows:
        row["group"] = _unit_group(row) if by_story else _actor_group(row)

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
        # The row's IDENTITY stays the game's own token either way; what is DRAWN
        # is the name it has. Keying on the drawn name would break the moment two
        # of them share one, which this cast does -- both protagonists are 管理员.
        entry.key = row["unit"] if by_story else row["actor"]
        entry.label = entry.key if by_story else (row["who"] or row["actor"])
        entry.group = row["group"]
        entry.detail = _unit_detail(row) if by_story else _actor_detail(row)
    opened = _opened(state)
    shown = filter_ui.restore_selection(state, opened)
    hidden = "" if shown or not opened else " · {0} still open (filtered out)".format(opened)
    state.status = "{0} of {1} {2}{3}{4}".format(
        len(order), len(table),
        "{0} unit(s)".format(state.channel) if by_story else "actor(s)",
        "" if len(order) == len(rows) else
        " · showing {0}, narrow the filter to see the rest".format(len(rows)),
        hidden)


def _unit_group(row):
    """The header a unit is drawn under: the mission that plays it, under that
    mission's own name. Only a unit no mission reaches falls back to the folder
    prefix the game's own file naming leads with."""
    if row.get("title"):
        return row["title"]
    return row.get("mission") or row["group"]


def _unit_detail(row):
    """The one-line summary of a unit: what it plays and where it happens. A shot
    count of zero is a unit whose animations the game does not ship separately,
    which is worth reading rather than hiding."""
    parts = []
    if row.get("place"):
        parts.append(row["place"])
    shots = int(row["shots"] or 0)
    if shots:
        parts.append("{0} shot(s)".format(shots))
    parts.append("{0} clip(s)".format(int(row["clips"] or 0)))
    lines = int(row.get("lines") or 0)
    if lines:
        parts.append("{0} line(s)".format(lines))
    actors = int(row["actors"] or 0)
    if actors:
        parts.append("{0} actor(s)".format(actors))
    if row["variants"]:
        parts.append(row["variants"])
    return " · ".join(parts)


# What kind of name the game has for a row, as headers, in the order a cast list
# is worth reading in. WHICH one a row is comes from the hook's join against the
# game's own rosters; only the wording and the order are ours.
_ACTOR_GROUPS = {datasets.ACTOR_CHARACTER: "Characters",
                 datasets.ACTOR_NPC: "Named cast",
                 datasets.ACTOR_PLACED: "Named cast"}
_ACTOR_RANK = {datasets.ACTOR_CHARACTER: 0, datasets.ACTOR_NPC: 1, datasets.ACTOR_PLACED: 2}
_UNNAMED_GROUP = "Unnamed (cameras, props, crowd)"


def _actor_group(row):
    """Whether the game gives this one a name, and of which kind. One the game's
    own rosters do not know is still listed -- the story animates plenty of
    cameras, props and crowd models -- it is just filed as what it is."""
    return _ACTOR_GROUPS.get(row["named"], _UNNAMED_GROUP)


def _actor_detail(row):
    """The one-line summary of one person: how much of the story they are in. The
    stories are NAMED in the box under the list rather than here, since a lead
    appears in sixty of them and no list row is that wide."""
    parts = []
    if row["title"]:
        parts.append(row["title"])
    stories = int(row["stories"] or 0)
    if stories:
        parts.append("{0} story(s)".format(stories))
    parts.append("{0} clip(s)".format(int(row["clips"] or 0)))
    cutscenes = int(row["cutscenes"] or 0)
    dialogs = int(row["dialogs"] or 0)
    if cutscenes:
        parts.append("{0} cutscene".format(cutscenes))
    if dialogs:
        parts.append("{0} dialogue".format(dialogs))
    return " · ".join(parts)


def _open_entry(state, key):
    """Read what the picked row plays and draw it grouped by the game's own split:
    by shot inside one cutscene, by what an asset drives inside one dialogue, and
    by the unit it belongs to when the pick was an actor."""
    by_story = state.mode == BY_STORY
    state.unit = key if by_story else ""
    state.actor = "" if by_story else key
    state.clips.clear()
    state.clip_status = ""
    _forget_context(state)
    if not key or cabmap_state.BRIDGE is None:
        return
    try:
        table = datasets.story_clips(channel=state.channel if by_story else "",
                                     unit=key if by_story else "",
                                     actor="" if by_story else key,
                                     language=_language())
    except Exception as exc:
        state.clip_status = "{0}: {1}".format(type(exc).__name__, exc)
        return
    _CLIPS[_selection_key(state)] = table
    _rebuild_clips(state)
    if by_story:
        _open_context(state, key)
    else:
        _open_actor(state, key)


def _open_actor(state, actor):
    """Who this one is and which stories they are in -- both already on the row the
    actor list was built from, since the hook answers them per actor."""
    row = _row_of(_top_table(state), actor, "actor")
    if row is None:
        return
    state.actor_who = row.get("who", "")
    state.actor_title = row.get("title", "")
    state.actor_named = row.get("named", "")
    state.actor_character = row.get("character", "")
    state.actor_stories = row.get("missions", "")
    state.actor_places = row.get("places", "")


def _forget_context(state):
    state.actor_who = ""
    state.actor_title = ""
    state.actor_named = ""
    state.actor_character = ""
    state.actor_stories = ""
    state.actor_places = ""
    state.mission = ""
    state.mission_title = ""
    state.mission_kind = ""
    state.mission_chapter = ""
    state.mission_level = ""
    state.mission_place = ""
    state.mission_description = ""
    state.mission_character = ""
    state.unit_summary = ""
    state.unit_relation = ""
    state.lines.clear()
    state.line_status = ""
    state.quests.clear()
    state.quest_status = ""


def _open_context(state, unit):
    """What the picked unit IS and what it says. The mission columns already rode
    in on the unit table, so this crossing is only for the two things a unit-sized
    row cannot hold: every line the unit speaks, and the quest graph of the mission
    playing it -- both cached by (id, args) on the C# side, so walking the list
    with the arrow keys re-reads nothing already seen."""
    table = _top_table(state)
    row = _row_of(table, unit)
    if row is None:
        return
    state.mission = row.get("mission", "")
    state.mission_title = row.get("title", "")
    state.mission_kind = row.get("kind", "")
    state.mission_chapter = row.get("chapter", "")
    state.mission_level = row.get("level", "")
    state.mission_place = row.get("place", "")
    state.unit_summary = row.get("summary", "")
    state.unit_relation = row.get("relation", "")

    language = _language()
    try:
        _LINES[_selection_key(state)] = datasets.story_lines(unit=unit, language=language)
    except Exception as exc:
        state.line_status = "{0}: {1}".format(type(exc).__name__, exc)
    else:
        _rebuild_lines(state)
    if not state.mission:
        state.quest_status = "No mission of this install names this unit."
        return
    try:
        quests = datasets.story_quests(state.mission, language)
        missions = datasets.story_missions(language)
    except Exception as exc:
        state.quest_status = "{0}: {1}".format(type(exc).__name__, exc)
        return
    _QUESTS[state.mission] = quests
    mission = _row_of(missions, state.mission, "mission")
    if mission is not None:
        state.mission_description = mission.get("description", "")
        state.mission_character = mission.get("who", "") or mission.get("character", "")
    _fill_quests(state, quests)


def _unit_speaking(table, spoken):
    """Which unit plays the dialogue scene or cutscene a mission NAMES. The game
    names those by what is said (``dlg_e7m4_4``), and the unit that says it is a
    file next door (``dlgtl_e7m4_4_sub_1``) -- the hook already states the pairing
    on every row, so this is a lookup and not a second naming rule here."""
    if table is None or not spoken:
        return ""
    for index, found in enumerate(table.values("spoken")):
        if found == spoken:
            return table.cell(index, "unit")
    return ""


def _row_of(table, key, column="unit"):
    """The one row of a loaded table whose key column is ``key``, as a dict. The
    tables are small and this runs once per pick, not once per redraw."""
    if table is None:
        return None
    keys = table.values(column)
    for index, found in enumerate(keys):
        if found == key:
            return {name: table.cell(index, name) for name in table.names}
    return None


def _rebuild_clips(state):
    table = _clips_table(state)
    with filter_ui.rebuilding():
        _fill_clips(state, table)


def _fill_clips(state, table):
    highlighted = filter_ui.selected_key(state, "clips", "clips_active_index", "container")
    state.clips.clear()
    if table is None:
        return
    checked = _CHECKED.get((state.channel, state.unit), {})
    matched = cabmap_state.BRIDGE.search_data_table(table, state.clip_search.strip(), None)
    by_story = state.mode == BY_STORY
    order = sorted(int(index) for index in matched)
    rows = [{name: table.cell(index, name)
             for name in ("channel", "unit", "shot", "kind", "actor", "name", "container",
                          "cab", "clip", "title", "place")}
            for index in order[:cabmap_state.LIST_CAP]]

    counts = {}
    for row in rows:
        counts[_bucket(row, by_story)] = counts.get(_bucket(row, by_story), 0) + 1

    current = None
    for row in rows:
        bucket = _bucket(row, by_story)
        if bucket != current:
            current = bucket
            header = state.clips.add()
            header.label = "{0}  ({1})".format(bucket, counts[bucket])
            header.is_group = True
            header.channel = row["channel"]
            header.unit = row["unit"]
        item = state.clips.add()
        item.channel = row["channel"]
        item.unit = row["unit"]
        item.shot = row["shot"]
        item.kind = row["kind"]
        item.actor = row["actor"]
        item.name = row["name"]
        item.container = row["container"]
        item.cab = row["cab"]
        item.clip = bool(int(row["clip"] or 0))
        item.selected = row["container"] in checked
        # By story the actor is the useful half of the name; by actor it is the
        # constant, so the name (which for a library clip says what it DOES) is.
        item.label = (row["actor"] or row["name"]) if by_story else row["name"]
    filter_ui.restore_selection(state, highlighted, "clips", "clips_active_index", "container")
    state.clip_status = "{0} of {1} row(s){2}{3}".format(
        len(order), len(table),
        "" if len(order) == len(rows) else " · showing {0}".format(len(rows)),
        " · {0} checked".format(len(checked)) if checked else "")


def _rebuild_lines(state):
    with filter_ui.rebuilding():
        _fill_lines(state, _lines_table(state))


def _fill_lines(state, table):
    """What the open unit says, in the game's own playback order. Filtering runs
    through the same C# engine the other lists use, over the very table the rows
    came from -- so searching a speaker's name or a phrase is one vectorized pass
    and nothing is matched here."""
    state.lines.clear()
    if table is None:
        state.line_status = ""
        return
    matched = cabmap_state.BRIDGE.search_data_table(table, state.line_search.strip(), None)
    order = sorted(int(index) for index in matched)
    rows = [{name: table.cell(index, name)
             for name in ("order", "kind", "speaker", "who", "text", "emotion")}
            for index in order[:cabmap_state.LIST_CAP]]
    for row in rows:
        item = state.lines.add()
        item.kind = row["kind"]
        item.speaker = row["speaker"]
        item.who = row["who"] or row["speaker"]
        item.text = row["text"]
        item.emotion = row["emotion"]
        item.order = int(float(row["order"] or 0))
        item.label = row["text"]
    state.line_status = "{0} of {1} line(s){2}".format(
        len(order), len(table),
        "" if len(order) == len(rows) else " · showing {0}".format(len(rows)))


def _fill_quests(state, table):
    """The mission's own quest graph, in the order its main path walks it. One
    drawn row per objective, under the quest it belongs to -- which is the shape
    the game itself tracks, and the reason a quest with several objectives reads
    as several things to do rather than one."""
    state.quests.clear()
    if table is None:
        state.quest_status = ""
        return
    rows = [{name: table.cell(index, name)
             for name in ("quest", "mainPath", "objective", "description", "waitsOn",
                          "place", "dialog", "cutscene")}
            for index in range(len(table))][:cabmap_state.LIST_CAP]
    current = None
    for row in rows:
        if row["quest"] != current:
            current = row["quest"]
            header = state.quests.add()
            header.label = row["quest"]
            header.quest = row["quest"]
            header.main_path = int(float(row["mainPath"] or -1))
            header.is_group = True
        item = state.quests.add()
        item.quest = row["quest"]
        item.description = row["description"]
        item.waits = row["waitsOn"]
        item.place = row["place"]
        item.dialog = row["dialog"]
        item.cutscene = row["cutscene"]
        item.main_path = int(float(row["mainPath"] or -1))
        item.label = row["description"] or row["waitsOn"] or row["quest"]
    state.quest_status = "{0} objective(s) over {1} quest(s)".format(
        len(rows), len({row["quest"] for row in rows}))


def _bucket(row, by_story):
    """The group a clip row is drawn under -- always the game's own split. Inside
    one unit that is the shot (a cutscene) or what the asset drives (a dialogue);
    across one actor it is the unit itself, prefixed by the channel it came from,
    since that is what tells a cutscene line apart from a library state."""
    if by_story:
        return row["shot"] or row["kind"] or "(ungrouped)"
    if not row["unit"]:
        return row["channel"]
    # Across one actor the useful header is WHICH STORY this is from, which is
    # the whole reason the actor view raises the question in the first place.
    return "{0} · {1}".format(row["title"] or row["channel"], row["unit"])


def _selected_entry(state):
    if 0 <= state.active_index < len(state.entries):
        entry = state.entries[state.active_index]
        if not entry.is_group:
            return entry
    return None


def _checked(state):
    """{container path: cab} for the open selection -- the WHOLE checked set,
    including rows the current filter is hiding."""
    return _CHECKED.get(_selection_key(state), {})


class RURI_UL_story_units(bpy.types.UIList):
    bl_idname = "RURI_UL_story_units"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if item.is_group:
            row = layout.row()
            row.enabled = False
            row.label(text=item.label, icon="OUTLINER_COLLECTION")
            return
        by_actor = context.scene.ruri_story.mode == BY_ACTOR
        row = layout.row(align=True)
        row.label(text=item.label,
                  icon="OUTLINER_OB_ARMATURE" if by_actor else "SEQ_STRIP_DUPLICATE")
        detail = row.row()
        detail.enabled = False
        detail.alignment = "RIGHT"
        detail.label(text=item.detail)

    def filter_items(self, context, data, propname):
        # Filtering already happened against the game's own columns in
        # _rebuild_top, not against the drawn string -- leave the list alone.
        return [], []


class RURI_UL_story_clips(bpy.types.UIList):
    bl_idname = "RURI_UL_story_clips"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if item.is_group:
            row = layout.row(align=True)
            title = row.row()
            title.enabled = False
            title.label(text=item.label, icon="SEQUENCE")
            # By actor, a group IS a story unit -- so it carries the way into it.
            if item.unit and context.scene.ruri_story.mode == BY_ACTOR:
                jump = row.operator(RURI_OT_story_goto_unit.bl_idname, text="", icon="ZOOM_SELECTED")
                jump.channel = item.channel
                jump.unit = item.unit
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


class RURI_UL_story_lines(bpy.types.UIList):
    bl_idname = "RURI_UL_story_lines"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        speaker = row.row()
        speaker.scale_x = 0.45
        speaker.alignment = "RIGHT"
        if item.kind == datasets.LINE_OPTION:
            speaker.label(text="", icon="TRIA_RIGHT")
        elif item.who:
            speaker.label(text=item.who)
        else:
            speaker.enabled = False
            speaker.label(text="", icon="REC")
        row.label(text=item.text)
        if item.emotion:
            mood = row.row()
            mood.enabled = False
            mood.alignment = "RIGHT"
            mood.scale_x = 0.5
            mood.label(text=item.emotion)

    def filter_items(self, context, data, propname):
        return [], []


class RURI_UL_story_quests(bpy.types.UIList):
    bl_idname = "RURI_UL_story_quests"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if item.is_group:
            row = layout.row(align=True)
            title = row.row()
            title.enabled = False
            title.label(text=item.label,
                        icon="KEYFRAME_HLT" if item.main_path >= 0 else "KEYFRAME")
            return
        row = layout.row(align=True)
        row.label(text=item.label or "(no text)", icon="DOT")
        # What the game itself says this objective waits on is the one link that is
        # stated rather than read off a file name -- so it is a click, not a note.
        played = item.dialog or item.cutscene
        if played:
            jump = row.row()
            jump.alignment = "RIGHT"
            opened = jump.operator(RURI_OT_story_goto_unit.bl_idname, text=played,
                                   icon="ZOOM_SELECTED")
            opened.channel = datasets.CUTSCENE if item.cutscene else datasets.DIALOG
            opened.spoken = played
        elif item.place:
            where = row.row()
            where.enabled = False
            where.alignment = "RIGHT"
            where.label(text=item.place)

    def filter_items(self, context, data, propname):
        return [], []


class RURI_OT_story_refresh(bpy.types.Operator):
    """Read the story list out of the loaded cabmap -- the units of this channel,
    or the actor index, whichever mode is on."""
    bl_idname = "ruri.story_refresh"
    bl_label = "Refresh Story List"
    bl_description = ("List the cutscenes / dialogue timelines the game ships animations for, "
                      "or everyone the story animates")

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = context.scene.ruri_story
        by_story = state.mode == BY_STORY
        try:
            table = datasets.story_units(state.channel, _language()) if by_story \
                else datasets.story_actors(language=_language())
        except Exception as exc:
            state.status = "{0}: {1}".format(type(exc).__name__, exc)
            self.report({"WARNING"}, state.status)
            return {"CANCELLED"}
        _UNITS[(state.mode, state.channel if by_story else "")] = table
        _rebuild_top(state)
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
            _CHECKED.pop(_selection_key(state), None)
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

        # An animation belongs to the one it animates, and the game says which
        # one that is -- so the checked rows are split by actor and each group is
        # built onto that actor's own rig. Only rows whose actor has no rig in
        # the scene fall back to the user's own selection, which is the case the
        # generic browser import was always for.
        by_actor = {}
        for item in state.clips:
            if item.is_group or not item.clip or item.container not in checked:
                continue
            by_actor.setdefault(item.actor, []).append(item.cab)
        for container, cab in checked.items():
            if not any(cab in cabs for cabs in by_actor.values()):
                by_actor.setdefault("", []).append(cab)

        built, homeless = 0, []
        for actor, cabs in by_actor.items():
            rig = _rig_for(context, actor)
            if rig is not None:
                _make_active(context, rig)
            elif actor:
                homeless.append(actor)
            if _import_cabs(context, cabs)[0]:
                built += len(cabs)
        if homeless:
            self.report({"WARNING"},
                        "No rig in the scene for {0} -- use Load Whole Cutscene, or load them "
                        "first.".format(", ".join(sorted(set(homeless))[:4])))
        if not built:
            return {"CANCELLED"}
        self.report({"INFO"}, "Built {0} animation(s) across {1} rig(s).".format(built, len(by_actor)))
        return {"FINISHED"}


def _cast_of(table):
    """{actor token: [clip cab]} for everything in this table the game ships a
    model for, in table order (which is shot order). One entry is one performer
    of the scene, with every animation it plays."""
    cast = {}
    for row in range(len(table)):
        if not table.cell(row, "clip") or table.cell(row, "kind") not in _MODEL_KINDS:
            continue
        actor = table.cell(row, "actor")
        if not actor:
            continue
        cabs = cast.setdefault(actor, [])
        cab = table.cell(row, "cab")
        if cab not in cabs:
            cabs.append(cab)
    return cast


def _character_ids():
    """{actor token: character id} off the hook's own join (a character data
    asset carrying that token). Read once per session; an actor the game ships
    no character data for simply has no entry."""
    if not _CHARACTER_IDS:
        try:
            table = datasets.story_actors()
        except Exception:
            return _CHARACTER_IDS
        for row in range(len(table)):
            character = table.cell(row, "character")
            if character:
                _CHARACTER_IDS[table.cell(row, "actor")] = character
    return _CHARACTER_IDS


_CHARACTER_IDS = {}


def _model_rows_for(actor):
    """The importable rows of one performer's model, in the order the game states
    it: the prefab its own character data declares, else the prefab named after
    it, else whatever part the game files under that name (npcs and monsters).
    Empty when the game ships no model for that token at all."""
    character = _character_ids().get(actor, "")
    if character:
        declared = roster_panel.character_model(character)
        if declared:
            rows = datasets.part_rows(declared, cast=datasets.CHARACTERS)
            if rows:
                return rows
    rows = datasets.model_rows(actor, "postmodel", cast=datasets.CHARACTERS)
    return rows or datasets.part_rows(actor)


def _import_cabs(context, cabs):
    """Run the browser's own import over exactly these CABs, and hand back the
    armatures it added. One import path, so a fix there is a fix here."""
    before = {obj.name for obj in context.scene.objects if obj.type == "ARMATURE"}
    cabmap_state.clear_selection()
    cabmap_state.SELECTED_CABS.update(cabs)
    result = bpy.ops.ruri.import_selected()
    added = [obj for obj in context.scene.objects
             if obj.type == "ARMATURE" and obj.name not in before]
    return ("FINISHED" in result), added


def _rig_for(context, actor):
    """The rig in the scene that IS this actor, or None.

    Two sources, in order of how much they actually know: the rigs this session
    loaded for a unit (exact -- the import itself said so), then a scene
    armature whose object name carries the actor's token, which is what a rig
    loaded through the roster in an earlier session looks like."""
    if not actor:
        return None
    known = bpy.data.objects.get(_ACTOR_RIGS.get(actor, ""))
    if known is not None and known.type == "ARMATURE" and known.name in context.scene.objects:
        return known
    token = actor.lower()
    matches = [obj for obj in context.scene.objects
               if obj.type == "ARMATURE" and token in obj.name.lower()]
    return matches[0] if len(matches) == 1 else None


def _make_active(context, armature):
    """Point the scene at one rig, which is how every standalone clip import
    resolves its target (prefab_importer.find_target_armature)."""
    for obj in context.selected_objects:
        obj.select_set(False)
    armature.select_set(True)
    context.view_layer.objects.active = armature


def _camera_of(context, unit):
    """The cutscene's camera object, created once per unit.

    A cutscene is a camera performance: its cam track drives a camera, not a rig,
    so the clip lands on an object action instead of pose bones."""
    name = unit + "_camera"
    existing = bpy.data.objects.get(name)
    if existing is not None and existing.type == "CAMERA":
        if existing.name not in context.scene.objects:
            context.scene.collection.objects.link(existing)
        return existing
    camera = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    camera.rotation_mode = "QUATERNION"
    context.scene.collection.objects.link(camera)
    return camera


_OBJECT_ACTIONS = {}


def _camera_action(camera, cab, clip_name):
    """Build the object action one camera clip states.

    The clip is a transform track like any other, so it is read through the same
    curve path every clip takes; what is camera-specific is only the two
    conventions at the end -- Unity's basis (converted by the shared reflection,
    exactly as a top-level import object is) and Unity's camera facing +Z with up
    +Y against Blender's -Z with up +Y, which is one fixed local quarter turn."""
    import numpy
    from mathutils import Matrix, Quaternion, Vector

    from ... import animation_builder, coordinate
    from ...RuriRipperPyBridge.unity import bridge_asset_db, class_registry

    # A timeline reuses takes -- the same clip is routinely placed twice -- and
    # each build is a bridge import, so one build per clip is the whole budget.
    cached = _OBJECT_ACTIONS.get((cab, clip_name))
    if cached is not None and cached[0].users >= 0:
        return cached

    assets, _roots, _seeds, clips_by_cab, _scenes = cabmap_state.BRIDGE.import_cabs(
        [cab], export_class_ids=[class_registry.id_for_name("AnimationClip")])
    database = bridge_asset_db.BridgeAssetDatabase(
        assets, clip_curve_blobs=cabmap_state.BRIDGE.clip_curves_by_guid,
        asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid)
    curves = None
    for guid in clips_by_cab.get(cab.lower(), []):
        found = database.clip_curves(guid)
        if found is not None and (clip_name in ("", found.name) or curves is None):
            curves = found
    if curves is None:
        return None

    rate = curves.sample_rate or 60.0
    frames = max(1, int(round(curves.max_time() * rate)) + 1)
    times = numpy.arange(frames, dtype=numpy.float64) / rate
    position = curves.positions[0].sample(times) if curves.positions else None
    rotation = curves.rotations[0].sample(times) if curves.rotations else None

    action = bpy.data.actions.new(curves.name)
    action.use_fake_user = True
    action[animation_builder.SAMPLE_RATE_KEY] = float(rate)
    fcurves, slot = animation_builder._prepare_channels(action, action.name, "OBJECT")
    facing = Matrix.Rotation(numpy.pi / 2.0, 4, "X")
    locations = numpy.zeros((frames, 3), dtype=numpy.float64)
    quaternions = numpy.zeros((frames, 4), dtype=numpy.float64)
    for frame in range(frames):
        translation = Matrix.Translation(Vector(position[frame]) if position is not None else Vector())
        turn = Quaternion((rotation[frame][3], rotation[frame][0], rotation[frame][1],
                           rotation[frame][2])).to_matrix().to_4x4() if rotation is not None \
            else Matrix.Identity(4)
        world = coordinate.convert_root_matrix(translation @ turn) @ facing
        locations[frame] = world.to_translation()
        turned = world.to_quaternion()
        quaternions[frame] = (turned.w, turned.x, turned.y, turned.z)

    keys = numpy.arange(frames, dtype=numpy.float64)
    if position is not None or rotation is not None:
        for index in range(3):
            _write_curve(fcurves.new("location", index=index), keys, locations[:, index])
        for index in range(4):
            _write_curve(fcurves.new("rotation_quaternion", index=index), keys, quaternions[:, index])
    # A clip that animates no transform at all animates VALUES -- a light's
    # intensity, a material's colour. Those land as animated custom properties on
    # the stand-in, so the curve the game plays is in the scene and readable
    # rather than dropped for having nowhere obvious to go.
    written = set()
    for channel in getattr(curves, "floats", []) or []:
        # The WHOLE path is the key: a clip animates several values whose leaf
        # names collide ("...position.x" and "...scale.x" are both "x"), and two
        # curves cannot share one custom property.
        name = "_".join(part for part in (getattr(channel, "path", ""),
                                          getattr(channel, "attribute", "")) if part)
        key = re.sub(r"[^0-9A-Za-z_]", "_", name) or "value"
        if key in written:
            continue
        written.add(key)
        samples = channel.sample(times)
        values = samples[:, 0] if getattr(samples, "ndim", 1) > 1 else samples
        camera[key] = float(values[0])
        _write_curve(fcurves.new('["{0}"]'.format(key), index=0), keys, values)
    if not len(fcurves):
        bpy.data.actions.remove(action)
        return None
    _OBJECT_ACTIONS[(cab, clip_name)] = (action, slot)
    return action, slot


def _write_curve(curve, frames, values):
    import numpy
    curve.keyframe_points.add(count=len(frames))
    flat = numpy.empty(len(frames) * 2, dtype=numpy.float64)
    flat[0::2] = frames
    flat[1::2] = values
    curve.keyframe_points.foreach_set("co", flat)
    curve.keyframe_points.foreach_set("interpolation", [1] * len(frames))
    curve.update()


def _actor_of_track(cast_of, plan, track):
    """Who an activation track switches on: the performer whose animation rides
    the SAME track. The game names both after the object, so the join is the
    track itself."""
    for row in plan:
        if row["track"] == track and row["trackKind"] in _ANIMATION_TRACKS:
            return cast_of.get(row["clipCab"], ("", ""))[1]
    return ""


def _spawn(context, row):
    """Load what a control clip spawns and hand it back as one object to switch
    on and off -- the prefab the timeline itself names."""
    before = {obj.name for obj in context.scene.objects}
    cabmap_state.clear_selection()
    cabmap_state.SELECTED_CABS.add(row["referenceCab"])
    if "FINISHED" not in bpy.ops.ruri.import_selected():
        return None
    added = [obj for obj in context.scene.objects if obj.name not in before]
    if not added:
        return None
    holder = _empty_for(context, "{0}_{1}".format(row["track"], row["reference"] or "effect"))
    for obj in added:
        if obj.parent is None and obj is not holder:
            obj.parent = holder
    return holder


def _empty_for(context, name):
    """A plain object standing in for something the story moves that is not a rig
    -- a prop, a light rig, a spawned effect. It carries the exact motion the
    timeline gives it, which is what a scene rebuild needs even when the thing
    itself is not importable."""
    existing = bpy.data.objects.get(name)
    if existing is None:
        existing = bpy.data.objects.new(name, None)
        existing.empty_display_type = "PLAIN_AXES"
        existing.rotation_mode = "QUATERNION"
    if existing.name not in context.scene.objects:
        context.scene.collection.objects.link(existing)
    return existing


def _keyframe_visibility(target, spans, fps, end):
    """Keyframe an object visible exactly across the spans the timeline activates
    it for. Constant interpolation, because activation is a switch and anything
    smoothed between two states is a state the game never shows."""
    if target is None or not spans:
        return
    for attribute in ("hide_viewport", "hide_render"):
        target.hide_viewport = True
        target.hide_render = True
        target.keyframe_insert(attribute, frame=0)
        for start, stop in spans:
            setattr(target, attribute, False)
            target.keyframe_insert(attribute, frame=max(0, int(round(start * fps))))
            setattr(target, attribute, True)
            target.keyframe_insert(attribute, frame=int(round(stop * fps)) + 1)
    animation = target.animation_data
    for curve in (animation.action.fcurves if animation and animation.action else []):
        for point in curve.keyframe_points:
            point.interpolation = "CONSTANT"


def _marker(context, name, frame):
    """One timeline marker where the story says something happens. Subtitles and
    audio events are not motion Blender can play back, but WHEN they happen is
    part of the performance and belongs on the timeline rather than in a log."""
    marker = context.scene.timeline_markers.new(name[:63], frame=int(round(frame)))
    return marker


def _report_exception(operator, headline, error):
    import traceback
    traceback.print_exc()
    operator.report({"ERROR"}, "{0}: {1}: {2}".format(headline, type(error).__name__, error))


def _action_named(clip):
    """The action built for one clip. The importer names an action after the
    clip's own m_Name, which is exactly what the timeline plan carries -- and
    Blender uniquifies a second import as "<name>.001", so a re-import lands on
    the newest one rather than silently re-using the first."""
    if not clip:
        return None
    exact = bpy.data.actions.get(clip)
    if exact is not None:
        return exact
    numbered = [action for action in bpy.data.actions
                if action.name.rsplit(".", 1)[0] == clip]
    return max(numbered, key=lambda action: action.name) if numbered else None


def _plan_rows(unit):
    """The unit's own playback plan, as plain dicts in timeline order."""
    table = datasets.story_timeline(unit)
    columns = ("playable", "track", "trackKind", "trackPath", "binding", "clip", "clipCab",
               "reference", "referenceCab", "start", "duration", "clipIn", "timeScale",
               "blendIn", "blendOut", "frameRate", "sampleRate", "clipLength", "muted")
    rows = [{name: table.cell(index, name) for name in columns} for index in range(len(table))]
    for row in rows:
        for number in ("start", "duration", "clipIn", "timeScale", "blendIn", "blendOut",
                       "frameRate", "sampleRate", "clipLength", "muted"):
            row[number] = float(row[number] or 0.0)
    rows.sort(key=lambda row: (row["playable"], row["trackPath"], row["start"]))
    return rows


def _cast_by_cab(table):
    """{clip cab: (kind, actor)} for one unit -- the join between the plan (which
    names clips) and the cast (which names what each one moves) is the CAB itself,
    so no name matching happens anywhere in this."""
    found = {}
    for row in range(len(table)):
        cab = table.cell(row, "cab")
        if cab:
            found[cab] = (table.cell(row, "kind"), table.cell(row, "actor"))
    return found


def _scene_fps(context, rows):
    """The frame rate the timeline itself states, applied to the scene so one
    Blender frame IS one game frame. A unit that states none keeps the scene's."""
    stated = {row["frameRate"] for row in rows if row["frameRate"] > 0}
    if not stated:
        return float(context.scene.render.fps)
    fps = max(stated)
    context.scene.render.fps = int(round(fps))
    context.scene.render.fps_base = 1.0
    return float(context.scene.render.fps)


def _action_rate(action, row, fps):
    """How many action frames one second of this clip is.

    Read off the STAMP the importer puts on the action it built, never off the
    clip asset's m_SampleRate: an ACL clip routinely decodes at a rate its own
    field does not carry (a 30 in the asset, 60 frames per second on the curves),
    and placing by the stated rate plays that shot at half speed. An action built
    before the stamp existed is measured against the clip's own length instead."""
    from ... import animation_builder
    stamped = action.get(animation_builder.SAMPLE_RATE_KEY)
    if stamped:
        return float(stamped)
    frames = action.frame_range[1] - action.frame_range[0]
    if row["clipLength"] > 0.0 and frames > 0.0:
        return frames / row["clipLength"]
    return row["sampleRate"] if row["sampleRate"] > 0 else fps


def _free_track(animation, name, start, end):
    """The NLA track this clip goes on: the one named after the game's own track,
    or the next layer of it.

    Timeline lets two clips of one track overlap (that is how it cross-blends);
    Blender does not, so an overlap becomes another layer of the same track
    rather than a dropped clip or a moved one."""
    layer = 0
    while True:
        layered = name if layer == 0 else "{0}.{1}".format(name, layer + 1)
        track = next((entry for entry in animation.nla_tracks if entry.name == layered), None)
        if track is None:
            track = animation.nla_tracks.new()
            track.name = layered
            return track
        if all(strip.frame_end <= start + 1e-3 or strip.frame_start >= end - 1e-3
               for strip in track.strips):
            return track
        layer += 1


def _place_strip(rig, row, action, fps):
    """Put one action on one rig where the timeline puts it.

    Timeline states a clip as (start, duration, clipIn, timeScale) in SECONDS
    against the clip's own sample rate; Blender states a strip as a frame span
    plus the action frames it consumes and a scale between them. The two are the
    same statement in different units, so this is a conversion, not a guess:
    the strip spans duration*fps frames and eats duration*timeScale*sampleRate
    action frames starting at clipIn*sampleRate."""
    animation = rig.animation_data or rig.animation_data_create()
    start = row["start"] * fps
    span = max(row["duration"] * fps, 1.0)
    track = _free_track(animation, row["track"] or row["trackPath"] or "Timeline", start, start + span)
    # Blender builds a new strip at the action's OWN length and refuses any
    # overlap, so it is born past everything already on the track and then moved
    # into place -- the region it moves into was just checked to be free.
    parking = max(int(round(start)),
                  int(max((strip.frame_end for strip in track.strips), default=0.0)) + 1)
    strip = track.strips.new(action.name, parking, action)
    sample = _action_rate(action, row, fps)
    consumed = max(row["duration"] * row["timeScale"] * sample, 1.0 / sample)
    # Order matters: the action range first, then the scale that stretches it to
    # the span the timeline gives it, and only then the move into place. Resizing
    # by frame_end_ui instead would rewrite the action range back to the span and
    # silently play the whole shot at the wrong speed.
    strip.action_frame_start = row["clipIn"] * sample
    strip.action_frame_end = strip.action_frame_start + consumed
    strip.scale = span / consumed
    strip.frame_start_ui = start
    strip.blend_in = min(row["blendIn"] * fps, span)
    strip.blend_out = min(row["blendOut"] * fps, span)
    # Outside its own span a Timeline clip contributes nothing -- holding the
    # last pose instead would leave every actor frozen in place after their
    # first shot, on top of whoever plays next.
    strip.extrapolation = "NOTHING"
    strip.mute = row["muted"] > 0
    return strip


class RURI_OT_story_load_unit(bpy.types.Operator):
    """Bring the whole scene up the way the game does: load every performer this
    unit animates, then build that one's own animations onto its own rig.

    The cast is the unit's own rows -- who plays in it and which clips are
    theirs is stated by the game's filing, so nothing here asks the user to
    match a clip to a rig by hand. What a cutscene moves besides its cast
    (cameras, items, props) is animation on objects the story spawns rather than
    on a loadable rig; those are counted in the report instead of silently
    dropped."""
    bl_idname = "ruri.story_load_unit"
    bl_label = "Load Whole Cutscene"
    bl_description = ("Load every actor this unit animates and build their animations onto them "
                      "-- no rig has to be selected first")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        state = context.scene.ruri_story
        return (context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None
                and state.mode == BY_STORY and bool(state.unit))

    def execute(self, context):
        from ... import animation_builder

        state = context.scene.ruri_story
        table = _clips_table(state)
        if table is None:
            self.report({"WARNING"}, "Open a unit first.")
            return {"CANCELLED"}
        try:
            plan = _plan_rows(state.unit)
        except Exception as exc:
            _report_exception(self, "Reading this unit's timeline failed", exc)
            return {"CANCELLED"}
        if not plan:
            self.report({"WARNING"}, "This unit's timeline places no clip -- nothing to play.")
            return {"CANCELLED"}

        cast_of = _cast_by_cab(table)
        cast = _cast_of(table)
        fps = _scene_fps(context, plan)

        loaded, unresolved = {}, []
        for actor, clip_cabs in cast.items():
            rig = _rig_for(context, actor) or self._load_actor(context, actor)
            if rig is None:
                unresolved.append(actor)
                continue
            loaded[actor] = rig
            _make_active(context, rig)
            _import_cabs(context, clip_cabs)

        camera = None
        placed, unplaced, marks = 0, [], 0
        spans = {}
        by_track = {}
        last = 0.0
        for row in plan:
            last = max(last, (row["start"] + row["duration"]) * fps)
            kind, actor = cast_of.get(row["clipCab"], ("", ""))
            if row["trackKind"] in _ANIMATION_TRACKS:
                target, action = None, None
                if actor and kind in _MODEL_KINDS and actor != _CAMERA_KIND:
                    target = loaded.get(actor)
                    action = _action_named(row["clip"]) if target is not None else None
                if target is None and row["clipCab"]:
                    # Everything a cutscene animates that is not a performer with
                    # a rig is a transform (or value) track on something the story
                    # spawns: the camera it films through, a prop, a light rig, an
                    # npc the game ships no model for. The camera is a real
                    # camera; the rest get a stand-in, so nothing the timeline
                    # plays is dropped for having nowhere obvious to go.
                    if kind == _CAMERA_KIND or actor == _CAMERA_KIND:
                        camera = camera or _camera_of(context, state.unit)
                        target = camera
                    else:
                        target = _empty_for(context, "{0}_{1}".format(state.unit, row["track"]))
                    built = _camera_action(target, row["clipCab"], row["clip"])
                    action = built[0] if built is not None else None
                if target is None or action is None:
                    unplaced.append(row["clip"])
                    continue
                by_track[row["track"]] = target
                _place_strip(target, row, action, fps)
                placed += 1
            elif row["trackKind"] == _ACTIVATION_TRACK:
                spans.setdefault(row["track"], []).append(
                    (row["start"], row["start"] + row["duration"]))
            elif row["trackKind"] in _MARKER_TRACKS:
                label = row["reference"] or row["clip"] or row["track"]
                _marker(context, "{0}: {1}".format(row["trackKind"].replace("Track", ""), label),
                        row["start"] * fps)
                marks += 1
            elif row["referenceCab"]:
                # An effect the story ignites: the timeline names the prefab and
                # when it lives, so it is loaded and switched on for exactly that
                # span rather than left out of the scene.
                spawned = _spawn(context, row)
                if spawned is not None:
                    by_track[row["track"]] = spawned
                    spans.setdefault(row["track"], []).append(
                        (row["start"], row["start"] + row["duration"]))

        for track, windows in spans.items():
            _keyframe_visibility(by_track.get(track) or loaded.get(_actor_of_track(cast_of, plan, track)),
                                 windows, fps, last)

        if placed:
            # A timeline's range is the WHOLE unit, not any one clip's span --
            # which is why the strips were laid down with frame_range=False and
            # the range is stated here instead, through the same one setter.
            animation_builder.set_frame_range(context.scene, 0.0, last)
        summary = ("{0}: {1} actor(s), {2} of {3} timeline clip(s) placed at {4} fps, "
                   "{5} activation window(s), {6} marker(s)").format(
            state.unit, len(loaded), placed, len(plan), int(fps), len(spans), marks)
        if unplaced:
            summary += "; {0} clip(s) had nothing to drive ({1})".format(
                len(unplaced), ", ".join(sorted(set(unplaced))[:3]))
        if unresolved:
            summary += "; no model shipped for " + ", ".join(unresolved[:4])
        self.report({"INFO"} if placed else {"WARNING"}, summary)
        return {"FINISHED"} if placed else {"CANCELLED"}

    def _load_actor(self, context, actor):
        """Bring one performer in, and remember which rig IS them."""
        rows = _model_rows_for(actor)
        if not rows:
            return None
        ok, added = _import_cabs(context, [row["cab"] for row in rows])
        if not ok or not added:
            return None
        _ACTOR_RIGS[actor] = added[0].name
        return added[0]


class RURI_OT_story_goto_unit(bpy.types.Operator):
    """Open the story this animation belongs to.

    The actor view answers "where does this one appear"; this is the other half
    of that question -- one click lands on that cutscene or dialogue with its own
    shots, cast and Load Whole Cutscene button, instead of leaving the user to
    retype an id they just read."""
    bl_idname = "ruri.story_goto_unit"
    bl_label = "Open This Story"
    bl_description = "Switch to By Story and open the cutscene / dialogue this animation belongs to"
    channel: StringProperty()
    unit: StringProperty()
    spoken: StringProperty()

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = context.scene.ruri_story
        if not self.unit and not self.spoken:
            self.report({"WARNING"}, "That row belongs to no story unit.")
            return {"CANCELLED"}
        channel = self.channel if self.channel in (datasets.CUTSCENE, datasets.DIALOG)             else datasets.CUTSCENE
        state.mode = BY_STORY
        state.channel = channel
        if _top_table(state) is None:
            try:
                _UNITS[(BY_STORY, channel)] = datasets.story_units(channel, _language())
            except Exception as exc:
                _report_exception(self, "Reading the {0} units failed".format(channel), exc)
                return {"CANCELLED"}
        unit = self.unit or _unit_speaking(_top_table(state), self.spoken)
        if not unit:
            self.report({"WARNING"},
                        "The game ships no {0} playing '{1}'.".format(channel, self.spoken))
            return {"CANCELLED"}
        # Narrow to it as well as select it: the list is hundreds of units long,
        # and a selection the user cannot see reads as nothing having happened.
        state.search = unit
        _rebuild_top(state)
        index = next((position for position, entry in enumerate(state.entries)
                      if not entry.is_group and entry.key == unit), -1)
        if index < 0:
            self.report({"WARNING"}, "'{0}' is not in the {1} list.".format(unit, channel))
            return {"CANCELLED"}
        state.active_index = index
        _open_entry(state, unit)
        self.report({"INFO"}, "Opened {0}.".format(unit))
        return {"FINISHED"}


class RURI_OT_story_reveal(bpy.types.Operator):
    """Open where the open selection's animations live, over in the bundle
    browser.

    The folder is the one the hook read off the game's own filing, never a path
    this add-on invented."""
    bl_idname = "ruri.story_reveal"
    bl_label = "Open Containing Folder"
    bl_description = "Switch to the bundle browser and open the folder these animations live in"

    @classmethod
    def poll(cls, context):
        state = context.scene.ruri_story
        return context.scene.ruri_cabmap.loaded and bool(_opened(state))

    def execute(self, context):
        state = context.scene.ruri_story
        opened = _opened(state)
        checked = _checked(state)
        if checked:
            container, cab = next(iter(checked.items()))
        else:
            drawn = [item for item in state.clips if not item.is_group]
            if not drawn:
                return bpy.ops.ruri.cabmap_reveal(query=opened)
            container, cab = drawn[0].container, drawn[0].cab
        return bpy.ops.ruri.cabmap_reveal(query=opened,
                                          folder=container.rpartition("/")[0], cab=cab)


def _opened(state):
    return state.unit if state.mode == BY_STORY else state.actor


def _kinds_in(state):
    """The kinds the open selection's rows carry, in first-seen order -- the
    buttons that make sense for THIS selection rather than a tabulated list of
    every kind the game has."""
    kinds = []
    for item in state.clips:
        if not item.is_group and item.clip and item.kind and item.kind not in kinds:
            kinds.append(item.kind)
    return kinds


def _draw_context(layout, state):
    """What the open unit IS, before anything about the files it is made of: the
    mission the game plays it from, where and when that happens, and -- for a
    dialogue -- the recap the game itself writes for the scene."""
    if not state.mission:
        note = layout.box()
        note.label(text="No mission of this install names this unit.", icon="QUESTION")
        return
    box = layout.box()
    head = box.row(align=True)
    head.label(text=state.mission_title or state.mission, icon="OUTLINER_OB_FONT")
    tail = head.row()
    tail.enabled = False
    tail.alignment = "RIGHT"
    tail.label(text=" · ".join(part for part in (state.mission_kind, state.mission_chapter,
                                                 state.mission) if part))
    where = box.row(align=True)
    where.enabled = False
    where.label(text=state.mission_place or state.mission_level or "(no level)", icon="WORLD")
    if state.mission_character:
        where.label(text=state.mission_character, icon="OUTLINER_OB_ARMATURE")
    for text, icon in ((state.mission_description, "INFO"), (state.unit_summary, "TEXT")):
        if text:
            _draw_paragraph(box, text, icon)


def _draw_actor_context(layout, state):
    """Who the open actor is, and the answer to the question this view exists for:
    WHICH STORIES they appear in, under those stories' own names."""
    if not state.actor_who:
        note = layout.box()
        note.label(text="The game names this one nowhere -- a camera, a prop or a crowd model.",
                   icon="QUESTION")
        return
    box = layout.box()
    head = box.row(align=True)
    head.label(text=state.actor_who, icon="OUTLINER_OB_ARMATURE")
    tail = head.row()
    tail.enabled = False
    tail.alignment = "RIGHT"
    tail.label(text=" · ".join(part for part in (state.actor_title, state.actor_character
                                                 or state.actor_named) if part))
    if state.actor_places:
        where = box.row()
        where.enabled = False
        where.label(text=state.actor_places, icon="WORLD")
    if state.actor_stories:
        _draw_paragraph(box, state.actor_stories, "SEQ_STRIP_DUPLICATE")


def _draw_paragraph(layout, text, icon):
    """Blender's label draws one line and clips it; a mission description is a
    sentence. Wrapping it is the panel's own job -- there is no wrapping label."""
    column = layout.column(align=True)
    first = True
    for chunk in _wrapped(text):
        row = column.row()
        row.enabled = False
        row.label(text=chunk, icon=icon if first else "BLANK1")
        first = False


def _wrapped(text, width=34):
    """CJK is the language most of this text is written in and it wraps anywhere,
    so the split is by width rather than by word -- with a break preferred at a
    space when the line happens to have one."""
    remaining = text.strip()
    while remaining:
        if len(remaining) <= width:
            yield remaining
            return
        cut = remaining.rfind(" ", 0, width + 1)
        cut = cut if cut > width // 2 else width
        yield remaining[:cut].rstrip()
        remaining = remaining[cut:].lstrip()


def _draw_script(box, state):
    """The other half of a unit: what it says, and what the mission playing it
    asks the player to do around it."""
    said = box.column(align=True)
    said.label(text="Said in this unit", icon="OUTLINER_OB_FONT")
    said.prop(state, "line_search", icon="VIEWZOOM", text="")
    box.template_list(RURI_UL_story_lines.bl_idname, "", state, "lines",
                      state, "lines_active_index", rows=10)
    box.label(text=state.line_status or "This unit speaks nothing the text tables carry.",
              icon="INFO")
    if not state.mission:
        return
    box.separator()
    box.label(text="{0} · what the player is asked to do".format(
        state.mission_title or state.mission), icon="KEYFRAME_HLT")
    box.template_list(RURI_UL_story_quests.bl_idname, "", state, "quests",
                      state, "quests_active_index", rows=8)
    box.label(text=state.quest_status, icon="INFO")


def draw_story_tab(layout, context):
    state = context.scene.ruri_story

    head = layout.row(align=True)
    head.prop(state, "mode", expand=True)
    if state.mode == BY_STORY:
        layout.row(align=True).prop(state, "channel", expand=True)
    filter_ui.draw_search_row(layout, state,
                              extra_operator=(RURI_OT_story_refresh.bl_idname, "FILE_REFRESH"))
    layout.template_list(RURI_UL_story_units.bl_idname, "", state, "entries",
                         state, "active_index", rows=8)
    layout.label(text=state.status, icon="INFO")

    opened = _opened(state)
    if not opened:
        layout.label(text="Pick a unit to see the animations it plays." if state.mode == BY_STORY
                     else "Pick an actor to see everything the story animates them through.",
                     icon="ANIM_DATA")
        return

    layout.separator()
    if state.mode == BY_STORY:
        _draw_context(layout, state)
    else:
        _draw_actor_context(layout, state)

    box = layout.box()
    box.label(text=opened, icon="SEQ_STRIP_DUPLICATE" if state.mode == BY_STORY
              else "OUTLINER_OB_ARMATURE")
    if state.mode == BY_STORY:
        box.row(align=True).prop(state, "content", expand=True)
        if state.content == BY_SCRIPT:
            _draw_script(box, state)
            return
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
    if state.mode == BY_ACTOR:
        # The row in front of the user names a story; make going there one click,
        # since "which cutscene is this from" is the question the actor view
        # raises and cannot answer on its own.
        highlighted = state.clips[state.clips_active_index]             if 0 <= state.clips_active_index < len(state.clips) else None
        jump = actions.row()
        jump.enabled = highlighted is not None and bool(highlighted.unit)
        opened = jump.operator(RURI_OT_story_goto_unit.bl_idname, icon="ZOOM_SELECTED",
                               text="Open {0}".format(highlighted.unit) if highlighted is not None
                               and highlighted.unit else "Open This Story")
        if highlighted is not None:
            opened.channel = highlighted.channel
            opened.unit = highlighted.unit
    if state.mode == BY_STORY:
        whole = actions.row()
        whole.scale_y = 1.3
        whole.operator(RURI_OT_story_load_unit.bl_idname, icon="SEQUENCE")
    actions.operator(RURI_OT_story_import.bl_idname, icon="IMPORT",
                     text="Import {0} Checked Animation(s)".format(len(checked)) if checked
                     else "Import Checked Animations")
    actions.operator(RURI_OT_story_reveal.bl_idname, icon="FILE_FOLDER")


_CLASSES = (
    RURI_PG_story_entry,
    RURI_PG_story_clip,
    RURI_PG_story_line,
    RURI_PG_story_quest,
    RURI_PG_story,
    RURI_UL_story_units,
    RURI_UL_story_clips,
    RURI_UL_story_lines,
    RURI_UL_story_quests,
    RURI_OT_story_refresh,
    RURI_OT_story_select,
    RURI_OT_story_load_unit,
    RURI_OT_story_goto_unit,
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
    _LINES.clear()
    _QUESTS.clear()
    _CHECKED.clear()
    _OBJECT_ACTIONS.clear()
    _ACTOR_RIGS.clear()
    _CHARACTER_IDS.clear()
