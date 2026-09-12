"""The StreamingScene tab's third half: the game's UI display stages.

``Scene`` and ``World`` next door browse places you walk around in. This one
browses the little lit stages an interface puts a model on -- CharInfo (the
character screen), CharFormation, WeaponInfo, the dialog stages -- and loads one
whole: its own sun, its own ambient, its own character-lighting overrides, and its
art.

The list is not a hand-written one. ``ui_scene_state`` finds these by asking the
game's own assets what they are (see its docstring); a version that ships another
display stage grows another row here with no code change. Which FIELD of which
asset drives which target is the decoder's own table (``datasets.ui_bindings``),
so no field name is spelled here either.

What this module does is RESOLVE those bindings into the shared statement
(:mod:`Kernel.app.staging`). Writing it is the host's: a sun, an ambient and a
scene camera are things an application either has or has not, and this side never
learns which. Nothing here imports a host.
"""

from __future__ import annotations

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import command
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas, staging
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...Kernel.app import view as app_view
from ...RuriRipperPyBridge.session import cabmap_state
from . import datasets, ui_scene_state

UI_SCENE = "ui"
STATE = "ruri_endfield_ui_scene"


def state_of(context):
    return host_port.current().panel_state(context, STATE)


#: This tab's live view and the seats that draw it. The rows are DISCOVERED here
#: (a stage is found by reading the assets the game ships, not by reading a table),
#: so they are published to the kernel and drawn as a view like every other list.
BOUND = app_view.Bound("Endfield:uistage")

#: What one discovered stage states, and what each column answers.
_COLUMNS_PUBLISHED = ("label|Stage", "key|Id", "group|Folder")
_ROLES = (app_view.LABEL, app_view.KEY | app_view.PAYLOAD, app_view.GROUP)

UI_SCENE_STATE = Schema("EndfieldUIScene", """The display-stage browser's state.""", (
    Field("search", app_state.STRING, "", "Filter", "Filter by stage or folder name",
          update="on_search", live=True),
    Field("rows", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("active_index", app_state.INT, 0),
    Field("import_art", app_state.BOOL, True, "Stage Prefab",
          "Import the stage's own prefab -- its floor, sky sphere, shadow plane and "
          "cameras -- into its own collection, and look through the camera the game "
          "looks through"),
    Field("apply_environment", app_state.BOOL, True, "Sun + Ambient",
          "Build the stage's directional light and set the world colour from its own "
          "sky SH"),
    Field("exposure_ev", app_state.FLOAT, 0.0, "Exposure",
          "Stops applied to everything this stage lights -- the sun AND the sky ambient "
          "together, so the balance the asset states is preserved. 0 is the asset's own "
          "values, unscaled. This control exists because the game sets its absolute level "
          "at RUNTIME by metering the frame (HGAutoExposure); no field in the asset pins "
          "it, so there is nothing to read and nothing honest to guess",
          soft_minimum=-8.0, soft_maximum=8.0),
    Field("apply_character_params", app_state.BOOL, True, "Character Params",
          "Push the stage's HGCharacterVolume onto every material of this game's own "
          "shading stack that is already loaded"),
    Field("reset_scene", app_state.BOOL, False, "Reset Scene",
          "Empty the document first. Off by default: a stage is normally loaded AROUND "
          "a character that is already here"),
    Field("status", app_state.STRING, ""),
), include=(schemas.LOADING_STATE,))


def _on_search(state, context):
    rebuild(state)


HANDLERS = app_state.Handlers("Endfield.ui_scene", on_search=_on_search)


def rebuild(state):
    """Hand what discovery found to the kernel and draw the view it answers with.
    The filter, the ordering and the folder sections all happen there."""
    BOUND.publish(_COLUMNS_PUBLISHED,
                  [(row["label"], row["id"], row["group"]) for row in ui_scene_state.SCENES],
                  _ROLES, state)


def selected(state):
    """The stage the user is on, as discovery stated it."""
    entry = BOUND.picked(state)
    return None if entry is None else ui_scene_state.row_by_id(entry.key)


# ---------------------------------------------------------------------------
# Resolving one stage into the shared statement
# ---------------------------------------------------------------------------
def _environment(row):
    """The stage's own light and ambient, as (target, value) pairs."""
    data = ui_scene_state.document(row["env"]["path"])
    if not data:
        return []
    found = []
    for binding in datasets.ui_bindings():
        target = binding["target"]
        if target not in staging.LIGHT_TARGETS and target != staging.WORLD_COLOR:
            continue
        value = _resolve(data, binding["source"])
        if value is not None:
            found.append((target, value))
    return found


def _character_params(row):
    """The stage's per-material overrides, as (slot, components, value)."""
    found = []
    for volume in row["char_volumes"]:
        data = volume["data"]
        for binding in datasets.ui_bindings():
            if binding["target"] != staging.CHARACTER_PARAMS:
                continue
            value = (ui_scene_state.overridden(data, binding["source"])
                     if binding["gate"] == "override" else data.get(binding["source"]))
            if value is not None:
                found.append((binding["slot"], binding["components"], value))
    return found


def _resolve(data, source):
    """One binding's source value, addressed the way the binding states it
    (``lightConfig.forwardDirect``). None when the asset does not carry it."""
    value = data
    for step in source.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(step)
    return value


def statement(state, row):
    """One stage as the shared statement -- what the host is handed."""
    return {
        "label": row["label"],
        "exposure": float(state.exposure_ev) if state.apply_environment else 0.0,
        "environment": _environment(row) if state.apply_environment else [],
        "character_params": _character_params(row) if state.apply_character_params else [],
        "prefabs": list(row["stage_prefabs"]) if state.import_art else [],
    }


# ---------------------------------------------------------------------------
# What the buttons do
# ---------------------------------------------------------------------------
def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_selection(context):
    return _loaded(context) and selected(state_of(context)) is not None


def _refresh(context, arguments):
    """Read the game's UI display stages out of its own config assets."""
    state = state_of(context)
    try:
        ui_scene_state.discover(cabmap_state.BRIDGE)
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    rebuild(state)
    state.status = ui_scene_state.STATUS
    return None


def _load(context, arguments):
    """Put the selected display stage into the document."""
    state = state_of(context)
    row = selected(state)
    if row is None:
        state.status = "Nothing selected."
        return
    host = host_port.current()
    if state.reset_scene and host_port.SCENE_GRAPH in host.capabilities:
        host.clear_scene(context)
    options = app_browser.as_options(app_browser.state_of(context), scene=True)
    stated = yield command.Read(lambda: statement(state, row), 0.4)
    yield command.Mark(0.6)
    lines = host.load_display_stage(context, stated, options)
    state.status = "{0}: {1}".format(row["label"],
                                     "; ".join(lines) if lines else "nothing to apply")


REFRESH = command.COMMANDS.define(
    "ruri.ui_scene_refresh", "Refresh", _refresh,
    description="Read the game's UI display stages out of its own config assets",
    icon="FILE_REFRESH", internal=True, poll=_loaded)
LOAD = command.COMMANDS.define(
    "ruri.ui_scene_load", "Load Stage", _load,
    description="Put the selected display stage into the scene",
    icon="IMPORT", requires=host_port.SCENE_GRAPH, poll=_has_selection, steps=True,
    status_state=STATE, failure="Loading this display stage failed")


_COLUMNS = (BOUND.column("", icon="LIGHT_AREA"),)
_GROUP_COLUMN = BOUND.column("", icon="FILE_FOLDER")


def draw_ui_scene_tab(layout, context):
    """The UI display stages: pick one, load it around whatever is already here."""
    state = state_of(context)
    command.draw_progress(layout, state)

    head = layout.row(align=True)
    head.prop(state, "search", text="", icon="VIEWZOOM")
    head.operator(REFRESH.id, text="", icon="FILE_REFRESH")

    if not ui_scene_state.SCENES:
        layout.label(text=ui_scene_state.STATUS, icon="INFO")
        layout.operator(REFRESH.id, icon="FILE_REFRESH")
        return

    app_view.draw_list(BOUND, layout, state, _COLUMNS, "endfield_ui_scenes",
                       group_column=_GROUP_COLUMN)
    if state.status:
        layout.label(text=state.status, icon="INFO")

    row = selected(state)
    if row is not None:
        box = layout.box()
        box.label(text=row["env"]["path"].rsplit("/", 1)[-1], icon="WORLD")
        for volume in row["volumes"]:
            box.label(text="{0}  ({1} override(s))".format(volume["name"],
                                                           len(volume["components"])),
                      icon="MODIFIER")
        for volume in row["char_volumes"]:
            box.label(text=volume["name"], icon="OUTLINER_OB_ARMATURE")
        for prefab_path in row["stage_prefabs"]:
            box.label(text=prefab_path.rsplit("/", 1)[-1], icon="OUTLINER_OB_GROUP_INSTANCE")
        if not row["stage_prefabs"]:
            box.label(text="(no prefab in this folder depends on its config)",
                      icon="MESH_DATA")

    options = layout.column(align=True)
    options.enabled = row is not None
    # 与 Scene/World 同一份导入选项 —— 画在按 Load 的地方。
    app_browser.draw_import_options(options, context)
    options.separator()
    options.prop(state, "apply_environment")
    exposure = options.row()
    exposure.enabled = state.apply_environment
    exposure.prop(state, "exposure_ev")
    options.prop(state, "apply_character_params")
    options.prop(state, "import_art")
    options.prop(state, "reset_scene")
    options.operator(LOAD.id, icon="IMPORT")


def register():
    host_port.current().register_state(STATE, UI_SCENE_STATE, HANDLERS)


def unregister():
    host_port.current().unregister_state(STATE)
    ui_scene_state.reset()
