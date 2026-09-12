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


ANIME_ENTRY = Schema("IllusionAnimeEntry", """One drawn line of any of this
section's lists.""", (
    Field("label", app_state.STRING, ""),
    Field("key", app_state.STRING, ""),
    Field("detail", app_state.STRING, ""),
    Field("is_group", app_state.BOOL, False),
))

ANIME = Schema("IllusionAnime", """The animation catalog's own state.""", (
    Field("section", app_state.ENUM, NORMAL, "Kind",
          items=((NORMAL, "Normal", "Poses, locomotion -- everything outside an H act"),
                 (SEX, "Sex", "H acts -- one animation per partner, side by side"))),
    Field("search", app_state.STRING, "", "Filter",
          "Filter by name, group or bundle", update="on_filter_edit", live=True),
    Field("entries", app_state.COLLECTION, element=ANIME_ENTRY),
    Field("active_index", app_state.INT, 0),
    Field("male_entries", app_state.COLLECTION, element=ANIME_ENTRY),
    Field("female_entries", app_state.COLLECTION, element=ANIME_ENTRY),
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
def _side_of(row_sex):
    """Which partner a row is for -- the catalog's own ``sex`` column (0 male,
    1 female, -1 unstated), which the hook derives from whatever the game states it
    with. Not re-derived here from a path: a title that files both partners on one
    sheet row has no per-partner path to read."""
    try:
        return _SIDES.get(int(float(row_sex)))
    except (TypeError, ValueError):
        return None


def _grouped(entries, rows, label_key, group_key, key_key, detail_key):
    """Fill one collection as a grouped list -- the shape every list here uses."""
    entries.clear()
    rows.sort(key=lambda row: (str(row[group_key]), str(row[label_key])))
    counts = {}
    for row in rows:
        counts[str(row[group_key])] = counts.get(str(row[group_key]), 0) + 1

    current = None
    for row in rows:
        group = str(row[group_key])
        if group != current:
            current = group
            header = entries.add()
            header.label = "{0}  ({1})".format(group, counts[group])
            header.is_group = True
        entry = entries.add()
        entry.label = str(row[label_key])
        entry.key = str(row[key_key])
        entry.detail = str(row[detail_key])


def rebuild(state):
    matched, table = datasets.search(datasets.ANIMATIONS, {},
                                     state.search.strip(), state.filter_rules)
    if table is None:
        state.entries.clear()
        state.male_entries.clear()
        state.female_entries.clear()
        return
    normal = []
    sides = {"male": {}, "female": {}}
    group_of = {"male": {}, "female": {}}
    order = []
    seen = set()
    for index in matched:
        family = str(table.cell(index, "family"))
        row = {"name": table.cell(index, "name"),
               "group": "{0} / {1}".format(table.cell(index, "groupName"),
                                           table.cell(index, "categoryName")),
               "row": str(index),
               "clip": table.cell(index, "clip")}
        side = _side_of(table.cell(index, "sex"))
        pair = str(table.cell(index, "pair"))
        if family != "h" or side is None or not pair:
            normal.append(row)
            continue
        # WHICH act a row is one partner's half of is the catalog's own ``pair``
        # column, so the two lists are built from ONE key set and stay index-aligned
        # -- row N on the left is row N's partner on the right. Never re-derived from
        # name+clip: a title that spells the clip per sex (hou_m_00 / hou_f_00) then
        # matches nothing, every act becomes two half-empty rows, and the two columns
        # drift a row apart.
        bucket = pair.rpartition("/")[0] or pair
        key = (bucket, pair)
        if key not in sides[side]:
            sides[side][key] = row
            group_of[side].setdefault(bucket, row["group"])
            if key not in seen:
                seen.add(key)
                order.append(key)
    _grouped(state.entries, normal, "name", "group", "row", "clip")
    if state.active_index >= len(state.entries):
        state.active_index = 0
    _grouped_pairs(state, order, sides, group_of)
    state.status = "{0} animation(s).".format(len(normal) + len(order))


def _grouped_pairs(state, order, sides, group_of):
    """Fill the male and female lists so their indices correspond.

    Both collections get headers and rows at the same indices and in the same
    order; a position only one partner has gets a blank placeholder on the other
    side rather than shifting every later row out of alignment.

    A header names the group the way that column's own rows are filed -- the two
    partners of one act sit in DIFFERENT groups (男H挿入 vs 女H挿入), and the
    position alone is often just a number, so a shared header would have to drop
    the half that actually reads as a name. Where a column has nothing at all for
    a position it borrows the other one's, since the band is the same act either
    way and a bare position number names nothing."""
    state.male_entries.clear()
    state.female_entries.clear()

    # Within a band, by the act's own id as a NUMBER: it is the order the game lists
    # them in, and sorting the pair string instead puts 10 before 2.
    def _rank(key):
        tail = key[1].rpartition("/")[2]
        return (key[0], 0, int(tail), "") if tail.lstrip("-").isdigit() else (key[0], 1, 0, key[1])

    order.sort(key=_rank)
    counts = {}
    for key in order:
        counts[key[0]] = counts.get(key[0], 0) + 1

    current = None
    for key in order:
        if key[0] != current:
            current = key[0]
            for side, other, entries in (("male", "female", state.male_entries),
                                         ("female", "male", state.female_entries)):
                header = entries.add()
                header.label = "{0}  ({1})".format(
                    group_of[side].get(current) or group_of[other].get(current) or current,
                    counts[current])
                header.is_group = True
        for side, entries in (("male", state.male_entries),
                              ("female", state.female_entries)):
            row = sides[side].get(key)
            entry = entries.add()
            if row is None:
                entry.label = "--"
                entry.key = ""
                entry.detail = ""
            else:
                entry.label = str(row["name"])
                entry.key = str(row["row"])
                entry.detail = str(row["clip"])
    if state.pair_index >= len(state.male_entries):
        state.pair_index = 0


def _row_of_entry(entry):
    table = datasets.table(datasets.ANIMATIONS)
    if entry is None or table is None or not entry.key:
        return None
    try:
        index = int(entry.key)
    except ValueError:
        return None
    if not 0 <= index < len(table):
        return None
    return {name: table.cell(index, name) for name in table.names}


def selected_animation(state):
    if 0 <= state.active_index < len(state.entries):
        entry = state.entries[state.active_index]
        if not entry.is_group:
            return _row_of_entry(entry)
    return None


def selected_side(state, side):
    """The catalog row selected in one of the paired lists, or None -- a header
    or a placeholder (the partner this position lacks) selects nothing.

    Both lists share ONE index: an H act is one animation per partner and the
    two collections are index-aligned, so the row chosen on either side IS the
    partner of the row shown on the other."""
    entries = state.male_entries if side == "male" else state.female_entries
    index = state.pair_index
    if not 0 <= index < len(entries):
        return None
    entry = entries[index]
    if entry.is_group:
        return None
    return _row_of_entry(entry)


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
    for step in _import_rows(context, [selected_animation(state_of(context))]):
        yield step


def _import_side(context, arguments):
    state = state_of(context)
    for step in _import_rows(context, [selected_side(state, arguments["side"])]):
        yield step


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
    app_layout.ListColumn("label", width=0.62, icon="OUTLINER_OB_ARMATURE"),
    app_layout.ListColumn("detail", align=app_layout.RIGHT, enabled=False),
)
_GROUP_COLUMN = app_layout.ListColumn("label", icon="OUTLINER_COLLECTION")


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
    layout.list(state, "entries", "active_index", _COLUMNS, rows=12,
                identifier="illusion_anime", group_key="is_group",
                group_column=_GROUP_COLUMN)
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
    for side, title, collection, identifier in (
            ("male", "Male", "male_entries", "illusion_anime_male"),
            ("female", "Female", "female_entries", "illusion_anime_female")):
        row = selected_side(state, side)
        column = split.column(align=True)
        column.label(text=title, icon="OUTLINER_OB_ARMATURE")
        column.list(state, collection, "pair_index", _COLUMNS, rows=12,
                    identifier=identifier, group_key="is_group",
                    group_column=_GROUP_COLUMN)
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
