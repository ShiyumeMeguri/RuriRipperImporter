"""The Anime section: the studio's animation catalog, imported onto whichever rig
is in the scene.

Split again by the kind the catalog itself distinguishes: ordinary animations are
one per row and listed flat, while an H act is a PAIR -- one animation for each
partner -- so that kind is drawn as two index-aligned lists instead.

Resolving a catalog row to actual clips is pure topology on the scan graph
(controller -> state machines -> states -> blend trees -> clips), which is this
game's filing and nothing about a host. Building those clips onto a rig is the
host's one clip entry (:meth:`Kernel.host.Host.import_clips`).

Nothing here imports a host.
"""

from __future__ import annotations

import re

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import command, filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...Kernel.app import view as app_view
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import bridge_asset_db, class_registry
from . import datasets

STATE = "ruri_illusion_anime"
SPEC_KEY = "Illusion:anime"

# The two kinds of animation the catalog's `family` column already separates,
# drawn as sub-tabs inside the section.
NORMAL = "normal"
SEX = "sex"

_SIDES = {0: "male", 1: "female"}


def state_of(context):
    return host_port.current().panel_state(context, STATE)


#: The three lists this section draws. The flat one is the ordinary animations; the
#: other two are the SAME act rows drawn twice, one partner's columns each, so row N
#: on the left is row N's partner on the right by construction rather than by a
#: placeholder pass that two different filters would drift apart.
FLAT = app_view.Bound("Illusion:anime")
MALE = app_view.Bound("Illusion:animeacts", seats="male_entries", index="pair_index")
FEMALE = app_view.Bound("Illusion:animeacts", seats="female_entries", index="pair_index")


ANIME = Schema("IllusionAnime", """The animation catalog's own state.""", (
    Field("section", app_state.ENUM, NORMAL, "Kind",
          items=((NORMAL, "Normal", "Poses, locomotion -- everything outside an H act"),
                 (SEX, "Sex", "H acts -- one animation per partner, side by side"))),
    Field("search", app_state.STRING, "", "Filter",
          "Filter by name, group or bundle", update="on_filter_edit", live=True),
    Field("rows", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("active_index", app_state.INT, 0),
    Field("male_entries", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("female_entries", app_state.COLLECTION, element=app_view.VIEW_ROW),
    Field("pair_index", app_state.INT, 0),
    Field("status", app_state.STRING, "Refresh to read the studio's animation catalog."),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE))


def _on_filter_edit(state, context):
    rebuild(state)


HANDLERS = app_state.Handlers(
    "Illusion.anime", base=filtering.HANDLERS, on_filter_edit=_on_filter_edit)

FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=SPEC_KEY,
    fields=(("name", "Name"), ("groupName", "Group"), ("categoryName", "Position"),
            ("clip", "Clip"), ("bundle", "Bundle")),
    state_for=state_of,
    apply=lambda context: rebuild(state_of(context)),
    row_for=lambda context: current_row(state_of(context))))


# ---------------------------------------------------------------------------
# The lists
# ---------------------------------------------------------------------------
def rebuild(state):
    """Ask the kernel for whichever shape this kind needs. The pairing itself is
    the hook's join (``anime.acts``): which two animations are one act is a fact
    about the game's catalog, not something to rebuild here on every keystroke."""
    with filtering.rebuilding():
        FLAT.open(datasets.table(datasets.ANIMATIONS), state,
                  standing=[cabmap_state.Rule("family", "is_not", "h", "include")])
        acts = datasets.table(datasets.ACTS)
        MALE.open(acts, state, ordered=True, label_column="maleName")
        FEMALE.open(acts, state, ordered=True, label_column="femaleName")
        state.status = FLAT.summary


def _catalog_row(index):
    """One row of the animation catalog, by its own row number -- what an act row
    points at for each partner."""
    table = datasets.table(datasets.ANIMATIONS)
    if table is None or not 0 <= index < len(table):
        return None
    return table.row(index)


def selected_animation(state):
    picked = FLAT.picked(state)
    return None if picked is None else picked.values()


def selected_side(state, side):
    """The catalog row selected in one of the paired lists, or None -- a header,
    or the partner this act does not have, selects nothing.

    Both lists ARE one list: an act is one row carrying both partners, so the row
    chosen on either side is by construction the partner of the other."""
    picked = (MALE if side == "male" else FEMALE).picked(state)
    if picked is None:
        return None
    at = int(float(picked.cell(side + "Row") or -1))
    return None if at < 0 else _catalog_row(at)


def current_row(state):
    """Whichever row the visible list has selected -- the quick filter builds its
    rules from this, so it must follow the kind the user is looking at."""
    if state.section != SEX:
        return selected_animation(state)
    return selected_side(state, "male") or selected_side(state, "female")


# ---------------------------------------------------------------------------
# Resolving a catalog row to clips
# ---------------------------------------------------------------------------
def _resolve_state_family(bridge, cabs, controller_name, family):
    """(clip asset keys, state names) for one catalog row.

    A row names a position's controller (overrideAsset, or the base asset) and a
    state FAMILY (`clip`): the controller's states are `<prefix>_<family><digit?>`
    variants (L_/M_/S_ camera-intensity tiers in KK's H controllers). Resolution
    is pure topology on the scan graph -- controller -> state machines -> states,
    each family state's motion clips collected through blend trees -- and the
    returned keys materialize exactly those clips, never the 2000-clip bundle."""
    graph = bridge.scan_cabs(cabs)
    controller_id = class_registry.id_for_name("AnimatorController")
    override_id = class_registry.id_for_name("AnimatorOverrideController")
    machine_id = class_registry.id_for_name("AnimatorStateMachine")
    state_id = class_registry.id_for_name("AnimatorState")
    blend_tree_id = class_registry.id_for_name("BlendTree")
    clip_id = class_registry.id_for_name("AnimationClip")

    controllers = graph.find(controller_id, controller_name)
    if len(controllers) != 1:
        present = sorted(graph.name(i) for i in graph.indices_of_class(controller_id))
        raise LookupError(
            "controller {0!r} matches {1} assets in {2} -- controllers present: {3}".format(
                controller_name, len(controllers), ", ".join(cabs), ", ".join(present)))
    states = graph.reachable(controllers[0], {controller_id, override_id, machine_id}, state_id)
    pattern = re.compile(r"^(?:[A-Za-z]+_)?{0}\d*$".format(re.escape(family)))
    family_states = [i for i in states if pattern.match(graph.name(i))]
    if not family_states:
        raise LookupError(
            "no state of {0!r} matches family {1!r} -- states present: {2}".format(
                controller_name, family, ", ".join(sorted(graph.name(i) for i in states))))
    clip_keys = {}
    for state_index in family_states:
        for clip_index in graph.reachable(state_index, {blend_tree_id}, clip_id):
            clip_keys[graph.key(clip_index)] = graph.name(state_index)
    if not clip_keys:
        raise LookupError(
            "family states {0} reference no AnimationClip -- the controller wires "
            "these states to something this resolver does not follow yet.".format(
                sorted(graph.name(i) for i in family_states)))
    return clip_keys, sorted(graph.name(i) for i in family_states)


def _catalog_label(row, state_name):
    """What the panel row says, as the action's name.

    These clips are named after internal controller states (`L_SLoop1`,
    `M_IN_Loop`) which say nothing about what the animation is; the catalog is
    where the readable Japanese identity lives, and it is what the user picked
    from. ``state_name`` only contributes the part that separates one member of
    a state family from another (the L/M/S camera tier and its index), because
    the family as a whole IS the catalog row."""
    variant = ""
    match = re.match(r"^(?:([A-Za-z]+)_)?{0}(\d*)$".format(re.escape(str(row["clip"]))),
                     state_name)
    if match:
        variant = (match.group(1) or "") + (match.group(2) or "")
    parts = [str(row["groupName"]), str(row["categoryName"]), str(row["name"])]
    if variant:
        parts.append(variant)
    return "_".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# What the buttons do
# ---------------------------------------------------------------------------
def _loaded(context):
    return app_browser.state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_selection(context):
    return _loaded(context) and current_row(state_of(context)) is not None


def _refresh(context, arguments):
    """Read every animation the studio catalogs, under its own names."""
    state = state_of(context)
    try:
        datasets.table(datasets.ANIMATIONS, refresh=True)
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    with filtering.rebuilding():
        rebuild(state)
    return None


def _import_rows(context, rows):
    """Build every catalog row onto the rig the user has in front of them. The one
    import body; both the flat list and the paired male/female lists call it."""
    state = state_of(context)
    host = host_port.current()
    if host.selected_rig(context) is None:
        state.status = "Select the character's armature first."
        return
    options = app_browser.as_options(app_browser.state_of(context))
    bridge = cabmap_state.BRIDGE
    total = 0
    labels = []
    lines = []
    for row in rows:
        if row is None:
            continue
        bundle = str(row.get("overrideBundle") or row["bundle"])
        controller_name = str(row.get("overrideAsset") or row["asset"])
        cabs = datasets.cabs_for([bundle])
        if not cabs:
            lines.append("'{0}' is not in the loaded cabmap.".format(bundle))
            continue
        try:
            resolved = yield command.Read(
                lambda _cabs=cabs, _name=controller_name, _family=str(row["clip"]):
                _resolve_state_family(bridge, _cabs, _name, _family), 0.3)
        except LookupError as exc:
            lines.append(str(exc))
            continue
        clip_keys, family_states = resolved
        assets = yield command.Read(
            lambda _cabs=cabs, _keys=clip_keys: bridge.import_cabs(
                _cabs, export_asset_keys=sorted(_keys))[0], 0.6)
        database = bridge_asset_db.BridgeAssetDatabase(
            assets, clip_curve_blobs=bridge.clip_curves_by_guid,
            asset_paths=bridge.asset_paths_by_guid,
            texture_srgb=bridge.texture_srgb_by_guid)
        guid_by_key = bridge.clip_guid_by_key
        display_names = {guid_by_key[key]: _catalog_label(row, state_name)
                         for key, state_name in clip_keys.items() if key in guid_by_key}
        yield command.Mark(0.8)
        built, warnings = host.import_clips(
            context, cabs[0], sorted(guid_by_key.values()), database, options,
            display_names=display_names, activate=True)
        lines.extend(warnings[:3])
        total += built
        labels.append("{0} ({1})".format(row["name"], ", ".join(family_states)))
    state.status = "{0} action(s): {1}{2}".format(
        total, " | ".join(labels), "  " + "  ".join(lines[:3]) if lines else "")


def _import(context, arguments):
    yield from _import_rows(context, [selected_animation(state_of(context))])


def _import_side(context, arguments):
    state = state_of(context)
    yield from _import_rows(context, [selected_side(state, arguments["side"])])


REFRESH = command.COMMANDS.define(
    "ruri.kk_anime_refresh", "Refresh Animations", _refresh,
    description="Read every animation the studio catalogs, under its own names",
    icon="FILE_REFRESH", internal=True, poll=_loaded)
IMPORT = command.COMMANDS.define(
    "ruri.kk_anime_import", "Import Animation", _import,
    description="Build this animation as an action on the selected rig",
    icon="ANIM_DATA", requires=host_port.ANIMATION, poll=_has_selection, steps=True,
    status_state=STATE, failure="Animation import failed")
IMPORT_SIDE = command.COMMANDS.define(
    "ruri.kk_hanime_import", "Import", _import_side,
    description="Build this partner's selected animation as an action on the selected rig",
    icon="ANIM_DATA", requires=host_port.ANIMATION, poll=_loaded, steps=True,
    status_state=STATE, failure="Animation import failed",
    arguments=(Field("side", app_state.STRING, "male"),))


# ---------------------------------------------------------------------------
# What it looks like
# ---------------------------------------------------------------------------
_COLUMNS = (
    FLAT.column("", width=0.62, icon="OUTLINER_OB_ARMATURE"),
    FLAT.column("clip", align=app_layout.RIGHT, enabled=False),
)
_GROUP_COLUMN = FLAT.column("", icon="OUTLINER_COLLECTION")


def _side_columns(bound, side):
    """One partner's half of the act list. Both halves are the SAME rows -- one
    per act -- so they cannot drift apart however either is drawn."""
    return (bound.column(side + "Name", width=0.62, icon="OUTLINER_OB_ARMATURE"),
            bound.column(side + "Clip", align=app_layout.RIGHT, enabled=False))


_MALE_COLUMNS = _side_columns(MALE, "male")
_FEMALE_COLUMNS = _side_columns(FEMALE, "female")
_ACT_GROUP = MALE.column("", icon="OUTLINER_COLLECTION")


def draw(layout, context):
    """The animation section: pick a kind, then the list shape that kind needs.

    An ordinary animation is one per row, so those are listed flat. An H act is
    two animations -- one per partner -- so flattening would scatter halves of
    the same act down the list; that kind gets the paired side-by-side view."""
    state = state_of(context)
    command.draw_progress(layout, state)
    layout.row(align=True).prop(state, "section", expand=True)
    search = filtering.draw_search_row(layout, state,
                                       extra_operator=(REFRESH.id, "FILE_REFRESH"))
    search.menu(filtering.QUICK_FILTER_MENU, text="", icon="COLLAPSEMENU")
    if state.section == SEX:
        _draw_sex(layout, state)
    else:
        _draw_normal(layout, state)
    layout.label(text=state.status, icon="INFO")


def _draw_normal(layout, state):
    app_view.draw_list(FLAT, layout, state, _COLUMNS, "illusion_anime",
                       rows=12, group_column=_GROUP_COLUMN, summary=False)
    row = selected_animation(state)
    actions = layout.column(align=True)
    actions.enabled = row is not None
    if row is not None:
        box = actions.box()
        box.label(text="{0}  ->  {1}".format(row["bundle"], row["asset"]))
        box.label(text="clip: {0}".format(row["clip"] or "(every clip in the controller)"))
    actions.operator(IMPORT.id, icon="ANIM_DATA")


def _draw_sex(layout, state):
    split = layout.split(factor=0.5)
    for side, title, bound, columns, identifier in (
            ("male", "Male", MALE, _MALE_COLUMNS, "illusion_anime_male"),
            ("female", "Female", FEMALE, _FEMALE_COLUMNS, "illusion_anime_female")):
        row = selected_side(state, side)
        column = split.column(align=True)
        column.label(text=title, icon="OUTLINER_OB_ARMATURE")
        app_view.draw_list(bound, column, state, columns, identifier, rows=12,
                           group_column=_ACT_GROUP, summary=False)
        caption = column.box()
        caption.enabled = row is not None
        caption.label(text=(str(row["name"]) if row is not None else "(nothing selected)"))
        caption.label(text=("clip: {0}".format(row["clip"]) if row is not None else " "))
        button = column.row(align=True)
        button.enabled = row is not None
        button.operator(IMPORT_SIDE.id, text="Import " + title,
                        icon="ANIM_DATA").side = side


def register():
    filtering.register_spec(FILTER_SPEC)
    host_port.current().register_state(STATE, ANIME, HANDLERS,
                                       extra={"FILTER_SPEC_KEY": SPEC_KEY})


def unregister():
    host_port.current().unregister_state(STATE)
