"""Every scene the game ships, under the path its own catalog states.

A scene is the one asset family whose address survives this game's build as a real
path, so the tree drawn here is the game's own folder tree rather than anything
reconstructed. Picking one and pressing Load runs the bundle browser's own import
over the cabs that scene resolved to -- which is why this tab needs nothing of the
host that the browser does not already need.

Nothing here imports a host.
"""

from __future__ import annotations

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import command, filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...RuriRipperPyBridge.session import cabmap_state
from . import datasets, roster

STATE = "ruri_exilium_scene"
SPEC_KEY = "EXILIUM:scene"

#: The loaded scene table. Module scope, not panel state: rebuilding the drawn list
#: must not cost a re-read, and a column table is not something a host's property
#: system can hold anyway.
_TABLE = {}

_FIELD_LABELS = {"key": "Path", "label": "Scene", "group": "Family", "detail": "Folder",
                 "archives": "Archives", "shipped": "Downloaded"}


def state_of(context):
    return host_port.current().panel_state(context, STATE)


SCENE_ENTRY = Schema("ExiliumSceneEntry", """One drawn line: a folder header or a
scene the catalog names.""", (
    Field("label", app_state.STRING, ""),
    Field("key", app_state.STRING, ""),
    Field("group", app_state.STRING, ""),
    Field("detail", app_state.STRING, ""),
    Field("shipped", app_state.BOOL, False),
    Field("is_group", app_state.BOOL, False),
))

SCENE = Schema("ExiliumScene", """The scene browser's whole state.""", (
    Field("search", app_state.STRING, "", "Filter",
          "Filter by scene name, folder or family",
          update="on_filter_edit", live=True),
    Field("entries", app_state.COLLECTION, element=SCENE_ENTRY),
    Field("active_index", app_state.INT, 0),
    Field("status", app_state.STRING, "Load a cabmap, then refresh the scene list."),
    Field("downloaded_only", app_state.BOOL, True, "Downloaded",
          "Hide the scenes the catalog names but this install never downloaded"),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE))


def _on_filter_edit(state, context):
    rebuild(state)


HANDLERS = app_state.Handlers(
    "EXILIUM.scene", base=filtering.HANDLERS, on_filter_edit=_on_filter_edit)


def _filter_fields():
    table = _TABLE.get("scenes")
    if table is None:
        return (("label", "Scene"),)
    names = sorted(table.names, key=lambda name: 0 if name == "label" else 1)
    return tuple((name, _FIELD_LABELS.get(name, name.replace("_", " ").title()))
                 for name in names)


FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=SPEC_KEY, fields=_filter_fields,
    state_for=state_of,
    apply=lambda context: rebuild(state_of(context))))


def rebuild(state):
    with filtering.rebuilding():
        _fill(state)


def _fill(state):
    chosen = filtering.selected_key(state)
    state.entries.clear()
    table = _TABLE.get("scenes")
    if table is None:
        return
    matched = cabmap_state.BRIDGE.search_data_table(
        table, state.search.strip(), state.filter_rules)
    labels = table.values("label")
    groups = table.values("group")
    shipped = table.values("shipped")
    order = sorted((int(index) for index in matched
                    if not state.downloaded_only or shipped[int(index)]),
                   key=lambda index: (groups[index], labels[index]))
    matched_count = len(order)
    found = [{name: table.cell(index, name)
              for name in ("key", "label", "group", "detail", "shipped")}
             for index in order[:cabmap_state.LIST_CAP]]

    counts = {}
    for row in found:
        counts[row["group"]] = counts.get(row["group"], 0) + 1

    current_group = None
    for row in found:
        if row["group"] and row["group"] != current_group:
            current_group = row["group"]
            header = state.entries.add()
            header.label = "{0}  ({1})".format(current_group, counts[current_group])
            header.group = current_group
            header.is_group = True
        entry = state.entries.add()
        entry.label = row["label"]
        entry.key = row["key"]
        entry.group = row["group"]
        entry.detail = row["detail"]
        entry.shipped = bool(float(row["shipped"] or 0))
    state.status = "{0} of {1} scene(s){2}".format(
        matched_count, table.row_count,
        "" if matched_count == len(found) else
        " · showing {0}, narrow your search to see the rest".format(len(found)))
    filtering.restore_selection(state, chosen)


def selected(state):
    if 0 <= state.active_index < len(state.entries):
        entry = state.entries[state.active_index]
        if not entry.is_group:
            return entry
    return None


# ---------------------------------------------------------------------------
# What the buttons do
# ---------------------------------------------------------------------------
def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_selection(context):
    return _loaded(context) and selected(state_of(context)) is not None


def _refresh(context, arguments):
    """Read every scene the game's own catalog names."""
    state = state_of(context)
    try:
        _TABLE["scenes"] = datasets.scenes()
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    rebuild(state)
    return None


def _load(context, arguments):
    """Import the selected scene through the browser's own import -- one import
    path, so a fix there is a fix here."""
    entry = selected(state_of(context))
    if entry is None:
        return
    for step in roster.load_address(context, entry.key, entry.label):
        yield step


def _reveal(context, arguments):
    entry = selected(state_of(context))
    if entry is None:
        return {"CANCELLED"}
    return roster.reveal_address(context, entry.key, entry.label)


REFRESH = command.COMMANDS.define(
    "ruri.exilium_scene_refresh", "Refresh Scenes", _refresh,
    description="Read the scene list out of the game's own catalog",
    icon="FILE_REFRESH", poll=_loaded)
LOAD = command.COMMANDS.define(
    "ruri.exilium_scene_load", "Load Scene", _load,
    description="Import this scene, exactly as the bundle browser would",
    icon="IMPORT", poll=_has_selection, steps=True, status_state=STATE,
    failure="Loading this scene failed")
REVEAL = command.COMMANDS.define(
    "ruri.exilium_scene_reveal", "Open Containing Folder", _reveal,
    description="Switch to the bundle browser and open where this scene lives",
    icon="FILE_FOLDER", poll=_has_selection)


#: A scene row. One the install never downloaded is dimmed rather than hidden --
#: it is real catalog data with nothing behind it here. Filtering already happened
#: against the game's own fields, so no row is hidden at draw time.
_COLUMNS = (
    app_layout.ListColumn("label", width=0.7,
                          icon=lambda row: ("SCENE_DATA" if row.shipped
                                            else "LIBRARY_DATA_BROKEN"),
                          active=lambda row: row.shipped),
    app_layout.ListColumn("detail", align=app_layout.RIGHT, enabled=False),
)
_GROUP_COLUMN = app_layout.ListColumn("label", icon="OUTLINER_COLLECTION")


def draw(layout, context):
    state = state_of(context)

    command.draw_progress(layout, state)
    head = layout.row(align=True)
    head.label(text="Scenes", icon="SCENE_DATA")
    head.operator(REFRESH.id, text="", icon="FILE_REFRESH")

    filtering.draw_search_row(layout, state)
    layout.list(state, "entries", "active_index", _COLUMNS, rows=10,
                identifier="exilium_scenes", group_key="is_group",
                group_column=_GROUP_COLUMN)
    layout.label(text=state.status, icon="INFO")

    options = layout.column(align=True)
    options.prop(state, "downloaded_only", toggle=True, icon="IMPORT")
    # 与浏览器同一份导入选项 —— Load 走的本来就是浏览器自己的导入。
    app_browser.draw_import_options(options, context)
    actions = options.column(align=True)
    actions.enabled = selected(state) is not None
    actions.operator(LOAD.id)
    actions.operator(REVEAL.id)


def register():
    host_port.current().register_state(
        STATE, SCENE, HANDLERS, extra={"FILTER_SPEC_KEY": SPEC_KEY})


def unregister():
    host_port.current().unregister_state(STATE)
    _TABLE.clear()
