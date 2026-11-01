"""Browse the game's cast the way the game itself lists it -- in any host.

The rows come from the game's own config containers: playable characters keyed by
charId and grouped by the game's own profession, and npcs collapsed to one row per
distinct model prefab. Names are the real localized names, in whichever language
the HOST is running in -- its locale picks which text container the C# side joins
through, so switching the application's language switches the roster with no
reload of anything else.

The list behaves like the bundle browser next door: type to filter, click to
select, Load to bring it in. A row's payload is its seed, so Load and Reveal are the
kernel's own verbs and what a member IS is the hook's statement source.

The tab's SHAPE -- the pane switch, the facet, the search row, the list, the
shared buttons under it, the Anim and Face panes -- is the one every cast tab has
(``Kernel.app.cast_panel``). What is stated here is only what is this game's:
which sources its Anim and Face panes have besides the engine's own, and the one
option it adds under the list.

Nothing here imports a host. The Blender panel next door is a shell that
materialises this declaration; Painter's dock renders the same one.
"""

from __future__ import annotations

from ...Kernel import host as host_port
from ...Kernel.app import cast_panel
from ...Kernel.app import command, filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...Kernel.app import view as app_view
from ...Kernel.bridge import cabmap_state
from . import datasets, weapons

STATE = "ruri_roster"
BROWSER_STATE = "ruri_cabmap"
SPEC_KEY = "Endfield:character"

CHARACTERS = datasets.CHARACTERS
UI_MODELS = datasets.UI_MODELS
NPCS = datasets.NPCS

#: This tab's live view and the seats that draw it. Which column is the name, the
#: id, the profession, the kind or the "has a model" test is each column's own
#: statement, made in the hook.
BOUND = app_view.Bound(SPEC_KEY)

#: Loaded row lists, by language. Module scope, not panel state: rebuilding the
#: drawn list must not cost a re-read, and a column table is not something a
#: host's property system can hold anyway.
_ROWS = {}


def state_of(context):
    return host_port.current().panel_state(context, STATE)


def browser_of(context):
    return host_port.current().panel_state(context, BROWSER_STATE)


def language(state):
    """The game language this roster is shown in.

    Which language the HOST reads in is a session fact, stated once when the driver
    came up and restated when it changes -- so this asks only the game-specific half:
    which of the languages this game ships does that locale read as."""
    return datasets.language_for_locale("")


def rows(state):
    return _ROWS.get(language(state))


def rebuild(state):
    """Ask the kernel for the drawn list as it is now stated.

    The filter is NOT evaluated here: the search text and the Include/Exclude
    rules go to the same C# engine the bundle browser searches with, over the very
    buffers this table was built from (one ASCII fold per column, then a parallel
    vectorized sweep, then the shared rule evaluator). This side receives lines and
    reads cells."""
    with filtering.rebuilding():
        BOUND.open(rows(state), state, note=language(state))


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
ROSTER = Schema("Roster", """What this game's cast tab remembers beyond the
shared record: which language it read the names in.""", (
    Field("language", app_state.STRING, ""),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE, cast_panel.CAST_STATE))


HANDLERS = cast_panel.handlers(BOUND, "Endfield.roster", rebuild)


FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=SPEC_KEY, fields=BOUND.fields,
    state_for=state_of,
    apply=lambda context: rebuild(state_of(context))))


# ---------------------------------------------------------------------------
# What is asked of a row
# ---------------------------------------------------------------------------
def _animation_rules(context, state):
    """Where this one's animations live, as a query for the bundle browser.

    Anchored on the container path, not the name: the id also keys thousands of
    per-line dialogue morph clips, and a name search buries the body animation
    library under them. Falls back to the body-type group's shared library when
    this one ships no animation folder of its own -- said out loud rather than
    substituted silently, because "these are not hers" matters.

    The rules are an Include SET (all must hold -- the rule editor's AND); the
    search box is left empty on purpose, for the user to narrow on top."""
    entry = BOUND.picked(state)
    if entry is None:
        return None
    kind = entry.cell("kind")
    found = datasets.animation_anchor(entry.key, NPCS if kind == NPCS else CHARACTERS)
    if found is None:
        return None
    rules = [
        {"field": "container", "relation": "contains", "value": found["anchor"],
         "action": "include"},
        {"field": "type_names", "relation": "contains", "value": "AnimationClip",
         "action": "include"},
    ]
    said = (
        "'{0}' ships no animations of its own; showing the {1} body-type library it "
        "actually plays ({2} rows).".format(entry.label, found["group"], found["hits"])
        if found["group"] else
        "{0}: {1} animation rows.".format(entry.label, found["hits"]))
    return rules, said


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def _loaded(context):
    return browser_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_selection(context):
    return _loaded(context) and BOUND.picked(state_of(context)) is not None


def _refresh(context, arguments):
    """Read the cast out of the game's own config containers."""
    state = state_of(context)
    tongue = language(state)
    state.language = tongue
    try:
        table = datasets.cast(tongue)
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    _ROWS[tongue] = table
    rebuild(state)
    cast_panel.opened(BOUND, state)
    return None


def _load_expressions(context, arguments):
    """Bring the picked one's SkeletalMorph expression library onto the rig in front of the
    user: a separate, much larger asset family than the model, so its own verb."""
    from . import face
    return face.load_library_for(context, BOUND.picked(state_of(context)), "")


REFRESH = command.COMMANDS.define(
    "ruri.roster_refresh", "Refresh Roster", _refresh,
    description="Read the character/npc roster out of the game's own data tables",
    poll=_loaded)
LOAD_EXPRESSIONS = command.COMMANDS.define(
    "ruri.roster_load_expressions", "Load Expressions", _load_expressions,
    description="Load the picked one's SkeletalMorph expression library onto the rig in "
                "front of you -- a separate, much larger asset family than the model",
    icon="SHAPEKEY_DATA", poll=_has_selection, requires=host_port.MorphTargets,
    steps=True, status_state=STATE, failure="Expression library load failed")


# ---------------------------------------------------------------------------
# The tab
# ---------------------------------------------------------------------------
#: Three siblings in a row share the width equally, which is what this list has
#: always looked like: name, then the game's own id, then the detail hard right.
#: As split factors that is a third of the whole, then half of what is left.
_COLUMNS = (
    BOUND.column("", label="Name", width=0.34, icon="OUTLINER_OB_ARMATURE"),
    BOUND.key_column("({0})", label="Id", width=0.5, enabled=False),
    BOUND.column("detail", label="Detail", align=app_layout.RIGHT),
)


def _draw_story(layout, context):
    """The animations story playback uses, filed the way the game files them."""
    from . import story
    story.draw_story_tab(layout, context)


def _draw_library(layout, context):
    """The SkeletalMorph emotion/pose/lipsync library: browse it, bind its ctrl
    drivers to a rig, bake its animations."""
    from . import face
    face.draw(layout, context)


PANEL = cast_panel.Panel(
    BOUND, _COLUMNS, "roster", REFRESH.id, state_of, STATE,
    animation_rules=_animation_rules, actions=(LOAD_EXPRESSIONS.id, weapons.FIND.id), facet=CHARACTERS,
    # 这个游戏自己写下来的两份目录,与引擎自己答得出的那两份并列在同一格里 ——
    # 「统一」不是把它们摊平成一份,是它们在同一个地方,并说清各自出自哪儿。
    animations=(cast_panel.Source("story", "Story",
                                  "The animations story playback uses, under the game's "
                                  "own filing -- a cutscene by shot, a dialogue by line",
                                  _draw_story),),
    # 这个游戏的脸**不是**混合形状:表情是 SkeletalMorph 把 ctrl 解算成骨骼的 ΔTRS。
    # 所以自家这份库排在第一位 —— 一格默认开在这个游戏真正的答案上,引擎那份
    # (只剩几个眉毛网格还带命名形状)留在后面给要的人。
    expressions=(cast_panel.Source("library", "Library",
                                   "The game's own SkeletalMorph emotion/pose/lipsync "
                                   "library, bindable to a rig and bakeable",
                                   _draw_library),
                 cast_panel.ENGINE_SHAPES))


def draw(layout, context):
    cast_panel.draw(PANEL, layout, context, state_of(context))


def register():
    host_port.current().register_state(
        STATE, ROSTER, HANDLERS, extra={"FILTER_SPEC_KEY": SPEC_KEY})


def unregister():
    host_port.current().unregister_state(STATE)
    cast_panel.forget(BOUND)
    BOUND.close()
    _ROWS.clear()
