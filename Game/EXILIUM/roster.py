"""Browse the game's cast the way the game itself lists it -- in any host.

Two panes over one dataset: ``Characters`` are the units the game lets you field,
named through whichever text package the HOST's own locale reads (so switching the
application's language switches the roster with no reload of anything else);
``Models`` is every model the config declares -- a character's outfits, the
enemies, the summons -- each already resolved to the address the catalog knows it
by.

The list behaves like the bundle browser next door: type to filter, click to
select, Load to bring it in, and one button reveals where the selection lives over
in that browser. Loading is deliberately not its own importer: it resolves the
row's own address to the CABs the loaded map holds, puts them in the browser's own
selection and runs the browser's own import, so a fix there is a fix here.

Nothing here imports a host.
"""

from __future__ import annotations

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import cast_panel
from ...Kernel.app import command, filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...Kernel.app import view as app_view
from ...RuriRipperPyBridge.session import cabmap_state
from . import datasets, mesh_resolver

STATE = "ruri_exilium_roster"
SPEC_KEY = "EXILIUM:character"

CHARACTERS = datasets.CHARACTERS
MODELS = datasets.MODELS

#: Loaded row tables, by (kind, language). Module scope, not panel state:
#: rebuilding the drawn list must not cost a re-read, and a column table is not
#: something a host's property system can hold anyway.
_ROWS = {}


def state_of(context):
    return host_port.current().panel_state(context, STATE)


# ---------------------------------------------------------------------------
# What the panel remembers
# ---------------------------------------------------------------------------
#: This tab's live view and the seats that draw it. The kind is not a facet:
#: this game keeps its two casts in two tables, so the switch picks the TABLE
#: and the view narrows nothing. Which column is the name, the id, the role or
#: the "downloaded" test is each column's own statement, made in the hook.
BOUND = app_view.Bound(SPEC_KEY)

ROSTER = Schema("ExiliumRoster", """The cast browser's whole state.""", (
    Field("facet", app_state.ENUM, None, "Kind",
          "Which of the game's own kinds to list", items="facet_items",
          update="on_filter_edit"),
    Field("search", app_state.STRING, "", "Filter",
          "Filter by displayed name, id or group",
          update="on_filter_edit", live=True),
    Field("rows", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("active_index", app_state.INT, 0),
    Field("status", app_state.STRING, "Load a cabmap, then refresh the roster."),
    Field("language", app_state.STRING, ""),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE, cast_panel.CAST_STATE))


def _on_filter_edit(state, context):
    rebuild(state)


def _facet_items(state, context):
    return BOUND.facet_choices(state, context)


HANDLERS = app_state.Handlers(
    "EXILIUM.roster", base=filtering.HANDLERS,
    facet_items=_facet_items,
    on_filter_edit=_on_filter_edit)


FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=SPEC_KEY, fields=BOUND.fields,
    state_for=state_of,
    apply=lambda context: rebuild(state_of(context))))


# ---------------------------------------------------------------------------
# The rows
# ---------------------------------------------------------------------------
def language(state):
    return datasets.language_for_locale(host_port.current().locale())


def rows(state):
    return _ROWS.get(language(state))


def rebuild(state):
    """Rebuild the drawn line list.

    The filter is NOT evaluated here: the search text and the Include/Exclude rules
    go to the same C# engine the bundle browser searches with, over the very buffers
    this table was built from. This side receives row ids and reads cells."""
    with filtering.rebuilding():
        BOUND.open(rows(state), state, note=language(state))


# ---------------------------------------------------------------------------
# What the buttons do
# ---------------------------------------------------------------------------
def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_selection(context):
    return _loaded(context) and BOUND.picked(state_of(context)) is not None


def _refresh(context, arguments):
    """Read the cast out of the game's own config tables."""
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


def _asset_name(address):
    """The asset an address names, by the game's own leaf. This game builds every
    asset under its GUID, so the exported file is named after the asset itself and
    the leaf of the address is the one thing both sides agree on."""
    leaf = str(address).replace("\\", "/").rsplit("/", 1)[-1]
    return leaf.rsplit(".", 1)[0]


def load_address(context, address, label):
    """Put whatever one address resolves to in the browser's own selection and run
    its own import, as steps.

    Shared by both tabs, because "load this one thing" is the same act whether the
    thing is a model or a scene."""
    state = state_of(context)
    if not address:
        state.status = "'{0}' has no address in the game's own catalog.".format(label)
        return
    found = yield command.Read(lambda: datasets.cabs_for([address]), 0.2)
    cabs = [row["cab"] for row in found if row["cab"]]
    if not cabs:
        known = any(row["container"] for row in found)
        state.status = (
            "'{0}' is in the catalog but this install carries no archive for it -- "
            "download it in the game first.".format(label) if known else
            "'{0}' is not in this install's catalog.".format(label))
        return
    # A character's renderers carry no mesh of their own, and the meshes its list
    # names live in other archives -- seed those too or the closure has nothing for
    # the resolver to find (see mesh_resolver).
    name = _asset_name(address)
    seeds = (mesh_resolver.seeds_for(address, cabs, name)
             if address.lower().endswith(".prefab") else cabs)
    cabmap_state.clear_selection()
    for cab in seeds:
        cabmap_state.SELECTED_CABS.add(cab)
    # This game pools dozens of unrelated archives into one file, so one cab's
    # resolved closure exports over a thousand roots that have nothing to do with
    # what was asked for -- loading one character used to bring in a scene's worth
    # of strangers. The address named exactly one asset, so name it to the import.
    for step in app_browser.IMPORT_SELECTED.run(
            context, {"reset_scene": False, "only_root_names": name}):
        yield step
    state.status = "Loaded '{0}' from {1} cab(s).".format(label, len(cabs))


def _load(context, arguments):
    entry = BOUND.picked(state_of(context))
    if entry is None:
        return
    for step in load_address(context, entry.address, entry.label):
        yield step


def reveal_address(context, address, fallback):
    """Reveal what one address resolves to. The query is the asset the GAME's own
    catalog named, never a path this add-on invented."""
    reveal = command.COMMANDS.get("ruri.cabmap_reveal")
    for row in (datasets.cabs_for([address]) if address else []):
        if row["cab"]:
            return reveal.run(context, {"query": row["container"], "cab": row["cab"],
                                        "folder": ""})
    return reveal.run(context, {"query": fallback, "cab": "", "folder": ""})


def _reveal(context, arguments):
    entry = BOUND.picked(state_of(context))
    if entry is None:
        return {"CANCELLED"}
    return reveal_address(context, entry.address, entry.key)


def _outfits(context, arguments):
    """List the selected character's own models, in the Models pane."""
    state = state_of(context)
    entry = BOUND.picked(state)
    if entry is None:
        return {"CANCELLED"}
    wanted = entry.key
    state.facet = MODELS
    if rows(state) is None:
        _refresh(context, {})
    state.filter_rules.clear()
    rule = state.filter_rules.add()
    # A rule offers the fields of the list it belongs to, and it learns which list
    # that is from its own spec_key -- stamp it before naming a field, or the enum
    # still holds the empty fallback vocabulary and the assignment raises.
    rule.spec_key = SPEC_KEY
    rule.field = "character"
    rule.relation = "is"
    rule.value = wanted
    rule.action = "include"
    rule.enabled = True
    rebuild(state)
    return None


def _outfits_poll(context):
    return _has_selection(context) and state_of(context).kind == CHARACTERS


REFRESH = command.COMMANDS.define(
    "ruri.exilium_roster_refresh", "Refresh Roster", _refresh,
    description="Read the cast out of the game's own config tables",
    icon="FILE_REFRESH", poll=_loaded)
LOAD = command.COMMANDS.define(
    "ruri.exilium_roster_load", "Load Model", _load,
    description="Import this one's model, exactly as the bundle browser would",
    icon="IMPORT", poll=_has_selection, steps=True, status_state=STATE,
    failure="Loading this one's model failed")
REVEAL = command.COMMANDS.define(
    "ruri.exilium_roster_reveal", "Open Containing Folder", _reveal,
    description="Switch to the bundle browser and open where this one's assets live",
    icon="FILE_FOLDER", poll=_has_selection)
OUTFITS = command.COMMANDS.define(
    "ruri.exilium_roster_outfits", "Show Outfits", _outfits,
    description="Switch to the Models pane and list only the models this one wears",
    icon="MOD_CLOTH", poll=_outfits_poll)


# ---------------------------------------------------------------------------
# What it looks like
# ---------------------------------------------------------------------------
def _id_cell(seat):
    """The id, unless the name already IS the id."""
    key = BOUND.cell(seat, "key")
    return "" if key == BOUND.cell(seat) else "({0})".format(key)


#: A cast row: the name, the id when the game gives it one of its own, and
#: whatever detail that projection carries. A row the install never downloaded is
#: dimmed rather than hidden while the filter says to show it -- it is real data
#: with nothing behind it here.
_COLUMNS = (
    BOUND.column("", width=0.55,
                 icon=lambda seat: ("OUTLINER_OB_ARMATURE" if BOUND.shipped(seat)
                                    else "LIBRARY_DATA_BROKEN"),
                 active=BOUND.shipped),
    app_layout.ListColumn(_id_cell, width=0.5, enabled=False),
    BOUND.column("detail", align=app_layout.RIGHT, enabled=False),
)
_GROUP_COLUMN = BOUND.column("", icon="OUTLINER_COLLECTION")



def _seeds(_context, state):
    """What the picked one IS, as archive names. This title addresses everything by
    a catalog ADDRESS, and which archives one address lives in is the hook's own
    join -- the same one Load walks."""
    entry = BOUND.picked(state)
    if entry is None or not entry.payload:
        return []
    return [row["cab"] for row in datasets.cabs_for([entry.payload]) if row["cab"]]


def draw(layout, context):
    state = state_of(context)

    command.draw_progress(layout, state)
    app_view.draw_head(BOUND, layout, state, REFRESH.id)
    app_view.draw_list(BOUND, layout, state, _COLUMNS, "exilium_roster",
                       group_column=_GROUP_COLUMN)

    entry = BOUND.picked(state)
    options = layout.column(align=True)
    # 与浏览器同一份导入选项 —— Load 走的本来就是浏览器自己的导入。
    app_browser.draw_import_options(options, context)
    actions = options.column(align=True)
    actions.enabled = entry is not None
    actions.operator(LOAD.id)
    shaders = layout.column(align=True)
    shaders.enabled = entry is not None
    shaders.prop(state, "shader_output")
    shaders.operator(cast_panel.SHADERS.id, icon="NODE_MATERIAL").panel = BOUND.key
    actions.operator(REVEAL.id)
    if entry is not None and entry.cell("kind") == CHARACTERS:
        actions.operator(OUTFITS.id)


def register():
    host_port.current().register_state(
        STATE, ROSTER, HANDLERS, extra={"FILTER_SPEC_KEY": SPEC_KEY})


def unregister():
    host_port.current().unregister_state(STATE)
    _ROWS.clear()
