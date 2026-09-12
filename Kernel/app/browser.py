"""The asset browser's state machine, for every host.

What a browser tab IS -- one INSTALL, identified by the productName its own build
carries -- and everything that follows from that: opening, closing and renaming
tabs, adopting a folder's published identity, resolving which decoder reads it,
the source-option form its decoder declares, the debounced search, and the window
of rows drawn onto the filtered result.

None of it was ever about Blender: it was host-free in all but four places (an
application-relative path, a timer, a redraw, one nested command), and those four
are what :class:`Kernel.host.Host` exists for.

A BROWSER TAB IS AN INSTALL, NOT A GAME. Several can be open at once, including
two copies of one title; a tab's game is what the game registry answers about its
product, and that is what selects its tabs, its face system and its retarget
tables. The upstream still decodes ONE game at a time, so switching tabs
re-selects the decoder.
"""

from __future__ import annotations

import json
import os
import re
import time

from .. import host as host_port
from .. import options as kernel_options
from . import command as app_command
from . import filtering, loading, schemas, texturing
from . import layout as vocabulary
from . import state as app_state
from ...RuriRipperPyBridge.runtime import bootstrap, pythonnet_bridge, workspace
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import class_registry, texture_roles
from ... import Game

BROWSER_TAB_ID = schemas.BROWSER_TAB_ID
BROWSER_TAB_LABEL = schemas.BROWSER_TAB_LABEL
BROWSER_TAB_DESCRIPTION = schemas.BROWSER_TAB_DESCRIPTION
UNNAMED_TAB_PREFIX = schemas.UNNAMED_TAB_PREFIX
SORT_COLUMNS = schemas.SORT_COLUMNS
STATE = "ruri_cabmap"

#: game root -> the identity that install published about itself, as the ONE
#: player the install IS. Cached: an install does not rename itself mid-session,
#: and this is read where a folder is typed, never from a draw.
_INSTALL_IDENTITY = {}

#: Every decoder compiled into the loaded DLL, read ONCE per process: the set is
#: compiled in and pythonnet loads that DLL once, for good. A miss (no bin dir
#: yet) is deliberately not cached, so one early call cannot empty the menu.
_DECODERS = None

#: Debounced search: what was typed last, when, and whether a tick is pending.
_pending_query = ""
_last_edit_time = 0.0
_timer_registered = False


def state_of(context):
    return host_port.current().panel_state(context, STATE)


#: What a browser row shows. A width is the NAME of a live panel property, because
#: the column boundaries are draggable sliders rather than constants; a column with
#: none takes whatever is left.
_ROW_COLUMNS = (
    vocabulary.ListColumn(lambda row: row.name or row.cab, "Name",
                          width="col_name_factor"),
    vocabulary.ListColumn(lambda row: row.container or row.cab, "Path",
                          width="col_container_factor"),
    vocabulary.ListColumn("type_names", "Type", width="col_type_factor"),
    vocabulary.ListColumn(lambda row: str(row.deps), "Deps", width="col_deps_factor"),
    vocabulary.ListColumn("source", "Source"),
)
#: A folder row is one full-width button that navigates instead of selecting.
_FOLDER_COLUMN = vocabulary.ListColumn(
    lambda row: "{0}/  ({1})".format(row.folder_name, row.file_count))


def _add_file_item(state, selected_cabs, idx, name_override=None):
    row = cabmap_state.ROWS.row(idx)
    item = state.window.add()
    item.is_folder = False
    item.row_index = idx
    item.cab = row["cab"]
    item.name = row["name"] if name_override is None else name_override
    item.container = row["container"]
    item.type_names = row["type_names"]
    item.source = row["source"]
    item.deps = int(row["deps"])
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
    filtering.restore_selection): the active index is a position into this
    window, and a search edit or a folder change would otherwise leave it
    pointing at whatever asset happened to land there."""
    with filtering.rebuilding():
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
    #
    # While THIS tab's cabmap is loaded, and only then: with no session of its own
    # there is no folder to observe, and what the question answers instead is the
    # root -- or another tab's folder, since the live session is whichever one was
    # last activated. Either way it is not this tab's, and writing it over the
    # folder the tab was left in is how a panel that reopens where you left it
    # forgets on the way in: the window is rebuilt before anything is loaded.
    if state.loaded:
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
        filtering.restore_selection(state, state.cursor_cab, "window", "active_index", "cab")
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
    filtering.restore_selection(state, state.cursor_cab, "window", "active_index", "cab")


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
    state = state_of(context)
    cabmap_state.refresh_visible(state.search, state.filter_rules)
    _rebuild_window(state)
    _redraw_all(context)


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

    host_port.current().schedule(0.05, _tick)


def _on_search_edit(self, context):
    _schedule_filter(self.search,
                     lambda: (_rebuild_window(state_of(context)), _redraw_all(context)))


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
    _INSTALL_IDENTITY.pop(host_port.current().absolute_path(config.game_root) if config.game_root else "", None)


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
    # By KEY, never by object identity: a host hands back a fresh wrapper
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
        if os.path.normcase(os.path.normpath(host_port.current().absolute_path(config.game_root))) == root:
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


#: What a filename may not carry, so an install's own product name can BE one.
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
    raw = host_port.current().absolute_path(state.cabmap_path) if state.cabmap_path else ""
    if raw and state.current_tab and (raw.endswith(("\\", "/")) or os.path.isdir(raw)):
        state.cabmap_path = os.path.join(raw, _default_cabmap_filename(state.current_tab))


def _resolve_build_output_path(state):
    """Resolve state.cabmap_path into a concrete output FILE path for Build -- belt-and-suspenders
    on top of _auto_default_cabmap_filename (which keeps the field itself defaulted as the user
    goes) in case cabmap_path still ends up bare (e.g. typed/pasted a folder right before
    clicking Build, with no chance for the update callback to run in between). Returns "" if
    there's truly nothing to build a path from."""
    _auto_default_cabmap_filename(state)
    return host_port.current().absolute_path(state.cabmap_path) if state.cabmap_path else ""


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
    identity = _install_identity(host_port.current().absolute_path(config.game_root) if config.game_root else "",
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
    _ensure_source_option_form(config, force=True)
    return config.decoder_id


#: The dropdown member that means "no value stated -- read with the decoder's own
#: default". Blender drops an enum item whose identifier is empty, so the member needs a
#: real one; the value the kernel carries for it is still the empty string.
OPTION_AUTO = "__auto__"


def _source_option_choices(row, context):
    """One choice option's own list, read off the row the decoder filled.

    The leading blank member is the decoder's own default: an option nobody set is
    read with what the decoder said, and a list with no member for that state could
    not say so -- which is what made this a menu with the row identity stashed on
    the window manager instead of a dropdown."""
    choices = [piece for piece in (row.choices or "").split("|") if piece]
    default = row.default or ""
    auto = "(auto: {0})".format(default) if default else "(auto)"
    return [(OPTION_AUTO, auto, "Read with the decoder's own default")] + [
        (choice, choice, "") for choice in choices]


def _option_choices(row):
    return [piece for piece in (row.choices or "").split("|") if piece]


def _rows_to_options(config):
    """The form's current values as {name: value}, every kind restated as the text the
    kernel carries (a flag as "true"/"false", a path as typed)."""
    options = {}
    for row in config.source_option_rows:
        if row.kind == "flag":
            options[row.name] = "true" if row.flag_value else "false"
        elif row.kind == "path":
            options[row.name] = host_port.current().absolute_path(row.path_value) if row.path_value else ""
        elif row.kind == "choice":
            options[row.name] = "" if row.choice_value == OPTION_AUTO else row.choice_value
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
        elif row.kind == "choice":
            # A stored value the decoder no longer offers falls back to its own
            # default rather than being written as a member that does not exist.
            row.choice_value = value if value in _option_choices(row) else OPTION_AUTO
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
        row.effective = table.cell(index, "effective") if "effective" in names else ""
    config.source_option_schema = dataset_id
    _options_to_rows(config, current)


def _load_source_option_rows(config, dataset_id):
    """Read the decoder's schema for this tab's install and fill the form from it -- what
    the Load Options Form button does, run for a tab the moment its decoder resolves."""
    root = host_port.current().absolute_path(config.game_root) if config.game_root else ""
    bridge = cabmap_state.ensure_bridge(config.decoder_id, root, _source_options(config))
    _fill_source_option_rows(config, bridge.game_data(dataset_id), dataset_id)


def _option_row_value(row):
    """The text a form row currently holds, whatever its kind. What the user typed -- which
    is not yet what the install is read with, see _missing_required_options."""
    if row.kind == "flag":
        return "true" if row.flag_value else "false"
    if row.kind == "choice":
        return "" if row.choice_value == OPTION_AUTO else row.choice_value
    return row.path_value if row.kind == "path" else row.value


def _missing_required_options(config):
    """The required options this install cannot be read without that are not IN EFFECT.

    Judged by what the tab has APPLIED -- the values the kernel actually opens the install
    with -- and never by what is typed in the form. A path typed into the field is not a
    value the decoder has until Apply pushes it, so counting it cleared the warning while
    the kernel still had no schema, and the import then failed deep in the decoder instead
    of being refused here. A flag is never missing; a path or a text is missing while the
    applied value is blank."""
    applied = _source_options(config)
    missing = []
    for row in config.source_option_rows:
        if not row.required or row.kind == "flag":
            continue
        if not (applied.get(row.name) or "").strip():
            missing.append(row)
    return missing


def _ensure_source_option_form(config, force=False):
    """Load the tab's options form from its decoder's published schema when a schema is
    declared and the form is not already up (or force), so a required option's warning --
    and the block on importing without it -- are live the moment the decoder is known, not
    only after the user opens the form by hand. Best-effort: a form that cannot be read
    (the install needs a key first) leaves the tab as it was."""
    module = _module_of(config)
    if module is None or not module.settings_schema or not bootstrap.is_ready():
        return
    if not force and config.source_option_schema == module.settings_schema and len(config.source_option_rows):
        return
    try:
        _load_source_option_rows(config, module.settings_schema)
    except Exception as exc:
        print("[RuriRipper] options form for {0} not loaded: {1}: {2}".format(
            config.key, type(exc).__name__, exc))


def _sync_bridge_to_tab(config):
    """Point the bridge at THIS tab's install, decoder and applied options right before a
    crossing. The bridge is process-wide and remembers, per install, the options that
    install's cabmap was loaded under, so restating here is what makes the tab the single
    source of truth at the moment of use. A no-op when it already matches; a tab with no
    folder states nothing."""
    if not config.game_root:
        return
    cabmap_state.ensure_bridge(config.decoder_id, host_port.current().absolute_path(config.game_root),
                              _source_options(config))


def _blocking_required_options(config):
    """The message to refuse an import with when a required option is unset, else "". The
    form is loaded first so a tab whose form was never opened by hand is still gated."""
    _ensure_source_option_form(config)
    missing = _missing_required_options(config)
    if not missing:
        return ""
    names = ", ".join(row.name for row in missing)
    return ("This build cannot be read without {0}. Set it in this tab's Load Options Form "
            "and click Apply, then import again.").format(names)


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
    root = host_port.current().absolute_path(config.game_root) if config.game_root else ""
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


# ---------------------------------------------------------------------------
# Texture roles: which property is which input, as the user states it
# ---------------------------------------------------------------------------
# The role table is DATA in layers (RuriRipperPyBridge.unity.texture_roles): the
# default layer is Unity's own vocabulary, a title module's folder holds that
# game's, and what the user decides in front of the names no layer states goes
# into the title's folder when the install has one, else beside the host's own
# presets under the game's own name.
_TEXTURE_ROLES_DIR = "TextureRoles"
_UNMAPPED_ROLE = schemas.UNMAPPED_ROLE


def _texture_roles_user_dir():
    """Where a user's own role layers live: this application's preset folder when
    it has one, because they are the user's data and belong where that application
    already shows them everything else they saved. Only a host with no such place
    files them under the workspace, which otherwise holds generated things."""
    preset = host_port.current().preset_dir()
    if not preset:
        return workspace.subdir(_TEXTURE_ROLES_DIR)
    path = os.path.join(preset, _TEXTURE_ROLES_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def _texture_role_layers(state):
    """(game, game layer, user layer, save target) for the current install. A title
    module -- one declared for THIS product -- owns its layer and takes the user's
    choices; a family module or no module at all leaves them to the preset folder."""
    game = _texture_role_game(state)
    module = _module_of(_active_config(state))
    game_layer = (os.path.join(module.directory, texture_roles.DEFAULT_LAYER_NAME)
                  if module is not None and module.directory else None)
    user_layer = os.path.join(_texture_roles_user_dir(), (game or "unknown") + ".json")
    owns = (game_layer is not None and bool(game)
            and module.game_name.lower() == game.lower())
    return game, game_layer, user_layer, (game_layer if owns else user_layer)


def _texture_role_game(state):
    """The game a role layer is filed under: the PRODUCT the install published (a
    Unity game's productName, an Unreal project's name), not the family module that
    reads it -- two Unreal titles are two vocabularies, not one."""
    config = _active_config(state)
    product = (config.game_name if config is not None else "") or ""
    return product or _active_game_name(state)


def _texture_role_table(state):
    _game, game_layer, user_layer, _save = _texture_role_layers(state)
    return texture_roles.RoleTable.load(texture_roles.layer_paths(game_layer, user_layer))


def _sync_texture_roles(state):
    """Mirror the builder's unmapped names into the rows the panel draws, keeping the
    role the user already picked for a name still listed. Called where materials were
    just built -- a draw may not write these rows."""
    game = _texture_role_game(state)
    unresolved = texturing.unresolved_for(game)
    chosen = {row.name: (row.role, row.channel) for row in state.texture_roles}
    state.texture_roles.clear()
    for name, entry in unresolved.items():
        row = state.texture_roles.add()
        row.name = name
        row.count = int(entry["count"])
        row.example = "{0} / {1}".format(entry["material"], entry["texture"])
        if name in chosen:
            row.role, row.channel = chosen[name]


def as_options(self, scene=False):
    """Every import option this host honours, plus what the SESSION knows that
    no switch does.

    The options are read off the ONE table that declares them, so a key here and
    the key an importer reads cannot drift apart. ``scene=True`` is the scene
    window / display stage road: identical but for which of the two remembered
    Game Shaders answers it takes (see scene_shaders)."""
    values = {}
    for entry in kernel_options.schema():
        value = getattr(self, entry.key)
        values[entry.key] = int(value) if entry.kind == kernel_options.INT else value
    if scene and "game_shaders" in values:
        values["game_shaders"] = self.scene_shaders
    # THE game this session is looking at, resolved exactly once here and stamped
    # onto every armature the import builds -- what a later cross-game retarget
    # selects its table by.
    values["source_game"] = _active_game_name(self)
    # Which property is which input, for THIS install: the default layer, the game
    # module's own and the user's, merged in that order.
    values["texture_roles"] = _texture_role_table(self)
    values["texture_roles_game"] = _texture_role_game(self)
    return values


def _active_filter_spec_key(context):
    """Which list the shared filter widget is currently editing: the tab on
    screen. The browser is the fallback, exactly as _active_tab treats it.

    Reached through the host's own panel-state lookup rather than off a scene:
    where this state is kept is precisely what the two hosts do not agree on, and
    a rule editor that can only find it in one of them opens empty in the other."""
    try:
        state = state_of(context)
    except KeyError:
        return BROWSER_TAB_ID
    active = _active_tab(state)
    return active.key if active is not None else BROWSER_TAB_ID


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def _say(context, message):
    """One line for the user. A panel shows it; nothing here knows how."""
    state_of(context).status = message


def _announce(context, message, level=host_port.INFO):
    """A command's outcome, in the place both hosts show it and in the console.

    The status line is what the panel draws -- Blender's own operator report is a
    toast this side cannot reach and Painter has no equivalent of, so the one
    channel that reads the same in both is the line the panel already has."""
    _say(context, message)
    host_port.current().log(level, message)


def _report_exception(context, prefix, exc):
    import traceback
    traceback.print_exc()
    _say(context, "{0}: {1}: {2}".format(prefix, type(exc).__name__, exc))


def _set_decoder(context, arguments):
    """Read THIS tab's install through one named decoder, overriding what its identity
resolved to. Only this tab moves: another tab's install keeps its own decoder and
its own loaded cabmap (the bridge switches between them per install)."""
    state = state_of(context)
    config = _ensure_active_config(state)
    config.decoder_id = arguments["decoder_id"]
    _set_current_tab(state, config.key)
    _redraw_all(context)
    return None


SET_DECODER = app_command.COMMANDS.define(
    "ruri.set_decoder", "Decoder", _set_decoder,
    description="Read THIS tab's install through one named decoder, overriding what its identity resolved to. Only this tab moves: another tab's install keeps its own decoder and its own loaded cabmap (the bridge switches between them per install).",
    internal=True,
    arguments=(app_state.Field("decoder_id", app_state.STRING, None),))

def _apply_source_options(context, arguments):
    """Push the form's values to the kernel and re-read the install under them -- an
archive key can be what makes the install readable, so its identity and decoder
are resolved again."""
    state = state_of(context)
    config = _ensure_active_config(state)
    set_source_options(config, _rows_to_options(config))
    try:
        decoder = _adopt_identity(state, config)
    except Exception as exc:
        _report_exception(context, "Apply options failed", exc)
        return {"CANCELLED"}
    if config.game_name:
        _rename_tab(state, config,
                    _unique_tab_key(state, config.game_name, held_by_key=config.key))
    _set_current_tab(state, config.key)
    _auto_default_cabmap_filename(state)
    _say(context, "{0} · {1}".format(config.game_name or "no identity", decoder or "no decoder"))
    _redraw_all(context)
    return None


def _apply_source_options_poll(context):
    return bootstrap.is_ready()


APPLY_SOURCE_OPTIONS = app_command.COMMANDS.define(
    "ruri.apply_source_options", "Apply", _apply_source_options,
    description="Push the form's values to the kernel and re-read the install under them -- an archive key can be what makes the install readable, so its identity and decoder are resolved again.",
    internal=True,
    poll=_apply_source_options_poll,
)

def _texture_roles_refresh(context, arguments):
    """List the texture property names the last imports could not map"""
    _sync_texture_roles(state_of(context))
    return None


TEXTURE_ROLES_REFRESH = app_command.COMMANDS.define(
    "ruri.texture_roles_refresh", "List Unmapped Textures", _texture_roles_refresh,
    description='List the texture property names the last imports could not map',
    internal=True,
)

def _select_tab(context, arguments):
    """One button of the CONTENT tab row (browser / this game's own tabs).
Buttons rather than an expanded EnumProperty because which tabs exist depends on
which game the current install is -- see RURI_PG_cabmap.active_tab."""
    state_of(context).active_tab = arguments["tab"]
    _redraw_all(context)
    return None


SELECT_TAB = app_command.COMMANDS.define(
    "ruri.select_tab", "Tab", _select_tab,
    description="One button of the CONTENT tab row (browser / this game's own tabs). Buttons rather than an expanded EnumProperty because which tabs exist depends on which game the current install is -- see RURI_PG_cabmap.active_tab.",
    internal=True,
    arguments=(app_state.Field("tab", app_state.STRING, None),))

def _select_install(context, arguments):
    """Click a tab in the always-visible tab bar: point the browser (and the
cabmap_state session behind it) at that install's cabmap."""
    _switch_current_tab(state_of(context), arguments["key"], context)
    _redraw_all(context)
    return None


SELECT_INSTALL = app_command.COMMANDS.define(
    "ruri.select_install", "Install", _select_install,
    description="Click a tab in the always-visible tab bar: point the browser (and the cabmap_state session behind it) at that install's cabmap.",
    internal=True,
    arguments=(app_state.Field("key", app_state.STRING, None),))

def _add_tab(state, key, context):
    """Open a fresh unnamed tab and switch to it. The next folder typed into it is
    what names it."""
    _ensure_tab(state, key or _next_unnamed_key(state))
    _switch_current_tab(state, key or _tab_keys(state)[-1], context)


def _open_tab(context, arguments):
    """Open a fresh, EMPTY browser tab and switch to it. That is the whole of it: the
tab is unnamed until a folder is typed into it, which then names it after that
build's own product. Nothing is preset and nothing is remembered -- which install
a user wants is the folder they pick, not a list this add-on keeps."""
    _add_tab(state_of(context), arguments["key"], context)
    _redraw_all(context)
    return None


OPEN_TAB = app_command.COMMANDS.define(
    "ruri.open_tab", "Open Tab", _open_tab,
    description="Open a fresh, EMPTY browser tab and switch to it. That is the whole of it: the tab is unnamed until a folder is typed into it, which then names it after that build's own product. Nothing is preset and nothing is remembered -- which install a user wants is the folder they pick, not a list this add-on keeps.",
    internal=True,
    arguments=(app_state.Field("key", app_state.STRING, None),))

def _drop_tab(state, key, context):
    """Close one tab -- remove its config entry, release its browser session, and
    hand focus to a remaining tab (a fresh unnamed one when none is left, so the
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


def _close_tab(context, arguments):
    """The tab's x button: close one install's tab -- drop its config entry and its
browser session, and hand focus to a remaining tab."""
    _drop_tab(state_of(context), arguments["key"], context)
    _redraw_all(context)
    return None


CLOSE_TAB = app_command.COMMANDS.define(
    "ruri.close_tab", "Close Tab", _close_tab,
    description="The tab's x button: close one install's tab -- drop its config entry and its browser session, and hand focus to a remaining tab.",
    internal=True,
    arguments=(app_state.Field("key", app_state.STRING, None),))

def _cabmap_sort(context, arguments):
    cabmap_state.cycle_sort(arguments["column"])
    _rebuild_window(state_of(context))
    return None


def _cabmap_sort_poll(context):
    return state_of(context).loaded


CABMAP_SORT = app_command.COMMANDS.define(
    "ruri.cabmap_sort", "Sort", _cabmap_sort,
    description='',
    poll=_cabmap_sort_poll,
    arguments=(app_state.Field("column", app_state.STRING, None),))

def _cabmap_enter_dir(context, arguments):
    """Click on a folder row (the list's own group column) -- descends one level
under CURRENT_DIR. Selection is deliberately left untouched: it's keyed
by cab (see cabmap_state.SELECTED_CABS), not by which folder you're
looking at, so multi-selecting files across several folder visits and
then batch-importing them all at once keeps working."""
    state = state_of(context)
    cabmap_state.browse_dir(cabmap_state.CURRENT_DIR + (arguments["folder_name"],))
    _rebuild_window(state)
    _redraw_all(context)
    return None


def _cabmap_enter_dir_poll(context):
    return state_of(context).loaded


CABMAP_ENTER_DIR = app_command.COMMANDS.define(
    "ruri.cabmap_enter_dir", "Open Folder", _cabmap_enter_dir,
    description='Browse into this virtual folder',
    internal=True,
    poll=_cabmap_enter_dir_poll,
    arguments=(app_state.Field("folder_name", app_state.STRING, None),))

def _cabmap_goto_dir(context, arguments):
    """Breadcrumb click -- jump straight to one ancestor level of CURRENT_DIR
(depth=0 is the virtual root) instead of stepping out one folder at a
time."""
    state = state_of(context)
    cabmap_state.browse_dir(cabmap_state.CURRENT_DIR[:arguments["depth"]])
    _rebuild_window(state)
    _redraw_all(context)
    return None


def _cabmap_goto_dir_poll(context):
    return state_of(context).loaded


CABMAP_GOTO_DIR = app_command.COMMANDS.define(
    "ruri.cabmap_goto_dir", "Go to Folder", _cabmap_goto_dir,
    description='Jump to this level of the virtual path',
    internal=True,
    poll=_cabmap_goto_dir_poll,
    arguments=(app_state.Field("depth", app_state.INT, None),))

def _cabmap_goto_row_folder(context, arguments):
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
    state = state_of(context)
    if not (0 <= arguments["index"] < len(state.window)):
        return {"CANCELLED"}
    item = state.window[arguments["index"]]
    if item.is_folder:
        return {"CANCELLED"}

    target_cab = item.cab
    folder = cabmap_state.folder_of(item.row_index, state.search)
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
    return None


def _cabmap_goto_row_folder_poll(context):
    return state_of(context).loaded


CABMAP_GOTO_ROW_FOLDER = app_command.COMMANDS.define(
    "ruri.cabmap_goto_row_folder", "Go to Containing Folder", _cabmap_goto_row_folder,
    description="Jump the folder browser to this file's virtual folder",
    internal=True,
    poll=_cabmap_goto_row_folder_poll,
    arguments=(app_state.Field("index", app_state.INT, None),))

def _cabmap_select_all(context, arguments):
    """Select All / None / Invert over the FILTERED row set (everything the
current search+rules match, not just the capped display window) -- bound
to Ctrl+A / Alt+A / Ctrl+I while the cursor is over the RuriRipper
sidebar, and mirrored as the All/None/Invert buttons under the list."""
    state = state_of(context)
    selection = cabmap_state.SELECTED_CABS
    visible_cabs = [cabmap_state.ROWS.cell(i, "cab") for i in cabmap_state.VISIBLE]
    if arguments["mode"] == "ALL":
        selection.update(visible_cabs)
    elif arguments["mode"] == "NONE":
        cabmap_state.clear_selection()
    else:
        for cab in visible_cabs:
            if cab in selection:
                selection.discard(cab)
            else:
                selection.add(cab)
    _sync_window_selection(state)
    _redraw_all(context)
    return None


def _cabmap_select_all_poll(context):
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


CABMAP_SELECT_ALL = app_command.COMMANDS.define(
    "ruri.cabmap_select_all", "Select All Rows", _cabmap_select_all,
    description='Select All / None / Invert over the FILTERED row set (everything the current search+rules match, not just the capped display window) -- bound to Ctrl+A / Alt+A / Ctrl+I while the cursor is over the RuriRipper sidebar, and mirrored as the All/None/Invert buttons under the list.',
    internal=True,
    poll=_cabmap_select_all_poll,
    arguments=(app_state.Field("mode", app_state.ENUM, None, items=(('ALL', 'All', 'Select every row matching the current filter'), ('NONE', 'None', 'Clear the selection'), ('INVERT', 'Invert', 'Invert the selection within the current filter'))),))

def _animation_select_all(context, arguments):
    """Check every clip the filter is currently showing, or uncheck all of them.

Checking is deliberately asymmetric: "All" means the rows in front of you
(filter to a folder, click All, and you have that folder), while "None"
clears the hidden ones too -- otherwise a filtered-away tick would ride
along into the import nobody could see it in."""
    for item in state_of(context).available_clips:
        if arguments["select"] and not item.visible:
            continue
        item.selected = arguments["select"]
    return None


def _animation_select_all_poll(context):
    return len(state_of(context).available_clips) > 0


ANIMATION_SELECT_ALL = app_command.COMMANDS.define(
    "ruri.animation_select_all", "Select All / None", _animation_select_all,
    description='Check every shown animation clip, or uncheck all of them',
    poll=_animation_select_all_poll,
    arguments=(app_state.Field("select", app_state.BOOL, True),))


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------
#: Ids the description names for surfaces a host registers itself: the column
#: width sliders (a popover), the per-row quick-filter and the decoder list
#: (menus).
COLUMN_WIDTHS_PANEL = "RURI_PT_column_widths_popover"
DECODER_MENU = "RURI_MT_decoder"


# ---------------------------------------------------------------------------
# How this install is READ: the decoder's own options form
# ---------------------------------------------------------------------------
def _bridge_ready(context):
    return bootstrap.is_ready()


def _load_source_option_schema(context, arguments):
    """Read which values this install is READ with off its decoder -- the decoder
publishes them as a dataset -- and lay the form out from that. A decoder that
publishes no such dataset leaves the form empty."""
    config = _ensure_active_config(state_of(context))
    try:
        _load_source_option_rows(config, arguments["dataset_id"])
    except Exception as exc:
        _report_exception(context, "Load options form failed", exc)
        return {"CANCELLED"}
    _redraw_all(context)
    return None


LOAD_SOURCE_OPTION_SCHEMA = app_command.COMMANDS.define(
    "ruri.load_source_option_schema", "Load Options Form", _load_source_option_schema,
    description="Read which values this install is READ with off its decoder and lay the form out from that",
    icon="PREFERENCES", internal=True, poll=_bridge_ready,
    arguments=(app_state.Field("dataset_id", app_state.STRING, ""),))


def draw_source_options(layout, context, config, dataset_id):
    """The generic form for how an install is READ: rows of the decoder's published
    schema, one widget per kind, a Load button while the form is unknown, an Apply
    button that pushes the values and re-reads the install.

    A game module names only WHICH dataset states its schema (``settings_schema``),
    so no option name is spelled anywhere on this side and a decoder that adds one
    needs no edit at all."""
    if not config.source_option_rows or config.source_option_schema != dataset_id:
        pending = layout.box()
        pending.alert = True
        pending.label(text="How this install is read is not loaded yet: the values it "
                           "cannot be read without (a reflection schema, archive keys) "
                           "are set in this form.", icon="ERROR")
        layout.operator(LOAD_SOURCE_OPTION_SCHEMA.id,
                        icon="PREFERENCES").dataset_id = dataset_id
        return
    missing = _missing_required_options(config)
    for row in missing:
        warning = layout.box()
        warning.alert = True
        if (_option_row_value(row) or "").strip():
            warning.label(text="Typed but not applied: {0}".format(row.name), icon="ERROR")
            warning.label(text="Click Apply to read this install with it.")
        else:
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
            line.prop(row, "choice_value", text=row.name)
        else:
            line.prop(row, "value", text=row.name)
        # An option the decoder already answered says so beside its own field. A
        # recognised title brings its engine, its archive keys and its reflection
        # schema with it, and a row left blank would read as "still to be found"
        # when the install is in fact already open on exactly that value.
        if row.effective and not (_option_row_value(row) or "").strip():
            line.label(text=row.effective, icon="CHECKMARK")
    layout.operator(APPLY_SOURCE_OPTIONS.id, icon="CHECKMARK")


# ---------------------------------------------------------------------------
# The install, the map, and the roles the user decided
# ---------------------------------------------------------------------------
def _reprobe_install(context, arguments):
    """Re-read what this tab's folder says it is, and re-resolve its decoder from
that. Needed only when the identity could not be read at the moment the folder was
typed -- a session opened before the bridge was up, or a bin dir configured
afterwards. Everything it does is what typing the folder already does."""
    state = state_of(context)
    config = _ensure_active_config(state)
    _INSTALL_IDENTITY.pop(
        host_port.current().absolute_path(config.game_root) if config.game_root else "", None)
    try:
        decoder = _adopt_identity(state, config)
    except Exception as exc:
        _report_exception(context, "Re-read install failed", exc)
        return {"CANCELLED"}
    if config.game_name:
        _rename_tab(state, config,
                    _unique_tab_key(state, config.game_name, held_by_key=config.key))
    _set_current_tab(state, config.key)
    _auto_default_cabmap_filename(state)
    _announce(context, "{0} {1} \u00b7 {2} {3} \u00b7 {4}".format(
        config.game_name or "no identity", config.game_version or "(no version)",
        config.engine_family or "Unity", config.engine_version or "unknown",
        decoder or "no decoder"))
    _redraw_all(context)
    return None


REPROBE_INSTALL = app_command.COMMANDS.define(
    "ruri.reprobe_install", "Re-read Install", _reprobe_install,
    description="Re-read this install's own identity (product, engine version) and re-resolve its decoder",
    icon="FILE_REFRESH", poll=_bridge_ready)


def _build_cabmap(context, arguments):
    """Scan the game root and build a fresh cabmap.

No decoder is a VALID configuration, not a missing prerequisite: a plain
un-bundled/un-encrypted Unity player build needs none at all -- the generic scan
handles it, with readable names harvested straight from the assets' own m_Name
fields. Decoders are only for games with custom encryption/VFS/typetree drift."""
    state = state_of(context)
    root = host_port.current().absolute_path(state.game_root) if state.game_root else ""
    if not root or not os.path.isdir(root):
        _announce(context, "Pick a valid game root directory first.", host_port.ERROR)
        return
    out = _resolve_build_output_path(state)
    if not out:
        _announce(context, "Pick an output path for the cabmap file first.", host_port.ERROR)
        return
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
    except OSError as exc:
        _announce(context, "Can't create output folder '{0}': {1}".format(
            os.path.dirname(out), exc), host_port.ERROR)
        return
    config = _ensure_active_config(state)
    options = _source_options(config)
    bridge = yield app_command.Read(
        lambda: cabmap_state.ensure_bridge(config.decoder_id, root, options), 0.05)
    code = yield app_command.Read(lambda: bridge.build_cab_map(root, out), 0.7)
    if code != 0:
        _announce(context, "Build failed (exit {0}) -- see console.".format(code),
                  host_port.ERROR)
        return
    yield app_command.Read(lambda: bridge.load_cab_map(out, key=config.key), 0.8)
    cabmap_state.activate(config.key, _module_game_name(config))
    yield app_command.Read(
        lambda: cabmap_state.load_rows(cabmap_state.key_to_dir(state.browsed_dir)), 0.95)
    loading.forget_archives()
    _reapply_and_refresh(context)
    if not len(cabmap_state.ROWS):
        # A game's bundles are only readable through that game's OWN decoder, and the
        # process runs one decoder at a time. Scanning a real game folder through a
        # FOREIGN game's decoder yields an empty map and a success code, so the
        # emptiness is the only place that mismatch can still be caught.
        _announce(context, (
            "Built 0 CABs from '{0}'. This build decoded with {1} -- a game's bundles "
            "are only readable through its own decoder. Pick this install's decoder on "
            "this tab, then build again.").format(
                root, config.decoder_id or "no decoder"), host_port.ERROR)
        return
    _announce(context, "Cabmap built: {0} CABs.".format(len(cabmap_state.ROWS)))


BUILD_CABMAP = app_command.COMMANDS.define(
    "ruri.build_cabmap", "Build", _build_cabmap,
    description="Scan the game root and build a fresh cabmap (can take a long time for a full game)",
    poll=_bridge_ready, steps=True, status_state=STATE, failure="Build cabmap failed")


def _load_cabmap(context, arguments):
    """Load an existing cabmap file -- required before browsing/importing anything."""
    state = state_of(context)
    path = host_port.current().absolute_path(state.cabmap_path) if state.cabmap_path else ""
    if not path or not os.path.isfile(path):
        _announce(context, "Pick a valid cabmap file first.", host_port.ERROR)
        return
    config = _ensure_active_config(state)
    root = host_port.current().absolute_path(state.game_root) if state.game_root else ""
    options = _source_options(config)
    bridge = yield app_command.Read(
        lambda: cabmap_state.ensure_bridge(config.decoder_id, root, options), 0.05)
    yield app_command.Read(lambda: bridge.load_cab_map(path, key=config.key), 0.5)
    cabmap_state.activate(config.key, _module_game_name(config))
    # Land back on the folder the user was browsing (persisted per install), not the
    # root -- browse_dir falls back to the root if this map has no such folder, so a
    # key left over from a different game is harmless.
    yield app_command.Read(
        lambda: cabmap_state.load_rows(cabmap_state.key_to_dir(state.browsed_dir)), 0.9)
    loading.forget_archives()
    _reapply_and_refresh(context)
    gone, rows = loading.unreachable_rows()
    if gone:
        _announce(context, (
            "{0} CAB(s) in this map live in {1} archive(s) the install no longer has -- "
            "the game replaced them in a patch. Anything filed there loads as empty "
            "until the cabmap is rebuilt.").format(rows, gone), host_port.WARNING)
        return
    _announce(context, "Cabmap loaded: {0} CABs.".format(len(cabmap_state.ROWS)))


LOAD_CABMAP = app_command.COMMANDS.define(
    "ruri.load_cabmap", "Load", _load_cabmap,
    description="Load an existing cabmap file -- required before browsing/importing anything",
    poll=_bridge_ready, steps=True, status_state=STATE, failure="Load cabmap failed")


def _texture_roles_save(context, arguments):
    """Write every decided role into this game's texture role layer; materials built
from now on resolve through it."""
    state = state_of(context)
    game, _game_layer, _user_layer, save_path = _texture_role_layers(state)
    entries = {row.name: texture_roles.entry_for(row.role, int(row.channel))
               for row in state.texture_roles if row.role != _UNMAPPED_ROLE}
    if not entries:
        _announce(context, "No role chosen yet.", host_port.WARNING)
        return {"CANCELLED"}
    texture_roles.save_entries(save_path, entries)
    texturing.forget(game, entries.keys())
    _sync_texture_roles(state)
    _announce(context, "Saved {0} role(s) to {1}; re-import to rebuild the materials.".format(
        len(entries), save_path))
    return None


TEXTURE_ROLES_SAVE = app_command.COMMANDS.define(
    "ruri.texture_roles_save", "Save Texture Roles", _texture_roles_save,
    description="Write every decided role into this game's texture role layer; materials built from now on resolve through it",
    icon="FILE_TICK")


# ---------------------------------------------------------------------------
# Selecting rows, and importing what is selected
# ---------------------------------------------------------------------------
def _selected_row(state):
    """The row under the cursor, or None when the cursor is on a folder."""
    if 0 <= state.active_index < len(state.window):
        item = state.window[state.active_index]
        if not item.is_folder:
            return item
    return None


def _selected_target_rows(state):
    """The row batch an import operates on: the multi-selection in master ROWS
    order, falling back to the row under the cursor so the click-then-import
    muscle memory keeps working when nothing is explicitly multi-selected."""
    rows = cabmap_state.selected_row_dicts()
    if rows:
        return rows
    item = _selected_row(state)
    if item is None:
        return []
    row = cabmap_state.rows_by_cab().get(item.cab)
    return [row] if row is not None else []


def _row_is_clip_only(row):
    """A row that hosts AnimationClips and no importable GameObject hierarchy --
    selecting it and clicking Import means "import these clips", not "import a
    prefab" (a clip CAB's closure contains no .prefab at all)."""
    return "AnimationClip" in row["type_names"] and "GameObject" not in row["type_names"]


def _cabmap_click(context, arguments):
    """Row click with file-browser selection semantics.

    The modifiers are ARGUMENTS rather than something read off an event here: what
    key was held is the one fact about a click only the application knows, so each
    host fills them from its own event and the rule they mean is written once."""
    state = state_of(context)
    index = arguments["index"]
    if not (0 <= index < len(state.window)):
        return {"CANCELLED"}
    item = state.window[index]
    selection = cabmap_state.SELECTED_CABS
    rows_index = item.row_index

    if arguments["range"]:
        # Range anchor->clicked over the CURRENT filtered+sorted order (what the
        # user is looking at). Both endpoints are clickable so both sit inside the
        # display window; an anchor that has since been filtered away degrades to a
        # single-row range.
        visible = cabmap_state.VISIBLE
        anchor = cabmap_state.SELECT_ANCHOR
        try:
            clicked_at = visible.index(rows_index)
        except ValueError:
            return {"CANCELLED"}
        try:
            anchor_at = visible.index(anchor) if anchor is not None else clicked_at
        except ValueError:
            anchor_at = clicked_at
        low, high = sorted((anchor_at, clicked_at))
        spanned = {cabmap_state.ROWS.cell(position, "cab")
                   for position in visible[low:high + 1]}
        if not arguments["extend"]:
            selection.clear()
        selection.update(spanned)
        # The anchor deliberately stays put: successive range clicks re-pivot around
        # the same anchor, the standard file-browser behaviour.
    elif arguments["extend"]:
        if item.cab in selection:
            selection.discard(item.cab)
        else:
            selection.add(item.cab)
        cabmap_state.set_select_anchor(rows_index)
    else:
        selection.clear()
        selection.add(item.cab)
        cabmap_state.set_select_anchor(rows_index)

    state.active_index = index
    state.cursor_cab = item.cab
    _sync_window_selection(state)
    _redraw_all(context)
    return None


CABMAP_CLICK = app_command.COMMANDS.define(
    "ruri.cabmap_click", "Select Row", _cabmap_click,
    description=("Select this row.\n"
                 "\u2022 Click: select only this row\n"
                 "\u2022 Ctrl+Click: toggle this row\n"
                 "\u2022 Shift+Click: select the range from the last clicked row\n"
                 "\u2022 Ctrl+Shift+Click: add that range to the selection"),
    internal=True, modifiers=True,
    arguments=(app_state.Field("index", app_state.INT, 0),))


def _importable_rows(context, state, config):
    """(rows, message) -- the selection an import would act on, or why it cannot."""
    blocked = _blocking_required_options(config)
    if blocked:
        return [], blocked
    _sync_bridge_to_tab(config)
    rows = _selected_target_rows(state)
    if not rows:
        return [], "No rows selected."
    return rows, ""


def _import_selected(context, arguments):
    """Import every selected row, sharing ONE dependency closure.

    A mixed selection resolves one union closure rather than two: a clip row's
    closure covers most of what a character row's does, so a second resolve
    re-read everything the first had just read.

    Nothing here builds anything. What a row IS -- a hierarchy, a loose asset, a
    performance -- is read off the closure the same way on either host, and what
    building it MEANS is the host's one import entry."""
    state = state_of(context)
    config = _ensure_active_config(state)
    rows, refused = _importable_rows(context, state, config)
    if refused:
        _announce(context, refused, host_port.ERROR)
        return
    host = host_port.current()
    options = as_options(state)
    if arguments["reset_scene"]:
        if host_port.SCENE_GRAPH not in host.capabilities:
            _announce(context, "This host has no scene to reset.", host_port.ERROR)
            return
        host.clear_scene(context)

    module = _module_of(config)
    if module is not None and module.importer is not None:
        # A build this host does not read natively states its own importer: the
        # closure road below is what a Unity build needs and pure overhead here.
        packages = [row["cab"] for row in rows]
        try:
            built = module.importer(context, packages, options)
        except Exception as exc:
            _report_exception(context, "Import failed", exc)
            return
        _announce(context, "{0} object(s) from {1} package(s).".format(
            len(built), len(packages)) if built else
            "The {0} selected package(s) built nothing.".format(len(packages)),
            host_port.INFO if built else host_port.WARNING)
        _redraw_all(context)
        return

    clips = [row for row in rows if _row_is_clip_only(row)]
    assets = [row for row in rows if not _row_is_clip_only(row)]
    if clips and host_port.ANIMATION not in host.capabilities:
        _announce(context, "{0} selected row(s) hold only animation, which this host "
                           "cannot put on anything.".format(len(clips)), host_port.WARNING)
        clips = []
    if not assets and not clips:
        return
    if arguments["reset_scene"] and not assets:
        _announce(context, "An animation needs an existing skeleton -- import without "
                           "resetting so the rig survives.", host_port.ERROR)
        return

    asset_cabs = [row["cab"] for row in assets]
    clip_cabs = list(dict.fromkeys(row["cab"] for row in clips))
    if assets and clips:
        resolved = yield app_command.Read(
            lambda: loading.resolve_union(asset_cabs, clip_cabs), 0.6)
        asset_closure = clip_closure = resolved
    else:
        asset_closure = clip_closure = None
        if assets:
            asset_closure = yield app_command.Read(
                lambda: loading.resolve_closure(asset_cabs), 0.6)
        if clips:
            # Export-side allowlist: this flow reads nothing but the exported clips.
            clip_closure = yield app_command.Read(
                lambda: loading.resolve_closure(
                    clip_cabs,
                    export_class_ids=[class_registry.id_for_name("AnimationClip")]), 0.6)

    yield app_command.Mark(0.7)
    lines = []
    imported = 0
    if assets:
        # Hierarchy/asset rows first: a co-selected character import may create the
        # very rig the clip rows then attach onto.
        built = host.import_packages(
            context,
            loading.Packages(assets[0]["cab"], assets[0]["name"], loading.PREFAB,
                             asset_cabs, named_roots=arguments["only_root_names"]),
            options, lines, asset_closure)
        imported = built.imported
        lines.extend(built.warnings)
    if clips:
        guids, missing = _clip_guids(clips, clip_closure["clips_by_cab"])
        if missing:
            lines.append("{0} selected row(s) exported no AnimationClip: {1}".format(
                len(missing), ", ".join(missing[:3])))
        if not guids:
            lines.append("The resolved closure exported no AnimationClip for the "
                         "selected row(s).")
        else:
            built, clip_lines = host.import_clips(
                context, clips[0]["cab"], guids, clip_closure["db"], options)
            imported += built
            lines.extend(clip_lines)

    _sync_texture_roles(state)
    _reapply_and_refresh(context)
    _announce(context, "  ".join(
        ["Imported {0} root(s) from {1} selected row(s).".format(imported, len(rows))]
        + lines[:3]), host_port.INFO if imported else host_port.WARNING)
    _redraw_all(context)


def _clip_guids(clip_rows, clips_by_cab):
    """The real clip guids the selected rows carry, through the cabmap's own CAB
    identity, plus the rows that exported none."""
    guids = []
    missing = []
    for row in clip_rows:
        found = clips_by_cab.get(row["cab"].lower(), [])
        if not found:
            missing.append(row["name"])
        for guid in found:
            if guid not in guids:
                guids.append(guid)
    return guids, missing


def _import_poll(context):
    state = state_of(context)
    return state.loaded and cabmap_state.BRIDGE is not None


IMPORT_SELECTED = app_command.COMMANDS.define(
    "ruri.import_selected", "Import Selected", _import_selected,
    description="Resolve every selected row's dependency closure in memory and import them",
    icon="IMPORT", poll=_import_poll, steps=True, status_state=STATE,
    failure="Import failed",
    arguments=(
        app_state.Field("reset_scene", app_state.BOOL, False),
        app_state.Field("only_root_names", app_state.STRING, "",
                        description="Semicolon-separated asset names. Set, the import keeps "
                                    "only the roots that ARE those assets instead of every "
                                    "root the closure exports -- what a caller that already "
                                    "knows which asset it asked for means")))


def _import_with_dependents(context, arguments):
    """Import the selected row(s) TOGETHER WITH whatever directly depends on them.

    Fixes the case where a bundled row imports empty on its own: a Mesh-only
    sub-asset carries no Material, and the Prefab whose Renderer pairs that mesh
    with one is a direct DEPENDENT, invisible to a plain forward import. The
    expansion happens here and the import itself is the same one, unchanged."""
    state = state_of(context)
    config = _ensure_active_config(state)
    rows, refused = _importable_rows(context, state, config)
    if refused:
        _announce(context, refused, host_port.ERROR)
        return
    seeds = [row["cab"] for row in rows]
    dependents = yield app_command.Read(
        lambda: cabmap_state.BRIDGE.find_direct_dependents(seeds), 0.15)
    added = [cab for cab in dependents if cab not in cabmap_state.SELECTED_CABS]
    cabmap_state.SELECTED_CABS.update(added)
    _sync_window_selection(state)
    if added:
        _announce(context, "Importing with {0} direct dependent(s) added.".format(len(added)))
    for step in _import_selected(context, arguments):
        yield step


IMPORT_WITH_DEPENDENTS = app_command.COMMANDS.define(
    "ruri.cabmap_import_with_dependents", "Import (With Dependents)", _import_with_dependents,
    description=("Find every CAB that directly depends on the selected row(s) -- e.g. the "
                 "Prefab/Material that actually uses a mesh-only CAB -- and import "
                 "everything together in one step"),
    icon="LOOP_BACK", poll=_import_poll, steps=True, status_state=STATE,
    failure="Import failed",
    arguments=(
        app_state.Field("reset_scene", app_state.BOOL, False),
        app_state.Field("only_root_names", app_state.STRING, "")))


def _import_secondary_motion(context, arguments):
    """The browser's own way in for the hair/cloth chains a model carries.

    The roster lists the cast the game declares; a model that carries these
    settings carries them whether or not anything declares it, so the rows are the
    other way in. What the settings ARE is the game's -- the host neither knows
    them nor which games have them."""
    state = state_of(context)
    host = host_port.current()
    rig = host.selected_rig(context)
    if rig is None:
        _announce(context, "Select the armature to write onto.", host_port.ERROR)
        return {"CANCELLED"}
    cabs = list(cabmap_state.selected_cabs())
    if not cabs:
        _announce(context, "Select the character's model prefab row first.", host_port.ERROR)
        return {"CANCELLED"}
    reader = Game.secondary_motion_of(_active_game_name(state))
    try:
        reading = reader(cabs) if reader is not None else None
        lines = (host.write_secondary_motion(context, rig, reading)
                 if reading is not None else None)
    except Exception as exc:
        _report_exception(context, "Secondary motion was not imported", exc)
        return {"CANCELLED"}
    if reading is None:
        _announce(context, "The selected rows state no secondary motion.", host_port.WARNING)
        return {"CANCELLED"}
    if lines is None:
        _announce(context, "This application has nothing that can hold those chains.",
                  host_port.WARNING)
        return {"CANCELLED"}
    _announce(context, "  ".join(lines))
    _redraw_all(context)
    return None


IMPORT_SECONDARY_MOTION = app_command.COMMANDS.define(
    "ruri.import_secondary_motion", "Import Cloth", _import_secondary_motion,
    description=("Write the selected model prefab's own hair/cloth/accessory chains "
                 "and their collision volumes onto the active armature, replacing "
                 "whatever it already carried"),
    icon="MOD_CLOTH", requires=host_port.SKELETON, poll=_import_poll)


def draw_column_widths(layout, context):
    """The draggable column widths. A popover rather than five sliders in the
    row: they are set once and then never again."""
    state = state_of(context)
    column = layout.column(align=True)
    column.label(text="Column widths")
    for key in ("col_name_factor", "col_container_factor", "col_type_factor",
                "col_deps_factor"):
        column.prop(state, key, slider=True)
    column.separator()
    column.prop(state, "browser_rows")


#: The handful of options worth an icon on their row, and the two that only mean
#: something in front of a rig the user has selected. Everything else about them
#: -- label, tooltip, default, kind, which host gets them -- is the table's.
_OPTION_ICONS = {"retarget_face": "USER", "import_secondary_motion": "MOD_CLOTH"}
_OPTION_NEEDS_RIG = ("retarget_face", "import_secondary_motion")
#: Options only a game that STATES the thing can offer. The host never learns
#: which games those are; it asks the registry per option.
_OPTION_ASKS_GAME = {"retarget_face": lambda name: Game.face_retarget_of(name),
                     "import_secondary_motion": lambda name: Game.secondary_motion_of(name)}


def draw_import_options(layout, context, state=None):
    """Every import option this host honours, in the order the table declares
    them.

    Generated rather than typed out: a widget that has drifted from the key the
    pipeline reads is a switch the user flips for nothing, and that is exactly
    what a second hand-written list of the same switches produced. An option whose
    capability this host does not answer never became a field, so it is not here
    at all.

    Public because it is not the browser tab's block: a game tab that loads a
    model draws the same switches, and drawing its own hand-picked subset is how
    it comes to name an option this host does not have."""
    state = state_of(context) if state is None else state
    host = host_port.current()
    game_name = _active_game_name(state)
    has_rig = host.selected_rig(context) is not None
    for entry in kernel_options.schema():
        asks = _OPTION_ASKS_GAME.get(entry.key)
        if asks is not None and asks(game_name) is None:
            continue
        row = layout.row(align=True)
        if entry.key == "link_shader_templates":
            # 只在着色栈真的会建图时才有意义 —— 不开 Game Shaders 就没有模板组可取。
            row.enabled = state.game_shaders
        elif entry.key in _OPTION_NEEDS_RIG:
            row.active = has_rig
        row.prop(state, entry.key, icon=_OPTION_ICONS.get(entry.key, ""))


def draw(layout, context):
    """The whole browser panel: which install is in front of us, then its content.

    Written once. Blender renders it into an N-panel, Painter into a dock, and
    neither renderer decides anything about what it says."""
    from ...RuriRipperPyBridge.runtime import bootstrap

    state = state_of(context)

    if not bootstrap.is_ready():
        error = bootstrap.last_error()
        if error:
            layout.label(text="pythonnet install failed:", icon="ERROR")
            layout.label(text=error[:60])
        else:
            layout.label(text="Installing pythonnet bridge...", icon="INFO")
        return

    top = layout.column()

    # Above the Game Root/Cabmap fields (which belong to the current tab), so tabs
    # open/switch/close before any cabmap is loaded. One tab per INSTALL, labelled
    # with the product name its own build carries. A grid rather than a row: a row
    # divides the width it has between however many tabs there are, so every tab
    # gets narrower as more open until each is a few clipped characters.
    tab_bar = top.grid_flow(columns=0, align=True)
    for key in _open_tab_keys(state):
        one_tab = tab_bar.row(align=True)
        one_tab.operator(SELECT_INSTALL.id, text=key,
                         depress=(key == state.current_tab)).key = key
        one_tab.operator(CLOSE_TAB.id, text="", icon="X").key = key
    tab_bar.operator(OPEN_TAB.id, text="", icon="ADD").key = ""

    top.prop(state, "game_root")

    # What the install SAID it is, and which decoder reads it. Both are read from
    # the build itself when the folder is typed; the menu is the override, and it
    # moves this tab alone.
    config = _active_config(state)
    identity = top.row(align=True)
    identity.label(text=("{0} {1}  -  {2} {3}".format(
        config.game_name, config.game_version or "(no version)",
        config.engine_family or "Unity", config.engine_version or "unknown")
        if config is not None and config.game_name else "No install identity"),
        icon="FILE_3D")
    identity.operator(REPROBE_INSTALL.id, text="", icon="FILE_REFRESH")
    decoder = top.row(align=True)
    decoder.menu(DECODER_MENU,
                 text=(config.decoder_id if config is not None and config.decoder_id
                       else "No decoder (plain Unity build)"),
                 icon="MODIFIER")

    # The values this install is READ with beyond its folder, drawn by the module
    # that claims it -- above the gate, because an archive key is what makes
    # building the cabmap possible at all. A module stating no such form leaves the
    # block absent; the core never spells an option name.
    module = _module_of(config)
    if config is not None and module is not None and module.settings_schema:
        draw_source_options(top.box(), context, config, module.settings_schema)

    top.prop(state, "cabmap_path")
    row = top.row(align=True)
    row.operator(BUILD_CABMAP.id)
    row.operator(LOAD_CABMAP.id)

    # A build or a load in flight, with the hook's own newest console line. The
    # panel stays live throughout because the work was DESCRIBED as steps.
    app_command.draw_progress(top, state)

    layout.separator()
    gated = layout.column()
    gated.enabled = state.loaded

    active = _active_tab(state)
    active_key = active.key if active is not None else BROWSER_TAB_ID
    tabs = gated.row(align=True)
    for key, label in _tab_bar(state):
        tabs.operator(SELECT_TAB.id, text=label, depress=(key == active_key)).tab = key

    # ``enabled = False`` greys a layout out; it does NOT stop the code that fills
    # it from running. Every tab below this line is ABOUT the loaded cabmap and
    # reads the bridge to draw itself, so without one the draw has to stop here
    # rather than be painted grey while it runs anyway.
    if not state.loaded:
        layout.label(text="Build or load a cabmap to browse/import.", icon="LOCKED")
        return

    if active is not None:
        active.draw(gated, context)
        return

    filtering.draw_search_row(gated, state)

    if not filtering.has_active_query(state):
        # Breadcrumb address bar -- only meaningful in the folder browser; a
        # search/rule result is a flat global match set, not scoped to a folder.
        crumbs = gated.row(align=True)
        crumbs.operator(CABMAP_GOTO_DIR.id, text="", icon="HOME").depth = 0
        for depth, segment in enumerate(cabmap_state.CURRENT_DIR, start=1):
            crumbs.label(text="/")
            crumbs.operator(CABMAP_GOTO_DIR.id, text=segment).depth = depth

    sort_column, sort_direction = cabmap_state.sort_state()
    sort_row = gated.row(align=True)
    for key, label in SORT_COLUMNS:
        arrow = ""
        if sort_column == key:
            arrow = " UP" if sort_direction == 1 else (" DOWN" if sort_direction == 2 else "")
        sort_row.operator(CABMAP_SORT.id, text=label + arrow).column = key
    sort_row.popover(COLUMN_WIDTHS_PANEL, text="", icon="ARROW_LEFTRIGHT")

    gated.list(state, "window", "active_index", _ROW_COLUMNS,
               rows=state.browser_rows, identifier="cabmap",
               on_click=CABMAP_CLICK.id, click_argument="index",
               group_key="is_folder", group_column=_FOLDER_COLUMN,
               group_command=CABMAP_ENTER_DIR.id,
               group_values=lambda row: {"folder_name": row.folder_name},
               row_action=CABMAP_GOTO_ROW_FOLDER.id, row_action_icon="FILE_FOLDER")

    selected_count = len(cabmap_state.SELECTED_CABS)
    select_bar = gated.row(align=True)
    for mode, label in (("ALL", "All"), ("NONE", "None"), ("INVERT", "Invert")):
        select_bar.operator(CABMAP_SELECT_ALL.id, text=label).mode = mode
    select_bar.separator()
    # Selection count when there is one; the click cheat-sheet otherwise.
    select_bar.label(text=("{0} selected".format(selected_count) if selected_count
                           else "Ctrl / Shift - Ctrl+A"))

    status_row = gated.row(align=True)
    status_row.label(text=state.status)
    status_row.menu(filtering.QUICK_FILTER_MENU, text="", icon="COLLAPSEMENU")

    options = gated.box()
    draw_import_options(options, context, state)

    # The texture property names no layer of the role table states, met while
    # building this game's materials: the user says what each one is, once.
    unmapped = texturing.unresolved_for(_texture_role_game(state))
    if unmapped or len(state.texture_roles):
        roles = options.box()
        head = roles.row(align=True)
        head.label(text="Unmapped textures: {0}".format(len(unmapped)), icon="QUESTION")
        head.operator(TEXTURE_ROLES_REFRESH.id, text="", icon="FILE_REFRESH")
        for role_row in state.texture_roles:
            line = roles.row(align=True)
            line.label(text="{0}  x{1}  {2}".format(
                role_row.name, role_row.count, role_row.example))
            line.prop(role_row, "role", text="")
            if texture_roles.is_channel_role(role_row.role):
                line.prop(role_row, "channel", text="")
        roles.operator(TEXTURE_ROLES_SAVE.id, icon="FILE_TICK")

    host = host_port.current()
    game_name = _active_game_name(state)
    batch = " {0}".format(selected_count) if selected_count > 1 else ""
    resettable = host_port.SCENE_GRAPH in host.capabilities
    actions = gated.row(align=True)
    actions.operator(IMPORT_SELECTED.id,
                     text="Import{0}{1}".format(batch, " (Append)" if resettable else "")
                     ).reset_scene = False
    if resettable:
        # "Append" only means something against "Reset": a host whose document IS
        # one mesh has one button, not one of each with the same effect.
        actions.operator(IMPORT_SELECTED.id,
                         text="Import{0} (Reset Scene)".format(batch)).reset_scene = True
    gated.operator(IMPORT_WITH_DEPENDENTS.id,
                   text="Import{0} (With Dependents)".format(batch),
                   icon="LOOP_BACK").reset_scene = False
    # A model that carries its own secondary motion carries it whether or not any
    # of the game's tables ever names that model -- so the rows get their own way
    # in, beside the import that would have brought it along.
    if (Game.secondary_motion_of(game_name) is not None
            and host_port.SKELETON in host.capabilities):
        cloth_row = gated.row(align=True)
        cloth_row.active = host.selected_rig(context) is not None
        cloth_row.operator(IMPORT_SECONDARY_MOTION.id)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
HANDLERS = app_state.Handlers(
    "browser", base=filtering.HANDLERS,
    get_game_root=_get_game_root, set_game_root=_set_game_root,
    get_cabmap_path=_get_cabmap_path, set_cabmap_path=_set_cabmap_path,
    get_loaded=_get_loaded,
    get_browsed_dir=_get_browsed_dir, set_browsed_dir=_set_browsed_dir,
    on_search_edit=_on_search_edit, on_animation_search=_on_animation_search,
    source_option_choices=_source_option_choices)


def register():
    """Put the browser's state where this host keeps panel state, and tell the
    rule editor how to find whichever list is on screen."""
    filtering.ACTIVE_SPEC_KEY = _active_filter_spec_key
    return host_port.current().register_state(
        STATE, schemas.BROWSER, HANDLERS,
        extra={"FILTER_SPEC_KEY": BROWSER_TAB_ID, "as_options": as_options})


def unregister():
    host_port.current().unregister_state(STATE)
    filtering.ACTIVE_SPEC_KEY = None
    cabmap_state.reset()
    _INSTALL_IDENTITY.clear()
    loading.forget_archives()


#: What the browser's own list can be filtered by, straight off cabmap_state's
#: field table -- the C# engine derives a row's value for each of these names, so
#: this list is a VIEW of that table, never a second copy of it.
FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=BROWSER_TAB_ID,
    fields=tuple((name, cabmap_state.FIELD_LABELS[name])
                 for name in cabmap_state.FILTER_FIELDS),
    state_for=state_of,
    row_for=lambda context: _selected_row(state_of(context)),
    apply=_reapply_and_refresh,
    # Deps is a count, so a one-click rule off a row means that exact number.
    quick_relation_for=lambda field: "is" if field == "deps" else "contains"))
