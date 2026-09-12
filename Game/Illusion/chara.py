"""A character is ASSEMBLED, not shipped: one skeleton plus a prefab per slot.

WHICH pieces, in what order, on which bone and with what correction is the hook's
answer (the ``chara.plan`` dataset, keyed by the card and the outfit). This module
turns that answer into the neutral statement every host understands
(:class:`Kernel.app.loading.Packages` of kind ``ASSEMBLY``) and hands it over.

What "assemble" MEANS is then the host's: Blender joins the pieces onto one
armature, Painter puts the same pieces in one project at the same places -- the
bone a piece hangs on is a Transform of the rig prefab either way. What does not
cross is driving her FACE and her ANIMATIONS, which are the other two sections and
say so with their own capabilities.

Nothing here imports a host.
"""

from __future__ import annotations

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import cast_panel
from ...Kernel.app import command, filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import loading, schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...Kernel.app import view as app_view
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import class_registry
from . import datasets

STATE = "ruri_kk_chara"
SPEC_KEY = "Illusion:cast"

#: The seven outfits the game's own customization slots are numbered by.
COORDINATES = ("School01", "School02", "Gym", "Swim", "Club", "Plain", "Pajamas")

# What a character part contributes, by class NAME. The exclusions are the deciding
# optimisation: one head bundle's closure carries the game's whole eye/eyebrow/nose
# PATTERN LIBRARY as several hundred textures a build never looks at.
_GEOMETRY = ("GameObject", "Transform", "Mesh", "SkinnedMeshRenderer", "MeshRenderer",
             "MeshFilter", "MonoBehaviour", "MonoScript")
_MATERIALS = ("Material", "Shader")
_TEXTURES = ("Texture2D",)


def _class_ids(options):
    names = list(_GEOMETRY)
    if options.get("import_materials", True):
        names.extend(_MATERIALS)
        if options.get("import_textures", True):
            names.extend(_TEXTURES)
    resolved = []
    for name in names:
        class_id = class_registry.id_for_name(name)
        if class_id is not None and class_id not in resolved:
            resolved.append(class_id)
    return resolved


def state_of(context):
    return host_port.current().panel_state(context, STATE)


# ---------------------------------------------------------------------------
# What the panel remembers
# ---------------------------------------------------------------------------
#: This tab's live view and the seats that draw it. WHICH column is the name, the
#: id, the section or the outfit count is not stated here -- each column says so
#: itself, where the hook builds the cast.
BOUND = app_view.Bound(SPEC_KEY)

CHARA = Schema("IllusionChara", """The character tab's own state.""", (
    Field("facet", app_state.ENUM, None, "Kind", "Which of the game's own kinds to list",
          items="facet_items", update="on_filter_edit"),
    Field("search", app_state.STRING, "", "Filter", "Filter by name, file or folder",
          update="on_filter_edit", live=True),
    Field("rows", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("active_index", app_state.INT, 0),
    Field("status", app_state.STRING, ""),
    Field("coordinate", app_state.ENUM, "0", "Outfit",
          "Which of the character's seven outfits to build",
          items=tuple((str(index), name, "The character's {0} outfit".format(name))
                      for index, name in enumerate(COORDINATES))),
    Field("build_hair", app_state.BOOL, True, "Hair"),
    Field("build_clothes", app_state.BOOL, True, "Clothes"),
    Field("build_accessories", app_state.BOOL, True, "Accessories"),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE, cast_panel.CAST_STATE))



FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=SPEC_KEY, fields=BOUND.fields,
    state_for=state_of,
    apply=lambda context: rebuild(state_of(context))))


# ---------------------------------------------------------------------------
# The rows
# ---------------------------------------------------------------------------
def selected_card(state):
    return BOUND.payload(state)


def plan(state):
    """Every piece the selected card wears in the selected outfit, as the hook
    resolved it."""
    card = selected_card(state)
    if not card:
        return []
    return datasets.rows(datasets.PLAN, cardPath=card, outfit=int(state.coordinate))


def wanted_plan(state):
    """The pieces the toggles ask for. The rig, the body and the head are never
    optional -- a character without them is not a lighter build, it is a broken
    one."""
    wanted = {"armature", "head_armature", "body", "tongue", datasets.HEAD}
    if state.build_hair:
        wanted.add(datasets.HAIR)
    if state.build_clothes:
        wanted.update((datasets.CLOTHES, datasets.SUB_CLOTHES))
    if state.build_accessories:
        wanted.add(datasets.ACCESSORY)
    return [part for part in plan(state) if part["slot"] in wanted]


def rebuild(state):
    with filtering.rebuilding():
        table = datasets.table(datasets.CAST)
        if table is None:
            state.status = datasets.why_empty(datasets.CAST) or "Load a cabmap, then refresh."
        BOUND.open(table, state)


HANDLERS = cast_panel.handlers(BOUND, "Illusion.chara", rebuild)



# ---------------------------------------------------------------------------
# What the buttons do
# ---------------------------------------------------------------------------
def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_card(context):
    return _loaded(context) and bool(selected_card(state_of(context)))


def _refresh(context, arguments):
    """Read the game's customization catalog and its character cards."""
    state = state_of(context)
    try:
        datasets.table(datasets.CAST, refresh=True)
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    rebuild(state)
    return None


def _float(part, column, fallback=0.0):
    try:
        return float(part.get(column, fallback))
    except (TypeError, ValueError):
        return fallback


def _parts(rows):
    """The plan restated in the neutral vocabulary an assembly is made of.

    Row 0 IS the skeleton -- that is the hook's ordering, not a guess here -- and
    every other row names the BONE it hangs on plus the correction the game's own
    table composed for it."""
    made = []
    for position, part in enumerate(rows):
        made.append({
            "asset": part["asset"],
            "label": part.get("label") or part["asset"],
            "anchor": part.get("parent") or "",
            "position": (_float(part, "movePosX"), _float(part, "movePosY"),
                         _float(part, "movePosZ")),
            "rotation": (_float(part, "moveRotX"), _float(part, "moveRotY"),
                         _float(part, "moveRotZ")),
            "scale": (_float(part, "moveSclX", 1.0), _float(part, "moveSclY", 1.0),
                      _float(part, "moveSclZ", 1.0)),
            "rig": position == 0,
        })
    return made


def _build(context, arguments):
    """Resolve every piece this character wears and hand them over as ONE thing."""
    state = state_of(context)
    name = BOUND.value(state)
    rows = wanted_plan(state)
    if not rows:
        state.status = "That card resolves to nothing importable."
        return
    bundles = []
    for part in rows:
        if part["bundle"] not in bundles:
            bundles.append(part["bundle"])
    cabs = datasets.cabs_for(bundles)
    if not cabs:
        state.status = "None of the {0} bundle(s) are in the loaded cabmap.".format(
            len(bundles))
        return

    options = app_browser.as_options(app_browser.state_of(context))
    packages = loading.Packages(
        selected_card(state), name or "Character", loading.ASSEMBLY,
        cabs, parts=_parts(rows), export_class_ids=_class_ids(options),
        manifest={"plan": rows})
    # One read for the whole plan: the pieces share bundles heavily, and a closure
    # resolved once is the difference between a character and a re-read per slot.
    resolved = yield command.Read(
        lambda: loading.resolve_closure(cabs, export_class_ids=packages.export_class_ids),
        0.7)
    yield command.Mark(0.8)
    lines = []
    built = host_port.current().import_packages(context, packages, options, lines, resolved)
    state.status = "{0}: {1} piece(s). {2}".format(
        packages.label, built.imported, "  ".join(built.warnings[:2]))
    return built


def _settle_build(context, built):
    """The row the plan marked as the face IS the expression system; remember WHICH
    prefab it was so the face can still be driven in a later session. Only where
    there is a rig to remember it on -- a host without one has no face section."""
    if built is None or built.armature is None:
        return {"FINISHED"} if built is not None and built.imported else {"CANCELLED"}
    from . import face
    for part in (built.manifest or {}).get("plan", []):
        if str(part.get("face") or "0") not in ("0", "0.0", ""):
            face.remember(built.armature, part["bundle"], part["asset"])
    return {"FINISHED"}


REFRESH = command.COMMANDS.define(
    "ruri.kk_chara_refresh", "Refresh Characters", _refresh,
    description="Read the game's customization catalog and its character cards",
    icon="FILE_REFRESH", poll=_loaded)
BUILD = command.COMMANDS.define(
    "ruri.kk_chara_build", "Load Model", _build,
    description="Resolve every part this character wears and assemble her",
    icon="IMPORT", poll=_has_card, steps=True, status_state=STATE,
    settle=_settle_build, failure="Character build failed")


# ---------------------------------------------------------------------------
# What it looks like
# ---------------------------------------------------------------------------
_COLUMNS = (
    BOUND.column("", width=0.7, icon="OUTLINER_OB_ARMATURE"),
    BOUND.column("file", align=app_layout.RIGHT, enabled=False),
)
_GROUP_COLUMN = BOUND.column("", icon="OUTLINER_COLLECTION")



def _seeds(_context, state):
    """What the picked card IS, as archive names: the bundles every piece she wears
    in the chosen outfit resolves to -- the same set Build hands over."""
    bundles = []
    for part in wanted_plan(state):
        if part["bundle"] not in bundles:
            bundles.append(part["bundle"])
    return datasets.cabs_for(bundles)


def _options(layout, context, state):
    """What this game adds under its cast list: which outfit she wears, which
    families of pieces to build, and what that comes to."""
    layout.prop(state, "coordinate")
    toggles = layout.row(align=True)
    toggles.prop(state, "build_hair", toggle=True)
    toggles.prop(state, "build_clothes", toggle=True)
    toggles.prop(state, "build_accessories", toggle=True)
    if not selected_card(state):
        return
    rows = plan(state)
    counts = {}
    for part in rows:
        counts[part["slot"]] = counts.get(part["slot"], 0) + 1
    box = layout.box()
    box.label(text="{0} part(s) from {1} bundle(s)".format(
        len(rows), len({part["bundle"] for part in rows})))
    box.label(text=", ".join("{0} {1}".format(count, slot)
                             for slot, count in sorted(counts.items())))


def _draw_catalog(layout, context):
    """The studio's own animation catalog, in the shape its own kinds need -- an
    ordinary animation one per row, an H act as two partners side by side."""
    from . import anime
    anime.draw(layout, context)


PANEL = cast_panel.Panel(
    BOUND, _COLUMNS, "illusion_cast", REFRESH.id, state_of, STATE, seeds=_seeds,
    group_column=_GROUP_COLUMN, options=_options, actions=(BUILD.id,),
    animations=(cast_panel.ENGINE_CLIPS,
                cast_panel.Source("catalog", "Catalog",
                                  "Every animation the studio catalogs, under its own "
                                  "names -- an H act as two partners side by side",
                                  _draw_catalog)))


def draw_tab(layout, context):
    cast_panel.draw(PANEL, layout, context, state_of(context))


def register():
    host_port.current().register_state(
        STATE, CHARA, HANDLERS, extra={"FILTER_SPEC_KEY": SPEC_KEY})


def unregister():
    host_port.current().unregister_state(STATE)
    cast_panel.forget(BOUND)
    BOUND.close()
