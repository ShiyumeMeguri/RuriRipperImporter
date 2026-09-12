"""Browse the game's cast the way the game itself lists it -- in any host.

The rows come from the game's own config containers: playable characters keyed by
charId and grouped by the game's own profession, and npcs collapsed to one row per
distinct model prefab. Names are the real localized names, in whichever language
the HOST is running in -- its locale picks which text container the C# side joins
through, so switching the application's language switches the roster with no
reload of anything else.

The list behaves like the bundle browser next door: type to filter, click to
select, Load to bring it in. What "bring it in" MEANS is the host's
(:meth:`Kernel.host.Host.import_packages`); this module's business ends at
resolving the row to what the game says it is made of.

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
from ...Kernel.app import loading, schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...Kernel.app import view as app_view
from ...RuriRipperPyBridge.session import cabmap_state
from . import cast, datasets

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


def detail_level(context):
    """Which detail level to load, as the ONE place that states it.

    Not a setting of this tab: the same number decides what the bundle browser
    imports and what a story unit stages, and three copies of it meant picking a
    level here changed nothing there."""
    return browser_of(context).detail_level


def language(state):
    """The game language this roster is shown in -- the host's own locale, mapped
    onto the languages the game ships."""
    return datasets.language_for_locale(host_port.current().locale())


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
ROSTER = Schema("Roster", """The cast browser's state.""", (
    Field("facet", app_state.ENUM, None, "Kind",
          "Which of the game's own kinds to list", items="facet_items",
          update="on_filter_edit"),
    Field("search", app_state.STRING, "", "Filter",
          "Filter by displayed name, id or group", update="on_filter_edit", live=True),
    Field("rows", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("active_index", app_state.INT, 0),
    Field("status", app_state.STRING, "Load a cabmap, then refresh the roster."),
    Field("language", app_state.STRING, ""),
    Field("load_expressions", app_state.BOOL, False, "Expressions",
          "Also load this character's SkeletalMorph expression library. Off by "
          "default: it is a separate, much larger asset family than the model"),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE, cast_panel.CAST_STATE))


HANDLERS = cast_panel.handlers(BOUND, "Endfield.roster", rebuild)


FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=SPEC_KEY, fields=BOUND.fields,
    state_for=state_of,
    apply=lambda context: rebuild(state_of(context))))


# ---------------------------------------------------------------------------
# What a row IS
# ---------------------------------------------------------------------------
def _member(entry):
    """One picked row in the game's own identity words.

    The kind is not a branch on behaviour: a playable character and an npc are two
    answers to one question (``cast.resolve``), and a character's in-world actor
    and the model its menus pose are two families of the same answer. Which one
    this row gets is the game's own filing, carried on the row."""
    kind = entry.cell("kind")
    return {"key": entry.key, "label": entry.label,
            "character": entry.key if kind in (CHARACTERS, UI_MODELS) else "",
            "template": entry.key if kind == NPCS else "",
            "family": datasets.UI_MODEL if kind == UI_MODELS else datasets.POST_MODEL}


def _loadable(context, entry):
    member = _member(entry)
    return member, cast.resolve([member], detail_level(context)).get(member["key"] or "")


def _seeds(context, state):
    """What the picked one IS, as archive names -- what every shared button below
    the list is asked of."""
    entry = BOUND.picked(state)
    if entry is None:
        return []
    _stated, packages = _loadable(context, entry)
    return list(packages.cabs) if packages is not None else []


def _anim_seeds(context, state):
    """Where this one's animations ALSO live.

    A character prefab names its animator, but the body animation library is a
    folder of its own the prefab never references -- and for a character that
    ships none, the shared library of its body type is what it actually plays.
    Both are facts this game states (``animation_anchor``), handed over as seeds
    so the one engine reader files those clips under whatever names them."""
    entry = BOUND.picked(state)
    if entry is None:
        return []
    kind = entry.cell("kind")
    return datasets.animation_cabs(
        entry.key, NPCS if kind == NPCS else CHARACTERS)


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
    return None


def _load(context, arguments):
    """What loading the selected one IS, as steps -- resolve, read, hand over."""
    state = state_of(context)
    entry = BOUND.picked(state)
    member, packages = yield command.Read(lambda: _loadable(context, entry), 0.3)
    if packages is None:
        return loading.Built(warnings=[
            "The game states no {0} for '{1}'.".format(
                member["family"], entry.label or entry.key)])
    resolved = yield command.Read(lambda: loading.resolve_closure(packages.cabs), 0.75)
    yield command.Mark(0.85)
    return host_port.current().import_packages(
        context, packages, browser_of(context).as_options(), resolved=resolved)


def _settle_load(context, built):
    """The face library is a separate asset family, so it is a separate import --
    offered only where there is a face to put it on."""
    state = state_of(context)
    if built is None or (built.armature is None and not built.imported):
        return {"CANCELLED"}
    if state.load_expressions and host_port.supports(host_port.MORPH_TARGETS):
        from . import face
        face.load_library_for(context, BOUND.picked(state),
                              (built.manifest or {}).get("facial_morph", ""))
    return {"FINISHED"}


def _reveal(context, arguments):
    """Open where the selected cast member lives, over in the bundle browser. The
    query is the id the game itself keys that row by -- no path convention of ours
    is involved."""
    state = state_of(context)
    entry = BOUND.picked(state)
    if entry is None:
        return {"CANCELLED"}
    reveal = command.COMMANDS.get("ruri.cabmap_reveal")
    kind = entry.cell("kind")
    if kind in (CHARACTERS, UI_MODELS):
        found = datasets.model_rows(
            entry.key,
            datasets.UI_MODEL if kind == UI_MODELS else datasets.POST_MODEL,
            cast=CHARACTERS)
        if found:
            row = found[0]
            return reveal.run(context, {"query": entry.key, "cab": row["cab"],
                                        "folder": row["container"].rpartition("/")[0]})
        return reveal.run(context, {"query": entry.key, "cab": "", "folder": ""})
    # An npc's own name reaches no mesh at all (the meshes are named after the art
    # family, not the template), so reveal the first mesh its slot table names.
    _info, hits, _missing = cast.model_parts(entry.key, detail_level(context))
    if hits:
        cab, meshes = hits[0]
        return reveal.run(context, {"query": meshes[0], "cab": cab, "folder": ""})
    return reveal.run(context, {"query": entry.key, "cab": "", "folder": ""})


REFRESH = command.COMMANDS.define(
    "ruri.roster_refresh", "Refresh Roster", _refresh,
    description="Read the character/npc roster out of the game's own data tables",
    poll=_loaded)
LOAD = command.COMMANDS.define(
    "ruri.roster_load", "Load Model", _load,
    description="Import this one's model, exactly as the bundle browser would",
    poll=_has_selection, steps=True, status_state=STATE, settle=_settle_load,
    failure="Loading this one's model failed")
REVEAL = command.COMMANDS.define(
    "ruri.roster_reveal", "Open Containing Folder", _reveal,
    description="Switch to the bundle browser and open where this one's assets live",
    poll=lambda context: BOUND.picked(state_of(context)) is not None)


# ---------------------------------------------------------------------------
# The tab
# ---------------------------------------------------------------------------
def _id_cell(seat):
    """The id, unless the name already IS the id."""
    key = BOUND.cell(seat, "key")
    return "" if key == BOUND.cell(seat) else "({0})".format(key)


#: Three siblings in a row share the width equally, which is what this list has
#: always looked like: name, then the game's own id, then the detail hard right.
#: As split factors that is a third of the whole, then half of what is left.
_COLUMNS = (
    BOUND.column("", label="Name", width=0.34, icon="OUTLINER_OB_ARMATURE"),
    # The game's own id, dimmed: with several rows sharing a display name it is
    # the only thing that tells them apart. Blank when the name already IS the
    # id, so nothing is printed twice.
    app_layout.ListColumn(_id_cell, "Id", width=0.5, enabled=False),
    BOUND.column("detail", label="Detail", align=app_layout.RIGHT),
)


def _options(layout, context, state):
    """The one thing this game adds under its cast list. The expression library is
    another asset family and another import, so it is a choice made before Load --
    and only where there is a face to put it on."""
    if host_port.supports(host_port.MORPH_TARGETS):
        layout.prop(state, "load_expressions", toggle=True, icon="SHAPEKEY_DATA")


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
    BOUND, _COLUMNS, "roster", REFRESH.id, state_of, STATE, seeds=_seeds,
    anim_seeds=_anim_seeds, options=_options, actions=(LOAD.id, REVEAL.id),
    # 这个游戏自己写下来的两份目录,与引擎自己答得出的那两份并列在同一格里 ——
    # 「统一」不是把它们摊平成一份,是它们在同一个地方,并说清各自出自哪儿。
    animations=(cast_panel.ENGINE_CLIPS,
                cast_panel.Source("story", "Story",
                                  "The animations story playback uses, under the game's "
                                  "own filing -- a cutscene by shot, a dialogue by line",
                                  _draw_story)),
    expressions=(cast_panel.ENGINE_SHAPES,
                 cast_panel.Source("library", "Library",
                                   "The game's own SkeletalMorph emotion/pose/lipsync "
                                   "library, bindable to a rig and bakeable",
                                   _draw_library)))


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
