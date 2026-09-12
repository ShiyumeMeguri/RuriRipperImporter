"""Every object this setup can place, listed off the decoder's own dataset.

``sims4.props`` states one row per object definition: the key it is addressed by, the name
its author gave it and the model it draws with. That is the whole of what a Prop tab needs
-- this game names nothing by path, so the key IS the address, and the same key is what the
importer is handed.

Importing one is the host's own import of a placement statement -- one import path, so a
fix there is a fix here.

Nothing here imports a host.
"""

from __future__ import annotations

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import command
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas
from ...Kernel.app import state as app_state
from ...Kernel.app.state import Field, Schema
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.sims4 import direct
from . import read

STATE = "ruri_sims4_props"

#: The dataset rows as last read. Module state like the browser's own caches: a draw never
#: crosses the CLR boundary, a command fills this and the rows redraw.
_ROWS = []


def state_of(context):
    return host_port.current().panel_state(context, STATE)


PROP = Schema("Sims4Prop", """One listed object, as the decoder states it.""", (
    Field("name", app_state.STRING, ""),
    Field("key", app_state.STRING, ""),
    Field("source", app_state.STRING, ""),
))

PROPS = Schema("Sims4Props", """The Prop tab's own state: the cut the list is drawn with,
and the rows it drew.""", (
    Field("filter", app_state.STRING, "", "Filter",
          "Keep the objects whose name, key or package contains this",
          update="on_filter", live=True),
    Field("entries", app_state.COLLECTION, element=PROP),
    Field("active", app_state.INT, 0),
    Field("status", app_state.STRING, ""),
), include=(schemas.LOADING_STATE,))


def _on_filter(state, context):
    rebuild(state)


HANDLERS = app_state.Handlers("Sims4.props", on_filter=_on_filter)


def _matches(row, state):
    needle = state.filter.strip().lower()
    if not needle:
        return True
    return any(needle in str(row.get(field, "")).lower()
               for field in ("name", "key", "source"))


def rebuild(state):
    state.entries.clear()
    for row in _ROWS:
        if not _matches(row, state):
            continue
        entry = state.entries.add()
        entry.name = row.get("name", "")
        entry.key = row.get("key", "")
        entry.source = row.get("source", "")
    state.active = min(state.active, max(len(state.entries) - 1, 0))
    state.status = "{0} of {1} object(s)".format(len(state.entries), len(_ROWS))


def selected(state):
    if 0 <= state.active < len(state.entries):
        return state.entries[state.active]
    return None


def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_selection(context):
    return _loaded(context) and selected(state_of(context)) is not None


def _refresh(context, arguments):
    """Read every object the setup can place off the decoder."""
    state = state_of(context)
    try:
        _ROWS[:] = direct.props(cabmap_state.BRIDGE)
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    rebuild(state)
    return None


def _import(context, arguments):
    """Read the selected object and hand it to the host."""
    state = state_of(context)
    entry = selected(state)
    if entry is None:
        return
    browser = app_browser.state_of(context)
    options = app_browser.as_options(browser)
    key = entry.key
    label = entry.name
    stated = yield command.Read(lambda: read.package(key, label, options), 0.7)
    if stated is None:
        state.status = "'{0}' places nothing this setup carries.".format(label)
        return
    yield command.Mark(0.8)
    built = host_port.current().import_packages(context, stated, options)
    state.status = "{0}: {1} placement(s). {2}".format(
        label, built.imported, "  ".join(built.warnings[:2]))


REFRESH = command.COMMANDS.define(
    "ruri.sims4_props_refresh", "List Objects", _refresh,
    description="Read every object this setup can place off the decoder",
    icon="FILE_REFRESH", internal=True, poll=_loaded)
IMPORT = command.COMMANDS.define(
    "ruri.sims4_prop_import", "Import Object", _import,
    description="Import this object whole, exactly as the browser would",
    icon="IMPORT", poll=_has_selection, steps=True, status_state=STATE,
    failure="Sims 4 object import failed")


_COLUMNS = (
    app_layout.ListColumn("name", width=0.5, icon="OBJECT_DATA"),
    app_layout.ListColumn("source", width=0.5, enabled=False),
)


def draw(layout, context):
    state = state_of(context)
    command.draw_progress(layout, state)
    head = layout.row(align=True)
    head.operator(REFRESH.id, icon="FILE_REFRESH")
    head.label(text=state.status)
    if not _ROWS:
        layout.label(text="List the objects to pick one.", icon="INFO")
        return
    layout.prop(state, "filter", text="", icon="VIEWZOOM")
    layout.list(state, "entries", "active", _COLUMNS, rows=12, identifier="sims4_props")
    app_browser.draw_import_options(layout, context)
    layout.operator(IMPORT.id)


def register():
    host_port.current().register_state(STATE, PROPS, HANDLERS)


def unregister():
    host_port.current().unregister_state(STATE)
    _ROWS[:] = []
