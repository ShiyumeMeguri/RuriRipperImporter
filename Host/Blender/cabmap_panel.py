"""N-panel UI: cabmap gate, a folder-tree browser over each row's virtual
container path (see cabmap_state's "Virtual folder tree" section) with a
global quick-search/rule-filter fallback, and import-with-dependencies
actions. Mirrors the WinForms 'Virtual Asset List' browser's flat feature set
(columns, search, tri-state sort, load/import actions) adapted to Blender's
bpy UI toolkit -- right-click context-menu actions become buttons, since
UIList has no native per-row context menu -- plus folder navigation the
WinForms browser never had: browsing starts at the virtual root instead of
dumping every row flat, and typing a search or adding a filter rule falls
back to that original flat, global result list.

Hard gate (no single-file import path exists in this panel at all): every
widget below the cabmap picker lives in a sub-layout with
`enabled = state.loaded`, every operator's poll() re-checks the same flag,
and this module never calls prefab_importer with a user-picked path -- only
with bridge-sourced in-memory data from a resolved cabmap selection.
"""

from __future__ import annotations

import json
import os
import re
import time
import traceback

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                        IntProperty, PointerProperty, StringProperty)

from . import (armature_builder, cross_game_retarget, filter_ui, material_builder,
               prefab_importer, render, rna, step_loader)
from ... import Game
from ...Kernel import host as host_port
from ...Kernel.app import browser as _browser
from ...Kernel.app import command as app_command
from ...Kernel.app import filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import loading, schemas, state as app_state
from ...RuriRipperPyBridge.runtime import bootstrap, pythonnet_bridge, workspace
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import (bridge_asset_db, class_registry, clip_paths,
                                         discovery, texture_roles)

# The only tab this module owns, because it is the only one that is not about a
# game: the cabmap itself. Every other tab is contributed by a game module (see
# Game) and shows up exactly while the current tab's install IS that game -- which
# is why nothing here names a game or knows how many tabs exist.
BROWSER_TAB_ID = schemas.BROWSER_TAB_ID
BROWSER_TAB_LABEL = schemas.BROWSER_TAB_LABEL
# 后处理是**宿主级**的:它占的是 scene.compositing_node_group / view transform,
# 与哪个游戏在浏览无关 —— 所以它不是任何游戏的 GameTab,而是装了后处理栈就出现的一格。
# 后处理是**宿主级**的:它占的是 scene.compositing_node_group / view transform,
# 与哪个游戏在浏览无关 —— 所以它不是任何游戏的 GameTab,而是装了后处理栈就出现的一格。
BROWSER_TAB_DESCRIPTION = schemas.BROWSER_TAB_DESCRIPTION
_SORT_COLUMNS = schemas.SORT_COLUMNS

# What the browser's rows can be filtered by, straight off cabmap_state's own field
# table -- the C# engine derives a row's value for each of these names, so this list
# is a view of that table, never a second copy of it.
BROWSER_FILTER_SPEC = _browser.FILTER_SPEC
















# --- search debounce --------------------------------------------------------
# The filtering itself is cabmap_state.reapply_filter; the TIMER is Blender's,
# which is why it lives here and not in the shared browser model. Only actually
# filter ~SEARCH_DEBOUNCE_SECONDS after the user stops typing, not on every
# keystroke: a synchronous substring match across 4 columns over 260k rows per
# keystroke visibly stutters the text field (the WinForms browser this one
# mirrors hit the same wall and used a 250ms Timer for the same reason).
_pending_query = None
_last_edit_time = 0.0
_timer_registered = False






# game root -> the identity that install published about itself, as the ONE player
# the install IS (RipperBlenderBridge.ReadInstall). Cached: an install does not
# rename itself mid-session, and this is read where a folder is typed, never from a
# draw callback -- crossing the CLR boundary while Blender paints is not allowed.
_INSTALL_IDENTITY = {}




















# --- per-install config + current tab -----------------------------------------
# A BROWSER TAB IS AN INSTALL, not a game. Each open tab keeps its own
# game_root/cabmap_path/browse-dir/loaded in a ``games`` CollectionProperty entry
# filed under its own ``key``; the browser is "on" ONE of them at a time
# (state.current_tab), and the scalar props above are a get/set VIEW onto that
# entry.
#
# Which install a tab is comes from the FOLDER, the moment one is typed: the
# productName the build publishes about itself (pythonnet_bridge.read_install) is
# the tab's key and its label. A folder that publishes none -- an empty tab, a path
# that is not a Unity install -- gets UNNAMED_TAB_PREFIX_N instead, which is what
# lets a second unnamed tab exist at all. That same productName IS the game
# (config.game_name), so two installs of one title are two tabs and one game, and a
# title this add-on ships no panels for is still a perfectly good tab.
UNNAMED_TAB_PREFIX = schemas.UNNAMED_TAB_PREFIX






























_FILENAME_UNSAFE = re.compile(r'[\\/:*?"<>|]')








def _report_exception(op, prefix, exc):
    """self.report() truncates to one line and str(exc) alone drops the
    exception type + traceback -- print the full traceback to console (where
    it's actually diagnosable) and surface a short, still-useful summary in
    Blender's status bar / info log."""
    traceback.print_exc()
    op.report({"ERROR"}, f"{prefix}: {type(exc).__name__}: {exc} (full traceback in console)")


# Every decoder compiled into the loaded DLL, as (product, version, engine_version),
# newest version first within a product. Read ONCE per process: the set is compiled
# in and pythonnet loads that DLL once, for good -- this is not a cache of something
# that might move, it is the one reading there is. A miss (no bin dir yet, install
# still running) is deliberately not cached, so one early call cannot empty the menu
# for the session.
_DECODERS = None












# The browser's state machine lives in the kernel now -- tabs, install identity,
# the source-option form, the debounced search, the window onto the filtered rows.
# Named here so every operator and every game panel below reads unchanged.

_active_config = _browser._active_config
_active_filter_spec_key = _browser._active_filter_spec_key
_active_game_name = _browser._active_game_name
_active_tab = _browser._active_tab
_add_file_item = _browser._add_file_item
_adopt_identity = _browser._adopt_identity
_apply_animation_filter = _browser._apply_animation_filter
_auto_default_cabmap_filename = _browser._auto_default_cabmap_filename
_blocking_required_options = _browser._blocking_required_options
_clip_folder = _browser._clip_folder
_close_tab = _browser._close_tab
_decoder_id = _browser._decoder_id
_decoders = _browser._decoders
_decoders_of = _browser._decoders_of
_default_cabmap_filename = _browser._default_cabmap_filename
_ensure_active_config = _browser._ensure_active_config
_ensure_source_option_form = _browser._ensure_source_option_form
_ensure_tab = _browser._ensure_tab
_fill_source_option_rows = _browser._fill_source_option_rows
_fill_window = _browser._fill_window
_find_config = _browser._find_config
_format_size = _browser._format_size
_game_tabs = _browser._game_tabs
_get_browsed_dir = _browser._get_browsed_dir
_get_cabmap_path = _browser._get_cabmap_path
_get_game_root = _browser._get_game_root
_get_loaded = _browser._get_loaded
_install_identity = _browser._install_identity
_load_source_option_rows = _browser._load_source_option_rows
_missing_required_options = _browser._missing_required_options
_module_game_name = _browser._module_game_name
_module_of = _browser._module_of
_next_unnamed_key = _browser._next_unnamed_key
_offscreen_selection_note = _browser._offscreen_selection_note
_on_animation_search = _browser._on_animation_search
_on_game_root_set = _browser._on_game_root_set
_on_search_edit = _browser._on_search_edit
_open_tab = _browser._open_tab
_open_tab_keys = _browser._open_tab_keys
_option_row_value = _browser._option_row_value
_options_to_rows = _browser._options_to_rows
_reapply_and_refresh = _browser._reapply_and_refresh
_rebuild_window = _browser._rebuild_window
_redraw_all = _browser._redraw_all
_rename_tab = _browser._rename_tab
_resolve_build_output_path = _browser._resolve_build_output_path
_resolve_decoder = _browser._resolve_decoder
_rows_to_options = _browser._rows_to_options
_schedule_filter = _browser._schedule_filter
_seed_cabmap_default = _browser._seed_cabmap_default
_set_browsed_dir = _browser._set_browsed_dir
_set_cabmap_path = _browser._set_cabmap_path
_set_current_tab = _browser._set_current_tab
_set_game_root = _browser._set_game_root
_source_options = _browser._source_options
_switch_current_tab = _browser._switch_current_tab
_sync_bridge_to_tab = _browser._sync_bridge_to_tab
_sync_window_selection = _browser._sync_window_selection
_tab_bar = _browser._tab_bar
_tab_keys = _browser._tab_keys
_tab_on_root = _browser._tab_on_root
_unique_tab_key = _browser._unique_tab_key
as_options = _browser.as_options
set_source_options = _browser.set_source_options




class RURI_MT_decoder(bpy.types.Menu):
    """The decoders this tab may be read through: every version its product ships,
    plus none at all. Not an EnumProperty -- Blender stores a dynamic enum's value as
    an index into a list that changes with the loaded DLL, and the id is the thing
    worth storing."""

    bl_idname = "RURI_MT_decoder"
    bl_label = "Decoder"

    def draw(self, context):
        layout = self.layout
        state = context.scene.ruri_cabmap
        config = _active_config(state)
        product = config.game_name if config is not None else ""
        family = config.engine_family if config is not None else ""
        entries = _decoders_of(product)
        if family and family.lower() != product.lower():
            entries = entries + _decoders_of(family)
        if not entries:
            layout.label(text="No decoder ships for {0}".format(product or "this install"),
                         icon="INFO")
        for entry in entries:
            label = "{0} {1}".format(entry[0], entry[1])
            if entry[2]:
                label = "{0}  ·  {1}".format(label, entry[2])
            layout.operator(_browser.SET_DECODER.id,
                            text=label).decoder_id = _decoder_id(entry)
        layout.separator()
        layout.operator(_browser.SET_DECODER.id,
                        text="None (plain Unity build)").decoder_id = ""




















# The handle the discovered-clip list is published under so it can be searched by
# the same engine as everything else (see _apply_animation_filter). One handle:
# re-publishing replaces, which is exactly what a re-discovery wants.
_ANIMATION_TABLE_HANDLE = "ruri.animation_browser"




























# The role table, the install re-probe, the read-options form and the cabmap
# build/load are the kernel's now -- every one of them is about the DATA, and they
# stayed here only because they were written as Operators. What the import path
# below still calls is named here so it reads unchanged.
_sync_texture_roles = _browser._sync_texture_roles


def _reveal(context, arguments):
    """Reveal something in the file browser from another tab -- the tab bar
    switches to the browser and the browser goes to where that thing lives.

    The caller passes an identifier the GAME itself uses (a roster id, an
    addressable name), never a path this add-on invented. If every bundle
    carrying that identifier sits under one virtual folder, the browser jumps to
    that folder and clears the search, which is the "open file location" the user
    asked for; when the hits are spread across folders there is no single right
    folder to open, so the search stays on and shows all of them.

    Generic on purpose: it lives with the browser it drives, and no game module
    has to reach into the browser's own state to use it."""
    state = context.scene.ruri_cabmap
    query = arguments.get("query", "")
    cab = arguments.get("cab", "")
    folder = arguments.get("folder", "")
    state.active_tab = BROWSER_TAB_ID
    for rule in state.filter_rules:
        rule.enabled = False

    if folder:
        state.search = ""
        cabmap_state.browse_dir(tuple(p for p in folder.split("/") if p))
    else:
        cabmap_state.apply_filter(query)
        matches = list(cabmap_state.VISIBLE)
        folders = {cabmap_state.folder_of(row, query)
                   for row in matches}
        # One folder is somewhere to open -- unless that folder is the ROOT, which
        # is what every row of a build whose containers carry no path at all
        # resolves to. Opening the root is not "where this lives": it is the whole
        # map, with the one thing that found the row thrown away on the way. So a
        # landing that says nothing leaves the search standing instead.
        landing = folders.pop() if len(folders) == 1 else None
        if landing:
            state.search = ""
            cabmap_state.browse_dir(landing)
        else:
            state.search = query
        if not matches:
            state.status = "Nothing in the loaded cabmap carries '{0}'.".format(query)

    _rebuild_window(state)
    # Opening the place is only half of "reveal": a listing that keeps whatever
    # row was selected before reads as having jumped to an unrelated asset -- and
    # on a build that files everything at the root it is the only half there is,
    # which is why this is the ONE landing both roads take.
    if cab and not _cursor_on(state, cab):
        state.status = ("Opened where '{0}' lives, but the listing on screen does "
                        "not carry it -- narrow it and it will be there.".format(cab))
    _redraw_all(context)
    return None


def _cursor_on(state, cab):
    """Put the cursor on one cab of the listing drawn now, and say whether it was
    there to put it on."""
    for position, item in enumerate(state.window):
        if not item.is_folder and item.cab == cab:
            state.active_index = position
            state.cursor_cab = item.cab
            cabmap_state.clear_selection()
            cabmap_state.SELECTED_CABS.add(item.cab)
            return True
    return False


def _show_rules(context, arguments):
    """Switch to the browser and show exactly the rows a caller's rule set selects.

    The caller states its query as Include/Exclude RULES in the browser's own
    field vocabulary -- which is what rules are for, and what the quick-search box
    is not: the box is left EMPTY on purpose so it stays available as the user's
    own further narrowing ON TOP of the rules (type "battle" and get that subset,
    without the button's own query having eaten the box)."""
    state = context.scene.ruri_cabmap
    try:
        wanted = json.loads(arguments.get("rules") or "[]")
    except ValueError as exc:
        state.status = "Bad rule payload: {0}".format(exc)
        return {"CANCELLED"}

    state.active_tab = BROWSER_TAB_ID
    # Replace, never accumulate: this is one caller's whole query, and rules left
    # over from the previous one would silently AND into it.
    state.filter_rules.clear()
    state.search = ""
    for spec in wanted:
        rule = state.filter_rules.add()
        # spec_key first: it is what makes the field enum resolve to this list's
        # vocabulary, so assigning field before it would not stick.
        rule.spec_key = BROWSER_FILTER_SPEC.key
        rule.field = spec["field"]
        rule.relation = spec.get("relation", "contains")
        rule.value = spec.get("value", "")
        rule.action = spec.get("action", "include")
        rule.enabled = True
    state.filter_rules_active_index = len(state.filter_rules) - 1
    _reapply_and_refresh(context)
    return None


def _browser_loaded(context):
    return context.scene.ruri_cabmap.loaded


REVEAL = app_command.COMMANDS.define(
    "ruri.cabmap_reveal", "Reveal in Browser", _reveal,
    description="Switch to the file browser and show where this lives",
    internal=True, poll=_browser_loaded,
    arguments=(
        app_state.Field("query", app_state.STRING, ""),
        app_state.Field("cab", app_state.STRING, "",
                        description="The exact CAB to highlight, so the row is selected "
                                    "rather than leaving whatever was selected before"),
        app_state.Field("folder", app_state.STRING, "",
                        description="The exact virtual folder to open. When the caller "
                                    "already knows which asset it means, this beats "
                                    "searching -- a search lands on every name that "
                                    "merely contains the query")))

SHOW_RULES = app_command.COMMANDS.define(
    "ruri.cabmap_show_rules", "Show Filtered in Browser", _show_rules,
    description="Switch to the file browser and filter it to these rows",
    internal=True, poll=_browser_loaded,
    arguments=(app_state.Field(
        "rules", app_state.STRING, "",
        description="JSON list of {field, relation, value, action} in the browser's own "
                    "filter vocabulary -- the same shape the rule editor writes"),))





class RURI_MT_quick_filter(bpy.types.Menu):
    """Dynamically built from the selected row's actual values -- Include/
    Exclude x every field the browser's spec declares, exactly the Process
    Monitor right-click pattern. The body is the shared one, so a field added
    to the spec shows up here with no edit."""
    bl_idname = "RURI_MT_quick_filter"
    bl_label = "Quick Filter Selected Row"

    def draw(self, context):
        layout = self.layout
        entries = filtering.quick_filter_menu_entries(context)
        if not entries:
            layout.label(text="No row selected", icon="INFO")
            return
        for entry in entries:
            operator = layout.operator(entry["command"], text=entry["text"],
                                       icon=entry.get("icon") or "NONE")
            for name, value in entry["values"].items():
                setattr(operator, name, value)


_selected_row = _browser._selected_row
_selected_target_rows = _browser._selected_target_rows
_row_is_clip_only = _browser._row_is_clip_only


def _roots_named(wanted, roots):
    """The roots that ARE the named assets, out of everything a closure exported.

    A root is named by the asset itself (its exported file's stem is its own m_Name),
    and the caller names it from what the GAME states -- an addressable's own leaf. So
    this is two of the game's own names meeting, not a display-string guess: what it
    cannot do is tell apart two assets the game genuinely gave the same name, and both
    are then imported rather than one silently chosen."""
    names = {piece.strip().lower() for piece in wanted.split(";") if piece.strip()}
    paths = cabmap_state.BRIDGE.asset_paths_by_guid or {}
    kept = []
    for guid in roots:
        path = paths.get(guid) or ""
        leaf = path.replace("\\", "/").rsplit("/", 1)[-1]
        if leaf.rsplit(".", 1)[0].lower() in names:
            kept.append(guid)
    return kept


def _import_single_asset(op, context, state, db, guid, class_name, name):
    """Import exactly one non-hierarchy asset by class: a per-asset browser
    row (a non-bundled file's Mesh/Material/Texture2D/Avatar/TextAsset, keyed
    "<file>::<pathID>") or one loose asset found inside a bundled CAB with no
    .prefab/.unity root at all (see _import_loose_closure_assets). Clips and
    GameObject hierarchies never reach here (dispatched earlier)."""
    if class_name == "Mesh":
        mesh_file = db.load_guid(guid)
        if mesh_file is None:
            op.report({"ERROR"}, "Resolved mesh document failed to parse -- see console.")
            return {"CANCELLED"}
        report = prefab_importer.import_mesh_from_db(context, db, mesh_file, state.as_options())
        for warning in report.warnings[:5]:
            op.report({"WARNING"}, warning)
        if not report.mesh_objects:
            op.report({"ERROR"}, "Mesh decoded empty -- see console.")
            return {"CANCELLED"}
        op.report({"INFO"}, f"Imported mesh '{name}'.")
        return {"FINISHED"}

    if class_name == "Material":
        try:
            from . import material_builder
        except ImportError:
            import material_builder
        builder = material_builder.MaterialBuilder(db, prefab_importer.resolve_options(state.as_options()))
        mat = builder.build_from_ref({"guid": guid})
        _sync_texture_roles(state)
        if mat is None:
            op.report({"ERROR"}, "Material failed to build -- see console.")
            return {"CANCELLED"}
        op.report({"INFO"}, f"Imported material '{mat.name}' (browse it in the material list).")
        return {"FINISHED"}

    texture_data = db.texture_bytes(guid) if hasattr(db, "texture_bytes") else None
    if class_name is None and texture_data is not None:
        # A texture row carries image bytes rather than a YAML document. Loading goes through the
        # material builder's loader so there is exactly one place that turns exported bytes into a
        # Blender image (and one place that knows about alpha reinterpretation).
        try:
            from . import material_builder
        except ImportError:
            import material_builder
        image = material_builder._image_from_texture_bytes(texture_data, name)
        if image is None:
            op.report({"ERROR"}, f"Texture '{name}' failed to load -- see console.")
            return {"CANCELLED"}
        op.report({"INFO"}, f"Imported texture '{name}' (packed into this .blend, see the image list).")
        return {"FINISHED"}

    if class_name == "Avatar":
        avatar_file = db.load_guid(guid)
        if avatar_file is None:
            op.report({"ERROR"}, "Resolved avatar document failed to parse -- see console.")
            return {"CANCELLED"}
        report = prefab_importer.import_avatar_from_db(context, db, avatar_file, state.as_options(), name)
        for warning in report.warnings[:5]:
            op.report({"WARNING"}, warning)
        if report.armature is None:
            op.report({"ERROR"}, "Avatar skeleton decoded empty -- see console.")
            return {"CANCELLED"}
        op.report({"INFO"}, f"Imported avatar skeleton '{name}' ({report.bones} bones).")
        return {"FINISHED"}

    if class_name == "TextAsset":
        text_file = db.load_guid(guid)
        doc = text_file.first("TextAsset") if text_file is not None else None
        if doc is None:
            op.report({"ERROR"}, "Resolved text asset failed to parse -- see console.")
            return {"CANCELLED"}
        text_block = bpy.data.texts.new(name)
        text_block.write(str(doc.data.get("m_Script") or ""))
        op.report({"INFO"}, f"Imported text asset '{name}' (see the Text Editor).")
        return {"FINISHED"}

    op.report({"ERROR"}, f"Row resolved to a {class_name or 'non-document'} asset -- no importer "
                         f"for this type yet (meshes, materials, textures, avatars, text assets, "
                         f"clips, and GameObject hierarchies are supported).")
    return {"CANCELLED"}


_LOOSE_ASSET_CLASSES = ("Mesh", "Material", "Avatar", "TextAsset")


def _import_loose_closure_assets(op, context, state, db):
    """Fallback for a resolved closure with no .prefab/.unity root at all -- a
    bundled CAB of loose Mesh/Material/Avatar/TextAsset data with no
    GameObject (e.g. a shared "materials" sub-bundle: selecting it used to
    silently import nothing, since the root-selection logic in
    _import_hierarchy_rows only ever looks for .prefab/.unity roots). Walks
    every guid in the resolved closure, classifies it with the same cheap
    peek discovery.discover_clip_refs uses, and imports
    everything _import_single_asset knows how to build -- the same "import
    everything reachable in the closure" philosophy _import_hierarchy_rows
    already applies to prefab roots (see its own comment on a bundled row
    pulling in more than just its own root). Returns the count imported."""
    imported = 0
    for guid in db.all_guids():
        text = db.raw_text(guid)
        if not text:
            continue
        class_name, name = discovery.peek_class_and_name(text)
        if class_name not in _LOOSE_ASSET_CLASSES:
            continue
        if _import_single_asset(op, context, state, db, guid,
                                class_name, name or guid) == {"FINISHED"}:
            imported += 1
    return imported


def _resolve_target_armature(context):
    """The armature a standalone clip import should drive, plus its rebuilt
    maps. The object comes from prefab_importer.find_target_armature: the
    user's explicit choice first -- the active armature, or the rig the active
    mesh is BOUND to (its Armature modifier) -- then the selection's single
    rig, then the scene's only armature. maps come from the Unity rig identity
    stamped on the armature at import time (any session), falling back to the
    live import-session state when the stamp predates the feature. Returns
    (arm_obj, maps) or (None, error_message)."""
    arm_obj = prefab_importer.find_target_armature(context)
    if arm_obj is None:
        return None, ("No unambiguous target skeleton -- select the armature (or a mesh "
                      "bound to the one) the animation should drive, then retry.")
    maps = prefab_importer.maps_from_stamped_armature(arm_obj)
    if maps is None:
        build_state = cabmap_state.ANIMATION_BUILD_STATE
        if (build_state is not None and build_state.get("arm_name") == arm_obj.name
                and build_state.get("maps") is not None):
            maps = build_state["maps"]
    if maps is None:
        return None, (f"Armature '{arm_obj.name}' carries no Unity rig identity (imported "
                      f"before this feature, or by another tool) -- re-import the character "
                      f"once, then animations attach to it standalone from then on.")
    return arm_obj, maps


def build_clips(context, clip_cab, clip_guids, db, options, display_names=None,
                activate=False):
    """THE clip build: resolve the target armature from the user's selection,
    verify the clips actually fit that skeleton (path/CRC32 match against the
    armature's own bone paths), then build actions. Returns (built, lines).

    This is what :meth:`Kernel.host.Host.import_clips` names -- a performance
    measured against a rest pose, which only a host with rigs can put anywhere."""
    arm_obj, maps_or_error = _resolve_target_armature(context)
    if arm_obj is None:
        return 0, [maps_or_error]
    # A muscle-encoded clip solves inside build_selected_animations, against the
    # avatar document stamped on this very armature -- no export scope, no
    # co-seeding, nothing avatar-related to do here.
    try:
        built, warnings = cross_game_retarget.load_clips_onto(
            context, cabmap_state.active_key(), clip_cab, clip_guids, db, arm_obj,
            maps_or_error, options, display_names, activate)
    except cross_game_retarget.CrossGameRetargetError as exc:
        return 0, [str(exc)]
    lines = list(warnings[:5])
    lines.append("Built {0} animation action(s) on {1}.".format(built, arm_obj.name))
    return built, lines


# The bridge crossing every import flow shares, and what a stale cabmap looks
# like when it happens -- both in the kernel now, because a second host crosses
# the same bridge the same way. Named here so every existing caller reads
# unchanged.
StaleCabmapError = loading.StaleCabmapError
resolve_import_closure = loading.resolve_closure
unreachable_rows = loading.unreachable_rows
unreachable_seeds = loading.unreachable_seeds
forget_archives = loading.forget_archives


def import_hierarchy_from_closure(operator, context, state, rows, resolved,
                                  only_root_names="", populate_browser=False,
                                  only_seeded=False, options=None):
    """Import hierarchy/asset rows out of an ALREADY-resolved closure.

    THE hierarchy import -- the operator is one caller of it, not its owner. A
    caller that resolved the closure itself (off the main thread, for a whole cast
    at once) gets exactly the dispatch a browser click gets: per-asset rows, scene
    roots, the loose-asset path, secondary motion.

    ``operator`` is anything with ``.report(level, message)``.

    Two ways to say "just what I asked for", for the two kinds of caller:
    ``only_root_names`` matches roots by asset NAME, for a caller that knows the
    name it wants; ``only_seeded`` imports exactly the roots THESE rows' CABs
    resolved to, by the bridge's own CAB identity. The second is what a caller
    sharing one closure with other callers needs -- with a whole cast in one
    closure, "every root the closure exports" is everybody at once, so an
    unrestricted import would give each member the entire cast.

    Returns (all_ok, imported_count)."""
    db = resolved['db']
    roots = resolved['roots']
    seed_roots = resolved['seed_roots']
    scene_roots = resolved['scene_roots']
    # A caller whose asset needs different options than the session's states them:
    # a placement hierarchy is empties, and the session skips empties by default.
    if options is None:
        options = state.as_options()
    ok = True
    imported = 0

    # Per-asset rows that resolved to a NON-hierarchy asset (Mesh/Material/
    # Texture2D) import exactly that one asset -- dispatched before any
    # roots logic, since a lone mesh/texture closure legitimately exports
    # zero .prefab/.unity roots.
    hierarchy_targets = []  # (row, primary_guid or None)
    for row in rows:
        cab = row["cab"]
        primary_guid = seed_roots.get(cab)
        if "::" in cab:  # per-asset virtual row of a non-bundled file
            if primary_guid is None:
                operator.report({"ERROR"}, f"'{row['name']}' didn't export as its own file (engine "
                                       f"built-in, or embedded in a scene/host hierarchy) -- "
                                       f"import its host file row instead.")
                ok = False
                continue
            text = db.raw_text(primary_guid)
            class_name = discovery.peek_class_and_name(text)[0] if text else None
            if class_name != "GameObject":
                if _import_single_asset(operator, context, state, db,
                                        primary_guid, class_name, row["name"]) == {"FINISHED"}:
                    imported += 1
                else:
                    ok = False
                continue
        hierarchy_targets.append((row, primary_guid))

    if not hierarchy_targets:
        if populate_browser:
            _populate_animation_browser(state, None)
        _sync_texture_roles(state)
        return ok, imported

    # Root selection, generalizing the single-row semantics row by row:
    # a scene row and a per-asset GameObject row import exactly their OWN
    # root (a level's closure drags in every shared .prefab the whole
    # dependency graph exports -- the scene already instantiates what it
    # uses); a plain bundled row imports every root its closure exports
    # (an actor prefab routinely pulls a portrait "uimodel" variant as a
    # second top-level asset). If any plain bundled row is in the batch,
    # the union closure's full root set imports (deduped) -- the same
    # outcome as importing those rows one at a time.
    restricted_roots = []
    unrestricted = False
    for row, primary_guid in hierarchy_targets:
        if primary_guid is not None and (primary_guid in scene_roots or "::" in row["cab"]):
            restricted_roots.append(primary_guid)
        else:
            unrestricted = True
    if only_seeded:
        # The rows' own roots, by CAB identity. A CAB that seeded no root of its own
        # contributes nothing rather than opening the whole closure -- with a shared
        # closure that would be everybody else's models.
        import_roots = [guid for _row, guid in hierarchy_targets if guid is not None]
        if not import_roots:
            operator.report({"WARNING"}, "None of {0} CAB(s) exported a root of its own.".format(
                len(hierarchy_targets)))
            return False, imported
    else:
        import_roots = list(roots) if unrestricted else restricted_roots
    if only_root_names:
        import_roots = _roots_named(only_root_names, import_roots)
        if not import_roots:
            operator.report({"ERROR"}, f"The closure exports no root named "
                                   f"'{only_root_names}' -- nothing was imported.")
            return False, imported
    if unrestricted and any(guid in scene_roots for guid in restricted_roots):
        operator.report({"WARNING"}, "Mixing a scene row with bundled prefab rows imports the "
                                 "bundled rows' full root set -- import scenes on their own "
                                 "for a minimal result.")
    if not import_roots:
        # No GameObject-rooted .prefab/.unity anywhere in the closure -- a
        # bundled CAB of loose Mesh/Material/Avatar/TextAsset data (see
        # _import_loose_closure_assets) rather than a dead end.
        loose_imported = _import_loose_closure_assets(operator, context, state, db)
        if loose_imported == 0:
            operator.report({"WARNING"}, "No importable (.prefab/.unity) asset, and no loose "
                                     "Mesh/Material/Avatar/TextAsset, found in the resolved closure.")
        if populate_browser:
            _populate_animation_browser(state, None)
        return loose_imported > 0, imported + loose_imported

    # The animation browser only applies to a SINGLE selected character --
    # attribute it through seed_roots (the cabmap's own CAB identity, never
    # a display-name match; see RipperBridge.import_cabs).
    primary_of_single = (hierarchy_targets[0][1]
                         if populate_browser and len(hierarchy_targets) == 1 else None)
    primary_report = None
    seen_roots = set()
    for root_guid in import_roots:
        if root_guid in seen_roots:
            continue
        seen_roots.add(root_guid)
        prefab_file = db.load_guid(root_guid)
        if prefab_file is None:
            continue
        report = prefab_importer.import_prefab_from_db(context, db, prefab_file, options)
        imported += 1
        for warning in report.warnings[:5]:
            operator.report({"WARNING"}, warning)
        if root_guid == primary_of_single:
            primary_report = report
        _import_secondary_motion(operator, context, state, report, rows)

    if populate_browser and imported and primary_report is None:
        operator.report({"WARNING"}, "Could not match an imported root back to the selected row -- "
                                 "animation browser not populated.")
    _populate_animation_browser(state, primary_report if populate_browser else None)
    _sync_texture_roles(state)
    return ok, imported


def _import_secondary_motion(operator, context, state, report, rows):
    """Hand this root's armature to the game, if the game states such settings and the
    import was asked for them. The host neither knows what those settings are nor which
    games have them -- it asks the registry and reports whatever comes back."""
    if not state.import_secondary_motion or report.armature is None:
        return
    _run_secondary_motion(operator, context, state, report.armature,
                          [row["cab"] for row in rows])


def _run_secondary_motion(operator, context, state, armature, cabs):
    """Ask the current game for the settings those CABs state, and word the answer.

    The host holds no opinion about what came back beyond how to say it: a game that
    states nothing returns nothing, and the lines are the game's own."""
    reader = Game.secondary_motion_of(_active_game_name(state))
    if reader is None:
        return None
    try:
        reading = reader(list(cabs))
        lines = (host_port.current().write_secondary_motion(context, armature, reading)
                 if reading is not None else None)
    except Exception as failure:
        operator.report({"WARNING"}, "Secondary motion was not imported: %s" % failure)
        return None
    if not lines:
        return None
    operator.report({"INFO"}, lines[0])
    for line in lines[1:]:
        operator.report({"WARNING"}, line)
    return lines


def _populate_animation_browser(state, report):
    """Refresh the Animations sub-panel from a just-finished import's report
    (or clear it out on multi-character imports / imports with no armature,
    where per-character clip browsing doesn't apply)."""
    state.available_clips.clear()
    state.animation_character_name = ""
    cabmap_state.clear_animation_build_state()
    if report is None or report.armature is None or not report.available_clips:
        return
    state.animation_character_name = report.armature.name
    for ref in report.available_clips:
        item = state.available_clips.add()
        item.guid = ref["guid"]
        item.name = ref["name"]
        item.folder = _clip_folder(ref.get("path"))
        item.size_bytes = ref["size_bytes"]
    _apply_animation_filter(state)
    cabmap_state.set_animation_build_state(
        report.db, report.armature.name, report.maps, report.path_to_meshobjects)


class RURI_UL_animation_clips(bpy.types.UIList):
    """Checkbox-per-clip list for the Animations sub-panel.

    Each row carries the game's own folder for that clip, dimmed and right
    aligned: a character's discovered closure routinely mixes its body library
    with cutscene and dialogue clips that share nothing but a name pattern, and
    the folder is the only thing on the row that says which is which. The rows
    arrive sorted by folder, so that column also reads as the grouping.

    Filtering is the panel's own search box, not Blender's built-in name filter:
    it matches the folder as well as the name, through the same C# engine the
    browser searches with (see _apply_animation_filter)."""
    bl_idname = "RURI_UL_animation_clips"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row = layout.row(align=True)
        row.prop(item, "selected", text="")
        row.label(text=item.name)
        if item.folder:
            folder = row.row()
            folder.enabled = False
            folder.alignment = "RIGHT"
            folder.label(text=item.folder.rpartition("/")[2])
        # size_bytes is 0 for a cheaply-discovered-but-not-yet-resolved clip
        # (see RURI_OT_discover_animations -- pure cabmap metadata has no
        # per-asset byte size); showing "0 B" would misleadingly read as an
        # empty clip rather than "size not known yet."
        size = row.row()
        size.alignment = "RIGHT"
        size.label(text=_format_size(item.size_bytes) if item.size_bytes > 0 else "size unknown")

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item if item.visible else 0 for item in items]
        order = bpy.types.UI_UL_list.sort_items_by_name(items, "name") if self.use_filter_sort_alpha else []
        return flags, order


class RURI_PT_column_widths_popover(bpy.types.Panel):
    """The draggable column widths, as a popover off the sort row."""
    bl_idname = _browser.COLUMN_WIDTHS_PANEL
    bl_label = "Column Widths"
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_ui_units_x = 14

    def draw(self, context):
        render.draw(app_layout.describe(_browser.draw_column_widths, context),
                    self.layout, context)




class RURI_PT_cabmap(bpy.types.Panel):
    """The add-on's N-panel: a renderer for the kernel's browser description.

    Nothing about what this panel SAYS lives here any more -- it is described
    once (Kernel.app.browser.draw) and Painter's dock renders the same
    description. What is Blender's is the panel class itself: where it sits, what
    category it appears under, and the layout object it hands the renderer."""
    bl_idname = "RURI_PT_cabmap"
    bl_label = "RuriRipper"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RuriRipper"

    def draw(self, context):
        render.draw(app_layout.describe(_browser.draw, context), self.layout, context)




class RURI_OT_discover_animations(bpy.types.Operator):
    """Cheap animation-clip discovery for the selected row: walks the
    ALREADY-LOADED cabmap's own dependency graph (CabMap.
    ResolveClosureCabNames -- pure in-memory, no VFS decrypt, no AssetRipper
    export) and filters to CABs whose TypeNames (also already loaded, per
    CAB) include AnimationClip. No db is resolved at this point -- a clip's
    guid is only ever needed once the user actually checks it and clicks
    Import Checked Animations, which is also the first point anything gets
    exported/built at all, including the character itself."""
    bl_idname = "ruri.discover_animations"
    bl_label = "Discover Animations"
    bl_description = "List the selected row(s)' animation clips from the cabmap's own dependency graph -- cheap, nothing exported/built yet"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        state = context.scene.ruri_cabmap
        return (state.loaded and cabmap_state.BRIDGE is not None
                and (cabmap_state.SELECTED_CABS
                     or 0 <= state.active_index < len(state.window)))

    def execute(self, context):
        state = context.scene.ruri_cabmap
        target_rows = _selected_target_rows(state)
        if not target_rows:
            self.report({"WARNING"}, "No rows selected.")
            return {"CANCELLED"}
        seed_cabs = [row["cab"] for row in target_rows]
        try:
            closure_cabs = cabmap_state.BRIDGE.resolve_closure_cab_names(seed_cabs)
        except Exception as exc:
            _report_exception(self, "Discover animations failed", exc)
            return {"CANCELLED"}

        rows_by_cab = cabmap_state.rows_by_cab()
        clip_rows = [rows_by_cab[cab] for cab in closure_cabs
                     if cab in rows_by_cab and "AnimationClip" in rows_by_cab[cab]["type_names"]]
        # By folder first, name second: a closure mixes several of the game's own
        # animation folders, and folder order is what makes that readable.
        clip_rows.sort(key=lambda r: (_clip_folder(r["container"]).lower(), r["name"].lower()))

        state.available_clips.clear()
        state.animation_character_name = (target_rows[0]["name"] if len(target_rows) == 1
                                          else f"{len(target_rows)} selected rows")
        for row in clip_rows:
            item = state.available_clips.add()
            # A CAB name for now, not a real Unity guid -- translated to real
            # clip guid(s) through the export's own clips_by_cab capture once
            # the lazy build below actually resolves this closure (a clip
            # CAB's fbx display name and its clips' m_Names genuinely differ,
            # and one CAB can host several clips -- identity, never names).
            item.guid = row["cab"]
            item.name = row["name"]
            item.folder = _clip_folder(row["container"])
            item.size_bytes = 0  # not known without resolving/exporting -- see RURI_UL_animation_clips
        _apply_animation_filter(state)
        carry = None
        prior = cabmap_state.ANIMATION_BUILD_STATE
        if prior and prior.get("arm_name"):
            prior_arm = bpy.data.objects.get(prior["arm_name"])
            if prior_arm is not None and prior_arm.type == "ARMATURE":
                carry = prior
        cabmap_state.set_animation_discovery_state(seed_cabs, state.as_options(), carry)

        if clip_rows:
            self.report({"INFO"}, f"Found {len(clip_rows)} clip(s). Check the ones you want, then Import Checked Animations.")
        else:
            self.report({"INFO"}, "No animation clips found in this selection's dependency closure.")
        return {"FINISHED"}


class RURI_OT_import_selected_animations(bpy.types.Operator):
    bl_idname = "ruri.import_selected_animations"
    bl_label = "Import Checked Animations"
    bl_description = "Build the character (if not already in the scene) and Blender actions for the checked clips"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        state = context.scene.ruri_cabmap
        return (cabmap_state.ANIMATION_BUILD_STATE is not None
                and any(item.selected for item in state.available_clips))

    def execute(self, context):
        state = context.scene.ruri_cabmap
        build_state = cabmap_state.ANIMATION_BUILD_STATE
        if build_state is None:
            self.report({"WARNING"}, "No character discovered -- click Discover Animations first.")
            return {"CANCELLED"}

        checked_keys = [item.guid for item in state.available_clips if item.selected]
        if not checked_keys:
            self.report({"WARNING"}, "No animations checked.")
            return {"CANCELLED"}

        arm_obj = bpy.data.objects.get(build_state["arm_name"]) if build_state["arm_name"] else None
        if arm_obj is not None and arm_obj.type != "ARMATURE":
            arm_obj = None
        if arm_obj is None or build_state["db"] is None:
            seed_cabs = list(build_state["seed_cabs"] or [])
            target = arm_obj or prefab_importer.find_target_armature(context)
            target_maps = (prefab_importer.maps_from_stamped_armature(target)
                           if target is not None and target.type == "ARMATURE" else None)
            if target_maps is not None:
                clip_id = class_registry.id_for_name("AnimationClip")
                try:
                    clip_assets, _roots, _seed_roots, clips_by_cab, _scene_roots = \
                        cabmap_state.BRIDGE.import_cabs(seed_cabs, export_class_ids=[clip_id])
                except Exception as exc:
                    _report_exception(self, "Import (bridge) failed", exc)
                    return {"CANCELLED"}
                clip_db = bridge_asset_db.BridgeAssetDatabase(
                    clip_assets, clip_curve_blobs=cabmap_state.BRIDGE.clip_curves_by_guid,
                    mesh_blobs=cabmap_state.BRIDGE.mesh_blobs_by_guid,
                    asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid,
                    texture_srgb=cabmap_state.BRIDGE.texture_srgb_by_guid)
                selected_guids = []
                for cab in checked_keys:
                    for guid in clips_by_cab.get(cab.lower(), []):
                        if guid not in selected_guids:
                            selected_guids.append(guid)
                if not selected_guids:
                    self.report({"ERROR"}, "The checked row(s) exported no AnimationClip -- see console.")
                    return {"CANCELLED"}
                ratio, checked = cross_game_retarget._binding_match(
                    clip_db, selected_guids, target_maps["path_to_bone"])
                target_family = cross_game_retarget.skeleton_of(target)
                session_family = cabmap_state.game_of(cabmap_state.active_key()) or ""
                retargetable = (target_family and session_family
                                and target_family.lower() != session_family.lower())
                if not checked or ratio > 0.0 or retargetable:
                    try:
                        built, warnings = cross_game_retarget.load_clips_onto(
                            context, cabmap_state.active_key(),
                            seed_cabs[0] if seed_cabs else None,
                            selected_guids, clip_db, target, target_maps,
                            state.as_options())
                    except cross_game_retarget.CrossGameRetargetError as exc:
                        self.report({"ERROR"}, str(exc))
                        return {"CANCELLED"}
                    for warning in warnings[:5]:
                        self.report({"WARNING"}, warning)
                    self.report({"INFO"}, "Imported %d animation(s) onto '%s'."
                                          % (built, target.name))
                    return {"FINISHED"}

            try:
                assets, roots, seed_roots, clips_by_cab, _scene_roots = \
                    cabmap_state.BRIDGE.import_cabs(seed_cabs)
            except Exception as exc:
                _report_exception(self, "Import (bridge) failed", exc)
                return {"CANCELLED"}
            db = bridge_asset_db.BridgeAssetDatabase(
                assets, clip_curve_blobs=cabmap_state.BRIDGE.clip_curves_by_guid,
            mesh_blobs=cabmap_state.BRIDGE.mesh_blobs_by_guid,
            asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid,
            texture_srgb=cabmap_state.BRIDGE.texture_srgb_by_guid)

            selected_guids = []
            for cab in checked_keys:
                for guid in clips_by_cab.get(cab.lower(), []):
                    if guid not in selected_guids:
                        selected_guids.append(guid)

            if not roots:
                # Animation-only closure (the discovered row(s) were clip
                # CABs): attach onto the user's selected skeleton instead of
                # requiring a character build.
                if not selected_guids:
                    self.report({"ERROR"}, "The checked row(s) exported no AnimationClip -- see console.")
                    return {"CANCELLED"}
                built, lines = build_clips(context, seed_cabs[0] if seed_cabs else None,
                                           selected_guids, db, state.as_options())
                for line in lines:
                    self.report({"INFO"} if built else {"ERROR"}, line)
                return {"FINISHED"} if built else {"CANCELLED"}

            # Character closure: build the character once. Its own asset is
            # resolved bridge-side through the cabmap's CAB identity
            # (seed_roots) -- not a name match. With a multi-row discovery the
            # FIRST seed that resolved to its own root asset is the character.
            primary_guid = next((seed_roots.get(cab) for cab in seed_cabs
                                 if seed_roots.get(cab)), None)
            prefab_file = db.load_guid(primary_guid) if primary_guid else None
            if prefab_file is None:
                self.report({"ERROR"}, "Could not resolve the discovered character's own asset "
                                       "in its exported closure.")
                return {"CANCELLED"}

            report = prefab_importer.import_prefab_from_db(context, db, prefab_file, build_state["options"])
            for warning in report.warnings[:5]:
                self.report({"WARNING"}, warning)
            if report.armature is None:
                self.report({"ERROR"}, "This character has no skeleton to attach animations to.")
                return {"CANCELLED"}
            arm_obj = report.armature
            cabmap_state.mark_animation_build_done(db, arm_obj.name, report.maps, report.path_to_meshobjects)
            build_state = cabmap_state.ANIMATION_BUILD_STATE

            # Upgrade the browser from CAB rows to the REAL clips (guid-keyed,
            # names + sizes now knowable), carrying the user's checked state
            # across through clips_by_cab -- this is deliberate and visible,
            # not a side effect: from here on the list shows exactly what can
            # be built, and a second Import needs no lazy build at all.
            refs = discovery.discover_clip_refs(db)
            state.available_clips.clear()
            state.animation_character_name = arm_obj.name
            for ref in refs:
                item = state.available_clips.add()
                item.guid = ref["guid"]
                item.name = ref["name"]
                item.folder = _clip_folder(ref["path"])
                item.size_bytes = ref["size_bytes"]
                item.selected = ref["guid"] in selected_guids
            _apply_animation_filter(state)
            if not selected_guids:
                self.report({"WARNING"}, "The checked row(s) mapped to no exported clip -- "
                                         "pick from the refreshed list and import again.")
                return {"CANCELLED"}
            guids = selected_guids
        else:
            # The armature (and a real guid-keyed browser) already exist --
            # every checked item.guid IS a clip guid; just validate.
            db = build_state["db"]
            guids = []
            unresolved = []
            for item in state.available_clips:
                if not item.selected:
                    continue
                # The one clip surface -- no full YAML parse just to sniff the class.
                if db.clip_curves(item.guid) is not None:
                    guids.append(item.guid)
                else:
                    unresolved.append(item.name)
            if unresolved:
                self.report({"WARNING"}, f"{len(unresolved)} checked clip(s) not found in the resolved "
                                         f"closure: {', '.join(unresolved[:3])}{'...' if len(unresolved) > 3 else ''}")
            if not guids:
                self.report({"ERROR"}, "None of the checked clips could be resolved.")
                return {"CANCELLED"}

        try:
            built, build_warnings = cross_game_retarget.load_clips_onto(
                context, cabmap_state.active_key(), None, guids, build_state["db"],
                arm_obj, build_state["maps"], state.as_options())
        except cross_game_retarget.CrossGameRetargetError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            _report_exception(self, "Animation import failed", exc)
            return {"CANCELLED"}
        for warning in build_warnings[:5]:
            self.report({"WARNING"}, warning)
        self.report({"INFO"}, f"Built {built} animation action(s) on {arm_obj.name}.")
        return {"FINISHED"}




class RURI_PT_animation_browser(bpy.types.Panel):
    """Checkbox animation browser -- discover, then select, then commit: always
    visible once a cabmap is loaded, with its own "Discover Animations"
    button front and center, rather than being an invisible side effect of
    the generic Import buttons gated behind an easy-to-miss checkbox (the
    original shape -- poll()'d on available_clips already being non-empty,
    only reachable by first checking "Discover Animations" above then
    clicking Import -- was reported back as "I checked the box and nothing
    happened," since checking a box is not a visibly-actionable step)."""
    bl_idname = "RURI_PT_animation_browser"
    bl_label = "Animations"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RuriRipper"
    bl_parent_id = "RURI_PT_cabmap"

    @classmethod
    def poll(cls, context):
        state = context.scene.ruri_cabmap
        return state.loaded and _active_tab(state) is None

    def draw(self, context):
        layout = self.layout
        state = context.scene.ruri_cabmap

        row = layout.row(align=True)
        row.operator(RURI_OT_discover_animations.bl_idname, icon="VIEWZOOM")

        if not state.available_clips:
            layout.label(text="Select a row above, then click Discover Animations.", icon="INFO")
            return

        layout.label(text=f"Clips for: {state.animation_character_name}", icon="ARMATURE_DATA")

        layout.prop(state, "animation_search", icon="VIEWZOOM", text="")

        row = layout.row(align=True)
        op = row.operator(_browser.ANIMATION_SELECT_ALL.id, text="All Shown")
        op.select = True
        op = row.operator(_browser.ANIMATION_SELECT_ALL.id, text="None")
        op.select = False

        layout.template_list(RURI_UL_animation_clips.bl_idname, "", state, "available_clips",
                             state, "available_clips_active_index", rows=8)

        total = len(state.available_clips)
        shown = sum(1 for item in state.available_clips if item.visible)
        selected = [item for item in state.available_clips if item.selected]
        folders = len({item.folder for item in state.available_clips if item.folder})
        # Says all three numbers, because they differ for real reasons: a filter
        # hides rows, and a check made before the filter is still going to be
        # imported even while its row is off screen.
        layout.label(text="{0} clip(s){1}{2} · {3} checked".format(
            total,
            "" if shown == total else f" · {shown} shown",
            "" if folders <= 1 else f" · {folders} folders",
            len(selected)))

        layout.operator(RURI_OT_import_selected_animations.bl_idname, icon="IMPORT",
                        text=f"Import {len(selected)} Checked Animation(s)" if selected
                        else "Import Checked Animations")


_CLASSES = (
    # PropertyGroups first, and RURI_PG_filter_rule/RURI_PG_cabmap_row/
    # RURI_PG_install_config specifically before RURI_PG_cabmap -- Blender requires a
    # CollectionProperty's target type to already be registered.
    RURI_UL_animation_clips,
    RURI_MT_quick_filter,
    RURI_MT_decoder,
    RURI_PT_column_widths_popover,
    RURI_PT_cabmap,
    RURI_OT_discover_animations,
    RURI_OT_import_selected_animations,
    RURI_PT_animation_browser,
)

_addon_keymaps = []


def register_keymaps():
    """Keymap entries LAST: an entry sets properties on the operator it names, so
    it can only be built once every command has its wrapper -- which is after the
    games have declared theirs."""
    return _register_keymaps()


def unregister_keymaps():
    return _unregister_keymaps()


def _register_keymaps():
    """Ctrl+A / Alt+A / Ctrl+I select-all/none/invert while hovering the
    RuriRipper sidebar. Registered in the "User Interface" keymap (the one
    active over any UI region); RURI_OT_cabmap_select_all.poll narrows it to
    the 3D View sidebar with the RuriRipper category actually in front, so
    the shortcuts never fire anywhere else."""
    window_manager = bpy.context.window_manager
    keyconfig = getattr(window_manager, "keyconfigs", None)
    addon_keyconfig = keyconfig.addon if keyconfig else None
    if addon_keyconfig is None:  # headless/background -- nothing to bind
        return
    keymap = addon_keyconfig.keymaps.new(name="User Interface", space_type="EMPTY")
    for key, use_ctrl, use_alt, mode in (("A", True, False, "ALL"),
                                         ("A", False, True, "NONE"),
                                         ("I", True, False, "INVERT")):
        item = keymap.keymap_items.new(_browser.CABMAP_SELECT_ALL.id, key, "PRESS",
                                       ctrl=use_ctrl, alt=use_alt)
        item.properties.mode = mode
        _addon_keymaps.append((keymap, item))


def _unregister_keymaps():
    for keymap, item in _addon_keymaps:
        keymap.keymap_items.remove(item)
    _addon_keymaps.clear()




def register():
    # Before this module's own classes: RURI_PG_cabmap holds a CollectionProperty
    # of filter_ui's rule type, and Blender needs that type registered first.
    filter_ui.register()
    _browser.register()
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _browser.unregister()
    filter_ui.unregister()
