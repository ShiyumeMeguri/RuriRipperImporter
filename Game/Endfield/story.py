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

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import command, filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...Kernel.app import view as app_view
from ...RuriRipperPyBridge.session import cabmap_state
from . import datasets

STATE = "ruri_story"


def state_of(context):
    return host_port.current().panel_state(context, STATE)


def projection(name):
    """This game's story playback, projected onto the host that is running.

    Same rule as the generated shader stack (:mod:`Kernel.shaderstack`): the
    folder IS the host's name, so the join needs no table, and a host with no
    projection has no folder -- which is exactly what the tab's ANIMATION
    capability already keeps unreachable there. Playing a cutscene is a director,
    not a thing a second host does differently."""
    import importlib
    return importlib.import_module("{0}.{1}.{2}".format(
        __package__, host_port.current().name, name))

STORY_SPEC_KEY = "Endfield:story"

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


def _language():
    """The game language this tab is shown in -- Blender's own locale, mapped onto
    the languages the game ships. Same rule as every other list in this add-on, so
    switching Blender's language switches the story with it."""
    return datasets.language_for_locale(host_port.current().locale())


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


def _filter_fields():
    """The rule vocabulary = the columns the loaded list actually has, so a column
    the hook adds is filterable the day it appears, and the two modes offer their
    own genuinely different fields."""
    return TOP.fields()


def _quick_relation(field):
    return "is" if field in ("shots", "clips", "assets", "actors", "units",
                             "cutscenes", "dialogs") else "contains"


STORY_FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=STORY_SPEC_KEY, fields=_filter_fields,
    state_for=state_of,
    apply=lambda context: _rebuild_top(state_of(context)),
    row_for=lambda context: _selected_entry(state_of(context)),
    quick_relation_for=_quick_relation))


def _on_filter_edit(state, context):
    _rebuild_top(state)


def _on_clip_filter_edit(state, context):
    _rebuild_clips(state)


def _on_line_filter_edit(state, context):
    _rebuild_lines(state)


def _on_view_change(state, context):
    """Switching mode or channel only redraws. It never calls the refresh
    operator: an operator invoked from a property update runs with the UI
    mid-update, and a failing poll there raises instead of reporting."""
    state.unit = ""
    state.actor = ""
    CLIPS.close()
    state.clip_status = ""
    _forget_context(state)
    if _top_table(state) is None:
        state.status = "Refresh to read the {0} out of the loaded cabmap.".format(
            "actor index" if state.mode == BY_ACTOR else state.channel + " units")
    _rebuild_top(state)


def _on_entry_pick(state, context):
    """Clicking a row opens it. Both clip reads are cabmap reads cached by
    (id, args) on the C# side, so walking the list with the arrow keys re-reads
    nothing already seen."""
    if filtering.is_rebuilding():
        return
    entry = _selected_entry(state)
    if entry is None or entry.key == (state.unit if state.mode == BY_STORY
                                      else state.actor):
        return
    _open_entry(state, entry.key)


def _clip_checked(seat):
    """Whether this row is ticked. The seat stores nothing: what is checked is
    keyed by what the row IS, so narrowing the filter after ticking twenty clips
    cannot quietly import whatever happened to still be on screen."""
    if CLIPS.view is None:
        return False
    return CLIPS.cell(seat, "container") in _CHECKED.get(_open_selection[0], {})


def _check_clip(seat, ticked):
    """A tick writes straight into the open selection's checked set."""
    if CLIPS.view is None:
        return
    checked = _CHECKED.setdefault(_open_selection[0], {})
    container = CLIPS.cell(seat, "container")
    if ticked and CLIPS.cell(seat, "clip"):
        checked[container] = CLIPS.cell(seat, "cab")
    else:
        checked.pop(container, None)


#: Which selection the drawn clip list belongs to. A seat's getter is called while
#: the list is being DRAWN, where there is no context to ask -- so the answer is
#: recorded when the list is opened, which is the only moment it changes.
_open_selection = [(None, "", "")]


#: The top list's live view and the seats that draw it. Both modes are views of
#: one of the hook's own tables -- a unit tally or an actor tally -- and which
#: column is the name, the id, the section or the summary is each column's own
#: statement, made where that tally is built.
TOP = app_view.Bound("Endfield:story")

#: The three lists under it. Each is a view of one of the hook's own tables, so
#: each states only WHERE it lives in this tab's state.
CLIPS = app_view.Bound("Endfield:storyclips", seats="clips",
                       index="clips_active_index", search="clip_search")
LINES = app_view.Bound("Endfield:storylines", seats="lines",
                       index="lines_active_index", search="line_search")
QUESTS = app_view.Bound("Endfield:storyquests", seats="quests",
                        index="quests_active_index", search="")


#: One seat of the clip list. It carries the tick the user makes, which is the one
#: thing about a drawn row that is NOT the view's to answer -- and even that stores
#: nothing here: it reads and writes the checked set, keyed by the row's own
#: container, so a refill cannot scramble what was ticked.
CLIP_SEAT = Schema("EndfieldClipSeat", """One seat of the clip list.""", (
    Field("row", app_state.INT, -1),
    Field("selected", app_state.BOOL, None, "Build",
          getter="clip_checked", setter="check_clip"),
))




#: How much of the place a unit happens in to bring with it. The names are the
#: projection's own, asked for by name so this side states no branch of its own.
SCENE_NONE = "none"
SCENE_WINDOW = "window"
SCENE_FULL = "full"

STORY = Schema("EndfieldStory", """The story browser's whole state.""", (
    Field("mode", app_state.ENUM, BY_STORY, "Browse", items=(
        (BY_STORY, "By Story",
         "One cutscene or dialogue timeline at a time, split into the shots the game "
         "splits it into"),
        (BY_ACTOR, "By Actor",
         "One character at a time: every animation the story plays them through, across "
         "every cutscene and dialogue, plus their own animation library")),
        update="on_view_change"),
    Field("channel", app_state.ENUM, datasets.CUTSCENE, "Channel", items=(
        (datasets.CUTSCENE, "Cutscene",
         "The game's cutscenes: body, camera and prop animation, split into the shots "
         "the game splits them into"),
        (datasets.DIALOG, "Dialogue",
         "The game's dialogue timelines: one facial animation per spoken line, next to "
         "the morph asset it drives")),
        update="on_view_change"),
    Field("search", app_state.STRING, "", "Filter",
          "Filter the list by any column the table has",
          update="on_filter_edit", live=True),
    Field("rows", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("active_index", app_state.INT, 0, update="on_entry_pick"),
    Field("status", app_state.STRING, "Load a cabmap, then refresh."),

    Field("unit", app_state.STRING, ""),
    Field("actor", app_state.STRING, ""),
    Field("scene_mode", app_state.ENUM, SCENE_NONE, "Level", items=(
        (SCENE_NONE, "No level",
         "Build only the performance -- the story plays without the place it happens in"),
        (SCENE_WINDOW, "Level around the camera",
         "Also bring in the part of the level the camera travels through"),
        (SCENE_FULL, "Whole level",
         "Also bring in the entire level -- heavy"))),
    Field("clip_search", app_state.STRING, "", "Filter",
          "Filter these animations by unit, shot, kind, actor or name",
          update="on_clip_filter_edit", live=True),
    Field("clips", app_state.COLLECTION, element=CLIP_SEAT),
    Field("clips_active_index", app_state.INT, 0),
    Field("clip_status", app_state.STRING, ""),

    Field("content", app_state.ENUM, BY_ANIMATION, "Show", items=(
        (BY_ANIMATION, "Animation",
         "The animations this unit plays, grouped the way the game splits them"),
        (BY_SCRIPT, "Script",
         "What this unit says and what the mission playing it asks the player to do"))),

    # What the open unit IS, as the mission that plays it states it. Flat strings
    # rather than a re-read: the panel redraws constantly and this is what it draws.
    Field("mission", app_state.STRING, ""),
    Field("mission_title", app_state.STRING, ""),
    Field("mission_kind", app_state.STRING, ""),
    Field("mission_chapter", app_state.STRING, ""),
    Field("mission_level", app_state.STRING, ""),
    Field("mission_place", app_state.STRING, ""),
    Field("mission_description", app_state.STRING, ""),
    Field("mission_character", app_state.STRING, ""),
    Field("unit_summary", app_state.STRING, ""),
    Field("unit_relation", app_state.STRING, ""),

    Field("line_search", app_state.STRING, "", "Filter",
          "Filter what is said by speaker, text or emotion",
          update="on_line_filter_edit", live=True),
    Field("lines", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("lines_active_index", app_state.INT, 0),
    Field("line_status", app_state.STRING, ""),

    Field("quests", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("quests_active_index", app_state.INT, 0),
    Field("quest_status", app_state.STRING, ""),

    # Who the open ACTOR is, and which stories they are in. Separate from the
    # mission fields above on purpose: the two views open different things, and one
    # set of strings meaning two things is how a panel starts drawing the wrong one.
    Field("actor_who", app_state.STRING, ""),
    Field("actor_title", app_state.STRING, ""),
    Field("actor_named", app_state.STRING, ""),
    Field("actor_character", app_state.STRING, ""),
    Field("actor_stories", app_state.STRING, ""),
    Field("actor_places", app_state.STRING, ""),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE))

HANDLERS = app_state.Handlers(
    "Endfield.story", base=filtering.HANDLERS,
    on_filter_edit=_on_filter_edit, on_clip_filter_edit=_on_clip_filter_edit,
    on_line_filter_edit=_on_line_filter_edit, on_view_change=_on_view_change,
    on_entry_pick=_on_entry_pick,
    clip_checked=lambda seat: _clip_checked(seat),
    check_clip=lambda seat, ticked: _check_clip(seat, ticked))


def _rebuild_top(state):
    """Ask the kernel for the drawn list on top -- units or actors, whichever mode
    is on. Nothing is matched, ordered, grouped, counted or worded here: the tally
    the hook built already carries its own sections and summaries, in the order it
    means them to be read.

    What is OPEN survives this: the selection's identity is its key, not its
    position, so the highlight is re-pointed at the same unit/actor afterwards --
    and a filter that hides it leaves it open rather than swapping it for
    whichever row inherited the index."""
    with filtering.rebuilding():
        opened = _opened(state)
        view = TOP.open(_top_table(state), state, ordered=True,
                        note=state.channel if state.mode == BY_STORY else "")
        if view is None:
            return
        state.status = view.summary + _elsewhere_note(state, view)
        if opened and view.index_of_key(opened) < 0:
            state.status += " · {0} still open (filtered out)".format(opened)


def _elsewhere_note(state, view):
    """Nothing HERE is not nothing ANYWHERE: the list is split in two and the search
    only sees the half in front, so an empty result is otherwise indistinguishable
    from the game not having it -- while the other half may be showing it.

    Only asked when this channel has none, and only of a table already read -- a
    search box must not trigger a cabmap read on every keystroke."""
    query = state.search.strip()
    if view.matched or not query or state.mode != BY_STORY:
        return ""
    other = datasets.DIALOG if state.channel == datasets.CUTSCENE else datasets.CUTSCENE
    table = _UNITS.get((BY_STORY, other))
    try:
        if table is None:
            table = datasets.story_units(other, _language())
            _UNITS[(BY_STORY, other)] = table
        found = len(cabmap_state.BRIDGE.search_data_table(table, query, None))
    except Exception:
        return ""
    return "" if not found else \
        " \u00b7 {0} match(es) in {1} -- switch Channel".format(found, other)


def _open_entry(state, key):
    """Read what the picked row plays and draw it grouped by the game's own split:
    by shot inside one cutscene, by what an asset drives inside one dialogue, and
    by the unit it belongs to when the pick was an actor."""
    by_story = state.mode == BY_STORY
    state.unit = key if by_story else ""
    state.actor = "" if by_story else key
    CLIPS.close()
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
    LINES.close()
    state.line_status = ""
    QUESTS.close()
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
    _rebuild_quests(state)


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
    """Ask the kernel for the clip list. Which column is its name and which is its
    section depend on the mode -- by story a clip is its actor under its shot, by
    actor it is its own name under the story it came from -- so the view is told
    which, rather than the table carrying both answers as one."""
    with filtering.rebuilding():
        _open_selection[0] = _selection_key(state)
        by_story = state.mode == BY_STORY
        CLIPS.open(_clips_table(state), state, ordered=True,
                   label_column="actorLabel" if by_story else "name",
                   group_column="shotSection" if by_story else "storySection")
        checked = _CHECKED.get(_open_selection[0], {})
        state.clip_status = ("" if CLIPS.view is None else CLIPS.summary) + (
            " · {0} checked".format(len(checked)) if checked else "")


def _rebuild_quests(state):
    """The mission's own quest graph, in the order its main path walks it -- which
    is the order the hook states it in, so the view keeps it."""
    with filtering.rebuilding():
        QUESTS.open(_quests_table(state), state, ordered=True)
        state.quest_status = "" if QUESTS.view is None else QUESTS.summary


def _selected_entry(state):
    return TOP.picked(state)


def _checked(state):
    """{container path: cab} for the open selection -- the WHOLE checked set,
    including rows the current filter is hiding."""
    return _CHECKED.get(_selection_key(state), {})


#: The four lists this tab shows, as the cells their rows have. Built where the
#: mode is known when the mode changes what a row means -- by actor a unit row is
#: a performer, by story it is a scene.
def _unit_columns(by_actor):
    return (TOP.column("", width=0.72,
                       icon="OUTLINER_OB_ARMATURE" if by_actor else "SEQ_STRIP_DUPLICATE"),
            TOP.column("detail", align=app_layout.RIGHT, enabled=False))


_UNIT_GROUP = TOP.column("", icon="OUTLINER_COLLECTION")

def _is_clip(seat):
    """Whether this row IS an animation. The game files assets next to its clips
    that are not clips (a dialogue timeline's morph asset), and only a clip can be
    built -- so only a clip's box can be ticked."""
    return bool(CLIPS.cell(seat, "clip"))


def _clip_kind(seat):
    return CLIPS.cell(seat, "kind")


_CLIP_COLUMNS = (
    app_layout.ListColumn("", width=0.08, prop="selected", enabled=_is_clip),
    app_layout.ListColumn(_clip_kind, width=0.28,
                          icon=lambda seat: _KIND_ICONS.get(_clip_kind(seat),
                                                            _DEFAULT_KIND_ICON)),
    CLIPS.column("", width=0.75),
    app_layout.ListColumn(lambda seat: "" if _is_clip(seat) else "not a clip",
                          align=app_layout.RIGHT, enabled=False),
)
#: By actor a clip group IS a story unit, so it carries the way into it; by story
#: it is a caption, which is what an empty values dict says.
_CLIP_GROUP = CLIPS.column("", icon="SEQUENCE")

def _is_option(seat):
    return LINES.cell(seat, "kind") == datasets.LINE_OPTION


def _speaker(seat):
    return LINES.cell(seat, "who")


_LINE_COLUMNS = (
    app_layout.ListColumn(
        lambda seat: "" if _is_option(seat) else _speaker(seat),
        width=0.2, align=app_layout.RIGHT,
        icon=lambda seat: ("TRIA_RIGHT" if _is_option(seat)
                           else ("" if _speaker(seat) else "REC")),
        enabled=lambda seat: bool(_speaker(seat)) or _is_option(seat)),
    LINES.column("text", width=0.8),
    LINES.column("emotion", align=app_layout.RIGHT, enabled=False),
)

def _quest_link(seat):
    return QUESTS.cell(seat, "dialog") or QUESTS.cell(seat, "cutscene")


_QUEST_COLUMNS = (
    QUESTS.column("", width=0.7, icon="DOT"),
    # What the game itself says this objective waits on is the one link that is
    # stated rather than read off a file name -- so it is a click. An objective
    # that waits on nothing states no arguments and the cell is its place instead.
    app_layout.ListColumn(lambda seat: _quest_link(seat) or QUESTS.cell(seat, "place"),
                          align=app_layout.RIGHT, icon="ZOOM_SELECTED",
                          command="ruri.story_goto_unit",
                          values=lambda seat: (
                              {"channel": (datasets.CUTSCENE if QUESTS.cell(seat, "cutscene")
                                           else datasets.DIALOG),
                               "spoken": _quest_link(seat)}
                              if _quest_link(seat) else {})),
)
_QUEST_GROUP = QUESTS.column(
    "", icon=lambda seat: ("KEYFRAME_HLT" if float(QUESTS.cell(seat, "mainPath") or -1) >= 0
                           else "KEYFRAME"))


# ---------------------------------------------------------------------------
# What the buttons do
# ---------------------------------------------------------------------------
def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_clips(context):
    return len(state_of(context).clips) > 0


def _has_checked(context):
    return _loaded(context) and bool(_checked(state_of(context)))


def _has_unit(context):
    state = state_of(context)
    return _loaded(context) and state.mode == BY_STORY and bool(state.unit)


def _has_opened(context):
    return _loaded(context) and bool(_opened(state_of(context)))


def _refresh(context, arguments):
    """Read the story list out of the loaded cabmap -- the units of this channel, or
    the actor index, whichever mode is on."""
    state = state_of(context)
    by_story = state.mode == BY_STORY
    try:
        table = datasets.story_units(state.channel, _language()) if by_story \
            else datasets.story_actors(language=_language())
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    _UNITS[(state.mode, state.channel if by_story else "")] = table
    _rebuild_top(state)
    return None


def _select(context, arguments):
    """Check or uncheck this unit's animations -- everything, nothing, or just the
    ones moving one kind of thing (the actors, say, and not the cameras and props
    alongside them)."""
    state = state_of(context)
    mode, kind = arguments["mode"], arguments["kind"]
    if mode == "NONE":
        # The whole set, not just what is drawn: unchecking has to be able to undo a
        # check made under a different filter.
        _CHECKED.pop(_selection_key(state), None)
    for row in CLIPS.rows():
        if not row["clip"]:
            continue
        if mode == "ALL" or (mode == "KIND" and row["kind"] == kind):
            _CHECKED.setdefault(_open_selection[0], {})[row["container"]] = row["cab"]
    return None


def _import_checked(context, arguments):
    """Build the checked animations as actions on the rigs in the document.

    An animation belongs to the one it animates, and the game says which one that is
    -- so the checked rows are split by actor and each group is built onto that
    actor's own rig. Only rows whose actor has no rig fall back to the user's own
    selection, which is the case the generic browser import was always for."""
    state = state_of(context)
    checked = _checked(state)
    if not checked:
        state.clip_status = "Nothing checked."
        return {"CANCELLED"}

    by_actor = {}
    for row in CLIPS.rows():
        if not row["clip"] or row["container"] not in checked:
            continue
        by_actor.setdefault(row["actor"], []).append(row["cab"])
    for _container, cab in checked.items():
        if not any(cab in cabs for cabs in by_actor.values()):
            by_actor.setdefault("", []).append(cab)

    built, homeless = projection("stage").land_clips(context, by_actor)
    lines = []
    if homeless:
        lines.append("No rig for {0} -- use Load Whole Cutscene, or load them "
                     "first.".format(", ".join(sorted(set(homeless))[:4])))
    if not built:
        state.clip_status = "  ".join(lines) or "Nothing built."
        return {"CANCELLED"}
    state.clip_status = "Built {0} animation(s) across {1} rig(s). {2}".format(
        built, len(by_actor), "  ".join(lines))
    return None


def _load_unit(context, arguments):
    """Bring the whole unit up on stage, the way the game plays it.

    Every decision about WHAT to build is the game's own, read off its Timeline and
    handed over as a directive stream; this command states the two things that are
    the user's -- which unit, and whether to drag the surrounding level in with it.
    The building itself is the projection, one function per directive.

    The cross-boundary reads (the timeline document, the curve prefetch, the cast's
    one animation closure) are ``Read`` steps, so the host drives them off its own
    main thread and the panel keeps a live line of the hook's own console."""
    state = state_of(context)
    stage_module = projection("stage")
    stage = None
    for step in stage_module.build_steps(context, state.unit, language=_language(),
                                         scene_mode=state.scene_mode):
        stage = yield step
    if stage is None:
        state.status = "This unit's timeline places nothing -- nothing to play."
        return
    built = stage.placed or stage.lines
    if built and arguments["play"]:
        projection("player").play_if_ready(context)
    state.status = stage_module.summary(stage)


def _goto_unit(context, arguments):
    """Open the story this animation belongs to.

    The actor view answers "where does this one appear"; this is the other half of
    that question -- one click lands on that cutscene or dialogue with its own shots,
    cast and Load Whole Cutscene button, instead of leaving the user to retype an id
    they just read."""
    state = state_of(context)
    unit, spoken = arguments["unit"], arguments["spoken"]
    if not unit and not spoken:
        state.status = "That row belongs to no story unit."
        return {"CANCELLED"}
    channel = arguments["channel"] if arguments["channel"] in (
        datasets.CUTSCENE, datasets.DIALOG) else datasets.CUTSCENE
    state.mode = BY_STORY
    state.channel = channel
    if _top_table(state) is None:
        try:
            _UNITS[(BY_STORY, channel)] = datasets.story_units(channel, _language())
        except Exception as exc:
            state.status = "Reading the {0} units failed: {1}: {2}".format(
                channel, type(exc).__name__, exc)
            return {"CANCELLED"}
    unit = unit or _unit_speaking(_top_table(state), spoken)
    if not unit:
        state.status = "The game ships no {0} playing '{1}'.".format(channel, spoken)
        return {"CANCELLED"}
    # Narrow to it as well as select it: the list is hundreds of units long, and a
    # selection the user cannot see reads as nothing having happened.
    state.search = unit
    _rebuild_top(state)
    index = TOP.view.index_of_key(unit) if TOP.view is not None else -1
    if index < 0:
        state.status = "'{0}' is not in the {1} list.".format(unit, channel)
        return {"CANCELLED"}
    state.active_index = index
    _open_entry(state, unit)
    state.status = "Opened {0}.".format(unit)
    return None


def _reveal(context, arguments):
    """Open where the open selection's animations live, over in the bundle browser.

    The folder is the one the hook read off the game's own filing, never a path this
    add-on invented."""
    state = state_of(context)
    opened = _opened(state)
    checked = _checked(state)
    reveal = command.COMMANDS.get("ruri.cabmap_reveal")
    if checked:
        container, cab = next(iter(checked.items()))
    else:
        drawn = CLIPS.rows()
        if not drawn:
            return reveal.run(context, {"cab": "", "query": opened, "folder": ""})
        container, cab = drawn[0]["container"], drawn[0]["cab"]
    return reveal.run(context, {"cab": cab, "query": opened,
                                "folder": container.rpartition("/")[0]})


REFRESH = command.COMMANDS.define(
    "ruri.story_refresh", "Refresh Story List", _refresh,
    description="List the cutscenes / dialogue timelines the game ships animations "
                "for, or everyone the story animates",
    icon="FILE_REFRESH", requires=host_port.ANIMATION, poll=_loaded)
SELECT = command.COMMANDS.define(
    "ruri.story_select", "Check Animations", _select,
    description="Check or uncheck the listed animations",
    requires=host_port.ANIMATION, poll=_has_clips,
    arguments=(Field("mode", app_state.STRING, "ALL"),
               Field("kind", app_state.STRING, "")))
IMPORT = command.COMMANDS.define(
    "ruri.story_import", "Import Checked Animations", _import_checked,
    description="Build the checked animations onto the rigs the game names -- the "
                "actor's own where there is one",
    icon="IMPORT", requires=host_port.ANIMATION, poll=_has_checked)
LOAD_UNIT = command.COMMANDS.define(
    "ruri.story_load_unit", "Load Whole Cutscene", _load_unit,
    description="Build this unit's performance: its cast, their animation, the camera "
                "it films through and the lines it speaks -- then press play",
    icon="SEQUENCE", requires=host_port.ANIMATION, poll=_has_unit, steps=True,
    status_state=STATE, failure="Building this unit's stage failed",
    arguments=(Field("play", app_state.BOOL, True, "Play when built",
                     "Start the story as soon as it is on stage, looking through the "
                     "camera the unit films with -- the way the game opens one"),))
GOTO_UNIT = command.COMMANDS.define(
    "ruri.story_goto_unit", "Open This Story", _goto_unit,
    description="Switch to By Story and open the cutscene / dialogue this animation "
                "belongs to",
    icon="ZOOM_SELECTED", requires=host_port.ANIMATION, poll=_loaded,
    arguments=(Field("channel", app_state.STRING, ""),
               Field("unit", app_state.STRING, ""),
               Field("spoken", app_state.STRING, "")))
REVEAL = command.COMMANDS.define(
    "ruri.story_reveal", "Open Containing Folder", _reveal,
    description="Switch to the bundle browser and open the folder these animations "
                "live in",
    icon="FILE_FOLDER", requires=host_port.ANIMATION, poll=_has_opened)


def _opened(state):
    return state.unit if state.mode == BY_STORY else state.actor


def _kinds_in(state):
    """The kinds the open selection's rows carry, in first-seen order -- the
    buttons that make sense for THIS selection rather than a tabulated list of
    every kind the game has."""
    kinds = []
    for row in CLIPS.rows():
        if row["clip"] and row["kind"] and row["kind"] not in kinds:
            kinds.append(row["kind"])
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
    app_view.draw_list(LINES, box, state, _LINE_COLUMNS, "story_lines", summary=False)
    box.label(text=state.line_status or "This unit speaks nothing the text tables carry.",
              icon="INFO")
    if not state.mission:
        return
    box.separator()
    box.label(text="{0} · what the player is asked to do".format(
        state.mission_title or state.mission), icon="KEYFRAME_HLT")
    app_view.draw_list(QUESTS, box, state, _QUEST_COLUMNS, "story_quests", rows=8,
                       group_column=_QUEST_GROUP, summary=False)
    box.label(text=state.quest_status, icon="INFO")


def draw_story_tab(layout, context):
    state = state_of(context)

    command.draw_progress(layout, state)

    head = layout.row(align=True)
    head.prop(state, "mode", expand=True)
    if state.mode == BY_STORY:
        layout.row(align=True).prop(state, "channel", expand=True)
    filtering.draw_search_row(layout, state,
                              extra_operator=(REFRESH.id, "FILE_REFRESH"))
    app_view.draw_list(TOP, layout, state, _unit_columns(state.mode == BY_ACTOR),
                       "story_units", rows=8, group_column=_UNIT_GROUP)
    if state.status:
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
    row.operator(SELECT.id, text="All").mode = "ALL"
    row.operator(SELECT.id, text="None").mode = "NONE"
    for kind in _kinds_in(state)[:4]:
        picked = row.operator(SELECT.id, text=kind.title())
        picked.mode = "KIND"
        picked.kind = kind

    by_actor = state.mode == BY_ACTOR
    box.list(state, "clips", "clips_active_index", _CLIP_COLUMNS, rows=10,
             identifier="story_clips", group_key=CLIPS.is_group,
             group_column=_CLIP_GROUP,
             group_command=GOTO_UNIT.id,
             visible_count=CLIPS.count,
             group_values=lambda seat: (
                 {"channel": CLIPS.cell(seat, "channel"), "unit": CLIPS.cell(seat, "unit")}
                 if CLIPS.cell(seat, "unit") and by_actor else {}))

    checked = _checked(state)
    box.label(text=state.clip_status, icon="INFO")
    actions = box.column(align=True)
    if state.mode == BY_ACTOR:
        # The row in front of the user names a story; make going there one click,
        # since "which cutscene is this from" is the question the actor view
        # raises and cannot answer on its own.
        picked = CLIPS.picked(state)
        highlighted = None if picked is None else picked.values()
        jump = actions.row()
        jump.enabled = highlighted is not None and bool(highlighted["unit"])
        opened = jump.operator(GOTO_UNIT.id, icon="ZOOM_SELECTED",
                               text="Open {0}".format(highlighted["unit"]) if highlighted is not None
                               and highlighted["unit"] else "Open This Story")
        if highlighted is not None:
            opened.channel = highlighted["channel"]
            opened.unit = highlighted.unit
    if state.mode == BY_STORY:
        whole = actions.column(align=True)
        built = whole.row()
        built.scale_y = 1.3
        built.operator(LOAD_UNIT.id, icon="SEQUENCE")
        whole.prop(state, "scene_mode", text="")
    actions.operator(IMPORT.id, icon="IMPORT",
                     text="Import {0} Checked Animation(s)".format(len(checked)) if checked
                     else "Import Checked Animations")
    actions.operator(REVEAL.id, icon="FILE_FOLDER")
    projection("player").draw_player(layout, context)


def register():
    projection("player").register()
    filtering.register_spec(STORY_FILTER_SPEC)
    host_port.current().register_state(
        STATE, STORY, HANDLERS, extra={"FILTER_SPEC_KEY": STORY_SPEC_KEY})


def unregister():
    host_port.current().unregister_state(STATE)
    projection("player").unregister()
    _UNITS.clear()
    _CLIPS.clear()
    _LINES.clear()
    _QUESTS.clear()
    _CHECKED.clear()
    projection("stage").forget()
