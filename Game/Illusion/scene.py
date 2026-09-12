"""Browse the game's places the way it lists them, and import one.

One list, because the game has one set of places under two names: every one is a
Unity level, and picking one and importing it is the whole interaction -- nothing
here is streamed, so there is no window to choose.

The list itself comes from the game's hook (the ``scene.places`` dataset) and the
filtering runs on the same C# engine the bundle browser uses, over that dataset's
own handle. Nothing on this side reads a byte of the game, and nothing here
imports a host: what "import it" MEANS is the host's one import entry.
"""

from __future__ import annotations

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import command, filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import loading, schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...Kernel.app import view as app_view
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import class_registry
from . import datasets

STATE = "ruri_kk_scene"
SPEC_KEY = "Illusion:scene"

# What a level contributes, by class NAME -- the ids come from the shared
# all-version registry, so nothing here can drift against the file format. The
# exclusions are the point: a closure carries catalogue art a build never looks at.
_GEOMETRY = ("GameObject", "Transform", "Mesh", "SkinnedMeshRenderer", "MeshRenderer",
             "MeshFilter", "MonoBehaviour", "MonoScript")
_MATERIALS = ("Material", "Shader")
_TEXTURES = ("Texture2D",)
_LIGHTING = ("Light", "Cubemap", "LightProbes", "RenderSettings", "LightmapSettings",
             "ReflectionProbe")


def _class_ids(options):
    names = list(_GEOMETRY) + list(_LIGHTING)
    if options.get("import_materials", True):
        names.extend(_MATERIALS)
        if options.get("import_textures", True):
            names.extend(_TEXTURES)
    resolved = []
    for name in names:
        found = class_registry.id_for_name(name)
        if found is not None and found not in resolved:
            resolved.append(found)
    return resolved


def state_of(context):
    return host_port.current().panel_state(context, STATE)


#: This tab's live view and the seats that draw it. Which column is the name, the
#: id or the family is each column's own statement, made where the hook builds the
#: place table.
BOUND = app_view.Bound(SPEC_KEY)

SCENE = Schema("IllusionScene", """The place browser's whole state.""", (
    Field("search", app_state.STRING, "", "Filter",
          "Filter by displayed name or bundle",
          update="on_filter_edit", live=True),
    Field("rows", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("active_index", app_state.INT, 0),
    Field("status", app_state.STRING, "Refresh to read the game's scene list."),
    Field("reset_scene", app_state.BOOL, True, "Reset Scene",
          "Delete existing scene objects before importing"),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE))


def _on_filter_edit(state, context):
    rebuild(state)


HANDLERS = app_state.Handlers(
    "Illusion.scene", base=filtering.HANDLERS, on_filter_edit=_on_filter_edit)

FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=SPEC_KEY, fields=BOUND.fields,
    state_for=state_of,
    apply=lambda context: rebuild(state_of(context))))


def selected(state):
    return BOUND.picked(state)


def selected_place(state):
    """Every column of the place the user is on. The drawn line IS the row, so
    there is nothing to look up."""
    entry = BOUND.picked(state)
    return None if entry is None else entry.values()


def rebuild(state):
    """Ask the kernel for the drawn list as it is now stated. Nothing is matched,
    sorted, grouped or counted here."""
    with filtering.rebuilding():
        table = datasets.table(datasets.PLACES)
        if table is None:
            state.status = datasets.why_empty(datasets.PLACES) or "Load a cabmap, then refresh."
        BOUND.open(table, state)


# ---------------------------------------------------------------------------
# What the buttons do
# ---------------------------------------------------------------------------
def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_selection(context):
    return _loaded(context) and selected(state_of(context)) is not None


def _refresh(context, arguments):
    """Read every place the game names, out of its own tables."""
    state = state_of(context)
    try:
        datasets.table(datasets.PLACES, refresh=True)
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    rebuild(state)
    return None


def _import(context, arguments):
    """Resolve this place's dependency closure and hand it to the host.

    The closure is narrowed to the classes a LEVEL is made of, which is this
    game's own fact -- its bundles carry catalogue art no build ever looks at."""
    state = state_of(context)
    place = selected_place(state)
    if place is None:
        state.status = "Nothing selected."
        return
    cabs = datasets.cabs_for([place["bundle"]])
    if not cabs:
        state.status = "'{0}' is not in the loaded cabmap.".format(place["bundle"])
        return
    host = host_port.current()
    if state.reset_scene and host_port.SCENE_GRAPH in host.capabilities:
        host.clear_scene(context)
    options = app_browser.as_options(app_browser.state_of(context), scene=True)
    wanted = _class_ids(options)
    resolved = yield command.Read(
        lambda: loading.resolve_closure(cabs, export_class_ids=wanted), 0.7)
    yield command.Mark(0.8)
    lines = []
    built = host.import_packages(
        context, loading.Packages(place["id"], place["name"], loading.PREFAB, cabs),
        options, lines, resolved)
    state.status = "{0}: {1} root(s) from {2} CAB(s). {3}".format(
        place["name"], built.imported, len(cabs), "  ".join(built.warnings[:2]))


def _reveal(context, arguments):
    place = selected_place(state_of(context))
    if place is None:
        return {"CANCELLED"}
    cabs = datasets.cabs_for([place["bundle"]])
    return command.COMMANDS.get("ruri.cabmap_reveal").run(
        context, {"query": place["asset"], "cab": cabs[0] if cabs else "", "folder": ""})


REFRESH = command.COMMANDS.define(
    "ruri.kk_scene_refresh", "Refresh Scenes", _refresh,
    description="Read every place the game names, out of its own tables",
    icon="FILE_REFRESH", poll=_loaded)
IMPORT = command.COMMANDS.define(
    "ruri.kk_scene_import", "Import", _import,
    description="Resolve this place's dependency closure and import it",
    icon="IMPORT", poll=_has_selection, steps=True, status_state=STATE,
    failure="Scene import failed")
REVEAL = command.COMMANDS.define(
    "ruri.kk_scene_reveal", "Open Containing Folder", _reveal,
    description="Switch to the bundle browser and open this place's bundle",
    icon="FILE_FOLDER", poll=_has_selection)


_COLUMNS = (BOUND.column("", icon="WORLD"),)
_GROUP_COLUMN = BOUND.column("", icon="OUTLINER_COLLECTION")


def draw(layout, context):
    state = state_of(context)

    command.draw_progress(layout, state)
    filtering.draw_search_row(layout, state, extra_operator=(REFRESH.id, "FILE_REFRESH"))
    app_view.draw_list(BOUND, layout, state, _COLUMNS, "illusion_places",
                       group_column=_GROUP_COLUMN)
    layout.label(text=state.status, icon="INFO")

    place = selected_place(state)
    actions = layout.column(align=True)
    actions.enabled = place is not None
    if place is not None:
        info = actions.box()
        info.label(text="{0}  ->  {1}".format(place["bundle"], place["asset"]))
        info.label(text="{0} CAB(s) · named by {1}".format(
            len(datasets.cabs_for([place["bundle"]])), place["sources"]))
    # 与浏览器同一份导入选项;"清场"只在真有场景图可清的宿主上有意义。
    app_browser.draw_import_options(actions, context)
    if host_port.SCENE_GRAPH in host_port.current().capabilities:
        actions.prop(state, "reset_scene")
    actions.operator(IMPORT.id)
    actions.operator(REVEAL.id)


def register():
    host_port.current().register_state(
        STATE, SCENE, HANDLERS, extra={"FILTER_SPEC_KEY": SPEC_KEY})


def unregister():
    host_port.current().unregister_state(STATE)
