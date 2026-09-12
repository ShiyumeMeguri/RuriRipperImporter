"""ONE cast panel, for every game that lets you pick somebody and bring them in.

Every game's cast tab is the same shape and used to be drawn four times: a
progress line, the facet switch and the search row, the list, what it came to,
the game's own options, the shared import options, then the buttons. What differs
is never the SHAPE -- it is what a button DOES, and that is the game's own answer
because only it knows what a row is made of.

So the shape is here and stated once, and a game fills the slots:

``options``   its own toggles under the list (an outfit, a model family, a part)
``actions``   its own buttons (Import, Load, Build -- whatever it calls it)
``seeds``     what the picked row IS, as archive names, for the buttons BELOW that
              every game shares

That last one is the whole trick. A shared button cannot know how a row resolves
-- one game's row is a package path, another's is a template id joined through
three tables -- but every game can answer "what archives is this one made of",
and every shared button is a question asked of exactly that. Three are asked of
it here: the shaders the row shades with, the animations it plays, and the
expression vocabulary its meshes were built with -- all three read out of the
SAME dependency closure an import of that row would load, and all three answered
by the engine rather than by a title's own filing, so a title that never wrote a
catalog still answers.

A title that DID write one adds it as another SOURCE beside the engine's, inside
the same pane (``animations`` / ``expressions``). That is the difference between
"unified" and "flattened": one Anim pane, one Face pane, and inside each, a
switch naming where the rows came from.

Nothing here imports a host.
"""

from __future__ import annotations

from . import command as app_command
from . import filtering
from . import layout as app_layout
from . import state as app_state
from . import view as app_view
from .state import Field, Schema
from .. import host as host_port
from ...RuriRipperPyBridge.session import cabmap_state

#: The panes every cast tab has. A game's own pane is not one of these -- it is a
#: SOURCE inside one of them, because "where do these animations come from" is a
#: question about rows and not a second place to look for animations.
CAST = "cast"
ANIM = "anim"
FACE = "face"

#: The source the engine itself answers, in every pane that has one. A game that
#: ships nothing of its own never names it; a game that does puts it in the list
#: beside its own.
ENGINE = "engine"

#: The datasets that answer it, and the argument each takes the selection in.
#: Published by the decoder for every Unity build, which is why no Unity game
#: module states either. An engine whose decoder words it differently says so on
#: its own panel (``anim_dataset`` / ``face_dataset``) -- the QUESTION is the same
#: one and so is everything drawn around the answer.
ANIMATIONS_DATASET = ("unity.animations", "cab")
BLEND_SHAPES_DATASET = ("unity.blendshapes", "cab")

#: What a cast panel remembers beyond its cast list. Included by each game's own
#: schema, so the shared panes below have somewhere to put their answer without
#: every game restating the fields.
CAST_STATE = Schema("CastState", """The cast panel's shared state.""", (
    Field("pane", app_state.ENUM, CAST, "Pane", "Which part of this tab to show",
          items="cast_panes", update="on_cast_pane"),
    Field("shader_output", app_state.STRING, "", "Shader Folder",
          "Where Decompile Shaders writes the source it reads out of this row",
          subtype=app_state.DIRECTORY),

    Field("anim_source", app_state.ENUM, None, "From",
          "Where these animations are listed from", items="cast_anim_sources",
          update="on_cast_anim_source"),
    Field("anim_facet", app_state.ENUM, None, "Played By",
          "Narrow to the animations one player names", items="cast_anim_facets",
          update="on_cast_anim_edit"),
    Field("anim_search", app_state.STRING, "", "Filter",
          "Filter by clip name or what plays it", update="on_cast_anim_edit", live=True),
    Field("anim_rows", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("anim_index", app_state.INT, 0),

    Field("face_source", app_state.ENUM, None, "From",
          "Where these expressions are listed from", items="cast_face_sources",
          update="on_cast_face_source"),
    Field("face_facet", app_state.ENUM, None, "Mesh",
          "Narrow to the shapes one mesh carries", items="cast_face_facets",
          update="on_cast_face_edit"),
    Field("face_search", app_state.STRING, "", "Filter",
          "Filter by expression name or mesh", update="on_cast_face_edit", live=True),
    Field("face_rows", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("face_index", app_state.INT, 0),
    Field("face_value", app_state.FLOAT, 1.0, "Weight",
          "What to set the selected shape to on the rig in front of you",
          subtype=app_state.FACTOR, minimum=0.0, maximum=1.0),
))

#: Panel key -> Panel. A shared button is one command for every game, so it is told
#: WHICH panel pressed it rather than guessing from whatever tab is on screen.
PANELS = {}

#: Panel key -> the two lists the shared panes draw, and the archive names they
#: were last read for. Not panel state: a column table is not something a host's
#: property system can hold, and re-reading one to redraw it would cost a closure
#: load per keystroke.
_LISTS = {}
_SEEDS = {}


class Source:
    """One place a pane's rows can come from.

    The engine's own answer is one of these; so is a title's hand-written
    catalog. A source with a ``draw`` of its own replaces the pane body with it
    -- which is how a catalog with a shape of its own (two partners side by side,
    a cutscene filed by shot) keeps that shape without becoming a second pane."""

    __slots__ = ("id", "label", "description", "draw")

    def __init__(self, id, label, description="", draw=None):
        self.id = id
        self.label = label
        self.description = description
        self.draw = draw

    def item(self):
        return (self.id, self.label, self.description)


#: The engine's own sources, named once. A game states them in its own list when
#: it also has sources of its own; a game that says nothing gets exactly these.
ENGINE_CLIPS = Source(ENGINE, "This One",
                      "Every animation this one's own animator plays, read out of the build")
ENGINE_SHAPES = Source(ENGINE, "This One",
                       "Every named blend shape this one's meshes carry, read out of the build")


class Panel:
    """One game's cast tab: the shared shape, and the slots this game fills."""

    __slots__ = ("bound", "columns", "identifier", "refresh", "group_column", "rows",
                 "options", "actions", "seeds", "shaders", "state_for", "state_name",
                 "animations", "expressions", "anim_seeds", "anim_dataset", "face_dataset")

    def __init__(self, bound, columns, identifier, refresh, state_for, state_name,
                 seeds=None, group_column=None, rows=10, options=None, actions=(),
                 shaders=None, animations=(ENGINE_CLIPS,), expressions=(ENGINE_SHAPES,),
                 anim_seeds=None, anim_dataset=ANIMATIONS_DATASET,
                 face_dataset=BLEND_SHAPES_DATASET):
        self.bound = bound
        #: The panel state a shared button writes its progress and status into.
        self.state_name = state_name
        self.columns = columns
        self.identifier = identifier
        self.refresh = refresh
        #: (context) -> this panel's state record.
        self.state_for = state_for
        #: (context, state) -> the archive names the picked row is made of, or ()
        #: when the game states none. What every shared button below is asked of.
        self.seeds = seeds
        self.group_column = group_column
        self.rows = rows
        #: (layout, context, state) -> draws this game's own options.
        self.options = options
        #: This game's own buttons, as command ids.
        self.actions = tuple(actions)
        #: (state, output) -> rows, for a game whose shaders are NOT Unity shader
        #: assets. Unreal ships none: a material's program is blobs in an archive
        #: shared with everything else the build cooked, so that title answers this
        #: itself. Every Unity title leaves it alone and gets the shared reader.
        self.shaders = shaders
        #: Where the Anim and Face panes list from. The engine's own answer unless
        #: the game says otherwise, and a game with a catalog of its own states
        #: both rather than choosing.
        self.animations = tuple(animations)
        self.expressions = tuple(expressions)
        #: (context, state) -> the archives this row's animations ALSO live in.
        #: A build that files a character's clips in a folder its prefab never
        #: references answers here -- with SEEDS, not with a second list: the one
        #: engine reader then files those clips under whatever names them, and the
        #: one loader still loads them by asset key.
        self.anim_seeds = anim_seeds
        #: WHICH dataset the engine source asks, and what it calls the selection.
        self.anim_dataset = anim_dataset
        self.face_dataset = face_dataset
        PANELS[bound.key] = self

    def picked(self, context):
        return self.bound.picked(self.state_for(context))

    def seeds_of(self, context, state):
        return list(self.seeds(context, state)) if self.seeds is not None else []

    def anim_seeds_of(self, context, state):
        found = list(self.seeds_of(context, state))
        if self.anim_seeds is not None:
            for cab in self.anim_seeds(context, state):
                if cab not in found:
                    found.append(cab)
        return found

    def sources(self, pane):
        return self.animations if pane == ANIM else self.expressions

    def asks_the_engine(self, pane):
        """Whether this pane has the engine's own answer among its sources. A title
        whose engine states none says so by leaving it out, and the button that
        would ask is simply not there."""
        return any(source.id == ENGINE for source in self.sources(pane))


# ---------------------------------------------------------------------------
# The two lists the shared panes draw
# ---------------------------------------------------------------------------
def anim_bound(bound):
    """This panel's animation list. Its own seats, its own search, its own facet:
    a second list in a tab is a second list, not the first one reused."""
    return _list_of(bound, ANIM, "anim_rows", "anim_index", "anim_search", "anim_facet")


def face_bound(bound):
    return _list_of(bound, FACE, "face_rows", "face_index", "face_search", "face_facet")


def _list_of(bound, pane, seats, index, search, facet):
    made = _LISTS.get((bound.key, pane))
    if made is None:
        # The rule editor is the TAB's, spent on the cast list; a second list says
        # so rather than silently inheriting rules naming columns it does not have.
        made = _LISTS[(bound.key, pane)] = app_view.Bound(
            bound.key + ":" + pane, seats=seats, index=index, search=search,
            facet=facet, rules="")
    return made


def _bound_for(panel, pane):
    return anim_bound(panel.bound) if pane == ANIM else face_bound(panel.bound)


# ---------------------------------------------------------------------------
# What the shared buttons do
# ---------------------------------------------------------------------------
def _panel(arguments):
    return PANELS.get(arguments.get("panel", ""))


def _status_state(arguments):
    return getattr(_panel(arguments), "state_name", "")


def _picked_anything(context):
    """A row has to be pickable and made of something before any of this is a
    question. Which panel is asking is whichever one is on screen -- a poll has no
    arguments, so it answers for every registered panel and lets the button's own
    panel argument decide when it is actually pressed."""
    if cabmap_state.BRIDGE is None:
        return False
    return any(panel.picked(context) is not None for panel in PANELS.values())


def _read_shaders(context, arguments):
    """Write out every shader the picked row reaches.

    A Unity title answers through the one shared reader -- the closure of what this
    row is made of, every material in it, each decompiled to the source of the
    shader it names. A title whose engine ships no shader ASSET answers for itself
    (``shaders``)."""
    panel = _panel(arguments)
    if panel is None:
        return {"CANCELLED"}
    state = panel.state_for(context)
    entry = panel.bound.picked(state)
    if entry is None:
        return {"CANCELLED"}
    output = host_port.current().absolute_path(state.shader_output) if state.shader_output else ""
    if not output:
        state.status = "State a Shader Folder first."
        return {"CANCELLED"}
    if panel.shaders is not None:
        rows = panel.shaders(state, output)
        state.status = "{0}: {1} archive(s) -> {2}".format(
            entry.label, len(rows), rows[0].get("output", output) if rows else output)
        return None
    seeds = panel.seeds_of(context, state)
    if not seeds:
        state.status = "{0}: this install ships nothing for that row.".format(entry.label)
        return {"CANCELLED"}
    found = yield app_command.Read(
        lambda: cabmap_state.BRIDGE.export_shaders(seeds, output), 0.8)
    state.status = "{0}: {1} shader(s) -> {2}".format(
        entry.label, 0 if found is None else found.row_count, output)


def _read_engine(context, panel, pane):
    """Read one of the engine's own answers about the picked row, then seat it.

    The two are the same act asked of the same seeds -- which archives this row IS
    -- so they are one body with the dataset as its argument rather than two that
    drift."""
    state = panel.state_for(context)
    entry = panel.bound.picked(state)
    if entry is None:
        return {"CANCELLED"}
    seeds = (panel.anim_seeds_of(context, state) if pane == ANIM
             else panel.seeds_of(context, state))
    if not seeds:
        state.status = "{0}: this install ships nothing for that row.".format(entry.label)
        return {"CANCELLED"}
    dataset, argument = panel.anim_dataset if pane == ANIM else panel.face_dataset
    table = yield app_command.Read(
        lambda: cabmap_state.BRIDGE.game_data(dataset, **{argument: seeds}), 0.8)
    _SEEDS[(panel.bound.key, pane)] = seeds
    bound = _bound_for(panel, pane)
    with filtering.rebuilding():
        bound.open(table, state, note=entry.label)
    state.pane = pane
    setattr(state, "anim_source" if pane == ANIM else "face_source", ENGINE)
    state.status = "{0}: {1}".format(entry.label, bound.summary)


def _read_animations(context, arguments):
    panel = _panel(arguments)
    if panel is None:
        return {"CANCELLED"}
    return (yield from _read_engine(context, panel, ANIM))


def _read_expressions(context, arguments):
    panel = _panel(arguments)
    if panel is None:
        return {"CANCELLED"}
    return (yield from _read_engine(context, panel, FACE))


def _load_clip(context, arguments):
    """Build the picked clip onto the rig in front of the user.

    Exported by its own asset key out of the very closure the list was read from,
    so loading ONE clip out of a two-thousand-clip library costs one clip's
    serialization rather than the library's."""
    panel = _panel(arguments)
    if panel is None:
        return {"CANCELLED"}
    state = panel.state_for(context)
    host = host_port.current()
    if host.selected_rig(context) is None:
        state.status = "Select the rig to put it on first."
        return {"CANCELLED"}
    bound = anim_bound(panel.bound)
    entry = bound.picked(state)
    if entry is None:
        return {"CANCELLED"}
    seeds = _SEEDS.get((panel.bound.key, ANIM)) or panel.anim_seeds_of(context, state)
    bridge = cabmap_state.BRIDGE
    key = entry.payload
    assets = yield app_command.Read(
        lambda: bridge.import_cabs(seeds, export_asset_keys=[key])[0], 0.7)
    guids = sorted(bridge.clip_guid_by_key.values())
    if not guids:
        state.status = "'{0}' exported no animation.".format(entry.label)
        return {"CANCELLED"}
    from ...RuriRipperPyBridge.unity import bridge_asset_db
    database = bridge_asset_db.BridgeAssetDatabase(
        assets, clip_curve_blobs=bridge.clip_curves_by_guid,
        asset_paths=bridge.asset_paths_by_guid,
        texture_srgb=bridge.texture_srgb_by_guid)
    yield app_command.Mark(0.85)
    from . import browser as app_browser
    built, lines = host.import_clips(
        context, entry.cell("cab"), guids, database,
        app_browser.as_options(app_browser.state_of(context)),
        display_names={guid: entry.label for guid in guids}, activate=True)
    state.status = "  ".join(["{0}: {1} action(s).".format(entry.label, built)] + lines[:2])


def _drive_face(context, arguments):
    """Put the picked expression on the rig in front of the user, at the stated
    weight. WHICH object carries a mesh and which of its keys is index N is the
    application's answer, never this side's."""
    panel = _panel(arguments)
    if panel is None:
        return {"CANCELLED"}
    state = panel.state_for(context)
    host = host_port.current()
    rig = host.selected_rig(context)
    if rig is None:
        state.status = "Select the character's rig first."
        return {"CANCELLED"}
    bound = face_bound(panel.bound)
    entry = bound.picked(state)
    if entry is None:
        return {"CANCELLED"}
    weight = float(state.face_value)
    mesh = entry.cell("mesh")
    index = entry.cell("index")
    touched, warnings = host.drive_blend_shapes(
        context, rig, {mesh: {int(float(index or 0)): weight}})
    state.status = "  ".join(
        ["{0} = {1:.2f} on {2} mesh(es).".format(entry.label, weight, touched)] + warnings[:2])
    return None


def _clear_face(context, arguments):
    """Every shape the drawn list holds back to zero -- the way out of a face left
    mid-expression, without hunting for which ones were set."""
    panel = _panel(arguments)
    if panel is None:
        return {"CANCELLED"}
    state = panel.state_for(context)
    host = host_port.current()
    rig = host.selected_rig(context)
    if rig is None:
        state.status = "Select the character's rig first."
        return {"CANCELLED"}
    bound = face_bound(panel.bound)
    weights = {}
    for row in bound.rows():
        weights.setdefault(str(row["mesh"]), {})[int(float(row["index"] or 0))] = 0.0
    if not weights:
        return {"CANCELLED"}
    touched, warnings = host.drive_blend_shapes(context, rig, weights)
    state.status = "  ".join(["Cleared on {0} mesh(es).".format(touched)] + warnings[:2])
    return None


PANEL_ARGUMENT = (app_state.Field("panel", app_state.STRING, ""),)

SHADERS = app_command.COMMANDS.define(
    "ruri.cast_shaders", "Decompile Shaders", _read_shaders,
    description="Write out every shader the selected row reaches, as source",
    icon="NODE_MATERIAL", poll=_picked_anything, steps=True,
    failure="Reading this one's shaders failed",
    status_state=_status_state, arguments=PANEL_ARGUMENT)

ANIMATIONS = app_command.COMMANDS.define(
    "ruri.cast_animations", "Find Animations", _read_animations,
    description="List every animation the selected row's own animator plays, in the Anim pane",
    icon="ANIM_DATA", poll=_picked_anything, steps=True,
    failure="Reading this one's animations failed",
    status_state=_status_state, arguments=PANEL_ARGUMENT)

EXPRESSIONS = app_command.COMMANDS.define(
    "ruri.cast_expressions", "Find Expressions", _read_expressions,
    description="List every named blend shape the selected row's meshes carry, in the Face pane",
    icon="SHAPEKEY_DATA", poll=_picked_anything, steps=True,
    requires=host_port.MORPH_TARGETS,
    failure="Reading this one's expressions failed",
    status_state=_status_state, arguments=PANEL_ARGUMENT)

LOAD_CLIP = app_command.COMMANDS.define(
    "ruri.cast_load_clip", "Load Clip", _load_clip,
    description="Build the selected animation onto the rig in front of you",
    icon="ANIM_DATA", poll=_picked_anything, steps=True,
    requires=host_port.ANIMATION, failure="Loading this animation failed",
    status_state=_status_state, arguments=PANEL_ARGUMENT)

DRIVE_FACE = app_command.COMMANDS.define(
    "ruri.cast_drive_face", "Show On Rig", _drive_face,
    description="Set the selected expression on the rig in front of you",
    icon="SHAPEKEY_DATA", poll=_picked_anything, requires=host_port.MORPH_TARGETS,
    arguments=PANEL_ARGUMENT)

CLEAR_FACE = app_command.COMMANDS.define(
    "ruri.cast_clear_face", "Clear All", _clear_face,
    description="Set every expression in this list back to zero on the rig",
    icon="X", poll=_picked_anything, requires=host_port.MORPH_TARGETS,
    arguments=PANEL_ARGUMENT)


# ---------------------------------------------------------------------------
# What a game binds to its schema
# ---------------------------------------------------------------------------
def handlers(bound, owner, rebuild, **extra):
    """The behaviour every cast panel's schema names, bound to THIS panel's lists.

    A game supplies what only it can -- how to re-read its own cast (``rebuild``)
    -- and nothing else. The facet switch, the two shared panes' switches and
    their search boxes are the same behaviour in every game, so they are written
    once and closed over the panel's own lists here."""

    def cast_panes(state, context):
        panel = PANELS.get(bound.key)
        made = [(CAST, "Cast", "Everyone this game ships a model for")]
        if panel is not None and panel.animations and host_port.supports(host_port.ANIMATION):
            made.append((ANIM, "Anim", "The animations the selected one plays"))
        if panel is not None and panel.expressions and host_port.supports(host_port.MORPH_TARGETS):
            made.append((FACE, "Face", "The expressions the selected one was built with"))
        return made

    def on_cast_pane(state, context):
        """The pane picks WHAT is on screen; nothing is read by switching to it.
        A command invoked from a property update runs with the UI mid-update, and
        its poll failing there raises rather than reporting."""

    def sources_of(pane):
        panel = PANELS.get(bound.key)
        return [source.item() for source in (panel.sources(pane) if panel else ())]

    def cast_anim_sources(state, context):
        return sources_of(ANIM) or [(ENGINE, "This One", "")]

    def cast_face_sources(state, context):
        return sources_of(FACE) or [(ENGINE, "This One", "")]

    def cast_anim_facets(state, context):
        return anim_bound(bound).facet_choices(state, context)

    def cast_face_facets(state, context):
        return face_bound(bound).facet_choices(state, context)

    def cast_anim_edit(state, context):
        _reopen(anim_bound(bound), state)

    def cast_face_edit(state, context):
        _reopen(face_bound(bound), state)

    def cast_source_change(state, context):
        """Switching source switches which rows are on screen; the source draws
        its own body, so nothing is re-read here."""

    return app_state.Handlers(
        owner, base=filtering.HANDLERS,
        facet_items=lambda state, context: bound.facet_choices(state, context),
        on_filter_edit=lambda state, context: rebuild(state),
        cast_panes=cast_panes,
        on_cast_pane=on_cast_pane,
        cast_anim_sources=cast_anim_sources,
        cast_face_sources=cast_face_sources,
        cast_anim_facets=cast_anim_facets,
        cast_face_facets=cast_face_facets,
        on_cast_anim_edit=cast_anim_edit,
        on_cast_face_edit=cast_face_edit,
        on_cast_anim_source=cast_source_change,
        on_cast_face_source=cast_source_change,
        **extra)


def _reopen(bound, state):
    """Re-ask for a shared pane's list as the search box now reads it. The table
    is the one already read for this row -- narrowing is the C# engine's over the
    very buffers it was built from, never a second read of the game."""
    if bound.table is None:
        return
    with filtering.rebuilding():
        bound.open(bound.table, state)


def forget(bound):
    """Release what a panel's shared panes are holding. Called from its
    unregister, because a pinned table outlives a reloaded add-on otherwise."""
    for pane in (ANIM, FACE):
        made = _LISTS.pop((bound.key, pane), None)
        if made is not None:
            made.close()
        _SEEDS.pop((bound.key, pane), None)
    PANELS.pop(bound.key, None)


# ---------------------------------------------------------------------------
# What it looks like
# ---------------------------------------------------------------------------
def _source(panel, pane, state):
    """The source this pane is showing, as the game declared it."""
    wanted = getattr(state, "anim_source" if pane == ANIM else "face_source", "")
    sources = panel.sources(pane)
    for source in sources:
        if source.id == wanted:
            return source
    return sources[0] if sources else None


def _draw_source_switch(panel, layout, state, pane):
    if len(panel.sources(pane)) > 1:
        layout.prop(state, "anim_source" if pane == ANIM else "face_source", text="")


def draw(panel, layout, context, state):
    """The whole cast tab: which pane, then that pane."""
    app_command.draw_progress(layout, state)
    # 页签内的分栏是下拉菜单,不是一排按钮:栏数随宿主能答出的能力走,铺开就把列表挤没了。
    panes = _pane_items(panel)
    if len(panes) > 1:
        layout.prop(state, "pane", text="")
    # A host that cannot answer a pane does not offer it, and a remembered pane it
    # no longer offers draws the one every host has rather than an empty panel.
    pane = state.pane if state.pane in panes else CAST
    if pane == ANIM:
        _draw_pane(panel, layout, context, state, ANIM, _draw_animations)
        return
    if pane == FACE:
        _draw_pane(panel, layout, context, state, FACE, _draw_expressions)
        return
    draw_cast(panel, layout, context, state)


def _pane_items(panel):
    made = [CAST]
    if panel.animations and host_port.supports(host_port.ANIMATION):
        made.append(ANIM)
    if panel.expressions and host_port.supports(host_port.MORPH_TARGETS):
        made.append(FACE)
    return made


def _draw_pane(panel, layout, context, state, pane, body):
    _draw_source_switch(panel, layout, state, pane)
    source = _source(panel, pane, state)
    if source is not None and source.draw is not None:
        source.draw(layout, context)
        return
    body(panel, layout, context, state)


def draw_cast(panel, layout, context, state):
    """The cast list and everything asked of the row it has selected."""
    app_view.draw_head(panel.bound, layout, state, panel.refresh)
    app_view.draw_list(panel.bound, layout, state, panel.columns, panel.identifier,
                       rows=panel.rows, group_column=panel.group_column)
    if state.status:
        layout.label(text=state.status, icon="INFO")

    picked = panel.bound.picked(state)
    options = layout.column(align=True)
    options.enabled = picked is not None
    if panel.options is not None:
        panel.options(options, context, state)
    # 与浏览器同一份导入选项 —— 每个游戏的导入走的都是宿主那一个入口。
    from . import browser as app_browser
    app_browser.draw_import_options(options, context)
    for action in panel.actions:
        options.operator(action)

    # 三个共通按钮问的是同一件事——这一行是由哪些档案做的——所以它们在一起,
    # 并且都只在有选中行时可用。
    asked = layout.column(align=True)
    asked.enabled = picked is not None
    asked.prop(state, "shader_output")
    asked.operator(SHADERS.id, icon="NODE_MATERIAL").panel = panel.bound.key
    if panel.asks_the_engine(ANIM) and host_port.supports(host_port.ANIMATION):
        asked.operator(ANIMATIONS.id, icon="ANIM_DATA").panel = panel.bound.key
    if panel.asks_the_engine(FACE) and host_port.supports(host_port.MORPH_TARGETS):
        asked.operator(EXPRESSIONS.id, icon="SHAPEKEY_DATA").panel = panel.bound.key


#: Both shared lists read the same two roles, so they are described once: what the
#: build calls this row, and whatever the build says about it, hard right.
def _shared_columns(bound):
    return (bound.column("", width=0.68),
            bound.column("detail", align=app_layout.RIGHT, enabled=False))


def _draw_animations(panel, layout, context, state):
    bound = anim_bound(panel.bound)
    app_view.draw_head(bound, layout, state, ANIMATIONS.id,
                       arguments={"panel": panel.bound.key})
    app_view.draw_list(bound, layout, state, _shared_columns(bound),
                       panel.identifier + "_anim", rows=panel.rows)
    if state.status:
        layout.label(text=state.status, icon="INFO")
    actions = layout.column(align=True)
    actions.enabled = bound.picked(state) is not None
    actions.operator(LOAD_CLIP.id, icon="ANIM_DATA").panel = panel.bound.key


def _draw_expressions(panel, layout, context, state):
    bound = face_bound(panel.bound)
    app_view.draw_head(bound, layout, state, EXPRESSIONS.id,
                       arguments={"panel": panel.bound.key})
    app_view.draw_list(bound, layout, state, _shared_columns(bound),
                       panel.identifier + "_face", rows=panel.rows)
    if state.status:
        layout.label(text=state.status, icon="INFO")
    actions = layout.column(align=True)
    actions.enabled = bound.picked(state) is not None
    actions.prop(state, "face_value")
    row = actions.row(align=True)
    row.operator(DRIVE_FACE.id, icon="SHAPEKEY_DATA").panel = panel.bound.key
    cleared = layout.column(align=True)
    cleared.enabled = bound.count > 0
    cleared.operator(CLEAR_FACE.id, icon="X").panel = panel.bound.key

