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
from ...Kernel.app import command
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas
from ...Kernel.app import state as app_state
from ...Kernel.app.state import Field, Schema
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.sims4 import direct
from . import read

STATE = "ruri_sims4_scene"

#: The dataset rows as last read. Module state like the browser's own caches: a draw never
#: crosses the CLR boundary, a command fills this and the rows redraw.
_ROWS = []


def state_of(context):
    return host_port.current().panel_state(context, STATE)


LOT = Schema("Sims4Lot", """One saved lot, as the decoder states it.""", (
    Field("name", app_state.STRING, ""),
    Field("key", app_state.STRING, ""),
    Field("creator", app_state.STRING, ""),
    Field("objects", app_state.STRING, ""),
    Field("unresolved", app_state.STRING, ""),
))

SCENE = Schema("Sims4Scene", """The Scene tab's own state: the cut the list is drawn with,
and the rows it drew.""", (
    Field("filter", app_state.STRING, "", "Filter",
          "Keep the lots whose name, creator or key contains this",
          update="on_filter", live=True),
    Field("entries", app_state.COLLECTION, element=LOT),
    Field("active", app_state.INT, 0),
    Field("status", app_state.STRING, ""),
), include=(schemas.LOADING_STATE,))


def _on_filter(state, context):
    rebuild(state)


HANDLERS = app_state.Handlers("Sims4.scene", on_filter=_on_filter)


def _matches(row, state):
    needle = state.filter.strip().lower()
    if not needle:
        return True
    return any(needle in str(row.get(field, "")).lower()
               for field in ("name", "creator", "key"))


def rebuild(state):
    state.entries.clear()
    for row in _ROWS:
        if not _matches(row, state):
            continue
        entry = state.entries.add()
        entry.name = row.get("name", "")
        entry.key = row.get("key", "")
        entry.creator = row.get("creator", "")
        entry.objects = str(row.get("objects", ""))
        entry.unresolved = str(row.get("unresolved", ""))
    state.active = min(state.active, max(len(state.entries) - 1, 0))
    state.status = "{0} of {1} lot(s)".format(len(state.entries), len(_ROWS))


def selected(state):
    if 0 <= state.active < len(state.entries):
        return state.entries[state.active]
    return None


def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_selection(context):
    return _loaded(context) and selected(state_of(context)) is not None


def _refresh(context, arguments):
    """Read every saved lot this setup carries off the decoder."""
    state = state_of(context)
    try:
        _ROWS[:] = direct.lots(cabmap_state.BRIDGE)
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    rebuild(state)
    return None


def _import(context, arguments):
    """Read the selected lot and hand it to the host."""
    state = state_of(context)
    entry = selected(state)
    if entry is None:
        return
    browser = app_browser.state_of(context)
    options = app_browser.as_options(browser)
    key = entry.key
    label = entry.name
    missing = entry.unresolved
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
    app_layout.ListColumn("name", width=0.45, icon="HOME"),
    app_layout.ListColumn("creator", width=0.25, enabled=False),
    app_layout.ListColumn("objects", width=0.15, align=app_layout.RIGHT, enabled=False),
    app_layout.ListColumn("unresolved", width=0.15, align=app_layout.RIGHT, enabled=False),
)


def draw(layout, context):
    state = state_of(context)
    command.draw_progress(layout, state)
    head = layout.row(align=True)
    head.operator(REFRESH.id, icon="FILE_REFRESH")
    head.label(text=state.status)
    if not _ROWS:
        layout.label(text="List the lots to pick one.", icon="INFO")
        return
    layout.prop(state, "filter", text="", icon="VIEWZOOM")
    layout.list(state, "entries", "active", _COLUMNS, rows=12, identifier="sims4_lots")
    app_browser.draw_import_options(layout, context)
    layout.operator(IMPORT.id)


def register():
    host_port.current().register_state(STATE, SCENE, HANDLERS)


def unregister():
    host_port.current().unregister_state(STATE)
    _ROWS[:] = []
