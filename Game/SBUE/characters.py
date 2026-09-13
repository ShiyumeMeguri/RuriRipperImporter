"""Every character the install ships, drawn by the ONE list kernel every game uses.

``unreal.characters`` states what the build ships as a cast, and a title whose own
design database files that cast under its own kinds says so on every row. WHICH
column carries the name, the id, the kind and the packages is not restated here:
each column says so itself, where the decoder builds it. So this module holds no
statement about the shape of that table at all -- the facet switch, the search, the
rule editor, the list, the sections and the status line are :mod:`Kernel.app.view`,
and a decoder that grows a kind or renames a column needs no edit anywhere here.

What is left is what is genuinely this engine's: importing a row is the host's own
import of the packages the decoder named for it, and decompiling the shaders its
materials compiled to has no counterpart anywhere else.

Nothing here imports a host.
"""

from __future__ import annotations

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import cast_panel
from ...Kernel.app import command, filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas
from ...Kernel.app import view as app_view
from ...Kernel.app.state import Schema
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unreal import direct
from . import datasets, read

STATE = "ruri_unreal_characters"
SPEC_KEY = "UnrealEngine:characters"

#: This tab's live view, and the seats that draw it. Module state like the
#: browser's own caches: a draw never crosses the CLR boundary -- a command asks
#: for a new view and the seats redraw.
BOUND = app_view.Bound(SPEC_KEY)

#: The cast table as last read. Held only to ask it for views; nothing on this
#: side ever reads a column of it.
_TABLE = [None]


def state_of(context):
    return host_port.current().panel_state(context, STATE)


def table():
    return _TABLE[0]


CHARACTERS = Schema("UnrealCharacters", """This engine's cast tab states nothing
of its own: what a row is, and everything drawn around it, is the shared record.""",
                    (), include=(schemas.FILTER_STATE, schemas.LOADING_STATE,
                                 cast_panel.CAST_STATE))


def rebuild(state):
    """Ask the kernel for this list as it is now stated. Nothing is evaluated on
    this side -- the text, the rules and the facet go over as typed."""
    with filtering.rebuilding():
        BOUND.open(table(), state)


HANDLERS = cast_panel.handlers(BOUND, "SBUE.characters", rebuild)

FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=SPEC_KEY, fields=BOUND.fields,
    state_for=state_of,
    apply=lambda context: rebuild(state_of(context))))


# ---------------------------------------------------------------------------
# What the buttons do
# ---------------------------------------------------------------------------
def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_selection(context):
    return _loaded(context) and BOUND.selected(state_of(context)) >= 0


def _packages(state):
    """The packages the picked row states, split the way the decoder joins its
    lists.

    A character is one row and may be several packages -- a build whose model is a
    body and a weapon says so -- and importing it is ONE thing the user asked for,
    so the whole row goes to the host as one statement."""
    return [package for package in BOUND.payload(state).split(direct.SLOT_SEPARATOR)
            if package]


def _refresh(context, arguments):
    """Read the cast this install ships off the decoder."""
    state = state_of(context)
    try:
        _TABLE[0] = datasets.cast()
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    state.status = ""
    rebuild(state)
    cast_panel.opened(BOUND, state)
    return None


def _import(context, arguments):
    """Read the selected row's packages and hand them to the host."""
    state = state_of(context)
    name = BOUND.value(state)
    if BOUND.selected(state) < 0:
        return
    browser = app_browser.state_of(context)
    blocked = app_browser._blocking_required_options(
        app_browser._ensure_active_config(browser))
    if blocked:
        state.status = blocked
        return
    options = app_browser.as_options(browser)
    packages = _packages(state)
    if not packages:
        state.status = "{0}: this install ships no model for that row.".format(name)
        return
    stated = yield command.Read(
        lambda: read.packages(packages, name, options, key=packages[0]), 0.7)
    if stated is None:
        state.status = "'{0}' places nothing this install carries.".format(packages[0])
        return
    yield command.Mark(0.8)
    built = host_port.current().import_packages(context, stated, options)
    state.status = "{0}: {1} placement(s). {2}".format(
        name, built.imported, "  ".join(built.warnings[:2]))



def _shaders(state, output):
    """This engine ships no shader ASSET: a material's program lives as blobs in an
    archive shared with everything else the build cooked. So it answers the shared
    button itself -- what lands on disk is the vertex and pixel stages as source,
    one file per variant."""
    packages = _packages(state)
    return datasets.shaders(packages, output) if packages else []


def _reveal(context, arguments):
    state = state_of(context)
    if BOUND.selected(state) < 0:
        return {"CANCELLED"}
    packages = _packages(state)
    return command.COMMANDS.get("ruri.cabmap_reveal").run(
        context, {"cab": packages[0] if packages else "",
                  "query": BOUND.value(state), "folder": ""})


REFRESH = command.COMMANDS.define(
    "ruri.unreal_characters_refresh", "List Characters", _refresh,
    description="Read the cast this install ships off the decoder",
    icon="FILE_REFRESH", internal=True, poll=_loaded)
IMPORT = command.COMMANDS.define(
    "ruri.unreal_character_import", "Load Model", _import,
    description="Import this row whole, exactly as the browser would",
    icon="IMPORT", poll=_has_selection, steps=True, status_state=STATE,
    failure="Unreal character import failed")
REVEAL = command.COMMANDS.define(
    "ruri.unreal_character_reveal", "Open Containing Folder", _reveal,
    description="Show the selected row's package in the file browser",
    icon="FILE_FOLDER", internal=True, poll=_has_selection)


#: Name, then the build's own id, then the build's own finer kind hard right --
#: which is what tells several rows sharing a display name apart. Each cell names
#: the ROLE it reads, never a column: one decoder answers for every Unreal build
#: and they do not file a cast under the same column names. Naming them outright
#: meant the list could not be drawn at all for a build without those columns.
_COLUMNS = (
    BOUND.column("", label="Name", width=0.4, icon="OUTLINER_OB_ARMATURE"),
    BOUND.role_column(app_view.KEY, label="Id", width=0.45, align=app_layout.RIGHT, enabled=False),
    BOUND.role_column(app_view.DETAIL, label="Type", align=app_layout.RIGHT, enabled=False),
)



PANEL = cast_panel.Panel(
    BOUND, _COLUMNS, "unreal_characters", REFRESH.id, state_of, STATE,
    seeds=lambda _context, state: _packages(state),
    actions=(IMPORT.id, REVEAL.id), shaders=_shaders,
    # 这套引擎把表情记在网格自己的 morph 列表里,不是 Unity 的混合形状 —— 问的是同一个
    # 问题,所以画在同一格里,只是换成这个解码器说它的那份数据集。
    face_dataset=("unreal.morphtargets", "packages"),
)


def draw(layout, context):
    cast_panel.draw(PANEL, layout, context, state_of(context))


def register():
    host_port.current().register_state(
        STATE, CHARACTERS, HANDLERS, extra={"FILTER_SPEC_KEY": SPEC_KEY})


def unregister():
    host_port.current().unregister_state(STATE)
    cast_panel.forget(BOUND)
    BOUND.close()
    _TABLE[0] = None
