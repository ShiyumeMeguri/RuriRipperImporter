"""Every saved lot this setup carries, listed off the decoder's own dataset.

A lot is a whole house -- its furniture, its lighting, its decoration, every piece the player
placed -- and the decoder states it as ONE placement statement. So importing one is the same
call the Prop tab makes with a different key, and the house arrives assembled rather than as a
pile of parts to line up by hand.

``unresolved`` is the column to read before importing: it counts the placements naming content
this setup does not carry. A house built with an expansion pack, read on a setup without that
pack, says so here instead of quietly arriving with holes in it.

Nothing here imports a host.
"""

from __future__ import annotations

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import command, filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas
from ...Kernel.app import state as app_state
from ...Kernel.app import view as app_view
from ...Kernel.app.state import Field, Schema
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.sims4 import direct
from . import read

STATE = "ruri_sims4_scene"
SPEC_KEY = "Sims4:lots"

#: This tab's live view and the seats that draw it, plus the table it reads. Module
#: state like the browser's own caches: a draw never crosses the CLR boundary, a
#: command asks for a new view and the seats redraw.
BOUND = app_view.Bound(SPEC_KEY)
_TABLE = [None]


def state_of(context):
    return host_port.current().panel_state(context, STATE)


SCENE = Schema("Sims4Scene", """The Scene tab's own state: the cut the list is drawn with,
and the rows it drew.""", (
    Field("search", app_state.STRING, "", "Filter",
          "Keep the lots whose name, creator or key contains this",
          update="on_filter", live=True),
    Field("rows", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("active_index", app_state.INT, 0),
    Field("status", app_state.STRING, ""),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE))


def _on_filter(state, context):
    rebuild(state)


HANDLERS = app_state.Handlers("Sims4.scene", base=filtering.HANDLERS,
                              on_filter=_on_filter)

FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=SPEC_KEY, fields=BOUND.fields,
    state_for=state_of,
    apply=lambda context: rebuild(state_of(context))))


def rebuild(state):
    """Ask the kernel for the list as it is now stated. Matching is NOT done here:
    the search text goes to the same vectorized engine every other list uses, so
    "contains" means one thing in this application rather than one thing per tab."""
    BOUND.open(_TABLE[0], state)


def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_selection(context):
    return _loaded(context) and BOUND.picked(state_of(context)) is not None


def _refresh(context, arguments):
    """Read every saved lot this setup carries off the decoder."""
    state = state_of(context)
    try:
        _TABLE[0] = cabmap_state.BRIDGE.game_data(direct.LOTS)
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    rebuild(state)
    return None


def _import(context, arguments):
    """Read the selected lot and hand it to the host."""
    state = state_of(context)
    entry = BOUND.picked(state)
    if entry is None:
        return
    browser = app_browser.state_of(context)
    options = app_browser.as_options(browser)
    key = entry.key
    label = entry.label
    missing = entry.cell("unresolved")
    stated = yield command.Read(lambda: read.package(key, label, options), 0.7)
    if stated is None:
        state.status = "'{0}' places nothing this setup carries.".format(label)
        return
    yield command.Mark(0.8)
    built = host_port.current().import_packages(context, stated, options)
    note = "" if missing in ("", "0") else "  {0} placement(s) name content this setup lacks.".format(missing)
    state.status = "{0}: {1} object(s).{2}".format(label, built.imported, note)


REFRESH = command.COMMANDS.define(
    "ruri.sims4_scene_refresh", "List Lots", _refresh,
    description="Read every saved lot this setup carries off the decoder",
    icon="FILE_REFRESH", internal=True, poll=_loaded)
IMPORT = command.COMMANDS.define(
    "ruri.sims4_scene_import", "Import Lot", _import,
    description="Import this whole lot, every placement at once",
    icon="IMPORT", poll=_has_selection, steps=True, status_state=STATE,
    failure="Sims 4 lot import failed")


_COLUMNS = (
    BOUND.column("", width=0.45, icon="HOME"),
    BOUND.column("creator", width=0.25, enabled=False),
    BOUND.column("objects", width=0.15, align=app_layout.RIGHT, enabled=False),
    BOUND.column("unresolved", width=0.15, align=app_layout.RIGHT, enabled=False),
)


def draw(layout, context):
    state = state_of(context)
    command.draw_progress(layout, state)
    if state.status:
        layout.label(text=state.status, icon="ERROR")
    if _TABLE[0] is None:
        layout.operator(REFRESH.id, icon="FILE_REFRESH")
        layout.label(text="List the lots to pick one.", icon="INFO")
        return
    app_view.draw_head(BOUND, layout, state, REFRESH.id)
    app_view.draw_list(BOUND, layout, state, _COLUMNS, "sims4_lots", rows=12)
    app_browser.draw_import_options(layout, context)
    layout.operator(IMPORT.id)


def register():
    host_port.current().register_state(STATE, SCENE, HANDLERS,
                                       extra={"FILTER_SPEC_KEY": SPEC_KEY})


def unregister():
    host_port.current().unregister_state(STATE)
    BOUND.close()
    _TABLE[0] = None
