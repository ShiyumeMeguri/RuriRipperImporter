"""Every character the install ships, drawn by the ONE cast browser every game uses.

``unreal.characters`` states what the build ships as a cast, and a title whose own
design database files that cast under its own kinds says so on every row. Which
columns carry the name, the id, the kind and the packages to import is all this
module states (:data:`CAST`); the switch, the search, the rule editor, the list and
the truncation all come from :mod:`Kernel.app.cast`, so this tab reads and performs
exactly like every other game's.

What is left here is what is genuinely this engine's: importing a row is the host's
own import of the packages the decoder named for it, and decompiling the shaders its
materials compiled to has no counterpart anywhere else.

Nothing here imports a host.
"""

from __future__ import annotations

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import cast as app_cast
from ...Kernel.app import command, filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unreal import direct
from . import datasets, read

STATE = "ruri_unreal_characters"
SPEC_KEY = "UnrealEngine:characters"

#: WHICH COLUMN ANSWERS WHAT, for the one cast browser. Everything this tab knows
#: about the decoder's rows is here: a decoder that renames a column changes this
#: line and nothing else, and one that adds a kind needs no edit at all.
CAST = app_cast.Cast(SPEC_KEY, label="name", identifier="id", detail="type",
                     kind="kind", payload="package")

#: The cast table as last read. Module state like the browser's own caches: a draw
#: never crosses the CLR boundary, a command fills this and the rows redraw.
_TABLE = [None]


def state_of(context):
    return host_port.current().panel_state(context, STATE)


def table():
    return _TABLE[0]


CHARACTERS = Schema("UnrealCharacters", """The Characters tab's own state: the cut the
list is drawn with, and the rows it drew.""", (
    Field("kind", app_state.ENUM, None, "Kind",
          "Which of the kinds this install files its cast under to list",
          items="kind_items", update="on_filter_edit"),
    Field("search", app_state.STRING, "", "Filter",
          "Filter by name, id, kind or type", update="on_filter_edit", live=True),
    Field("shader_output", app_state.STRING, "", "Shader Folder",
          "Where Decompile Shaders writes this character's shader source",
          subtype=app_state.DIRECTORY),
    Field("entries", app_state.COLLECTION, element=app_cast.CAST_ENTRY),
    Field("active_index", app_state.INT, 0),
    Field("status", app_state.STRING,
          "Refresh to read the cast out of this install's own tables."),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE))


def _on_filter_edit(state, context):
    rebuild(state)


def _kind_items(state, context):
    return CAST.kind_items(table())


HANDLERS = app_state.Handlers("SBUE.characters", base=filtering.HANDLERS,
                              on_filter_edit=_on_filter_edit,
                              kind_items=_kind_items)


def rebuild(state):
    with filtering.rebuilding():
        app_cast.fill(state, CAST, table())


def selected(state):
    return app_cast.selected(state)


FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=SPEC_KEY, fields=lambda: CAST.fields(table()),
    state_for=state_of,
    apply=lambda context: rebuild(state_of(context))))


# ---------------------------------------------------------------------------
# What the buttons do
# ---------------------------------------------------------------------------
def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_selection(context):
    return _loaded(context) and selected(state_of(context)) is not None


def _packages(entry):
    """The packages one row states, split the way the decoder joins its lists.

    A character is one row and may be several packages -- a build whose model is a
    body and a weapon says so -- and importing it is ONE thing the user asked for,
    so the whole row goes to the host as one statement."""
    return [package for package in entry.payload.split(direct.SLOT_SEPARATOR) if package]


def _refresh(context, arguments):
    """Read the cast this install ships off the decoder."""
    state = state_of(context)
    try:
        _TABLE[0] = datasets.cast()
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    rebuild(state)
    return None


def _import(context, arguments):
    """Read the selected row's packages and hand them to the host."""
    state = state_of(context)
    entry = selected(state)
    if entry is None:
        return
    browser = app_browser.state_of(context)
    blocked = app_browser._blocking_required_options(
        app_browser._ensure_active_config(browser))
    if blocked:
        state.status = blocked
        return
    options = app_browser.as_options(browser)
    packages = _packages(entry)
    if not packages:
        state.status = "{0}: this install ships no model for that row.".format(entry.label)
        return
    stated = yield command.Read(
        lambda: read.packages(packages, entry.label, options, key=packages[0]), 0.7)
    if stated is None:
        state.status = "'{0}' places nothing this install carries.".format(packages[0])
        return
    yield command.Mark(0.8)
    built = host_port.current().import_packages(context, stated, options)
    state.status = "{0}: {1} placement(s). {2}".format(
        entry.label, built.imported, "  ".join(built.warnings[:2]))


def _shaders(context, arguments):
    """Decompile every shader variant this row's materials compiled to.

    Unreal ships no shader asset: a material's program lives as blobs in an archive
    shared with everything else the build cooked. What comes out is the vertex and
    pixel stages as source, one file per variant, under the stated folder."""
    state = state_of(context)
    entry = selected(state)
    if entry is None:
        return
    output = host_port.current().absolute_path(state.shader_output) if state.shader_output else ""
    if not output:
        state.status = "State a Shader Folder first."
        return
    packages = _packages(entry)
    if not packages:
        state.status = "{0}: this install ships no model for that row.".format(entry.label)
        return
    rows = yield command.Read(lambda: datasets.shaders(packages[0], output), 0.9)
    if not rows:
        state.status = "{0}: no archive carries a shader map for its materials.".format(entry.label)
        return
    state.status = "{0}: {1} archive(s) -> {2}".format(
        entry.label, len(rows), rows[0].get("output", output))


def _reveal(context, arguments):
    entry = selected(state_of(context))
    if entry is None:
        return {"CANCELLED"}
    packages = _packages(entry)
    return command.COMMANDS.get("ruri.cabmap_reveal").run(
        context, {"cab": packages[0] if packages else "", "query": entry.label, "folder": ""})


REFRESH = command.COMMANDS.define(
    "ruri.unreal_characters_refresh", "List Characters", _refresh,
    description="Read the cast this install ships off the decoder",
    icon="FILE_REFRESH", internal=True, poll=_loaded)
IMPORT = command.COMMANDS.define(
    "ruri.unreal_character_import", "Import Character", _import,
    description="Import this row whole, exactly as the browser would",
    icon="IMPORT", poll=_has_selection, steps=True, status_state=STATE,
    failure="Unreal character import failed")
SHADERS = command.COMMANDS.define(
    "ruri.unreal_character_shaders", "Decompile Shaders", _shaders,
    description="Decompile every shader variant this row's materials compiled to",
    icon="NODE_MATERIAL", poll=_has_selection, steps=True, status_state=STATE,
    failure="Unreal shader decompile failed")
REVEAL = command.COMMANDS.define(
    "ruri.unreal_character_reveal", "Reveal", _reveal,
    description="Show the selected row's package in the file browser",
    icon="FILE_FOLDER", internal=True, poll=_has_selection)


#: Name, then the build's own id, then the build's own finer kind hard right --
#: which is what tells several rows sharing a display name apart.
_COLUMNS = (
    app_layout.ListColumn("label", "Name", width=0.4, icon="OUTLINER_OB_ARMATURE"),
    app_layout.ListColumn("key", "Id", width=0.45, align=app_layout.RIGHT, enabled=False),
    app_layout.ListColumn("detail", "Type", align=app_layout.RIGHT, enabled=False),
)


def draw(layout, context):
    state = state_of(context)
    command.draw_progress(layout, state)
    app_cast.draw_head(layout, state, CAST, table(), REFRESH.id)
    app_cast.draw_list(layout, state, _COLUMNS, "unreal_characters")
    entry = selected(state)
    options = layout.column(align=True)
    options.enabled = entry is not None
    # 与浏览器同一份导入选项 —— 这里走的也是宿主那一个导入入口。
    app_browser.draw_import_options(options, context)
    options.operator(IMPORT.id, icon="IMPORT")
    options.operator(REVEAL.id, icon="FILE_FOLDER")
    layout.prop(state, "shader_output")
    layout.operator(SHADERS.id, icon="NODE_MATERIAL")


def register():
    host_port.current().register_state(
        STATE, CHARACTERS, HANDLERS, extra={"FILTER_SPEC_KEY": SPEC_KEY})


def unregister():
    host_port.current().unregister_state(STATE)
    _TABLE[0] = None
