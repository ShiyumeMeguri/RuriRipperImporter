"""ONE cast panel, for every game that lets you pick somebody and bring them in.

Every game's cast tab is the same shape and has always been drawn four times: a
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
and every shared button is a question asked of exactly that.

Nothing here imports a host.
"""

from __future__ import annotations

from . import command as app_command
from . import filtering
from . import state as app_state
from . import view as app_view
from .state import Field, Schema
from ...RuriRipperPyBridge.session import cabmap_state

#: What a cast panel remembers beyond its list. Included by each game's own schema,
#: so the shared buttons below have somewhere to put their answer without every
#: game restating the field.
CAST_STATE = Schema("CastState", """The cast panel's shared state.""", (
    Field("shader_output", app_state.STRING, "", "Shader Folder",
          "Where Decompile Shaders writes the source it reads out of this row",
          subtype=app_state.DIRECTORY),
))

#: Panel key -> Panel. A shared button is one command for every game, so it is told
#: WHICH panel pressed it rather than guessing from whatever tab is on screen.
PANELS = {}


class Panel:
    """One game's cast tab: the shared shape, and the slots this game fills."""

    __slots__ = ("bound", "columns", "identifier", "refresh", "group_column", "rows",
                 "options", "actions", "seeds", "shaders", "state_for", "state_name")

    def __init__(self, bound, columns, identifier, refresh, state_for, state_name,
                 seeds=None, group_column=None, rows=10, options=None, actions=(),
                 shaders=None):
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
        PANELS[bound.key] = self

    def picked(self, context):
        return self.bound.picked(self.state_for(context))

    def seeds_of(self, context, state):
        return list(self.seeds(context, state)) if self.seeds is not None else []


def _panel(arguments):
    return PANELS.get(arguments.get("panel", ""))


def _can_read_shaders(context):
    """A row has to be pickable and made of something before its shaders are a
    question. Which panel is asking is whichever one is on screen -- a poll has no
    arguments, so it answers for every registered panel and lets the button's own
    panel argument decide when it is actually pressed."""
    if cabmap_state.BRIDGE is None:
        return False
    return any(panel.picked(context) is not None for panel in PANELS.values())


def _read_shaders(context, arguments):
    """Write out every shader the picked row reaches.

    A Unity title answers through the one shared reader -- the closure of what this
    row is made of, every Shader asset in it, decompiled to source. A title whose
    engine ships no shader ASSET answers for itself (``shaders``)."""
    panel = _panel(arguments)
    if panel is None:
        return {"CANCELLED"}
    state = panel.state_for(context)
    entry = panel.bound.picked(state)
    if entry is None:
        return {"CANCELLED"}
    from ..host import current
    output = current().absolute_path(state.shader_output) if state.shader_output else ""
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


SHADERS = app_command.COMMANDS.define(
    "ruri.cast_shaders", "Decompile Shaders", _read_shaders,
    description="Write out every shader the selected row reaches, as source",
    icon="NODE_MATERIAL", poll=_can_read_shaders, steps=True,
    failure="Reading this one's shaders failed",
    status_state=lambda arguments: getattr(_panel(arguments), "state_name", ""),
    arguments=(app_state.Field("panel", app_state.STRING, ""),))


def draw(panel, layout, context, state):
    """The whole cast tab, in the order every one of them has always drawn it."""
    app_command.draw_progress(layout, state)
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

    shaders = layout.column(align=True)
    shaders.enabled = picked is not None
    shaders.prop(state, "shader_output")
    shaders.operator(SHADERS.id, icon="NODE_MATERIAL").panel = panel.bound.key
