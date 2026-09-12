"""Every character the install ships, listed off the decoder's own dataset.

``unreal.characters`` states what the build ships as a cast. The family answer is the
packages holding a skeletal mesh under the content roots the title says its cast lives
on, read off the cabmap -- so it answers for a build that publishes no reflection
schema, which an actor listing cannot. A title whose own design database names its
cast REPLACES that dataset with its own, and then the rows carry the name the game
shows a player, in the language the host reads in. Either way this panel draws the
same columns and never knows which it got.

A build that files its cast under its own kinds says so on every row, and the switch
above the list is built FROM those rows: one entry per kind the decoder actually
returned, in the decoder's own words. Nothing here decides what a monster is, and a
build that adds a kind shows it with no edit -- a build that states none has one
switch entry and the list reads as it always did.

Importing one is the host's own import of that package -- one import path, so a fix
there is a fix here. That is also why this tab crosses: a character is an OBJECT,
and what the decoder hands over for it are the normalised forms every host's
builder already takes.

Nothing here imports a host.
"""

from __future__ import annotations

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import command
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unreal import direct
from . import datasets, read

STATE = "ruri_unreal_characters"

#: The dataset rows as last read. Module state like the browser's own caches: a
#: draw never crosses the CLR boundary, a command fills this and the rows redraw.
_ROWS = []


def state_of(context):
    return host_port.current().panel_state(context, STATE)


CHARACTER = Schema("UnrealCharacter", """One listed character, as the decoder states it.""", (
    Field("name", app_state.STRING, ""),
    Field("identifier", app_state.STRING, ""),
    Field("detail", app_state.STRING, ""),
    Field("package", app_state.STRING, ""),
    Field("folder", app_state.STRING, ""),
))

#: The column the decoder states a row's kind in, and the one it states the finer
#: grain in. Rows that carry neither are all one kind, which is what a build with no
#: kinds of its own looks like.
KIND = "kind"
DETAIL = "type"
EVERY = "*"

CHARACTERS = Schema("UnrealCharacters", """The Characters tab's own state: the cut the
list is drawn with, and the rows it drew.""", (
    Field("kind", app_state.ENUM, None, "Kind",
          "Which of the kinds the build files its cast under to list",
          items="kind_choices", update="on_filter"),
    Field("filter", app_state.STRING, "", "Filter",
          "Keep the characters whose name, id, type or folder contains this",
          update="on_filter", live=True),
    Field("shader_output", app_state.STRING, "", "Shader Folder",
          "Where Decompile Shaders writes this character's shader source",
          subtype=app_state.DIRECTORY),
    Field("entries", app_state.COLLECTION, element=CHARACTER),
    Field("active", app_state.INT, 0),
    Field("status", app_state.STRING, ""),
), include=(schemas.LOADING_STATE,))


def _on_filter(state, context):
    rebuild(state)


def _kinds():
    """Every kind the rows carry, most populous first, with how many carry it."""
    counted = {}
    for row in _ROWS:
        counted[row.get(KIND, "")] = counted.get(row.get(KIND, ""), 0) + 1
    return sorted(counted.items(), key=lambda pair: (-pair[1], pair[0]))


def _kind_choices(state, context):
    counted = _kinds()
    if len(counted) < 2:
        return [(EVERY, "All", "Everything the build ships")]
    return [(EVERY, "All", "{0} row(s)".format(len(_ROWS)))] + [
        (kind or EVERY, kind or "Unfiled", "{0} row(s)".format(count))
        for kind, count in counted]


HANDLERS = app_state.Handlers("SBUE.characters", on_filter=_on_filter,
                              kind_choices=_kind_choices)


def _matches(row, state):
    kind = state.kind or EVERY
    if kind != EVERY and (row.get(KIND, "") or EVERY) != kind:
        return False
    needle = state.filter.strip().lower()
    if not needle:
        return True
    return any(needle in str(row.get(field, "")).lower()
               for field in ("name", "id", KIND, DETAIL, "package", "folder"))


def rebuild(state):
    state.entries.clear()
    for row in _ROWS:
        if not _matches(row, state):
            continue
        entry = state.entries.add()
        entry.name = row.get("name", "")
        entry.identifier = str(row.get("id", ""))
        entry.detail = str(row.get(DETAIL, ""))
        entry.package = row.get("package", "")
        entry.folder = row.get("folder", "")
    state.active = min(state.active, max(len(state.entries) - 1, 0))
    shipped = sum(1 for row in _ROWS if row.get("package"))
    state.status = "{0} of {1} row(s), {2} with a model".format(
        len(state.entries), len(_ROWS), shipped)


def selected(state):
    if 0 <= state.active < len(state.entries):
        return state.entries[state.active]
    return None


def _packages(entry):
    """The packages one row states, split the way the decoder joins its lists.

    A character is one row and may be several packages -- a build whose model is a
    body and a weapon says so -- and importing it is ONE thing the user asked for,
    so the whole row goes to the host as one statement."""
    return [package for package in entry.package.split(direct.SLOT_SEPARATOR) if package]


# ---------------------------------------------------------------------------
# What the buttons do
# ---------------------------------------------------------------------------
def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_selection(context):
    return _loaded(context) and selected(state_of(context)) is not None


def _refresh(context, arguments):
    """Read every character the install ships off the decoder."""
    state = state_of(context)
    try:
        _ROWS[:] = datasets.characters()
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    rebuild(state)
    return None


def _import(context, arguments):
    """Read the selected character's package and hand it to the host."""
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
        state.status = "{0}: this install ships no model for that role.".format(entry.name)
        return
    stated = yield command.Read(
        lambda: read.packages(packages, entry.name, options, key=packages[0]), 0.7)
    if stated is None:
        state.status = "'{0}' places nothing this install carries.".format(packages[0])
        return
    yield command.Mark(0.8)
    built = host_port.current().import_packages(context, stated, options)
    state.status = "{0}: {1} placement(s). {2}".format(
        entry.name, built.imported, "  ".join(built.warnings[:2]))


def _shaders(context, arguments):
    """Decompile every shader variant this character's materials compiled to.

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
        state.status = "{0}: this install ships no model for that role.".format(entry.name)
        return
    rows = yield command.Read(lambda: datasets.shaders(packages[0], output), 0.9)
    if not rows:
        state.status = "{0}: no archive carries a shader map for its materials.".format(entry.name)
        return
    state.status = "{0}: {1} archive(s) -> {2}".format(
        entry.name, len(rows), rows[0].get("output", output))


def _reveal(context, arguments):
    entry = selected(state_of(context))
    if entry is None:
        return {"CANCELLED"}
    return command.COMMANDS.get("ruri.cabmap_reveal").run(
        context, {"cab": _packages(entry)[0] if _packages(entry) else "",
                  "query": entry.name, "folder": ""})


REFRESH = command.COMMANDS.define(
    "ruri.unreal_characters_refresh", "List Characters", _refresh,
    description="Read every character the install ships off the decoder",
    icon="FILE_REFRESH", internal=True, poll=_loaded)
IMPORT = command.COMMANDS.define(
    "ruri.unreal_character_import", "Import Character", _import,
    description="Import this character whole, exactly as the browser would",
    icon="IMPORT", poll=_has_selection, steps=True, status_state=STATE,
    failure="Unreal character import failed")
SHADERS = command.COMMANDS.define(
    "ruri.unreal_character_shaders", "Decompile Shaders", _shaders,
    description="Decompile every shader variant this character's materials compiled to",
    icon="NODE_MATERIAL", poll=_has_selection, steps=True, status_state=STATE,
    failure="Unreal shader decompile failed")
REVEAL = command.COMMANDS.define(
    "ruri.unreal_character_reveal", "Reveal", _reveal,
    description="Show the selected character's package in the file browser",
    icon="FILE_FOLDER", internal=True, poll=_has_selection)


_COLUMNS = (
    app_layout.ListColumn("name", width=0.4, icon="OUTLINER_OB_ARMATURE"),
    app_layout.ListColumn("identifier", width=0.22, align=app_layout.RIGHT, enabled=False),
    app_layout.ListColumn("detail", width=0.22, enabled=False),
    app_layout.ListColumn("folder", width=0.7, enabled=False),
)


def draw(layout, context):
    state = state_of(context)
    command.draw_progress(layout, state)
    head = layout.row(align=True)
    head.operator(REFRESH.id, icon="FILE_REFRESH")
    head.label(text=state.status)
    if not _ROWS:
        layout.label(text="List the characters to pick one.", icon="INFO")
        return
    if len(_kinds()) > 1:
        layout.prop(state, "kind", expand=True)
    layout.prop(state, "filter", text="", icon="VIEWZOOM")
    layout.list(state, "entries", "active", _COLUMNS, rows=12, identifier="unreal_characters")
    # 与浏览器同一份导入选项 —— 这里走的也是宿主那一个导入入口。
    app_browser.draw_import_options(layout, context)
    actions = layout.row(align=True)
    actions.operator(IMPORT.id)
    actions.operator(REVEAL.id)
    layout.prop(state, "shader_output")
    layout.operator(SHADERS.id, icon="NODE_MATERIAL")


def register():
    host_port.current().register_state(STATE, CHARACTERS, HANDLERS)


def unregister():
    host_port.current().unregister_state(STATE)
    _ROWS[:] = []
