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

try:
    from . import (Game, armature_builder, cross_game_retarget, filter_ui, material_builder,
                   prefab_importer, step_loader)
    from .RuriRipperPyBridge.runtime import bootstrap, pythonnet_bridge, workspace
    from .RuriRipperPyBridge.session import cabmap_state
    from .RuriRipperPyBridge.unity import (bridge_asset_db, class_registry, clip_paths, discovery,
                                          texture_roles)
except ImportError:  # standalone (non-package) testing
    import Game
    import armature_builder
    import cross_game_retarget
    import filter_ui
    import material_builder
    import prefab_importer
    import step_loader
    from RuriRipperPyBridge.runtime import bootstrap, pythonnet_bridge, workspace
    from RuriRipperPyBridge.session import cabmap_state
    from RuriRipperPyBridge.unity import (bridge_asset_db, class_registry, clip_paths, discovery,
                                          texture_roles)

# The only tab this module owns, because it is the only one that is not about a
# game: the cabmap itself. Every other tab is contributed by a game module (see
# Game) and shows up exactly while the current tab's install IS that game -- which
# is why nothing here names a game or knows how many tabs exist.
BROWSER_TAB_ID = "assetbundle"
BROWSER_TAB_LABEL = "VirtualAssetBundle"
# 后处理是**宿主级**的:它占的是 scene.compositing_node_group / view transform,
# 与哪个游戏在浏览无关 —— 所以它不是任何游戏的 GameTab,而是装了后处理栈就出现的一格。
# 后处理是**宿主级**的:它占的是 scene.compositing_node_group / view transform,
# 与哪个游戏在浏览无关 —— 所以它不是任何游戏的 GameTab,而是装了后处理栈就出现的一格。
BROWSER_TAB_DESCRIPTION = "Browse/search the loaded cabmap's rows and import individual assets"
_SORT_COLUMNS =(("name", "Name"), ("type_names", "Type"), ("deps", "Deps"), ("source", "Source"))

# What the browser's rows can be filtered by, straight off cabmap_state's own field
# table -- the C# engine derives a row's value for each of these names, so this list
# is a view of that table, never a second copy of it.
BROWSER_FILTER_SPEC = filter_ui.register_spec(filter_ui.FilterSpec(
    key=BROWSER_TAB_ID,
    fields=tuple((f, cabmap_state.FIELD_LABELS[f]) for f in cabmap_state.FILTER_FIELDS),
    state_for=lambda context: context.scene.ruri_cabmap,
    apply=lambda context: _reapply_and_refresh(context),
    # Deps is a count, so a one-click rule off a row means that exact number.
    quick_relation_for=lambda field: "is" if field == "deps" else "contains"))


def _add_file_item(state, selected_cabs, idx, name_override=None):
    row = cabmap_state.ROWS[idx]
    item = state.window.add()
    item.is_folder = False
    item.row_index = idx
    item.cab = row["cab"]
    item.name = row["name"] if name_override is None else name_override
    item.container = row["container"]
    item.type_names = row["type_names"]
    item.source = row["source"]
    item.deps = row["deps"]
    item.selected = row["cab"] in selected_cabs
    return item


def _rebuild_window(state):
    """Materialize state.window for whichever view is active. Search/rule
    results (has_active_query) stay the flat list this always was; otherwise
    this is the folder browser: CURRENT_SUBFOLDERS first (folders always
    sort before files, like a real file browser), then CURRENT_DIR's own
    files -- both share ONE DISPLAY_CAP budget so state.window never grows
    past the size the original flat-only list was already tuned for.

    The highlighted row is restored by its CAB afterwards (see
    filter_ui.restore_selection): the active index is a position into this
    window, and a search edit or a folder change would otherwise leave it
    pointing at whatever asset happened to land there."""
    with filter_ui.rebuilding():
        _fill_window(state)


def _offscreen_selection_note(state):
    """What the import will actually read that this view is NOT showing.

    The selection is a set of CABs and survives a search edit on purpose (a key the
    filter hides stays picked). Silently, though, that reads as a bug: search idle,
    pick a clip, search dead, press Import -- and idle is what comes out, with
    nothing on screen saying so. So the count of hidden-but-selected rows rides on
    the status line."""
    hidden = len(cabmap_state.SELECTED_CABS - {item.cab for item in state.window})
    if not hidden:
        return ""
    return (f"  --  {hidden} selected row(s) are hidden by this view; "
            f"Import/Read still acts on them (clear the search to see them)")


def _fill_window(state):
    state.window.clear()
    selected = cabmap_state.SELECTED_CABS
    # The one funnel every navigation path already runs through (enter dir, breadcrumb
    # jump, jump-to-row's folder, Build/Load) -- record the browsed folder here rather
    # than at each of those call sites, so no future navigation can forget to.
    state.browsed_dir = cabmap_state.dir_to_key()

    if cabmap_state.has_active_query(state.search, state.filter_rules):
        total, window = cabmap_state.display_window()
        for idx, _row in window:
            _add_file_item(state, selected, idx)
        shown = len(window)
        cap_note = (f" (capped at {cabmap_state.DISPLAY_CAP} -- narrow your search to see the rest)"
                    if total > shown else "")
        state.status = (f"Showing {shown} / {total} matching virtual files{cap_note}."
                        + _offscreen_selection_note(state))
        filter_ui.restore_selection(state, state.cursor_cab, "window", "active_index", "cab")
        return

    folders = cabmap_state.CURRENT_SUBFOLDERS
    budget = cabmap_state.DISPLAY_CAP
    shown_folders = folders[:budget]
    for folder_name, file_count in shown_folders:
        item = state.window.add()
        item.is_folder = True
        item.folder_name = folder_name
        item.file_count = file_count

    files = cabmap_state.VISIBLE
    shown_files = files[:max(0, budget - len(shown_folders))]
    for idx in shown_files:
        _add_file_item(state, selected, idx, name_override=cabmap_state.leaf_name_in_current_dir(idx))

    total_items = len(folders) + len(files)
    shown_items = len(shown_folders) + len(shown_files)
    cap_note = (f" (showing the first {budget} of {total_items} items -- open a subfolder to narrow down)"
                if total_items > shown_items else "")
    path_label = "/" + "/".join(cabmap_state.CURRENT_DIR)
    state.status = (f"{path_label}  --  {len(folders)} folder(s), {len(files)} file(s){cap_note}"
                    + _offscreen_selection_note(state))
    filter_ui.restore_selection(state, state.cursor_cab, "window", "active_index", "cab")


def _sync_window_selection(state):
    """Refresh only the per-row selection flags of the already-materialized
    window -- selection changes must not pay the full window rebuild."""
    selected = cabmap_state.SELECTED_CABS
    for item in state.window:
        item.selected = item.cab in selected


def _redraw_all(context):
    screen = getattr(context, "screen", None)
    for area in (screen.areas if screen else []):
        area.tag_redraw()


def _reapply_and_refresh(context):
    """Re-run whichever view is active -- the flat quick-search+Include/
    Exclude-rule results, or the folder listing for CURRENT_DIR -- and
    rebuild the displayed window. Call after ANY rule or search change, or a
    fresh Build/Load."""
    state = context.scene.ruri_cabmap
    cabmap_state.refresh_visible(state.search, state.filter_rules)
    _rebuild_window(state)
    _redraw_all(context)


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


def _schedule_filter(query, on_ready):
    global _pending_query, _last_edit_time, _timer_registered

    _pending_query = query
    _last_edit_time = time.monotonic()
    if _timer_registered:
        return
    _timer_registered = True

    def _tick():
        global _timer_registered
        if time.monotonic() - _last_edit_time < cabmap_state.SEARCH_DEBOUNCE_SECONDS:
            return 0.05
        try:
            # Keeps whatever Include/Exclude rules are currently active.
            cabmap_state.reapply_filter(_pending_query)
            on_ready()
        finally:
            # MUST run even if the filter or the callback raises -- this flag is
            # the only thing _schedule_filter checks before registering a new
            # timer, so leaving it True after an exception would silently
            # disable search for the rest of the session (every later keystroke
            # would just update _pending_query and register nothing).
            _timer_registered = False
        return None  # unregister this timer

    bpy.app.timers.register(_tick, first_interval=0.05)


def _on_search_edit(self, context):
    _schedule_filter(self.search,
                     lambda: (_rebuild_window(context.scene.ruri_cabmap), _redraw_all(context)))


# game root -> the identity that install published about itself, as the ONE player
# the install IS (RipperBlenderBridge.ReadInstall). Cached: an install does not
# rename itself mid-session, and this is read where a folder is typed, never from a
# draw callback -- crossing the CLR boundary while Blender paints is not allowed.
_INSTALL_IDENTITY = {}


def _install_identity(root, source_options=None):
    """What the install at ``root`` says it is: {"company", "product", "game_version",
    "engine_version", "engine"}, or None for a folder that is no install of any engine
    the kernel probes (or before the DLL is up).

    Two small files per player and nothing else for a Unity build -- see
    pythonnet_bridge.read_install; another engine's build is read by that engine's own
    probe, which may need this tab's source options (an archive key) to open it. A
    build whose engine assets are not plain still names itself; it just reports no
    engine version, which is a fact about that install, not a failure."""
    root = str(root or "")
    if not root:
        return None
    if root not in _INSTALL_IDENTITY:
        try:
            players = pythonnet_bridge.read_install(root, source_options)
        except Exception:
            return None  # DLL not up yet -- do NOT cache, or one early miss would stick
        project = next((player for player in players if player["is_project"]), None)
        _INSTALL_IDENTITY[root] = project
    return _INSTALL_IDENTITY[root]


def _module_of(config):
    """The game module this tab's install is claimed by -- by the product it
    published, else by the engine family it runs on -- or None."""
    if config is None:
        return None
    return Game.module_for(config.game_name, config.engine_family)


def _module_game_name(config):
    """WHICH GAME (upstream GameType member) this tab's install is read as: the
    module's own name when a module claims it (a family module names the family),
    else the product the install published."""
    module = _module_of(config)
    if module is not None:
        return module.game_name
    return config.game_name if config is not None else ""


def _source_options(config):
    """This tab's source options as a dict -- how its install is read beyond its
    folder, stored as JSON on the tab (see RURI_PG_install_config.source_options)."""
    if config is None or not config.source_options:
        return {}
    try:
        loaded = json.loads(config.source_options)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def set_source_options(config, options):
    """Store ``options`` on the tab and forget the cached identity, so the next probe
    reads the install with them (an archive key can be what makes it readable)."""
    config.source_options = json.dumps(options or {}, sort_keys=True)
    _INSTALL_IDENTITY.pop(bpy.path.abspath(config.game_root) if config.game_root else "", None)


def _game_tabs(state):
    """The content tabs of the game the CURRENT TAB's install is -- one install's,
    not every open tab's. Each install has its own browser tab, so drawing the union
    would stack two games' Scene/Character tabs into one row."""
    config = _active_config(state)
    return Game.tabs_of(config.game_name, config.engine_family) if config is not None else []


def _active_tab(state):
    """The game tab actually being shown, or None for the browser.

    The stored tab only counts while its own game is still the one in front of the
    panel -- pointing a tab at a different install must take its tabs away rather
    than leave the panel drawing a game that is no longer there. Read-only, so a
    draw callback can call it."""
    for tab in _game_tabs(state):
        if tab.key == state.active_tab:
            return tab
    return None


def _tab_bar(state):
    """(key, label) for every content tab currently offered: the browser, then the
    current game's own."""
    entries = [(BROWSER_TAB_ID, BROWSER_TAB_LABEL)]
    entries.extend((tab.key, tab.label) for tab in _game_tabs(state))
    return entries


def _active_game_name(state):
    """WHICH GAME the browser's current install is, as the upstream GameType member
    -- read straight off the live cabmap_state session (which the panel keeps pinned
    to the current tab), so an import stamps the game it was actually browsed under.
    "" for an install no game module claims. NOT the tab's own key: two installs of
    one title are two tabs and one game."""
    return cabmap_state.active_game() or ""


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
UNNAMED_TAB_PREFIX = "unknown"


def _find_config(state, key):
    for config in state.games:
        if config.key == key:
            return config
    return None


def _active_config(state):
    """The config entry the browser is currently on, or None -- read-only, so a
    draw/getter never mutates the collection."""
    return _find_config(state, state.current_tab)


def _tab_keys(state):
    return [config.key for config in state.games]


def _open_tab_keys(state):
    """Every open tab in tab order, for a READ-ONLY caller (the draw). A fresh scene
    has no config entry yet and its current_tab default stands in for one, so the tab
    metaphor is never absent and drawing never has to create anything -- the entry
    itself appears the moment the user writes to that tab (_ensure_active_config)."""
    keys = _tab_keys(state)
    return keys if state.current_tab in keys else keys + [state.current_tab]


def _unique_tab_key(state, wanted, held_by_key=None):
    """``wanted``, or ``wanted_2`` / ``wanted_3`` / ... when another tab already holds
    it. Two installs of the same title are a real case (a repack beside a vanilla
    copy), and two tabs sharing a key would share a session and a cabmap slot.

    A numbered key therefore means TWO DIFFERENT FOLDERS -- pointing a second tab at
    a folder some tab already has is not a second install and never gets one (see
    _tab_on_root)."""
    # By KEY, never by object identity: bpy hands back a fresh PropertyGroup wrapper
    # on every access, so `config is held_by` is False even for the very same entry --
    # which is how re-typing a tab's own folder used to mint it a "<name>_2".
    taken = {config.key for config in state.games if config.key != held_by_key}
    if wanted not in taken:
        return wanted
    ordinal = 2
    while "{0}_{1}".format(wanted, ordinal) in taken:
        ordinal += 1
    return "{0}_{1}".format(wanted, ordinal)


def _tab_on_root(state, root, excluding_key=None):
    """The open tab already pointed at ``root``, or None. An install is a FOLDER, so
    two tabs on one folder are one install listed twice -- and the second would mint
    a "<name>_2" key that reads as a different copy of the game when it is the very
    same one."""
    for config in state.games:
        if config.key == excluding_key or not config.game_root:
            continue
        if os.path.normcase(os.path.normpath(bpy.path.abspath(config.game_root))) == root:
            return config
    return None


def _next_unnamed_key(state):
    """The next free ``unknown_N`` -- the name a tab carries until its folder names
    it. Numbered, because a tab exists before it is pointed anywhere and every tab
    needs an identity of its own from the first frame; without one, a second unnamed
    tab would collide with the first and simply refuse to open."""
    taken = set(_tab_keys(state))
    ordinal = 1
    while "{0}_{1}".format(UNNAMED_TAB_PREFIX, ordinal) in taken:
        ordinal += 1
    return "{0}_{1}".format(UNNAMED_TAB_PREFIX, ordinal)


def _ensure_tab(state, key):
    """The one place a browser tab is created. It is created EMPTY -- a tab has no
    identity beyond the folder its user points it at."""
    config = _find_config(state, key)
    if config is None:
        config = state.games.add()
        config.key = key
    return config


def _ensure_active_config(state):
    return _ensure_tab(state, state.current_tab or _next_unnamed_key(state))


def _set_current_tab(state, key):
    """Point the browser at install ``key`` and pin the cabmap_state session to
    match, so ROWS/search/import all read that install -- and tell it which decoder
    that install is, which is what a game-specific table is later selected by."""
    state.current_tab = key or ""
    config = _find_config(state, key)
    cabmap_state.activate(key or None, _module_game_name(config))


def _switch_current_tab(state, key, context):
    changed = state.current_tab != (key or "")
    _set_current_tab(state, key)
    if changed:
        _reapply_and_refresh(context)


def _rename_tab(state, config, new_key):
    """Give a tab its real name once the folder states one, carrying the browser
    session and the loaded cabmap over with it (cabmap_state.rename) -- a tab that
    learns its name does not re-read a map it already paid for."""
    old_key = config.key
    if old_key == new_key:
        return
    cabmap_state.rename(old_key, new_key)
    config.key = new_key
    if state.current_tab == old_key:
        state.current_tab = new_key


def _open_tab(state, key, context):
    """Open a fresh unnamed tab and switch to it. The next folder typed into it is
    what names it."""
    _ensure_tab(state, key or _next_unnamed_key(state))
    _switch_current_tab(state, key or _tab_keys(state)[-1], context)


def _close_tab(state, key, context):
    """Close one tab -- remove its scene config entry, release its browser session,
    and hand focus to a remaining tab (a fresh unnamed one when none is left, so the
    panel is never tabless)."""
    for index, config in enumerate(state.games):
        if config.key == key:
            state.games.remove(index)
            break
    cabmap_state.drop(key or None)
    remaining = _tab_keys(state)
    if state.current_tab == key or not remaining:
        _set_current_tab(state, remaining[0] if remaining
                         else _ensure_tab(state, _next_unnamed_key(state)).key)
    _reapply_and_refresh(context)


_FILENAME_UNSAFE = re.compile(r'[\\/:*?"<>|]')


def _default_cabmap_filename(key):
    """A cabmap is named after the INSTALL it maps -- the product name the build
    itself carries, which is this tab's key ("HoneyCome.cabmap"). Not after the
    decoder: which decoder read it is a passing state of the session, while the map
    is a fact about that folder, and naming it after the decoder meant a map built
    before the install was identified landed as a nondescript "output.cabmap"."""
    return _FILENAME_UNSAFE.sub("_", key).strip() + ".cabmap"


def _auto_default_cabmap_filename(state):
    """If state.cabmap_path is a non-empty path that (still) resolves to a bare folder, fill in the
    default filename -- writes straight onto state.cabmap_path so
    it's what's shown in the field AND what Blender's file-browser popup pre-fills/lets you edit
    the next time the user clicks its folder icon (that browser seeds its filename box from the
    property's CURRENT string value, so the default has to already be in the property before the
    popup opens, not just patched in at Build time). A completely empty cabmap_path is left
    alone here -- there's no folder yet to build a default INTO (see _on_game_root_set, which
    seeds one first)."""
    raw = bpy.path.abspath(state.cabmap_path) if state.cabmap_path else ""
    if raw and state.current_tab and (raw.endswith(("\\", "/")) or os.path.isdir(raw)):
        state.cabmap_path = os.path.join(raw, _default_cabmap_filename(state.current_tab))


def _resolve_build_output_path(state):
    """Resolve state.cabmap_path into a concrete output FILE path for Build -- belt-and-suspenders
    on top of _auto_default_cabmap_filename (which keeps the field itself defaulted as the user
    goes) in case cabmap_path still ends up bare (e.g. typed/pasted a folder right before
    clicking Build, with no chance for the update callback to run in between). Returns "" if
    there's truly nothing to build a path from."""
    _auto_default_cabmap_filename(state)
    return bpy.path.abspath(state.cabmap_path) if state.cabmap_path else ""


def _report_exception(op, prefix, exc):
    """self.report() truncates to one line and str(exc) alone drops the
    exception type + traceback -- print the full traceback to console (where
    it's actually diagnosable) and surface a short, still-useful summary in
    Blender's status bar / info log."""
    traceback.print_exc()
    op.report({"ERROR"}, f"{prefix}: {type(exc).__name__}: {exc} (full traceback in console)")


class RURI_PG_cabmap_row(bpy.types.PropertyGroup):
    """One windowed/displayed row -- a small proxy, never the full 260k-row set.
    `selected` is a pure display MIRROR of cabmap_state.SELECTED_CABS (the
    authoritative selection, which survives the window being rebuilt on every
    filter/sort edit) -- all mutation goes through RURI_OT_cabmap_click /
    RURI_OT_cabmap_select_all, never by writing this flag directly.

    Does double duty as a virtual FOLDER entry too (is_folder=True):
    RURI_UL_cabmap.draw_item branches on it, so the folder browser and the
    flat search/rule results share one CollectionProperty, one UIList, and
    one template_list -- only folder_name/file_count are meaningful on a
    folder row, only the fields below are meaningful on a file row."""
    is_folder: BoolProperty(default=False)
    folder_name: StringProperty()
    file_count: IntProperty()  # recursive file count under a folder row; unused on a file row
    row_index: IntProperty()
    cab: StringProperty()
    name: StringProperty()
    container: StringProperty()
    type_names: StringProperty()
    source: StringProperty()
    deps: IntProperty()
    selected: BoolProperty(default=False)


# Every decoder compiled into the loaded DLL, as (product, version, engine_version),
# newest version first within a product. Read ONCE per process: the set is compiled
# in and pythonnet loads that DLL once, for good -- this is not a cache of something
# that might move, it is the one reading there is. A miss (no bin dir yet, install
# still running) is deliberately not cached, so one early call cannot empty the menu
# for the session.
_DECODERS = None


def _decoders():
    global _DECODERS
    if _DECODERS is not None:
        return _DECODERS
    try:
        _DECODERS = pythonnet_bridge.list_decoders()
    except Exception:
        return []
    return _DECODERS


def _decoders_of(product):
    """One product's decoders, newest first -- exactly the choices a tab may pick from."""
    wanted = (product or "").lower()
    return [entry for entry in _decoders() if entry[0].lower() == wanted]


def _decoder_id(entry):
    """A decoder's id is its product and its version, joined -- the same string the
    kernel builds, so nothing here parses one apart."""
    return "{0}_{1}".format(entry[0], entry[1])


def _resolve_decoder(identity):
    """Which decoder the KERNEL picks for this identity (HookCatalog.Resolve), or ""
    when the product ships none -- a plain Unity build is read by the generic path.
    Asked here and nowhere else, so the panel never re-implements the rule."""
    try:
        return pythonnet_bridge.resolve_decoder(
            identity["product"], identity["game_version"], identity["engine_version"],
            identity.get("engine", ""))
    except Exception:
        return ""


def _adopt_identity(state, config):
    """Read what the tab's folder says it is and set the tab from it: which product,
    which engine version, and which decoder reads that.

    This is the ONE place a tab learns its game. It runs where a folder is typed and
    where the user asks to re-read one -- never from a draw, which may neither cross
    the CLR boundary nor write scene data. A folder that is no Unity install (or a
    DLL that is not up yet) clears the identity rather than keeping a stale one."""
    identity = _install_identity(bpy.path.abspath(config.game_root) if config.game_root else "",
                                 _source_options(config))
    if identity is None:
        config.game_name = ""
        config.game_version = ""
        config.engine_version = ""
        config.engine_family = ""
        config.decoder_id = ""
        return ""
    config.game_name = identity["product"]
    config.game_version = identity["game_version"]
    config.engine_version = identity["engine_version"]
    config.engine_family = identity.get("engine", "")
    _decoders()
    config.decoder_id = _resolve_decoder(identity)
    # The form the install is read with, loaded now rather than on a click: its rows
    # carry which options are required, and the warning for an unset one has to be up
    # from the moment the tab knows its decoder.
    module = _module_of(config)
    if module is not None and module.settings_schema and bootstrap.is_ready():
        try:
            _load_source_option_rows(config, module.settings_schema)
        except Exception as exc:
            print("[RuriRipper] options form for {0} not loaded: {1}: {2}".format(
                config.key, type(exc).__name__, exc))
    return config.decoder_id


class RURI_OT_set_decoder(bpy.types.Operator):
    """Read THIS tab's install through one named decoder, overriding what its identity
    resolved to. Only this tab moves: another tab's install keeps its own decoder and
    its own loaded cabmap (the bridge switches between them per install)."""

    bl_idname = "ruri.set_decoder"
    bl_label = "Decoder"
    bl_options = {"INTERNAL"}
    decoder_id: StringProperty()

    def execute(self, context):
        state = context.scene.ruri_cabmap
        config = _ensure_active_config(state)
        config.decoder_id = self.decoder_id
        _set_current_tab(state, config.key)
        _redraw_all(context)
        return {"FINISHED"}


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
            layout.operator(RURI_OT_set_decoder.bl_idname,
                            text=label).decoder_id = _decoder_id(entry)
        layout.separator()
        layout.operator(RURI_OT_set_decoder.bl_idname,
                        text="None (plain Unity build)").decoder_id = ""


def _rows_to_options(config):
    """The form's current values as {name: value}, every kind restated as the text the
    kernel carries (a flag as "true"/"false", a path as typed)."""
    options = {}
    for row in config.source_option_rows:
        if row.kind == "flag":
            options[row.name] = "true" if row.flag_value else "false"
        elif row.kind == "path":
            options[row.name] = bpy.path.abspath(row.path_value) if row.path_value else ""
        else:
            options[row.name] = row.value
    return options


def _options_to_rows(config, options):
    for row in config.source_option_rows:
        value = options.get(row.name, row.default)
        if row.kind == "flag":
            row.flag_value = str(value).lower() in ("1", "true")
        elif row.kind == "path":
            row.path_value = value
        else:
            row.value = value


def _fill_source_option_rows(config, table, dataset_id):
    """The decoder's published schema becomes the tab's form rows, keeping the values the
    tab already stores; a schema that states which options are required marks them."""
    current = _source_options(config)
    config.source_option_rows.clear()
    names = set(table.names)
    for index in range(len(table)):
        row = config.source_option_rows.add()
        row.name = table.cell(index, "name")
        row.kind = table.cell(index, "kind")
        row.choices = table.cell(index, "choices")
        row.default = table.cell(index, "default")
        row.description = table.cell(index, "description")
        row.required = "required" in names and str(table.cell(index, "required")) == "1"
    config.source_option_schema = dataset_id
    _options_to_rows(config, current)


def _load_source_option_rows(config, dataset_id):
    """Read the decoder's schema for this tab's install and fill the form from it -- what
    the Load Options Form button does, run for a tab the moment its decoder resolves."""
    root = bpy.path.abspath(config.game_root) if config.game_root else ""
    bridge = cabmap_state.ensure_bridge(config.decoder_id, root, _source_options(config))
    _fill_source_option_rows(config, bridge.game_data(dataset_id), dataset_id)


def _missing_required_options(config):
    """The required rows the tab has no value for: a flag is never missing, a path or a
    text is missing while blank."""
    missing = []
    for row in config.source_option_rows:
        if not row.required or row.kind == "flag":
            continue
        value = row.path_value if row.kind == "path" else row.value
        if not (value or "").strip():
            missing.append(row)
    return missing


class RURI_OT_load_source_option_schema(bpy.types.Operator):
    """Read which values this install is READ with off its decoder -- the decoder
    publishes them as a dataset -- and lay the form out from that. Needs the decoder
    applied, which is what the bridge does for this tab's install; a decoder that
    publishes no such dataset leaves the form empty."""

    bl_idname = "ruri.load_source_option_schema"
    bl_label = "Load Options Form"
    bl_options = {"INTERNAL"}
    dataset_id: StringProperty()

    @classmethod
    def poll(cls, context):
        return bootstrap.is_ready()

    def execute(self, context):
        state = context.scene.ruri_cabmap
        config = _ensure_active_config(state)
        root = bpy.path.abspath(config.game_root) if config.game_root else ""
        try:
            bridge = cabmap_state.ensure_bridge(config.decoder_id, root, _source_options(config))
            table = bridge.game_data(self.dataset_id)
        except Exception as exc:
            _report_exception(self, "Load options form failed", exc)
            return {"CANCELLED"}
        _fill_source_option_rows(config, table, self.dataset_id)
        _redraw_all(context)
        return {"FINISHED"}


class RURI_OT_apply_source_options(bpy.types.Operator):
    """Push the form's values to the kernel and re-read the install under them -- an
    archive key can be what makes the install readable, so its identity and decoder
    are resolved again."""

    bl_idname = "ruri.apply_source_options"
    bl_label = "Apply"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return bootstrap.is_ready()

    def execute(self, context):
        state = context.scene.ruri_cabmap
        config = _ensure_active_config(state)
        set_source_options(config, _rows_to_options(config))
        try:
            decoder = _adopt_identity(state, config)
        except Exception as exc:
            _report_exception(self, "Apply options failed", exc)
            return {"CANCELLED"}
        if config.game_name:
            _rename_tab(state, config,
                        _unique_tab_key(state, config.game_name, held_by_key=config.key))
        _set_current_tab(state, config.key)
        _auto_default_cabmap_filename(state)
        self.report({"INFO"}, "{0} · {1}".format(config.game_name or "no identity", decoder or "no decoder"))
        _redraw_all(context)
        return {"FINISHED"}


class RURI_OT_set_source_option_choice(bpy.types.Operator):
    bl_idname = "ruri.set_source_option_choice"
    bl_label = "Choose"
    bl_options = {"INTERNAL"}
    option: StringProperty()
    choice: StringProperty()

    def execute(self, context):
        config = _ensure_active_config(context.scene.ruri_cabmap)
        for row in config.source_option_rows:
            if row.name == self.option:
                row.value = self.choice
        _redraw_all(context)
        return {"FINISHED"}


class RURI_MT_source_option_choice(bpy.types.Menu):
    """The choices of ONE choice-kind option, read off the row the panel put in
    ``bl_label`` position -- Blender menus take no arguments, so the option's name
    rides on the window manager."""

    bl_idname = "RURI_MT_source_option_choice"
    bl_label = "Choice"

    def draw(self, context):
        config = _active_config(context.scene.ruri_cabmap)
        wanted = context.window_manager.get("ruri_source_option_menu", "")
        row = next((r for r in config.source_option_rows if r.name == wanted), None) if config else None
        if row is None:
            return
        column = self.layout.column()
        for choice in [c for c in row.choices.split("|") if c]:
            op = column.operator(RURI_OT_set_source_option_choice.bl_idname, text=choice)
            op.option = row.name
            op.choice = choice


class RURI_OT_open_source_option_choice(bpy.types.Operator):
    bl_idname = "ruri.open_source_option_choice"
    bl_label = "Choose"
    bl_options = {"INTERNAL"}
    option: StringProperty()

    def execute(self, context):
        context.window_manager["ruri_source_option_menu"] = self.option
        bpy.ops.wm.call_menu(name=RURI_MT_source_option_choice.bl_idname)
        return {"FINISHED"}


def draw_source_options(layout, context, config, schema_dataset_id):
    """The generic form for how an install is READ: rows of the decoder's published
    schema, one widget per kind, a Load button while the form is unknown, an Apply
    button that pushes the values and re-reads the install. A game module names only
    WHICH dataset states its schema."""
    if not config.source_option_rows or config.source_option_schema != schema_dataset_id:
        pending = layout.box()
        pending.alert = True
        pending.label(text="How this install is read is not loaded yet: the values it cannot be "
                           "read without (a reflection schema, archive keys) are set in this form.",
                      icon="ERROR")
        op = layout.operator(RURI_OT_load_source_option_schema.bl_idname, icon="PREFERENCES")
        op.dataset_id = schema_dataset_id
        return
    missing = _missing_required_options(config)
    for row in missing:
        warning = layout.box()
        warning.alert = True
        warning.label(text="Required and not set: {0}".format(row.name), icon="ERROR")
        warning.label(text=row.description)
    missing_names = {row.name for row in missing}
    box = layout.column(align=True)
    for row in config.source_option_rows:
        line = box.row(align=True)
        line.alert = row.name in missing_names
        if row.kind == "flag":
            line.prop(row, "flag_value", text=row.name)
        elif row.kind == "path":
            line.prop(row, "path_value", text=row.name)
        elif row.kind == "choice":
            line.label(text=row.name)
            op = line.operator(RURI_OT_open_source_option_choice.bl_idname,
                               text=row.value or "(auto: {0})".format(row.default) if row.default else row.value or "(auto)")
            op.option = row.name
        else:
            line.prop(row, "value", text=row.name)
    layout.operator(RURI_OT_apply_source_options.bl_idname, icon="CHECKMARK")


class RURI_PG_animation_clip(bpy.types.PropertyGroup):
    """One discovered-but-not-yet-built animation clip -- see
    discovery.discover_clip_refs. `selected` drives the
    checkbox in RURI_UL_animation_clips; nothing here has been parsed past a
    cheap name/size peek, so ticking a box is free until Import is clicked.

    `folder` is the game's own folder for that clip, which is what a list of a
    few hundred clips is worth reading by; `visible` is the filter's verdict on
    this row. The filter HIDES rather than removes, so a clip checked before the
    box was typed into is still checked -- and still imported -- afterwards."""
    guid: StringProperty()
    name: StringProperty()
    folder: StringProperty()
    size_bytes: IntProperty()
    selected: BoolProperty(default=False)
    visible: BoolProperty(default=True)


# The handle the discovered-clip list is published under so it can be searched by
# the same engine as everything else (see _apply_animation_filter). One handle:
# re-publishing replaces, which is exactly what a re-discovery wants.
_ANIMATION_TABLE_HANDLE = "ruri.animation_browser"


def _apply_animation_filter(state):
    """Decide which discovered clips the list shows.

    The query runs through the SAME vectorized C# engine every other list here
    searches with: the discovered clips are published as a host table
    (open_host_table) and matched there, rather than scanned with a Python
    substring test that would quietly mean something different from "contains"
    in the browser next door."""
    query = state.animation_search.strip()
    if not query or cabmap_state.BRIDGE is None:
        for item in state.available_clips:
            item.visible = True
        return
    try:
        handle = cabmap_state.BRIDGE.open_host_table(
            _ANIMATION_TABLE_HANDLE, ("name", "folder"),
            [(item.name, item.folder) for item in state.available_clips])
        matched = {int(row) for row in cabmap_state.BRIDGE.search_data_table(handle, query, None)}
    except Exception as exc:
        print(f"[RuriRipper] animation filter failed: {exc}")
        return
    for index, item in enumerate(state.available_clips):
        item.visible = index in matched


def _on_animation_search(self, context):
    _apply_animation_filter(self)


def _clip_folder(path):
    """The folder part of a clip's own container/export path, which is what the
    list groups by. Empty for a clip discovered with no path known yet."""
    return (path or "").rpartition("/")[0]


def _format_size(num_bytes):
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"~{size:.0f}{unit}" if unit == "B" else f"~{size:.1f}{unit}"
        size /= 1024.0
    return f"~{size:.1f}GB"


def _get_game_root(self):
    config = _active_config(self)
    return config.game_root if config is not None else ""


def _set_game_root(self, value):
    config = _ensure_active_config(self)
    config.game_root = value
    _on_game_root_set(self)


def _get_cabmap_path(self):
    config = _active_config(self)
    return config.cabmap_path if config is not None else ""


def _set_cabmap_path(self, value):
    _ensure_active_config(self).cabmap_path = value


def _get_browsed_dir(self):
    config = _active_config(self)
    return config.browsed_dir if config is not None else ""


def _set_browsed_dir(self, value):
    _ensure_active_config(self).browsed_dir = value


def _get_loaded(self):
    """Whether THIS TAB's cabmap is loaded right now -- DERIVED from the live
    session, never stored.

    A loaded cabmap is process state: a bridge, a decoder and a row table, none of
    which can be written into a .blend. A scene property can, and a stored flag
    therefore comes back True in a session that has none of them -- every gate that
    trusts it then opens onto ``cabmap_state.BRIDGE`` being None, which is not a
    check any caller can be expected to repeat. Reading the session makes the flag
    mean the same thing in the first draw after opening a file as it did when it
    was set."""
    config = _active_config(self)
    if config is None:
        return False
    session = cabmap_state.SESSIONS.get(config.key)
    return session is not None and len(session.ROWS) > 0


def _seed_cabmap_default(state):
    """Fill the current tab's Cabmap with a default the first time it has a root but no
    cabmap yet: the folder, then _auto_default_cabmap_filename appends a filename built
    from the current game's hook(s) -- so Blender's file-browser popup (opened from the
    Cabmap folder icon) already has a filename pre-filled, since that popup seeds its
    filename box from the property's current string and cannot be told a default
    separately. A tab that already carries a cabmap (typed, or a loaded map) is left
    alone, and a loaded tab is never touched at all."""
    config = _active_config(state)
    if config is None or state.loaded:
        return
    if not config.cabmap_path and config.game_root:
        config.cabmap_path = config.game_root
    _auto_default_cabmap_filename(state)


def _on_game_root_set(state):
    """Runs at the tail of Game Root's setter (a get/set property gets no separate
    update callback). The folder that was just typed IS the statement of which install
    this tab is, so everything else follows from it here and nowhere else: the tab
    reads the identity the build publishes (_adopt_identity), takes the install's own
    productName as its name while keeping its session and any loaded cabmap
    (_rename_tab), and gets its Cabmap field seeded from that name.

    A folder that publishes no identity leaves the tab on its unknown_N key with no
    decoder, which is a usable state -- a plain Unity build needs none -- not an error.

    Nothing moves between tabs: a tab is the install in front of it -- and when that
    install is already open on another tab, this one gives the folder back and the
    browser goes there, rather than minting a second identity for one folder."""
    config = _active_config(state)
    if config is None:
        return
    root = bpy.path.abspath(config.game_root) if config.game_root else ""
    _INSTALL_IDENTITY.pop(root, None)
    if root:
        already = _tab_on_root(state, os.path.normcase(os.path.normpath(root)),
                              excluding_key=config.key)
        if already is not None:
            config.game_root = ""
            _set_current_tab(state, already.key)
            return
    _adopt_identity(state, config)
    if config.game_name:
        _rename_tab(state, config,
                    _unique_tab_key(state, config.game_name, held_by_key=config.key))
    _set_current_tab(state, config.key)
    _seed_cabmap_default(state)


# --- texture roles: which property is which input, as the user states it ---------
# The role table is DATA in layers (RuriRipperPyBridge.unity.texture_roles): the default
# layer is Unity's own vocabulary, a title module's folder holds that game's, and what the
# user decides in front of the names no layer states goes into the title's folder when
# the install has one, else into the workspace under the game's own name.

_TEXTURE_ROLES_WORKSPACE_DIR = "texture_roles"
_UNMAPPED_ROLE = "unmapped"
_ROLE_ITEMS = [(_UNMAPPED_ROLE, "(choose)", "Not decided yet")] + [
    (role, label, description) for role, label, description in texture_roles.ROLES]
_CHANNEL_ITEMS = [(str(index), name, "Channel " + name)
                  for index, name in enumerate(texture_roles.CHANNEL_NAMES)]


def _texture_role_layers(state):
    """(game, game layer, user layer, save target) for the current install. A title module
    -- one declared for THIS product -- owns its layer and takes the user's choices; a
    family module or no module at all leaves them to the workspace."""
    game = _texture_role_game(state)
    module = _module_of(_active_config(state))
    game_layer = (os.path.join(module.directory, texture_roles.DEFAULT_LAYER_NAME)
                  if module is not None and module.directory else None)
    user_layer = os.path.join(workspace.subdir(_TEXTURE_ROLES_WORKSPACE_DIR),
                              (game or "unknown") + ".json")
    owns = (game_layer is not None and bool(game)
            and module.game_name.lower() == game.lower())
    return game, game_layer, user_layer, (game_layer if owns else user_layer)


def _texture_role_game(state):
    """The game a role layer is filed under: the PRODUCT the install published (a Unity
    game's productName, an Unreal project's name), not the family module that reads
    it -- two Unreal titles are two vocabularies, not one."""
    config = _active_config(state)
    product = (config.game_name if config is not None else "") or ""
    return product or _active_game_name(state)


def _texture_role_table(state):
    _game, game_layer, user_layer, _save = _texture_role_layers(state)
    return texture_roles.RoleTable.load(texture_roles.layer_paths(game_layer, user_layer))


def _sync_texture_roles(state):
    """Mirror the builder's unmapped names into the rows the panel draws, keeping the role
    the user already picked for a name still listed. Called where materials were just
    built -- a draw may not write these rows."""
    game = _texture_role_game(state)
    unresolved = material_builder.unresolved_for(game)
    chosen = {row.name: (row.role, row.channel) for row in state.texture_roles}
    state.texture_roles.clear()
    for name, entry in unresolved.items():
        row = state.texture_roles.add()
        row.name = name
        row.count = int(entry["count"])
        row.example = "{0} / {1}".format(entry["material"], entry["texture"])
        if name in chosen:
            row.role, row.channel = chosen[name]


class RURI_PG_texture_role(bpy.types.PropertyGroup):
    """One texture property name no layer of the role table states, met while building
    the current game's materials, with the role the user picks for it."""
    name: StringProperty()
    count: IntProperty()
    example: StringProperty()
    role: EnumProperty(name="Role", items=_ROLE_ITEMS, default=_UNMAPPED_ROLE)
    channel: EnumProperty(name="Channel", items=_CHANNEL_ITEMS, default="0")


class RURI_OT_texture_roles_refresh(bpy.types.Operator):
    """List the texture property names the last imports could not map"""
    bl_idname = "ruri.texture_roles_refresh"
    bl_label = "List Unmapped Textures"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        _sync_texture_roles(context.scene.ruri_cabmap)
        return {"FINISHED"}


class RURI_OT_texture_roles_save(bpy.types.Operator):
    """Write every decided role into this game's texture role layer; materials built from now on resolve through it"""
    bl_idname = "ruri.texture_roles_save"
    bl_label = "Save Texture Roles"

    def execute(self, context):
        state = context.scene.ruri_cabmap
        game, _game_layer, _user_layer, save_path = _texture_role_layers(state)
        entries = {row.name: texture_roles.entry_for(row.role, int(row.channel))
                   for row in state.texture_roles if row.role != _UNMAPPED_ROLE}
        if not entries:
            self.report({"WARNING"}, "No role chosen yet.")
            return {"CANCELLED"}
        texture_roles.save_entries(save_path, entries)
        material_builder.forget_unresolved(game, entries.keys())
        _sync_texture_roles(state)
        self.report({"INFO"}, "Saved {0} role(s) to {1}; re-import to rebuild the materials.".format(
            len(entries), save_path))
        return {"FINISHED"}


class RURI_PG_source_option(bpy.types.PropertyGroup):
    """One value an install is READ with, as the decoder declared it: name, kind
    (text|flag|choice|path|entries), the choices a choice kind offers ('|'-joined),
    its default and what it means. The form the host draws IS the decoder's published
    schema -- these rows are filled from that dataset, never authored here."""
    name: StringProperty()
    kind: StringProperty()
    value: StringProperty(name="Value")
    path_value: StringProperty(name="Path", subtype="FILE_PATH")
    flag_value: BoolProperty(name="Enabled")
    choices: StringProperty()
    default: StringProperty()
    description: StringProperty()
    # The mounted build cannot be read without this one (the decoder said so).
    required: BoolProperty(default=False)


class RURI_PG_install_config(bpy.types.PropertyGroup):
    """ONE INSTALL's browser inputs -- one open tab. Several can be set up at once
    (see RURI_PG_cabmap.games); the scalar game_root/cabmap_path/browsed_dir/loaded
    on RURI_PG_cabmap are a get/set VIEW onto whichever of these the browser is
    currently on (state.current_tab).

    ``key`` is WHICH INSTALL: the productName the build itself carries, or
    ``unknown_N`` for a folder that publishes none. It is the tab's label and the
    identity its browser session and cabmap slot are filed under. ``game_name`` is
    that same productName as the install PUBLISHED it -- two installs of one title
    are two tabs (two keys) sharing one game -- ``game_version`` is its own
    bundleVersion and ``engine_version`` the Unity version its serialized files
    state. All are read once, where the folder is typed (_adopt_identity), never
    re-derived.

    ``decoder_id`` is the ONE decoder this tab reads through: resolved from that
    identity by the kernel, replaceable per tab (RURI_MT_decoder), and empty for a
    plain Unity build that needs none."""
    key: StringProperty()
    game_name: StringProperty()
    game_version: StringProperty()
    engine_version: StringProperty()
    # The engine FAMILY the install runs on ("Unity", "UnrealEngine", ...), as the
    # kernel's probe reported it: what selects a family-wide decoder and the module
    # that draws this engine's panels when the product itself has neither.
    engine_family: StringProperty()
    decoder_id: StringProperty()
    game_root: StringProperty()
    cabmap_path: StringProperty()
    browsed_dir: StringProperty()
    # How this install is READ beyond its folder, as JSON {name: value}: whatever its
    # decoder declared it needs to open the files (an archive key, an engine version,
    # a schema file). Stated by the module that claims the install (its source_options
    # form), pushed to the kernel before every probe, build and load.
    source_options: StringProperty()
    # The form rows behind that JSON, filled from the decoder's own schema dataset.
    source_option_rows: CollectionProperty(type=RURI_PG_source_option)
    source_option_schema: StringProperty()


class RURI_PG_cabmap(filter_ui.FilterStateMixin, step_loader.LoadingState,
                     bpy.types.PropertyGroup):
    # Which filter spec this state belongs to -- see filter_ui.FilterStateMixin.
    FILTER_SPEC_KEY = BROWSER_TAB_ID

    # Per-INSTALL inputs live in `games`, one entry per open tab; the four scalars
    # below (game_root/cabmap_path/browsed_dir/loaded) are a get/set VIEW onto the
    # entry for current_tab, so every operator that reads state.game_root /
    # state.loaded keeps working unchanged while each tab keeps its own.
    games: CollectionProperty(type=RURI_PG_install_config)
    current_tab: StringProperty(default="{0}_1".format(UNNAMED_TAB_PREFIX))

    game_root: StringProperty(name="Game Root", subtype="DIR_PATH",
                              description="The game's install root directory -- typing one names "
                                          "this tab after the product the build calls itself",
                              get=_get_game_root, set=_set_game_root)
    cabmap_path: StringProperty(name="Cabmap", subtype="FILE_PATH",
                                description="Existing cabmap FILE to load, or output path to build one -- "
                                            "defaults to a filename built from the install's own name, editable",
                                get=_get_cabmap_path, set=_set_cabmap_path)
    # Read-only on purpose: it is an observation of the live session, so there is
    # nothing for a caller to set and no way for it to disagree with reality.
    loaded: BoolProperty(get=_get_loaded)
    # A plain string, not an EnumProperty: the tab set is whatever the current
    # install's game contributes, and Blender's dynamic-enum items callback stores an
    # index into a list that changes the moment the browser moves to another install
    # -- the stored value would then point at a different tab. A key nobody offers
    # any more simply falls back to the browser (see _active_tab).
    active_tab: StringProperty(default=BROWSER_TAB_ID)
    search: StringProperty(name="Search", update=_on_search_edit,
                           description="Filter by Name / Container / Source / Type")
    # The virtual folder the browser is in, as cabmap_state.dir_to_key's flat string.
    # Lives on the Scene (so it survives a .blend save) rather than in cabmap_state,
    # whose CURRENT_DIR is live session state -- reloading a cabmap then lands back on
    # this folder instead of dumping the user at the root every time. Per game (a
    # get/set view onto the active config), so each game reopens where it was left.
    browsed_dir: StringProperty(get=_get_browsed_dir, set=_set_browsed_dir)
    status: StringProperty(default="No cabmap loaded.")
    window: CollectionProperty(type=RURI_PG_cabmap_row)
    active_index: IntProperty()
    # WHICH row the cursor is on, by identity. active_index is a position into a
    # filtered, capped window, so a search edit or a folder change moves what sits
    # under it; this is what the window is re-pointed at afterwards, and it
    # survives the row being filtered out and coming back.
    cursor_cab: StringProperty()

    # Row column widths (RURI_UL_cabmap.draw_item), freely draggable like any
    # Blender slider -- template_list has no native drag-resizable column
    # headers the way the WinForms reference's ListView does (MainForm.
    # AssetList.cs: Columns.Add("Name", 240)/("Container", 320)/("Type", 150)/
    # ("Source", 200)/("Deps", 50)), so this is the Blender-native substitute.
    # Each factor is relative to the space LEFT after the previous column
    # (UILayout.split() semantics, nested). Container/Type/Deps keep that
    # reference's pixel widths converted through the same nesting (Container
    # 320/960 of what's left after Name, Type 150/960 of what's left after
    # both, Deps 50/960 of what's left after all three -- Source, no slider,
    # is the row's last cell and fills whatever remains); Name overrides it at
    # 0.8, since the asset name is the one cell whose content is a long unique
    # identifier worth reading in full and the reference's 240/960 truncated
    # nearly every row. Because the other three factors are relative, widening
    # Name alone shrinks them all in proportion -- on screen that lands at
    # Name .800 / Container .089 / Type .042 / Deps .014 / Source .056.
    col_name_factor: FloatProperty(
        name="Name", default=0.7, min=0.05, max=0.95, subtype="FACTOR",
        description="Width of the Name column")
    col_container_factor: FloatProperty(
        name="Path", default=0.4444, min=0.05, max=0.95, subtype="FACTOR",
        description="Width of the Path (virtual container path) column")
    col_type_factor: FloatProperty(
        name="Type", default=0.375, min=0.05, max=0.95, subtype="FACTOR",
        description="Width of the Type column")
    col_deps_factor: FloatProperty(
        name="Deps", default=0.2, min=0.05, max=0.95, subtype="FACTOR",
        description="Width of the Deps column -- Source fills whatever's left")

    detail_level: IntProperty(name="Detail Level", default=0, min=-1, soft_max=4,
                              description="Which detail level to build: 0 is the highest "
                                          "the game authored, -1 builds every level")
    import_materials: BoolProperty(name="Import Materials", default=True)
    # 着色栈开不开是**按导入什么**分的,不是一个全局偏好:一个角色十几张材质,
    # 那点代价换来的是它本来的样子(NPR/SDF 脸影/毛壳/描边),当然默认开;
    # 一个场景窗口几百上千张,同样的代价就是几秒对几分钟 —— 而地形石头用内置
    # BSDF 看着并不差。所以两条路各有各的默认,谁也不该迁就谁。
    character_shaders: BoolProperty(
        name="Game Shaders", default=True,
        description="Rebuild the game's own shading stack (NPR lighting, SDF face "
                    "shadows, fur shells, outlines) instead of Blender's built-in "
                    "BSDF. On by default for characters -- a character is a dozen "
                    "materials and this is what makes it look like itself")
    scene_shaders: BoolProperty(
        name="Game Shaders", default=False,
        description="Rebuild the game's own shading stack instead of Blender's "
                    "built-in BSDF. Off by default for scenes -- a scene window is "
                    "hundreds of materials, and geometry/textures are identical "
                    "either way. Turn it on when you are actually rendering this "
                    "window rather than still deciding what to import")
    # 模板组从哪来。默认 append:模板成为这份文件自己的数据 —— 挪目录/换机器/被第三个
    # 文件再 link 一次都照画。开这个开关则改为 link 一份**放在当前 .blend 旁边**的产物
    # 副本(相对路径 `//`,不是插件目录的绝对路径):体积小、同目录多文件共享一份,代价是
    # 那份副本得跟着 .blend 一起搬 —— 断了 Blender 不报错,塞一个零节点空壳顶上,
    # 材质输出恒 0、整个模型变黑。
    link_shader_templates: BoolProperty(
        name="Link Shader Templates", default=False,
        description="Where the game shading stack's node-group templates live. Off "
                    "(default) APPENDS them so they become this file's own data: the "
                    ".blend still renders correctly after you move it, hand it to "
                    "someone else, archive it, or link it from a third file. On LINKS "
                    "a copy of the template .blend placed NEXT TO this file and "
                    "referenced by a relative path -- smaller files, one shared copy "
                    "for every .blend in that folder, but the copy has to travel with "
                    "them. A link that stops resolving is not an error in Blender: it "
                    "silently substitutes an empty stand-in and the whole model "
                    "renders black")
    import_textures: BoolProperty(name="Import Textures", default=True)
    import_skeleton: BoolProperty(name="Import Skeleton", default=True)
    import_empties: BoolProperty(
        name="Import Empties", default=False,
        description="Keep every GameObject as an Empty. Off keeps only the "
                    "empties that hold imported content in the hierarchy")
    # 只有"把设置写在模型上"的游戏答得出这件事,所以这个开关只在那种安装前面出现
    # (见 Game.secondary_motion_of)。核心永远不知道是哪些游戏,只知道这一个答没答。
    import_secondary_motion: BoolProperty(
        name="Import Cloth", default=False,
        description="Bring the model's own secondary motion across: the hair, cloth and "
                    "accessory chains its author tuned on the model itself, plus the "
                    "collision volumes they collide with, written onto the imported "
                    "armature. Replaces whatever that armature already carried. Which "
                    "bone is which is resolved the same way an animation's is")
    retarget_face: BoolProperty(
        name="Retarget Face", default=False,
        description="For a clip whose facial animation is baked into its bone tracks "
                    "(UI and cutscene clips are), work out WHICH library expressions "
                    "that performance is -- measured on the character the clip was "
                    "authored on -- and have the character in the scene play those same "
                    "named expressions through its own face table. No geometry crosses "
                    "between the two faces. The expression library loads itself the "
                    "first time this is needed")
    import_animations: BoolProperty(
        name="Discover Animations", default=True,
        description="List this character's animation clips in the Animations "
                    "panel below after import. Clips are NOT built until you "
                    "check them there and click Import -- a single clip can "
                    "be 100+MB, so nothing is loaded automatically")
    animation_character_name: StringProperty(default="")
    animation_search: StringProperty(
        name="Filter", options={"TEXTEDIT_UPDATE"}, update=_on_animation_search,
        description="Filter the discovered clips by name or by the game's own folder")
    available_clips: CollectionProperty(type=RURI_PG_animation_clip)
    available_clips_active_index: IntProperty()
    texture_roles: CollectionProperty(type=RURI_PG_texture_role)

    def as_options(self, scene=False):
        """``scene=True`` 是场景窗口/展示台那条路 —— 它和角色路的唯一区别就是
        着色栈的默认(见 character_shaders / scene_shaders 的注)。"""
        return {
            "detail_level": self.detail_level,
            "import_materials": self.import_materials,
            "game_shaders": self.scene_shaders if scene else self.character_shaders,
            "link_shader_templates": self.link_shader_templates,
            "import_textures": self.import_textures,
            "import_skeleton": self.import_skeleton,
            "import_animations": self.import_animations,
            "import_empties": self.import_empties,
            "retarget_face": self.retarget_face,
            "import_secondary_motion": self.import_secondary_motion,
            # THE game this session is looking at, resolved exactly once here and
            # stamped onto every armature the import builds -- what a later
            # cross-game retarget selects its table by.
            "source_game": _active_game_name(self),
            # Which property is which input, for THIS install: the default layer, the
            # game module's own and the user's, merged in that order.
            "texture_roles": _texture_role_table(self),
            "texture_roles_game": _texture_role_game(self),
        }


class RURI_OT_reprobe_install(bpy.types.Operator):
    """Re-read what this tab's folder says it is, and re-resolve its decoder from that.

    Needed only when the identity could not be read at the moment the folder was typed
    -- a .blend opened before the bridge was up, or a bin dir configured afterwards.
    Everything it does is what typing the folder already does."""

    bl_idname = "ruri.reprobe_install"
    bl_label = "Re-read Install"
    bl_description = "Re-read this install's own identity (product, Unity version) and re-resolve its decoder"

    @classmethod
    def poll(cls, context):
        return bootstrap.is_ready()

    def execute(self, context):
        state = context.scene.ruri_cabmap
        config = _ensure_active_config(state)
        _INSTALL_IDENTITY.pop(bpy.path.abspath(config.game_root) if config.game_root else "", None)
        try:
            decoder = _adopt_identity(state, config)
        except Exception as exc:
            _report_exception(self, "Re-read install failed", exc)
            return {"CANCELLED"}
        if config.game_name:
            _rename_tab(state, config,
                        _unique_tab_key(state, config.game_name, held_by_key=config.key))
        _set_current_tab(state, config.key)
        _auto_default_cabmap_filename(state)
        self.report({"INFO"}, "{0} {1} · {2} {3} · {4}".format(
            config.game_name or "no identity", config.game_version or "(no version)",
            config.engine_family or "Unity", config.engine_version or "unknown",
            decoder or "no decoder"))
        return {"FINISHED"}


class RURI_OT_build_cabmap(bpy.types.Operator):
    bl_idname = "ruri.build_cabmap"
    bl_label = "Build Cabmap"
    bl_description = "Scan the game root and build a fresh cabmap (can take a long time for a full game)"

    # No decoder is a VALID configuration, not a missing prerequisite: a plain
    # un-bundled/un-encrypted Unity player build (level0/sharedassetsN.assets/resources.assets)
    # needs none at all -- the generic scan handles it, with readable names harvested
    # straight from the assets' own m_Name fields (GameBundleHook.HarvestAssetNames). Decoders
    # are only for games with custom encryption/VFS/typetree drift.
    @classmethod
    def poll(cls, context):
        return bootstrap.is_ready()

    def execute(self, context):
        state = context.scene.ruri_cabmap
        root = bpy.path.abspath(state.game_root) if state.game_root else ""
        if not root or not os.path.isdir(root):
            self.report({"ERROR"}, "Pick a valid game root directory first.")
            return {"CANCELLED"}
        out = _resolve_build_output_path(state)
        if not out:
            self.report({"ERROR"}, "Pick an output path for the cabmap file first.")
            return {"CANCELLED"}
        out_dir = os.path.dirname(out)
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            self.report({"ERROR"}, f"Can't create output folder '{out_dir}': {exc}")
            return {"CANCELLED"}
        config = _ensure_active_config(state)
        try:
            bridge = cabmap_state.ensure_bridge(config.decoder_id, root, _source_options(config))
            code = bridge.build_cab_map(root, out)
            if code != 0:
                self.report({"ERROR"}, f"Build failed (exit {code}) -- see console.")
                return {"CANCELLED"}
            bridge.load_cab_map(out, key=config.key)
            cabmap_state.activate(config.key, _module_game_name(config))
            cabmap_state.load_rows(cabmap_state.key_to_dir(state.browsed_dir))
            _reapply_and_refresh(context)
        except Exception as exc:
            _report_exception(self, "Build cabmap failed", exc)
            return {"CANCELLED"}
        if not len(cabmap_state.ROWS):
            # A game's bundles are only readable through that game's OWN decoder, and
            # the process runs one decoder at a time. Scanning a real game folder
            # through a FOREIGN game's decoder yields an empty map and a success code,
            # so the emptiness is the only place that mismatch can still be caught.
            self.report({"ERROR"}, (
                "Built 0 CABs from '{0}'. This build decoded with {1} -- a game's "
                "bundles are only readable through its own decoder. Pick this install's "
                "decoder on this tab, then build again.").format(
                    root, config.decoder_id or "no decoder"))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Cabmap built: {len(cabmap_state.ROWS)} CABs.")
        return {"FINISHED"}


class RURI_OT_load_cabmap(bpy.types.Operator):
    bl_idname = "ruri.load_cabmap"
    bl_label = "Load Cabmap"
    bl_description = "Load an existing cabmap file -- required before browsing/importing anything"

    # No decoder is valid -- see RURI_OT_build_cabmap.poll.
    @classmethod
    def poll(cls, context):
        return bootstrap.is_ready()

    def execute(self, context):
        state = context.scene.ruri_cabmap
        path = bpy.path.abspath(state.cabmap_path) if state.cabmap_path else ""
        if not path or not os.path.isfile(path):
            self.report({"ERROR"}, "Pick a valid cabmap file first.")
            return {"CANCELLED"}
        config = _ensure_active_config(state)
        try:
            bridge = cabmap_state.ensure_bridge(config.decoder_id,
                                                bpy.path.abspath(state.game_root) if state.game_root else "",
                                                _source_options(config))
            bridge.load_cab_map(path, key=config.key)
            cabmap_state.activate(config.key, _module_game_name(config))
            # Land back on the folder the user was browsing (persisted per game on the
            # scene), not the root -- browse_dir falls back to the root if this map has
            # no such folder, so a key left over from a different game is harmless.
            cabmap_state.load_rows(cabmap_state.key_to_dir(state.browsed_dir))
            _reapply_and_refresh(context)
        except Exception as exc:
            _report_exception(self, "Load cabmap failed", exc)
            return {"CANCELLED"}
        gone, rows = unreachable_rows()
        if gone:
            self.report({"WARNING"},
                        f"{rows} CAB(s) in this map live in {gone} archive(s) the install "
                        f"no longer has -- the game replaced them in a patch. Anything "
                        f"filed there loads as empty until the cabmap is rebuilt.")
        self.report({"INFO"}, f"Cabmap loaded: {len(cabmap_state.ROWS)} CABs.")
        return {"FINISHED"}


class RURI_OT_select_tab(bpy.types.Operator):
    """One button of the CONTENT tab row (browser / this game's own tabs).
    Buttons rather than an expanded EnumProperty because which tabs exist depends on
    which game the current install is -- see RURI_PG_cabmap.active_tab."""
    bl_idname = "ruri.select_tab"
    bl_label = "Tab"
    bl_options = {"INTERNAL"}
    tab: StringProperty()

    @classmethod
    def description(cls, context, properties):
        if properties.tab == BROWSER_TAB_ID:
            return BROWSER_TAB_DESCRIPTION
        tab = Game.tab_by_key(properties.tab)
        return tab.description if tab is not None else cls.bl_label

    def execute(self, context):
        context.scene.ruri_cabmap.active_tab = self.tab
        _redraw_all(context)
        return {"FINISHED"}


class RURI_OT_select_install(bpy.types.Operator):
    """Click a tab in the always-visible tab bar: point the browser (and the
    cabmap_state session behind it) at that install's cabmap."""
    bl_idname = "ruri.select_install"
    bl_label = "Install"
    bl_options = {"INTERNAL"}
    key: StringProperty()

    def execute(self, context):
        _switch_current_tab(context.scene.ruri_cabmap, self.key, context)
        _redraw_all(context)
        return {"FINISHED"}


class RURI_OT_open_tab(bpy.types.Operator):
    """Open a fresh, EMPTY browser tab and switch to it. That is the whole of it: the
    tab is unnamed until a folder is typed into it, which then names it after that
    build's own product. Nothing is preset and nothing is remembered -- which install
    a user wants is the folder they pick, not a list this add-on keeps."""
    bl_idname = "ruri.open_tab"
    bl_label = "Open Tab"
    bl_options = {"INTERNAL"}
    key: StringProperty()

    def execute(self, context):
        _open_tab(context.scene.ruri_cabmap, self.key, context)
        _redraw_all(context)
        return {"FINISHED"}


class RURI_OT_close_tab(bpy.types.Operator):
    """The tab's x button: close one install's tab -- drop its config entry and its
    browser session, and hand focus to a remaining tab."""
    bl_idname = "ruri.close_tab"
    bl_label = "Close Tab"
    bl_options = {"INTERNAL"}
    key: StringProperty()

    def execute(self, context):
        _close_tab(context.scene.ruri_cabmap, self.key, context)
        _redraw_all(context)
        return {"FINISHED"}


class RURI_OT_cabmap_sort(bpy.types.Operator):
    bl_idname = "ruri.cabmap_sort"
    bl_label = "Sort"
    column: StringProperty()

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded

    def execute(self, context):
        cabmap_state.cycle_sort(self.column)
        _rebuild_window(context.scene.ruri_cabmap)
        return {"FINISHED"}


class RURI_OT_cabmap_enter_dir(bpy.types.Operator):
    """Click on a folder row (RURI_UL_cabmap.draw_item) -- descends one level
    under CURRENT_DIR. Selection is deliberately left untouched: it's keyed
    by cab (see cabmap_state.SELECTED_CABS), not by which folder you're
    looking at, so multi-selecting files across several folder visits and
    then batch-importing them all at once keeps working."""
    bl_idname = "ruri.cabmap_enter_dir"
    bl_label = "Open Folder"
    bl_description = "Browse into this virtual folder"
    bl_options = {"INTERNAL"}
    folder_name: StringProperty()

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded

    def execute(self, context):
        state = context.scene.ruri_cabmap
        cabmap_state.browse_dir(cabmap_state.CURRENT_DIR + (self.folder_name,))
        _rebuild_window(state)
        _redraw_all(context)
        return {"FINISHED"}


class RURI_OT_cabmap_goto_dir(bpy.types.Operator):
    """Breadcrumb click -- jump straight to one ancestor level of CURRENT_DIR
    (depth=0 is the virtual root) instead of stepping out one folder at a
    time."""
    bl_idname = "ruri.cabmap_goto_dir"
    bl_label = "Go to Folder"
    bl_description = "Jump to this level of the virtual path"
    bl_options = {"INTERNAL"}
    depth: IntProperty()

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded

    def execute(self, context):
        state = context.scene.ruri_cabmap
        cabmap_state.browse_dir(cabmap_state.CURRENT_DIR[:self.depth])
        _rebuild_window(state)
        _redraw_all(context)
        return {"FINISHED"}


class RURI_OT_cabmap_reveal(bpy.types.Operator):
    """Reveal something in the file browser from another tab -- the tab bar
    switches to the browser and the browser goes to where that thing lives.

    The caller passes an identifier the GAME itself uses (a roster id, an
    addressable name), never a path this add-on invented. If every bundle
    carrying that identifier sits under one virtual folder, the browser jumps
    to that folder and clears the search, which is the "open file location"
    the user asked for; when the hits are spread across folders there is no
    single right folder to open, so the search stays on and shows all of them.

    Generic on purpose: it lives here with the browser it drives, and no game
    module has to reach into the browser's own state to use it."""
    bl_idname = "ruri.cabmap_reveal"
    bl_label = "Reveal in Browser"
    bl_description = "Switch to the file browser and show where this lives"
    bl_options = {"INTERNAL"}
    query: StringProperty()
    cab: StringProperty(
        description="The exact CAB to highlight, so the row is selected rather than "
                    "leaving whatever was selected before. A cab is unambiguous; the "
                    "displayed name is abbreviated for a multi-path row")
    folder: StringProperty(
        description="The exact virtual folder to open. When the caller already knows which "
                    "asset it means, this beats searching -- a search lands on every name "
                    "that merely contains the query")

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded

    def execute(self, context):
        state = context.scene.ruri_cabmap
        state.active_tab = BROWSER_TAB_ID
        for rule in state.filter_rules:
            rule.enabled = False

        if self.folder:
            state.search = ""
            cabmap_state.browse_dir(tuple(p for p in self.folder.split("/") if p))
            _rebuild_window(state)
            # Opening the folder is only half of "reveal": without moving the
            # highlight the list keeps whatever row was selected before, which
            # reads as having jumped to a completely unrelated asset.
            if self.cab:
                for position, item in enumerate(state.window):
                    if not item.is_folder and item.cab == self.cab:
                        state.active_index = position
                        state.cursor_cab = item.cab
                        cabmap_state.clear_selection()
                        cabmap_state.SELECTED_CABS.add(item.cab)
                        break
            _redraw_all(context)
            return {"FINISHED"}

        cabmap_state.apply_filter(self.query)
        matches = list(cabmap_state.VISIBLE)
        folders = {cabmap_state.folder_of(row, cabmap_state.best_path_index_for_jump(row, self.query))
                   for row in matches}
        if len(folders) == 1:
            state.search = ""
            cabmap_state.browse_dir(folders.pop())
        else:
            state.search = self.query
        _rebuild_window(state)
        _redraw_all(context)
        if not matches:
            self.report({"WARNING"}, f"Nothing in the loaded cabmap carries '{self.query}'.")
        return {"FINISHED"}


class RURI_OT_cabmap_show_rules(bpy.types.Operator):
    """Switch to the browser and show exactly the rows a caller's rule set selects.

    The caller states its query as Include/Exclude RULES in the browser's own
    field vocabulary -- which is what rules are for, and what the quick-search
    box is not: the box is left EMPTY on purpose so it stays available as the
    user's own further narrowing ON TOP of the rules (type "battle" and get that
    subset, without the button's own query having eaten the box).

    Generic on purpose, like reveal next door: no game module reaches into the
    browser's state, and the browser stays the only thing that knows how rules
    are stored and re-applied."""
    bl_idname = "ruri.cabmap_show_rules"
    bl_label = "Show Filtered in Browser"
    bl_description = "Switch to the file browser and filter it to these rows"
    bl_options = {"INTERNAL"}
    rules: StringProperty(
        description="JSON list of {field, relation, value, action} in the browser's own "
                    "filter vocabulary -- the same shape the rule editor writes")

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded

    def execute(self, context):
        try:
            wanted = json.loads(self.rules) if self.rules else []
        except ValueError as exc:
            self.report({"ERROR"}, f"Bad rule payload: {exc}")
            return {"CANCELLED"}

        state = context.scene.ruri_cabmap
        state.active_tab = BROWSER_TAB_ID
        # Replace, never accumulate: this is one caller's whole query, and rules
        # left over from the previous one would silently AND into it.
        state.filter_rules.clear()
        state.search = ""
        for spec in wanted:
            rule = state.filter_rules.add()
            # spec_key first: it is what makes the field enum resolve to this
            # list's vocabulary, so assigning field before it would not stick.
            rule.spec_key = BROWSER_FILTER_SPEC.key
            rule.field = spec["field"]
            rule.relation = spec.get("relation", "contains")
            rule.value = spec.get("value", "")
            rule.action = spec.get("action", "include")
            rule.enabled = True
        state.filter_rules_active_index = len(state.filter_rules) - 1
        _reapply_and_refresh(context)
        return {"FINISHED"}


class RURI_OT_cabmap_goto_row_folder(bpy.types.Operator):
    """One-click 'reveal in folder' -- jumps the folder browser straight to
    the virtual folder this row's container path lives under, the way a
    normal file explorer's "Open file location" does. UIList has no per-row
    right-click context menu (see this module's docstring and
    RURI_MT_quick_filter, which hits the same wall for its own quick-filter
    actions), so this is a small icon button on the row instead -- one click,
    no menu, actually faster than a right-click would have been.

    A row can carry more than one container path (rare, but see
    cabmap_state.best_path_index_for_jump's docstring for a confirmed report
    of the bug that skipping this caused): which one to jump to is resolved
    against the search text BEFORE it gets cleared below, since that's the
    only signal that says which of the row's paths is actually the one the
    user was looking at.

    Drops out of search/rule-filtered view on jump: has_active_query gates
    the folder browser off whenever a search or an enabled rule is active
    (see refresh_visible), so landing on the right CURRENT_DIR wouldn't
    actually be visible otherwise. Rules are only disabled, not deleted --
    the user's filter setup survives, just switched off (re-enable it from
    the funnel popover) -- same as unticking a rule's own checkbox there."""
    bl_idname = "ruri.cabmap_goto_row_folder"
    bl_label = "Go to Containing Folder"
    bl_description = "Jump the folder browser to this file's virtual folder"
    bl_options = {"INTERNAL"}
    index: IntProperty()

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded

    def execute(self, context):
        state = context.scene.ruri_cabmap
        if not (0 <= self.index < len(state.window)):
            return {"CANCELLED"}
        item = state.window[self.index]
        if item.is_folder:
            return {"CANCELLED"}

        target_cab = item.cab
        path_index = cabmap_state.best_path_index_for_jump(item.row_index, state.search)
        folder = cabmap_state.folder_of(item.row_index, path_index)
        state.search = ""
        for rule in state.filter_rules:
            rule.enabled = False
        cabmap_state.browse_dir(folder)
        _rebuild_window(state)

        for position, row_item in enumerate(state.window):
            if not row_item.is_folder and row_item.cab == target_cab:
                state.active_index = position
                break

        _redraw_all(context)
        return {"FINISHED"}


class RURI_OT_cabmap_click(bpy.types.Operator):
    """Row click with file-browser selection semantics -- the whole row is
    drawn as (flat) operator buttons precisely so this invoke() sees the
    click's modifier keys, which template_list's own active-index handling
    never exposes."""
    bl_idname = "ruri.cabmap_click"
    bl_label = "Select Row"
    bl_description = ("Select this row.\n"
                      "• Click: select only this row\n"
                      "• Ctrl+Click: toggle this row\n"
                      "• Shift+Click: select the range from the last clicked row\n"
                      "• Ctrl+Shift+Click: add that range to the selection")
    bl_options = {"INTERNAL"}
    index: IntProperty()

    @classmethod
    def description(cls, context, properties):
        """Per-row hover tooltip -- Blender's native hover-and-dwell popup (its
        delay is the user's own Preferences > Interface > Tooltips setting, no
        timer needed here), the same dynamic-description mechanism
        RURI_OT_cabmap_select_all already uses for its per-mode text. Mirrors
        the WinForms reference's selection info panel (MainForm.AssetList.cs's
        virtualListView_SelectedIndexChanged: "CAB: ...\\nSource: ...\\n
        Dependencies: ...\\n\\n<container paths>") so the row's full virtual
        path(s) are readable even when its column is too narrow to show them
        (or item.name is abbreviated to "leaf (+N)" for a multi-path row --
        see RowTable.name)."""
        scene = getattr(context, "scene", None)
        state = getattr(scene, "ruri_cabmap", None) if scene is not None else None
        if state is None or not (0 <= properties.index < len(state.window)):
            return cls.bl_description
        item = state.window[properties.index]
        if item.is_folder:
            return cls.bl_description
        paths = (item.container or item.cab).replace("  |  ", "\n")
        return (f"{item.name or item.cab}\n"
                f"CAB: {item.cab}\n"
                f"Source: {item.source}\n"
                f"Dependencies: {item.deps}\n"
                f"\n{paths}\n"
                f"\n{cls.bl_description}")

    def invoke(self, context, event):
        state = context.scene.ruri_cabmap
        if not (0 <= self.index < len(state.window)):
            return {"CANCELLED"}
        item = state.window[self.index]
        selection = cabmap_state.SELECTED_CABS
        rows_index = item.row_index

        if event.shift:
            # Range anchor->clicked over the CURRENT filtered+sorted order
            # (what the user is looking at). Both endpoints are clickable so
            # both sit inside the display window; an anchor that has since
            # been filtered away degrades to a single-row range.
            visible = cabmap_state.VISIBLE
            anchor = cabmap_state.SELECT_ANCHOR
            try:
                clicked_pos = visible.index(rows_index)
            except ValueError:
                return {"CANCELLED"}
            try:
                anchor_pos = visible.index(anchor) if anchor is not None else clicked_pos
            except ValueError:
                anchor_pos = clicked_pos
            lo, hi = sorted((anchor_pos, clicked_pos))
            range_cabs = {cabmap_state.ROWS[i]["cab"] for i in visible[lo:hi + 1]}
            if not event.ctrl:
                selection.clear()
            selection.update(range_cabs)
            # Anchor deliberately stays put: successive Shift+Clicks re-pivot
            # around the same anchor, the standard file-browser behaviour.
        elif event.ctrl:
            if item.cab in selection:
                selection.discard(item.cab)
            else:
                selection.add(item.cab)
            cabmap_state.set_select_anchor(rows_index)
        else:
            selection.clear()
            selection.add(item.cab)
            cabmap_state.set_select_anchor(rows_index)

        state.active_index = self.index
        state.cursor_cab = item.cab
        _sync_window_selection(state)
        _redraw_all(context)
        return {"FINISHED"}


class RURI_OT_cabmap_select_all(bpy.types.Operator):
    """Select All / None / Invert over the FILTERED row set (everything the
    current search+rules match, not just the capped display window) -- bound
    to Ctrl+A / Alt+A / Ctrl+I while the cursor is over the RuriRipper
    sidebar, and mirrored as the All/None/Invert buttons under the list."""
    bl_idname = "ruri.cabmap_select_all"
    bl_label = "Select All Rows"
    bl_options = {"INTERNAL"}
    mode: EnumProperty(items=[
        ("ALL", "All", "Select every row matching the current filter"),
        ("NONE", "None", "Clear the selection"),
        ("INVERT", "Invert", "Invert the selection within the current filter"),
    ])

    @classmethod
    def description(cls, context, properties):
        return {
            "ALL": "Select every row matching the current filter (Ctrl+A over the panel)",
            "NONE": "Clear the selection entirely (Alt+A over the panel)",
            "INVERT": "Invert the selection within the current filter (Ctrl+I over the panel)",
        }[properties.mode]

    @classmethod
    def poll(cls, context):
        # Reached from two directions: the buttons under the list (always in
        # the right panel already) and the addon keymap, which fires for a
        # keypress over ANY UI region anywhere -- the area/region/category
        # checks scope the shortcut to the RuriRipper sidebar specifically.
        scene = getattr(context, "scene", None)
        state = getattr(scene, "ruri_cabmap", None)
        if state is None or not state.loaded or _active_tab(state) is not None:
            return False
        if not cabmap_state.VISIBLE:
            return False
        area = getattr(context, "area", None)
        region = getattr(context, "region", None)
        if area is None or area.type != "VIEW_3D" or region is None or region.type != "UI":
            return False
        category = getattr(region, "active_panel_category", None)
        return category in (None, "RuriRipper")

    def execute(self, context):
        state = context.scene.ruri_cabmap
        selection = cabmap_state.SELECTED_CABS
        visible_cabs = [cabmap_state.ROWS[i]["cab"] for i in cabmap_state.VISIBLE]
        if self.mode == "ALL":
            selection.update(visible_cabs)
        elif self.mode == "NONE":
            cabmap_state.clear_selection()
        else:
            for cab in visible_cabs:
                if cab in selection:
                    selection.discard(cab)
                else:
                    selection.add(cab)
        _sync_window_selection(state)
        _redraw_all(context)
        return {"FINISHED"}


class RURI_MT_quick_filter(bpy.types.Menu):
    """Dynamically built from the selected row's actual values -- Include/
    Exclude x every field the browser's spec declares, exactly the Process
    Monitor right-click pattern. The body is the shared one, so a field added
    to the spec shows up here with no edit."""
    bl_idname = "RURI_MT_quick_filter"
    bl_label = "Quick Filter Selected Row"

    def draw(self, context):
        layout = self.layout
        row = _selected_row(context.scene.ruri_cabmap)
        if row is None:
            layout.label(text="No row selected", icon="INFO")
            return
        filter_ui.draw_quick_filter_menu(layout, BROWSER_FILTER_SPEC,
                                         lambda field: getattr(row, field))


def _selected_row(state):
    if 0 <= state.active_index < len(state.window):
        item = state.window[state.active_index]
        if not item.is_folder:
            return item
    return None


def _row_is_clip_only(row):
    """A browser row (dict form) that hosts AnimationClips and no importable
    GameObject hierarchy -- selecting it and clicking Import means "import
    these clips", not "import a prefab" (a clip CAB's closure contains no
    .prefab at all; confirmed against the real game, its dependency count is
    literally 0). Covers both a bundled clip CAB and a non-bundled per-asset
    AnimationClip row (see prefab_importer/ReadFullMetadataRows --
    "<file>::<pathID>")."""
    return "AnimationClip" in row["type_names"] and "GameObject" not in row["type_names"]


def _selected_target_rows(state):
    """The row batch an import/discover operates on: the multi-selection in
    master ROWS order, falling back to the active (highlighted) row so the
    original click-then-import muscle memory keeps working when nothing is
    explicitly multi-selected."""
    rows = cabmap_state.selected_row_dicts()
    if rows:
        return rows
    item = _selected_row(state)
    if item is None:
        return []
    row = cabmap_state.rows_by_cab().get(item.cab)
    return [row] if row is not None else []


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


def _import_clips_standalone(op, context, state, clip_cab, clip_guids, db):
    """Shared tail of both standalone clip flows (a clip-only row through
    Import (Append)/(Reset Scene), and Import Checked Animations discovered
    off a clip-only row): resolve the target armature from the user's
    selection, verify the clips actually fit that skeleton (path/CRC32 match
    against the armature's own bone paths), then build actions. Returns the
    operator result set."""
    arm_obj, maps_or_error = _resolve_target_armature(context)
    if arm_obj is None:
        op.report({"ERROR"}, maps_or_error)
        return {"CANCELLED"}
    maps = maps_or_error


    # A muscle-encoded clip solves inside build_selected_animations, against the avatar
    # document stamped on this very armature -- no export scope, no co-seeding, nothing
    # avatar-related to do here.

    try:
        built, warnings = cross_game_retarget.load_clips_onto(
            context, cabmap_state.active_key(), clip_cab, clip_guids, db, arm_obj,
            maps, state.as_options())
    except cross_game_retarget.CrossGameRetargetError as exc:
        op.report({"ERROR"}, str(exc))
        return {"CANCELLED"}
    except Exception as exc:
        _report_exception(op, "Animation import failed", exc)
        return {"CANCELLED"}
    for warning in warnings[:5]:
        op.report({"WARNING"}, warning)
    op.report({"INFO"}, f"Built {built} animation action(s) on {arm_obj.name}.")
    return {"FINISHED"}


class RURI_OT_cabmap_import_with_dependents(bpy.types.Operator):
    """Import the selected row(s) TOGETHER WITH whatever directly depends on
    them, in ONE click -- not a discovery list to hand-pick from. Fixes the
    case where a bundled row imports empty/incomplete on its own: a Mesh-only
    FBX sub-asset carries no Material of its own, but the Prefab whose
    Renderer component actually pairs that mesh with a material is a direct
    DEPENDENT, invisible to a plain forward import (see
    cabmap_state.BRIDGE.find_direct_dependents / RipperBlenderBridge.
    FindDirectDependents for the reverse lookup itself).

    Implementation: expands the multi-selection (cabmap_state.SELECTED_CABS)
    with the reverse-lookup hits, then delegates straight to
    RURI_OT_import_selected via bpy.ops -- every existing per-row/type
    dispatch (prefab roots, loose Mesh/Material/Avatar/TextAsset) runs
    completely unchanged, just over a bigger CAB set."""
    bl_idname = "ruri.cabmap_import_with_dependents"
    bl_label = "Import (With Dependents)"
    bl_description = ("Find every CAB that directly depends on the selected row(s) -- e.g. the "
                      "Prefab/Material that actually uses a mesh-only CAB -- and import "
                      "everything together in one step")
    bl_options = {"REGISTER", "UNDO"}
    reset_scene: BoolProperty(default=False)

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
            dependent_cabs = cabmap_state.BRIDGE.find_direct_dependents(seed_cabs)
        except Exception as exc:
            _report_exception(self, "Find dependents failed", exc)
            return {"CANCELLED"}

        added = [cab for cab in dependent_cabs if cab not in cabmap_state.SELECTED_CABS]
        cabmap_state.SELECTED_CABS.update(added)
        _sync_window_selection(state)
        if added:
            self.report({"INFO"}, f"Importing with {len(added)} direct dependent(s) added to the selection.")
        return bpy.ops.ruri.import_selected(reset_scene=self.reset_scene)


# {session key: ({cab: source archive}, {missing archive: row count})} for the
# loaded map. Built once per session off one enumeration, because the answer is a
# property of the install and the map, and neither changes while a session is open.
_ARCHIVES = {}


def _archives():
    """Which archive each CAB lives in, and which of those archives are gone.

    The map names 40-odd chunk files for a quarter-million CABs, so asking the
    filesystem once per ARCHIVE answers it for every CAB in it."""
    key = cabmap_state.active_key()
    if key in _ARCHIVES:
        return _ARCHIVES[key]
    table = cabmap_state.BRIDGE.enumerate_table()
    root = cabmap_state.BRIDGE.game_root or ""
    by_cab = {}
    rows = {}
    for index in range(len(table.cabs)):
        source = str(table.source(index))
        by_cab[table.cab(index)] = source
        rows[source] = rows.get(source, 0) + 1
    gone = {source: count for source, count in rows.items()
            if not os.path.isfile(os.path.join(root, source.replace("\\", "/")))}
    _ARCHIVES[key] = (by_cab, gone)
    return _ARCHIVES[key]


def unreachable_rows():
    """(archives gone, cab rows in them) for the loaded map -- what a rebuild would
    bring back. (0, 0) for a map that matches the install it was built from."""
    try:
        _by_cab, gone = _archives()
    except Exception:
        return 0, 0
    return len(gone), sum(gone.values())


def forget_archives():
    _ARCHIVES.clear()


def unreachable_seeds(seeds):
    """The seeds whose archive the install no longer has, with the archive named.

    Asked BEFORE a crossing, so a load that cannot possibly work says why instead
    of resolving a closure and reporting the empty result as the thing's own
    shape."""
    try:
        by_cab, gone = _archives()
    except Exception:
        return []
    return [(cab, by_cab[cab]) for cab in seeds
            if cab in by_cab and by_cab[cab] in gone]


class StaleCabmapError(RuntimeError):
    """The map names an archive the install no longer has.

    Its own type because the remedy is specific and is not "try again": the game
    replaced a chunk, and the map has to be rebuilt against what is on disk now."""


def resolve_import_closure(seeds, export_class_ids=None):
    """The one bridge crossing every import flow shares: resolve a cab set's
    dependency closure, export it in memory, and wrap it in the asset db that
    snapshots the bridge's per-call blob maps (clip curves / mesh blobs / asset
    paths, each replaced wholesale by import_cabs -- so the db must be built here,
    right after the crossing, before any other crossing can overwrite them).

    Touches NO bpy on purpose: a modal loader runs this on a worker thread and
    hands the result to the main thread to build from, and a synchronous caller
    runs it inline. Returns {db, roots, seed_roots, clips_by_cab, scene_roots}."""
    seeds = list(seeds)
    stale = unreachable_seeds(seeds)
    if stale and len(stale) == len(seeds):
        raise StaleCabmapError(
            "All {0} of these CAB(s) live in '{1}', which this install no longer has -- "
            "the game replaced that archive in a patch and the cabmap predates it. "
            "Rebuild the cabmap.".format(len(stale), stale[0][1]))
    assets, roots, seed_roots, clips_by_cab, scene_roots = \
        cabmap_state.BRIDGE.import_cabs(seeds, export_class_ids=export_class_ids)
    db = bridge_asset_db.BridgeAssetDatabase(
        assets, clip_curve_blobs=cabmap_state.BRIDGE.clip_curves_by_guid,
        mesh_blobs=cabmap_state.BRIDGE.mesh_blobs_by_guid,
        asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid,
        texture_srgb=cabmap_state.BRIDGE.texture_srgb_by_guid)
    return {"db": db, "roots": roots, "seed_roots": seed_roots,
            "clips_by_cab": clips_by_cab, "scene_roots": scene_roots}


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


class RURI_OT_import_selected(bpy.types.Operator):
    """Batch import over the multi-selection: every selected clip-only row and
    every selected hierarchy/asset row each share ONE bridge closure resolve
    (a union closure loads shared dependencies once instead of per row), then
    each row keeps its own per-type dispatch semantics."""
    bl_idname = "ruri.import_selected"
    bl_label = "Import Selected"
    bl_description = "Resolve every selected row's dependency closure in memory and import them into the scene"
    bl_options = {"REGISTER", "UNDO"}
    reset_scene: BoolProperty(default=False)
    only_root_names: StringProperty(
        default="",
        description="Semicolon-separated asset names. Set, the import keeps only the roots that "
                    "ARE those assets instead of every root the closure exports -- what a caller "
                    "that already knows which asset it asked for means. A game whose archives are "
                    "pooled (one file carrying dozens of unrelated bundles) exports a closure of "
                    "roots that have nothing to do with the request, and without this a roster "
                    "entry loads a scene's worth of strangers")

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = context.scene.ruri_cabmap
        target_rows = _selected_target_rows(state)
        if not target_rows:
            self.report({"WARNING"}, "No rows selected.")
            return {"CANCELLED"}

        clip_rows = [row for row in target_rows if _row_is_clip_only(row)]
        other_rows = [row for row in target_rows if not _row_is_clip_only(row)]

        if self.reset_scene:
            if not other_rows:
                self.report({"ERROR"}, "An animation needs an existing skeleton -- use "
                                       "Import (Append) so the armature survives.")
                return {"CANCELLED"}
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)

        # A mixed selection (character rows + clip rows) resolves ONE union closure
        # instead of two: the clip flow's closure co-seeds the whole character anyway
        # (avatar scope for the muscle solve), so the sequential second resolve
        # re-loaded everything the first one just loaded. Per-root CAB attribution
        # (bridge root_cabs_by_guid) keeps the hierarchy import's root set identical.
        union = None
        if other_rows and clip_rows:
            union = self._resolve_union_closure(context, other_rows, clip_rows)
            if union is None:
                return {"CANCELLED"}

        imported = 0
        if other_rows:
            # Hierarchy/asset rows first: a co-selected character import may
            # create the very armature the clip rows then attach onto.
            _ok, imported = self._import_hierarchy_rows(
                context, state, other_rows, populate_browser=(len(target_rows) == 1),
                preresolved=union)

        clips_ok = True
        if clip_rows:
            clips_ok = self._import_clip_rows(context, state, clip_rows, preresolved=union)

        if other_rows:
            self.report({"INFO"}, f"Imported {imported} asset root(s) from "
                                  f"{len(other_rows)} selected row(s).")
        # Partial success still finishes (each failure already reported its
        # own ERROR/WARNING); only a batch that produced nothing cancels.
        if imported == 0 and not (clip_rows and clips_ok):
            return {"CANCELLED"}
        # 收尾(顶点腿 / 后处理 / 兑现节点)不在这里,也不在任何一个导入入口:
        # 造出来的东西自己会 announce,derived_state 空闲时统一落地。
        return {"FINISHED"}

    def _resolve_union_closure(self, context, other_rows, clip_rows):
        """One bridge resolve covering a mixed selection: the hierarchy rows plus the
        clip rows. Returns the shared closure pieces both flows consume, with the
        hierarchy import's root set already restricted to ITS OWN sub-closure -- a
        root is dropped only when its CAB attribution places it POSITIVELY outside
        the hierarchy rows' closure; an unattributed root stays, exactly as inclusive
        as the separate-resolve flow was. None on bridge failure (already reported).

        Clips need no avatar/rig co-seeding: a muscle-encoded clip solves at build
        time against the armature's own stamped avatar, and hashed curve paths
        re-anchor through the suffix-CRC join -- the closure is exactly the rows."""
        hierarchy_cabs = [row["cab"] for row in other_rows]
        seeds = list(hierarchy_cabs)
        try:
            for row in clip_rows:
                if row["cab"] not in seeds:
                    seeds.append(row["cab"])
            assets, roots, seed_roots, clips_by_cab, scene_roots = \
                cabmap_state.BRIDGE.import_cabs(seeds)
            union_closure = {c.lower() for c in
                             cabmap_state.BRIDGE.resolve_closure_cab_names(seeds)}
            hierarchy_closure = {c.lower() for c in
                                 cabmap_state.BRIDGE.resolve_closure_cab_names(hierarchy_cabs)}
        except Exception as exc:
            _report_exception(self, "Import (bridge) failed", exc)
            return None
        clip_only_cabs = union_closure - hierarchy_closure
        root_cabs = cabmap_state.BRIDGE.root_cabs_by_guid
        hierarchy_roots = [guid for guid in roots
                           if root_cabs.get(guid, "") not in clip_only_cabs]
        db = bridge_asset_db.BridgeAssetDatabase(
            assets, clip_curve_blobs=cabmap_state.BRIDGE.clip_curves_by_guid,
            mesh_blobs=cabmap_state.BRIDGE.mesh_blobs_by_guid,
            asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid,
            texture_srgb=cabmap_state.BRIDGE.texture_srgb_by_guid)
        return {
            "db": db,
            "roots": hierarchy_roots,
            "seed_roots": seed_roots,
            "scene_roots": scene_roots,
            "clips_by_cab": clips_by_cab,
        }

    def _import_hierarchy_rows(self, context, state, rows, populate_browser, preresolved=None):
        """One shared closure resolve for every non-clip row, then the shared
        dispatch. Returns (all_ok, imported_count)."""
        resolved = preresolved
        if resolved is None:
            try:
                resolved = resolve_import_closure([row["cab"] for row in rows])
            except Exception as exc:
                _report_exception(self, "Import (bridge) failed", exc)
                return False, 0
        return import_hierarchy_from_closure(self, context, state, rows, resolved,
                                             self.only_root_names, populate_browser)

    def _import_clip_rows(self, context, state, clip_rows, preresolved=None):
        """One shared closure resolve for every selected clip-only row, then a
        single standalone build of the union clip set onto the target armature.
        The closure is exactly the clip rows -- no avatar/rig co-seeding: a
        muscle-encoded clip solves at build time against the armature's own
        stamped avatar, and hashed curve paths re-anchor through the suffix-CRC
        join. A mixed selection hands the already-resolved union closure in via
        ``preresolved`` and no second bridge resolve happens at all. Returns
        success."""
        if preresolved is not None:
            return self._build_clip_rows(context, state, clip_rows,
                                         preresolved["clips_by_cab"], preresolved["db"])
        try:
            # Export-side allowlist: this flow reads nothing but the exported clips.
            resolved = resolve_import_closure(
                list(dict.fromkeys(row["cab"] for row in clip_rows)),
                export_class_ids=[class_registry.id_for_name("AnimationClip")])
        except Exception as exc:
            _report_exception(self, "Import (bridge) failed", exc)
            return False
        return self._build_clip_rows(context, state, clip_rows,
                                     resolved["clips_by_cab"], resolved["db"])

    def _build_clip_rows(self, context, state, clip_rows, clips_by_cab, db):
        """Shared tail of both clip resolve paths: translate the rows to real clip
        guids through clips_by_cab (the cabmap's own identity) and build them onto
        the target armature."""
        clip_guids = []
        missing = []
        for row in clip_rows:
            row_guids = clips_by_cab.get(row["cab"].lower(), [])
            if not row_guids:
                missing.append(row["name"])
            for guid in row_guids:
                if guid not in clip_guids:
                    clip_guids.append(guid)
        if missing:
            self.report({"WARNING"}, f"{len(missing)} selected row(s) exported no AnimationClip: "
                                     f"{', '.join(missing[:3])}{'...' if len(missing) > 3 else ''}")
        if not clip_guids:
            self.report({"ERROR"}, "The resolved closure exported no AnimationClip for the "
                                   "selected row(s) -- see console.")
            return False

        result = _import_clips_standalone(self, context, state, clip_rows[0]["cab"],
                                          clip_guids, db)
        return result == {"FINISHED"}


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
    provider = Game.secondary_motion_of(_active_game_name(state))
    if provider is None:
        return None
    try:
        result = provider(context, armature, list(cabs))
    except Exception as failure:
        operator.report({"WARNING"}, "Secondary motion was not imported: %s" % failure)
        return None
    if result is None:
        return None
    lines = result.lines()
    operator.report({"INFO"}, lines[0])
    for line in lines[1:]:
        operator.report({"WARNING"}, line)
    return result


class RURI_OT_import_secondary_motion(bpy.types.Operator):
    """The browser's own way in, for a model the game's own tables never name.

    The roster lists the cast the game declares; a model that carries these settings
    carries them whether or not anything declares it, so the rows are the other way in."""

    bl_idname = "ruri.import_secondary_motion"
    bl_label = "Import Cloth"
    bl_description = ("Write the selected model prefab's own hair/cloth/accessory chains "
                      "and their collision volumes onto the active armature, replacing "
                      "whatever it already carried")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        state = context.scene.ruri_cabmap
        armature = context.object
        if armature is None or armature.type != "ARMATURE":
            self.report({"ERROR"}, "Select the armature to write onto.")
            return {"CANCELLED"}
        cabs = list(cabmap_state.selected_cabs())
        if not cabs:
            self.report({"ERROR"}, "Select the character's model prefab row first.")
            return {"CANCELLED"}
        if _run_secondary_motion(self, context, state, armature, cabs) is None:
            self.report({"WARNING"}, "The selected rows state no secondary motion.")
            return {"CANCELLED"}
        return {"FINISHED"}


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


class RURI_UL_cabmap(bpy.types.UIList):
    """A FOLDER row (item.is_folder, only ever present in the browse view --
    see _rebuild_window) is one full-width button that navigates instead of
    selecting. Every column of a FILE row is the SAME click operator
    (full-row click target) so selection works like a file browser:
    plain/Ctrl/Shift clicks all land in RURI_OT_cabmap_click.invoke with
    their modifiers intact. NONE_OR_STATUS emboss keeps unselected rows flat
    like labels while depress=True renders selected file rows as a solid
    highlight bar."""
    bl_idname = "RURI_UL_cabmap"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row = layout.row(align=True)
        row.emboss = "NONE_OR_STATUS"

        if item.is_folder:
            op = row.operator(RURI_OT_cabmap_enter_dir.bl_idname,
                              text=f"{item.folder_name}/  ({item.file_count})", icon="FILE_FOLDER")
            op.folder_name = item.folder_name
            return

        selected = item.selected

        def cell(parent, text):
            op = parent.operator(RURI_OT_cabmap_click.bl_idname, text=text, depress=selected)
            op.index = index

        # Column boundaries come from data's col_*_factor sliders (freely
        # draggable, see RURI_PT_column_widths_popover) instead of hardcoded
        # splits -- each factor is relative to the space LEFT after the
        # previous column (UILayout.split() semantics), not a fraction of the
        # whole row; Source (last cell) always just fills what remains.
        split = row.split(factor=data.col_name_factor, align=True)
        cell(split, item.name or item.cab)
        rest = split.split(factor=data.col_container_factor, align=True)
        cell(rest, item.container or item.cab)
        rest2 = rest.split(factor=data.col_type_factor, align=True)
        cell(rest2, item.type_names)
        tail = rest2.split(factor=data.col_deps_factor, align=True)
        cell(tail, str(item.deps))
        source_and_goto = tail.split(factor=0.88, align=True)
        cell(source_and_goto, item.source)
        goto = source_and_goto.operator(RURI_OT_cabmap_goto_row_folder.bl_idname, text="", icon="FILE_FOLDER")
        goto.index = index


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
    """Column-width sliders for the virtual file list below -- Blender's
    template_list draws each row through hand-rolled split() columns (see
    RURI_UL_cabmap.draw_item), not a native ListView with drag-resizable
    column headers the way the WinForms reference has (MainForm.AssetList.cs's
    virtualListView.Columns.Add(...) widths, which ARE mouse-draggable there).
    This popover is the Blender-native equivalent: every split factor lives on
    the scene as a plain FloatProperty, so each row below is a normal
    click-and-drag Blender slider (and, like any Blender number field, can
    also be typed into directly, or right-click > Reset to Default Value).

    HEADER region for the same reason as RURI_PT_filter_popover: a UI-region
    panel is a sidebar panel, and a categoryless one becomes a "Misc" tab."""
    bl_idname = "RURI_PT_column_widths_popover"
    bl_label = "Column Widths"
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_ui_units_x = 12

    def draw(self, context):
        layout = self.layout
        state = context.scene.ruri_cabmap
        col = layout.column(align=True)
        col.prop(state, "col_name_factor", text="Name", slider=True)
        col.prop(state, "col_container_factor", text="Path", slider=True)
        col.prop(state, "col_type_factor", text="Type", slider=True)
        col.prop(state, "col_deps_factor", text="Deps", slider=True)
        layout.label(text="Source fills whatever's left.", icon="INFO")


class RURI_PT_cabmap(bpy.types.Panel):
    bl_idname = "RURI_PT_cabmap"
    bl_label = "RuriRipper"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RuriRipper"

    def draw(self, context):
        layout = self.layout
        state = context.scene.ruri_cabmap

        if not bootstrap.is_ready():
            err = bootstrap.last_error()
            if err:
                layout.label(text="pythonnet install failed:", icon="ERROR")
                layout.label(text=err[:60])
            else:
                layout.label(text="Installing pythonnet bridge...", icon="INFO")
            return

        top = layout.column()

        # Above the Game Root/Cabmap fields (which belong to the current tab) and on
        # `top`, not `gated`, so tabs open/switch/close before any cabmap is loaded.
        # One tab per INSTALL, labelled with the product name its own build carries.
        # grid_flow rather than a row: a row divides the width it has between however
        # many tabs there are, so every tab gets narrower as more open until each is a
        # few clipped characters. This wraps to a second line instead, keeping each tab
        # wide enough to read its name -- which is the whole point of naming them.
        tab_bar = top.grid_flow(row_major=True, columns=0, even_columns=True,
                                even_rows=False, align=True)
        for key in _open_tab_keys(state):
            one_tab = tab_bar.row(align=True)
            one_tab.operator(RURI_OT_select_install.bl_idname, text=key,
                             depress=(key == state.current_tab)).key = key
            one_tab.operator(RURI_OT_close_tab.bl_idname, text="", icon="X").key = key
        tab_bar.operator(RURI_OT_open_tab.bl_idname, text="", icon="ADD").key = ""

        top.prop(state, "game_root")

        # What the install SAID it is, and which decoder reads it. Both are read from
        # the build itself when the folder is typed; the menu is the override, and it
        # moves this tab alone.
        config = _active_config(state)
        identity = top.row(align=True)
        identity.label(text=("{0} {1}  ·  {2} {3}".format(
            config.game_name, config.game_version or "(no version)",
            config.engine_family or "Unity", config.engine_version or "unknown")
            if config is not None and config.game_name else "No install identity"),
            icon="FILE_3D")
        identity.operator(RURI_OT_reprobe_install.bl_idname, text="", icon="FILE_REFRESH")
        decoder = top.row(align=True)
        decoder.menu(RURI_MT_decoder.bl_idname,
                     text=(config.decoder_id if config is not None and config.decoder_id
                           else "No decoder (plain Unity build)"),
                     icon="MODIFIER")

        # The values this install is READ with beyond its folder, drawn by the module
        # that claims it (an engine family's module, typically) -- above the gate,
        # because an archive key is what makes building the cabmap possible at all.
        # A module stating no such form leaves the block absent; the core never
        # spells an option name.
        module = _module_of(config)
        if config is not None and module is not None and module.source_options is not None:
            module.source_options(top.box(), context, config)

        top.prop(state, "cabmap_path")
        row = top.row(align=True)
        row.operator(RURI_OT_build_cabmap.bl_idname, text="Build")
        row.operator(RURI_OT_load_cabmap.bl_idname, text="Load")

        layout.separator()
        gated = layout.column()
        gated.enabled = state.loaded

        active = _active_tab(state)
        active_key = active.key if active is not None else BROWSER_TAB_ID
        tabs = gated.row(align=True)
        for key, label in _tab_bar(state):
            op = tabs.operator(RURI_OT_select_tab.bl_idname, text=label,
                               depress=(key == active_key))
            op.tab = key

        # ``enabled = False`` greys a layout out; it does NOT stop the code that
        # fills it from running. Every tab below this line is ABOUT the loaded
        # cabmap and reads the bridge to draw itself, so without one the draw has
        # to stop here rather than be painted grey while it runs anyway -- which
        # is how a stored-open tab turned "no session" into an AttributeError
        # thrown out of a draw callback.
        if not state.loaded:
            layout.label(text="Build or load a cabmap to browse/import.", icon="LOCKED")
            return

        if active is None:
            filter_ui.draw_search_row(gated, state)

            if not filter_ui.has_active_query(state):
                # Breadcrumb address bar -- only meaningful in the folder browser; a search/rule
                # result is a flat global match set, not scoped to CURRENT_DIR (see apply_filter).
                crumbs = gated.row(align=True)
                op = crumbs.operator(RURI_OT_cabmap_goto_dir.bl_idname, text="", icon="HOME")
                op.depth = 0
                for depth, segment in enumerate(cabmap_state.CURRENT_DIR, start=1):
                    crumbs.label(text="/")
                    op = crumbs.operator(RURI_OT_cabmap_goto_dir.bl_idname, text=segment)
                    op.depth = depth

            sort_col, sort_dir = cabmap_state.sort_state()
            sort_row = gated.row(align=True)
            for col_key, col_label in _SORT_COLUMNS:
                arrow = ""
                if sort_col == col_key:
                    arrow = " ▲" if sort_dir == 1 else (" ▼" if sort_dir == 2 else "")
                op = sort_row.operator(RURI_OT_cabmap_sort.bl_idname, text=col_label + arrow)
                op.column = col_key
            sort_row.popover(RURI_PT_column_widths_popover.bl_idname, text="", icon="ARROW_LEFTRIGHT")

            gated.template_list(RURI_UL_cabmap.bl_idname, "", state, "window",
                                state, "active_index", rows=12)

            selected_count = len(cabmap_state.SELECTED_CABS)
            select_bar = gated.row(align=True)
            op = select_bar.operator(RURI_OT_cabmap_select_all.bl_idname, text="All")
            op.mode = "ALL"
            op = select_bar.operator(RURI_OT_cabmap_select_all.bl_idname, text="None")
            op.mode = "NONE"
            op = select_bar.operator(RURI_OT_cabmap_select_all.bl_idname, text="Invert")
            op.mode = "INVERT"
            select_bar.separator()
            # Selection count when there is one; the click cheat-sheet otherwise
            # (the full semantics live in each row's tooltip).
            select_bar.label(text=(f"{selected_count} selected" if selected_count
                                   else "Ctrl / Shift · Ctrl+A"))

            row = gated.row(align=True)
            row.label(text=state.status)
            row.menu(RURI_MT_quick_filter.bl_idname, text="", icon="COLLAPSEMENU")

            opts = gated.box()
            opts.prop(state, "detail_level")
            opts.prop(state, "import_materials")
            shader_row = opts.row()
            shader_row.enabled = state.import_materials
            shader_row.prop(state, "character_shaders")
            # 只在着色栈真的会建图时才有意义 —— 不开 Game Shaders 就没有模板组可取。
            link_row = opts.row()
            link_row.enabled = state.import_materials and state.character_shaders
            link_row.prop(state, "link_shader_templates")
            # The texture property names no layer of the role table states, met while
            # building this game's materials: the user says what each one is, once.
            unmapped = material_builder.unresolved_for(_texture_role_game(state))
            if unmapped or len(state.texture_roles):
                roles_box = opts.box()
                head = roles_box.row(align=True)
                head.label(text="Unmapped textures: {0}".format(len(unmapped)), icon="QUESTION")
                head.operator(RURI_OT_texture_roles_refresh.bl_idname, text="", icon="FILE_REFRESH")
                for role_row in state.texture_roles:
                    line = roles_box.row(align=True)
                    line.label(text="{0}  x{1}  {2}".format(role_row.name, role_row.count, role_row.example))
                    line.prop(role_row, "role", text="")
                    if texture_roles.is_channel_role(role_row.role):
                        line.prop(role_row, "channel", text="")
                roles_box.operator(RURI_OT_texture_roles_save.bl_idname, icon="FILE_TICK")
            opts.prop(state, "import_textures")
            opts.prop(state, "import_skeleton")
            opts.prop(state, "import_empties")
            # Only where the current game states what a face IS -- the host never
            # learns which games those are, it asks the registry (see Game.GameModule).
            if Game.face_retarget_of(_active_game_name(state)) is not None:
                face = opts.row(align=True)
                face.active = any(obj.type == "ARMATURE" for obj in context.scene.objects)
                face.prop(state, "retarget_face", icon="USER")
            # Same rule for the settings a model carries about how it swings.
            if Game.secondary_motion_of(_active_game_name(state)) is not None:
                opts.prop(state, "import_secondary_motion", icon="MOD_CLOTH")

            batch = f" {selected_count}" if selected_count > 1 else ""
            actions = gated.row(align=True)
            op = actions.operator(RURI_OT_import_selected.bl_idname, text=f"Import{batch} (Append)")
            op.reset_scene = False
            op = actions.operator(RURI_OT_import_selected.bl_idname, text=f"Import{batch} (Reset Scene)")
            op.reset_scene = True
            op = gated.operator(RURI_OT_cabmap_import_with_dependents.bl_idname,
                               text=f"Import{batch} (With Dependents)", icon="LOOP_BACK")
            op.reset_scene = False
            # A model that carries its own secondary motion carries it whether or not
            # any of the game's tables ever names that model -- so the rows get their
            # own way in, beside the import that would have brought it along.
            if Game.secondary_motion_of(_active_game_name(state)) is not None:
                cloth_row = gated.row(align=True)
                cloth_row.active = (context.object is not None
                                    and context.object.type == "ARMATURE")
                cloth_row.operator(RURI_OT_import_secondary_motion.bl_idname,
                                   icon="MOD_CLOTH")
        else:
            active.draw(gated, context)


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
        clip_rows.sort(key=lambda r: (_clip_folder(r.container_path()).lower(), r["name"].lower()))

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
            item.folder = _clip_folder(row.container_path())
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
                return _import_clips_standalone(self, context, state,
                                                seed_cabs[0] if seed_cabs else None,
                                                selected_guids, db)

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
            refs = discovery.discover_clip_refs(db, prefab_file)
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


class RURI_OT_animation_select_all(bpy.types.Operator):
    """Check every clip the filter is currently showing, or uncheck all of them.

    Checking is deliberately asymmetric: "All" means the rows in front of you
    (filter to a folder, click All, and you have that folder), while "None"
    clears the hidden ones too -- otherwise a filtered-away tick would ride
    along into the import nobody could see it in."""
    bl_idname = "ruri.animation_select_all"
    bl_label = "Select All / None"
    bl_description = "Check every shown animation clip, or uncheck all of them"
    bl_options = {"REGISTER", "UNDO"}
    select: BoolProperty(default=True)

    @classmethod
    def poll(cls, context):
        return len(context.scene.ruri_cabmap.available_clips) > 0

    def execute(self, context):
        for item in context.scene.ruri_cabmap.available_clips:
            if self.select and not item.visible:
                continue
            item.selected = self.select
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
        op = row.operator(RURI_OT_animation_select_all.bl_idname, text="All Shown")
        op.select = True
        op = row.operator(RURI_OT_animation_select_all.bl_idname, text="None")
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
    RURI_PG_cabmap_row,
    RURI_PG_animation_clip,
    RURI_PG_source_option,
    RURI_PG_install_config,
    RURI_PG_texture_role,
    RURI_PG_cabmap,
    RURI_UL_cabmap,
    RURI_UL_animation_clips,
    RURI_OT_cabmap_click,
    RURI_OT_cabmap_enter_dir,
    RURI_OT_cabmap_goto_dir,
    RURI_OT_cabmap_reveal,
    RURI_OT_cabmap_show_rules,
    RURI_OT_cabmap_goto_row_folder,
    RURI_OT_cabmap_select_all,
    RURI_MT_quick_filter,
    RURI_MT_decoder,
    RURI_PT_column_widths_popover,
    RURI_PT_cabmap,
    RURI_OT_set_decoder,
    RURI_OT_reprobe_install,
    RURI_OT_load_source_option_schema,
    RURI_OT_apply_source_options,
    RURI_OT_set_source_option_choice,
    RURI_MT_source_option_choice,
    RURI_OT_open_source_option_choice,
    RURI_OT_build_cabmap,
    RURI_OT_load_cabmap,
    RURI_OT_texture_roles_refresh,
    RURI_OT_texture_roles_save,
    RURI_OT_select_tab,
    RURI_OT_select_install,
    RURI_OT_open_tab,
    RURI_OT_close_tab,
    RURI_OT_cabmap_sort,
    RURI_OT_cabmap_import_with_dependents,
    RURI_OT_import_secondary_motion,
    RURI_OT_import_selected,
    RURI_OT_discover_animations,
    RURI_OT_import_selected_animations,
    RURI_OT_animation_select_all,
    RURI_PT_animation_browser,
)

_addon_keymaps = []


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
        item = keymap.keymap_items.new(RURI_OT_cabmap_select_all.bl_idname, key, "PRESS",
                                       ctrl=use_ctrl, alt=use_alt)
        item.properties.mode = mode
        _addon_keymaps.append((keymap, item))


def _unregister_keymaps():
    for keymap, item in _addon_keymaps:
        keymap.keymap_items.remove(item)
    _addon_keymaps.clear()


def _active_filter_spec_key(context):
    """Which list the shared filter widget is currently editing: the tab on
    screen. The browser is the fallback, exactly as _active_tab treats it."""
    state = getattr(context.scene, "ruri_cabmap", None)
    if state is None:
        return BROWSER_TAB_ID
    active = _active_tab(state)
    return active.key if active is not None else BROWSER_TAB_ID


def register():
    # Before this module's own classes: RURI_PG_cabmap holds a CollectionProperty
    # of filter_ui's rule type, and Blender needs that type registered first.
    filter_ui.register()
    filter_ui.ACTIVE_SPEC_KEY = _active_filter_spec_key
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_cabmap = PointerProperty(type=RURI_PG_cabmap)
    _register_keymaps()


def unregister():
    _unregister_keymaps()
    del bpy.types.Scene.ruri_cabmap
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    filter_ui.ACTIVE_SPEC_KEY = None
    filter_ui.unregister()
    cabmap_state.reset()
    _INSTALL_IDENTITY.clear()
