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
from ...Kernel.app import command, filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import loading, schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import class_registry
from .. import section
from . import SECTIONS, datasets

STATE = "ruri_kk_chara"
SPEC_KEY = "Illusion:cast"

MODEL_SECTION = "model"
FACE_SECTION = "face"
ANIME_SECTION = "anime"

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
CAST_ENTRY = Schema("IllusionCastEntry", """One drawn line of the cast list.""", (
    Field("label", app_state.STRING, ""),
    Field("key", app_state.STRING, ""),
    Field("detail", app_state.STRING, ""),
    Field("is_group", app_state.BOOL, False),
))

CHARA = Schema("IllusionChara", """The character tab's own state.""", (
    Field("section", app_state.ENUM, MODEL_SECTION, "Section", items="section_items"),
    Field("search", app_state.STRING, "", "Filter", "Filter by name, file or folder",
          update="on_cast_edit", live=True),
    Field("entries", app_state.COLLECTION, element=CAST_ENTRY),
    Field("active_index", app_state.INT, 0),
    Field("status", app_state.STRING, "Refresh to read the game's characters."),
    Field("coordinate", app_state.ENUM, "0", "Outfit",
          "Which of the character's seven outfits to build",
          items=tuple((str(index), name, "The character's {0} outfit".format(name))
                      for index, name in enumerate(COORDINATES))),
    Field("build_hair", app_state.BOOL, True, "Hair"),
    Field("build_clothes", app_state.BOOL, True, "Clothes"),
    Field("build_accessories", app_state.BOOL, True, "Accessories"),
    # Whose named expressions the Face section lists -- the character's own
    # personality number, as her card states it. Read off the card by the build, so
    # it is right without anyone having to know it.
    Field("personality", app_state.INT, 0, "Personality", minimum=-100, soft_maximum=100),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE))


#: What each pane of this tab is called, and which declared section answers for
#: it. The capability itself is stated ONCE, next to the module it gates (this
#: package's SECTIONS): driving a face is blend shapes and playing an animation is
#: a timeline, and a host with neither still assembles the character.
_SECTION_PANES = ((MODEL_SECTION, "Model",
                   "Build a character from one of the game's own cards", "chara"),
                  (FACE_SECTION, "Face", "Drive the head's blend-shape patterns",
                   "face"),
                  (ANIME_SECTION, "Anime", "The studio's animation catalog", "anime"))


def _section_items(state, context):
    return [(key, label, description)
            for key, label, description, name in _SECTION_PANES
            if section(SECTIONS, name).available]


def _on_cast_edit(state, context):
    rebuild(state)


HANDLERS = app_state.Handlers(
    "Illusion.chara", base=filtering.HANDLERS,
    section_items=_section_items, on_cast_edit=_on_cast_edit)

FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=SPEC_KEY, fields=(("name", "Name"), ("file", "File"), ("folder", "Folder")),
    state_for=state_of,
    apply=lambda context: rebuild(state_of(context))))


# ---------------------------------------------------------------------------
# The rows
# ---------------------------------------------------------------------------
def selected(state):
    if 0 <= state.active_index < len(state.entries):
        entry = state.entries[state.active_index]
        if not entry.is_group:
            return entry
    return None


def selected_card(state):
    entry = selected(state)
    return entry.key if entry else ""


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
        _fill(state)


def _fill(state):
    chosen = filtering.selected_key(state)
    state.entries.clear()
    matched, table = datasets.search(datasets.CAST, {}, state.search.strip(),
                                     state.filter_rules)
    if table is None:
        state.status = datasets.why_empty(datasets.CAST) or "Load a cabmap, then refresh."
        return
    rows = [{name: table.cell(index, name) for name in table.names} for index in matched]
    rows.sort(key=lambda row: (row.get("folder", ""), row.get("name", "")))

    counts = {}
    for row in rows:
        counts[row.get("folder", "")] = counts.get(row.get("folder", ""), 0) + 1
    current = None
    for row in rows:
        folder = row.get("folder", "")
        if folder and folder != current:
            current = folder
            header = state.entries.add()
            header.label = "{0}  ({1})".format(folder, counts[folder])
            header.is_group = True
        entry = state.entries.add()
        entry.label = row.get("name") or row.get("file") or row.get("path", "")
        entry.key = row.get("path", "")
        entry.detail = row.get("file", "")
    state.status = "{0} of {1} character(s).".format(len(rows), len(table))
    filtering.restore_selection(state, chosen)


def personality_of(state):
    """The personality number the selected card states, which is whose named
    expressions the Face section lists."""
    table = datasets.table(datasets.CAST)
    card = selected_card(state)
    if table is None or not card:
        return 0
    for index in range(len(table)):
        if table.cell(index, "path") == card:
            return int(datasets.number(table, index, "personality"))
    return 0


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
    entry = selected(state)
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
        selected_card(state), entry.label if entry else "Character", loading.ASSEMBLY,
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
    state.personality = personality_of(state)
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
    "ruri.kk_chara_build", "Build Character", _build,
    description="Resolve every part this character wears and assemble her",
    icon="IMPORT", poll=_has_card, steps=True, status_state=STATE,
    settle=_settle_build, failure="Character build failed")


# ---------------------------------------------------------------------------
# What it looks like
# ---------------------------------------------------------------------------
_COLUMNS = (
    app_layout.ListColumn("label", width=0.7, icon="OUTLINER_OB_ARMATURE"),
    app_layout.ListColumn("detail", align=app_layout.RIGHT, enabled=False),
)
_GROUP_COLUMN = app_layout.ListColumn("label", icon="OUTLINER_COLLECTION")


def draw_model(layout, context):
    state = state_of(context)
    command.draw_progress(layout, state)
    filtering.draw_search_row(layout, state, extra_operator=(REFRESH.id, "FILE_REFRESH"))
    layout.list(state, "entries", "active_index", _COLUMNS, rows=10,
                identifier="illusion_cast", group_key="is_group",
                group_column=_GROUP_COLUMN)
    layout.label(text=state.status, icon="INFO")

    card = selected_card(state)
    options = layout.column(align=True)
    options.enabled = bool(card)
    options.prop(state, "coordinate")
    toggles = options.row(align=True)
    toggles.prop(state, "build_hair", toggle=True)
    toggles.prop(state, "build_clothes", toggle=True)
    toggles.prop(state, "build_accessories", toggle=True)
    if card:
        rows = plan(state)
        counts = {}
        for part in rows:
            counts[part["slot"]] = counts.get(part["slot"], 0) + 1
        box = options.box()
        box.label(text="{0} part(s) from {1} bundle(s)".format(
            len(rows), len({part["bundle"] for part in rows})))
        box.label(text=", ".join("{0} {1}".format(count, slot)
                                 for slot, count in sorted(counts.items())))
    # 与浏览器同一份导入选项 —— 组装走的也是宿主那一个导入入口。
    app_browser.draw_import_options(options, context)
    options.operator(BUILD.id)


def draw_tab(layout, context):
    """The Character tab: pick a section this host can offer, then draw it."""
    state = state_of(context)
    layout.row(align=True).prop(state, "section", expand=True)
    if state.section == MODEL_SECTION:
        draw_model(layout, context)
        return
    # The other two are a face and a timeline, so each is imported only where
    # this host answers what it needs.
    if state.section == FACE_SECTION:
        from . import face
        face.draw(layout, context)
        return
    from . import anime
    anime.draw(layout, context)


def register():
    host_port.current().register_state(
        STATE, CHARA, HANDLERS, extra={"FILTER_SPEC_KEY": SPEC_KEY})


def unregister():
    host_port.current().unregister_state(STATE)
