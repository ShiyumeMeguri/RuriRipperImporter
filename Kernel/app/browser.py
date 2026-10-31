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
from . import filtering, loading, look, schemas, texturing
from . import layout as vocabulary
from . import state as app_state
from ...Kernel.bridge import bootstrap, pythonnet_bridge, workspace
from ...Kernel.bridge import cabmap_state
from ...Kernel.unity import texture_roles
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


def _redraw():
    host_port.current().redraw()


def _reapply_and_refresh(context):
    """Re-run whichever view is active -- the flat quick-search+Include/
    Exclude-rule results, or the folder listing for CURRENT_DIR -- and
    rebuild the displayed window. Call after ANY rule or search change, or a
    fresh Build/Load."""
    state = state_of(context)
    cabmap_state.refresh_visible(state.search, state.filter_rules)
    _rebuild_window(state)
    _redraw()


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
                     lambda: (_rebuild_window(state_of(context)), _redraw()))


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
        except Exception as exc:
            # The READER did not answer -- which is not the same thing as the folder
            # having nothing to say, and must not be drawn as though it were. Kept so
            # the panel can state the reason instead of "no identity"; not cached, or
            # one early miss would stick.
            _IDENTITY_BLOCKED[0] = str(exc)
            return None
        _IDENTITY_BLOCKED[0] = ""
        project = next((player for player in players if player["is_project"]), None)
        _INSTALL_IDENTITY[root] = project
    return _INSTALL_IDENTITY[root]


#: Why the last identity read answered nothing, when the reason was the reader
#: rather than the folder. An unconfigured decoder and a folder that is no install
#: look identical in the panel otherwise -- and the first one is not a fact about
#: the install at all (see evidence-check-the-instrument-first).
_IDENTITY_BLOCKED = [""]


def _identity_line(config):
    """What the identity row says: what the build called itself, or -- when nothing
    could be read because the reader is not up -- that, in its own words."""
    if config is not None and config.game_name:
        return "{0} {1}  -  {2} {3}".format(
            config.game_name, config.game_version or "(no version)",
            config.engine_family or "Unity", config.engine_version or "unknown")
    blocked = _IDENTITY_BLOCKED[0]
    return blocked.split(" (")[0].split(". ")[0] if blocked else "No install identity"


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


def _draw_look(layout, context):
    """How the frame is finally shown. What THIS install's game states about it comes first: a
    game's post-processing objects and the algorithm that grades a frame from them are its own,
    so the body is the game's. Then this host's own settings over whatever stack built the
    document (``Kernel.app.look``)."""
    config = active_config(state_of(context))
    own = Game.look_of(config.game_name, config.engine_family) if config is not None else None
    if own is not None:
        own(layout, context)
    look.draw(layout, context)


#: The one tab every install has beside the browser. It is about the DOCUMENT, so it is
#: offered whatever the install is, and with no cabmap loaded at all.
LOOK_TAB = Game.GameTab("look", "Look",
                        "How the frame is finally shown: this game's own grading, and this "
                        "application's settings over what its shading stack built",
                        _draw_look)


def _content_tabs(state):
    """The content tabs beside the browser: the CURRENT TAB's game's own -- one install's,
    not every open tab's, or two games' Scene/Character tabs would stack into one row --
    then the document's."""
    config = active_config(state)
    owned = Game.tabs_of(config.game_name, config.engine_family) if config is not None else []
    return owned + [LOOK_TAB]


def _active_tab(state):
    """The content tab actually being shown, or None for the browser.

    The stored tab only counts while its own game is still the one in front of the
    panel -- pointing a tab at a different install must take its tabs away rather
    than leave the panel drawing a game that is no longer there. Read-only, so a
    draw callback can call it."""
    for tab in _content_tabs(state):
        if tab.key == state.active_tab:
            return tab
    return None


def _tab_bar(state):
    """(key, label) for every content tab currently offered: the browser, the current
    game's own, then the document's."""
    entries = [(BROWSER_TAB_ID, BROWSER_TAB_LABEL)]
    entries.extend((tab.key, tab.label) for tab in _content_tabs(state))
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


def active_config(state):
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
    """Fill the form from the values IN EFFECT: what this tab applied, else what the decoder
    already answers this install with, else the schema's default.

    A recognised title brings its engine, its archive keys and its reflection schema with it,
    and those answers belong in the fields that hold them. Rendering them beside an empty
    field instead made two places state one value, and left the field -- the one a reader
    looks at -- saying the install was still missing what it was already open on."""
    for row in config.source_option_rows:
        applied = options.get(row.name, "")
        value = applied if str(applied).strip() else (row.effective or row.default)
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


def _get_game_root(self):
    config = active_config(self)
    return config.game_root if config is not None else ""


def _set_game_root(self, value):
    config = _ensure_active_config(self)
    config.game_root = value
    _on_game_root_set(self)


def _get_cabmap_path(self):
    config = active_config(self)
    return config.cabmap_path if config is not None else ""


def _set_cabmap_path(self, value):
    _ensure_active_config(self).cabmap_path = value


#: The folder an install's decompiled shader source goes in, under the install itself.
SHADER_OUTPUT_FOLDER = "RuriShaderOutput"


def _default_shader_output(config):
    """Where THIS install's shader source goes when nobody said otherwise: a folder of
    its own under the install's root. Derived on read rather than seeded once, so a tab
    that was set up before this field existed has a place to write to as well, and a
    user who empties the field gets the default back instead of nothing."""
    root = host_port.current().absolute_path(config.game_root) if config.game_root else ""
    return os.path.join(root, SHADER_OUTPUT_FOLDER) if root else ""


def _get_shader_output(self):
    config = active_config(self)
    if config is None:
        return ""
    return config.shader_output or _default_shader_output(config)


def _set_shader_output(self, value):
    _ensure_active_config(self).shader_output = value


def _get_browsed_dir(self):
    config = active_config(self)
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
    config = active_config(self)
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
    config = active_config(state)
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
    config = active_config(state)
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
# The role table is DATA in layers (Kernel.unity.texture_roles): the
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
    module = _module_of(active_config(state))
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
    config = active_config(state)
    product = (config.game_name if config is not None else "") or ""
    return product or _active_game_name(state)


def _reader_role_layers(state):
    """The role layers a statement is read through, over the reader's own default: the layer
    of the module declared for THIS product, then the user's own -- the files that exist."""
    _game, game_layer, user_layer, save = _texture_role_layers(state)
    owned = game_layer if save == game_layer else None
    return [path for path in (owned, user_layer) if path and os.path.isfile(path)]


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


def as_options(self):
    """Every import option this host honours, plus what the SESSION knows that
    no switch does.

    The options are read off the ONE table that declares them, so a key here and
    the key an importer reads cannot drift apart -- and every road reads the same
    answers, a scene window as much as a character. An option the game in front
    of us does not offer is absent from its panel, so it answers its default here
    whatever an earlier game's panel left stored."""
    game_name = _active_game_name(self)
    values = {}
    for entry in kernel_options.schema():
        asks = _OPTION_ASKS_GAME.get(entry.key)
        if asks is not None and not asks(game_name):
            values[entry.key] = entry.default
            continue
        value = getattr(self, entry.key)
        values[entry.key] = int(value) if entry.kind == kernel_options.INT else value
    # THE game this session is looking at, resolved exactly once here and stamped
    # onto every armature the import builds.
    values["source_game"] = game_name
    # The layers the reader resolves this install's materials through, over its own
    # default, and the product the names none of them states are filed under.
    values["role_layers"] = _reader_role_layers(self)
    values["role_product"] = _texture_role_game(self)
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
    _redraw()
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
    _redraw()
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
    _redraw()
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
    _redraw()
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
    _redraw()
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
    _redraw()
    return None


def _retry_install(context, arguments):
    from ...Kernel import bootstrap
    bootstrap.install_dependencies()
    _redraw()
    return None


RETRY_INSTALL = app_command.COMMANDS.define(
    "ruri.retry_install", "Retry Install", _retry_install,
    description="Run the pythonnet and clr-loader install again after it failed, without restarting the application.",
    internal=True)

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
    _redraw()
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
    _redraw()
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

    _redraw()
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
    _redraw()
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
    _redraw()
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
    _redraw()
    return None


REPROBE_INSTALL = app_command.COMMANDS.define(
    "ruri.reprobe_install", "Re-read Install", _reprobe_install,
    description="Re-read this install's own identity (product, engine version) and re-resolve its decoder",
    icon="FILE_REFRESH", poll=_bridge_ready)


def _load_rows(state):
    """Read the loaded map's rows into the session and, still off the main thread, the
    rows the decompile button's enablement asks about on every redraw -- gathering them
    is most of a second on a map of the largest installs."""
    cabmap_state.load_rows(cabmap_state.key_to_dir(state.browsed_dir))
    cabmap_state.cabs_where(SHADED)


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
    yield app_command.Read(lambda: _load_rows(state), 0.95)
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
    yield app_command.Read(lambda: _load_rows(state), 0.9)
    _reapply_and_refresh(context)
    gone, rows = cabmap_state.unreachable_rows()
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


def _target_cabs(state):
    """The cabs an action on the selection acts on: the multi-selection, falling
    back to the row under the cursor so the click-then-import muscle memory keeps
    working when nothing is explicitly multi-selected."""
    if cabmap_state.SELECTED_CABS:
        return cabmap_state.SELECTED_CABS
    item = _selected_row(state)
    return {item.cab} if item is not None else set()


def _selected_target_rows(state):
    """The row batch an import operates on, in master ROWS order."""
    return cabmap_state.rows_of(_target_cabs(state))


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
    _redraw()
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
    return _selected_target_rows(state), ""


def _import_selected(context, arguments):
    """Import every selected row as ONE selection.

    The rows that place things are one statement, read once; the rows that hold only
    performances play onto the rig in front of the user. What a row IS is the reader's
    answer for its archive -- nothing here reads a closure or builds anything."""
    state = state_of(context)
    config = _ensure_active_config(state)
    rows, refused = _importable_rows(context, state, config)
    if refused:
        _announce(context, refused, host_port.ERROR)
        return
    host = host_port.current()
    things = [row["cab"] for row in rows if not _row_is_clip_only(row)]
    clips = [row["cab"] for row in rows if _row_is_clip_only(row)]
    if clips and host_port.Timeline not in host.capabilities:
        _announce(context, "{0} selected row(s) hold only animation, which this host "
                           "cannot put on anything.".format(len(clips)), host_port.WARNING)
        clips = []
    if not things and not clips:
        return
    if arguments["reset_scene"] and not things:
        _announce(context, "An animation needs an existing skeleton -- import without "
                           "resetting so the rig survives.", host_port.ERROR)
        return
    yield from _load_steps(context, state, things, clips, arguments["reset_scene"])


def _load(context, arguments):
    """Load seeds a panel handed over -- the payload of the row it has picked. Every panel's
    load button is this one command, so no panel has a load of its own."""
    state = state_of(context)
    config = _ensure_active_config(state)
    blocked = _blocking_required_options(config)
    if blocked:
        _announce(context, blocked, host_port.ERROR)
        return
    _sync_bridge_to_tab(config)
    seeds = loading.seeds_in(arguments["seeds"])
    if not seeds:
        _announce(context, "That row states nothing this install can load.", host_port.WARNING)
        return
    yield from _load_steps(context, state, seeds, (), arguments["reset_scene"], arguments["panel"])


def _reveal(context, arguments):
    """Open where a picked row lives: the first archive its seed reads, as the reader resolves
    it -- so a row reveals what loading it would open."""
    host = host_port.current()
    seeds = loading.seeds_in(arguments["seeds"])
    target = host.panel_state(context, arguments["panel"]) if arguments["panel"] else state_of(context)
    if not seeds:
        target.status = "That row states nothing this install carries."
        return {"CANCELLED"}
    table = cabmap_state.BRIDGE.game_data("core.statement.archives", seed=seeds)
    if len(table) == 0:
        target.status = "The loaded map files nothing for that row."
        return {"CANCELLED"}
    cab = table.cell(0, "cab")
    _open_location(context, table.cell(0, "container") or cab, cab)
    return None


def _open_location(context, query, cab):
    """Switch to the browser and open where ``query`` lives, with ``cab`` under the cursor.

    Every row carrying the query under one folder opens that folder and clears the search --
    the "open file location" asked for. Hits spread across folders have no one right folder,
    so the search stays on and shows them all; and a build whose containers carry no path
    files everything at the root, where opening the root would be the whole map with the one
    thing that found the row thrown away, so that too leaves the search standing."""
    state = state_of(context)
    state.active_tab = BROWSER_TAB_ID
    for rule in state.filter_rules:
        rule.enabled = False
    cabmap_state.apply_filter(query)
    matches = list(cabmap_state.VISIBLE)
    folders = {cabmap_state.folder_of(row, query) for row in matches}
    landing = folders.pop() if len(folders) == 1 else None
    if landing:
        state.search = ""
        cabmap_state.browse_dir(landing)
    else:
        state.search = query
    if not matches:
        state.status = "Nothing in the loaded cabmap carries '{0}'.".format(query)
    _rebuild_window(state)
    if cab and not _cursor_on(state, cab):
        state.status = ("Opened where '{0}' lives, but the listing on screen does not carry it "
                        "-- narrow it and it will be there.".format(cab))
    _redraw()


def _cursor_on(state, cab):
    """Put the cursor on one archive of the listing drawn now, and say whether it was there."""
    for position, item in enumerate(state.window):
        if not item.is_folder and item.cab == cab:
            state.active_index = position
            state.cursor_cab = item.cab
            cabmap_state.clear_selection()
            cabmap_state.SELECTED_CABS.add(item.cab)
            return True
    return False


def show_rules(context, rules):
    """Switch to the browser and show exactly the rows a rule set selects.

    The rules REPLACE whatever was there -- rules left over from another query would silently
    AND into this one -- and the quick-search box is left empty, so it stays the user's own
    narrowing on top of them."""
    state = state_of(context)
    state.active_tab = BROWSER_TAB_ID
    state.filter_rules.clear()
    state.search = ""
    for spec in rules:
        rule = state.filter_rules.add()
        rule.spec_key = FILTER_SPEC.key
        rule.field = spec["field"]
        rule.relation = spec.get("relation", "contains")
        rule.value = spec.get("value", "")
        rule.action = spec.get("action", "include")
        rule.enabled = True
    state.filter_rules_active_index = len(state.filter_rules) - 1
    _reapply_and_refresh(context)


def _load_steps(context, state, seeds, clips, reset_scene, panel=""):
    """Read the statement off the main thread, place it, then play the performances."""
    host = host_port.current()
    options = as_options(state)
    if reset_scene and host_port.SceneGraph in host.capabilities:
        host.clear_scene(context)
    stated = None
    if seeds:
        stated = yield app_command.Read(lambda: loading.read(seeds, options), 0.6)
    yield app_command.Mark(0.7)
    lines = []
    placed = 0
    rig = None
    if stated is not None:
        with stated:
            built = loading.place(context, stated, options, lines)
        placed = built.imported
        rig = built.rig
        lines.extend(built.warnings)
    played = 0
    if clips:
        played, clip_lines = loading.perform(context, clips, rig=rig, options=options,
                                             activate=True)
        lines.extend(clip_lines)
    _sync_texture_roles(state)
    # A load changes nothing a listing shows, so the listing is not rebuilt -- only the
    # window drawn from it, which is a page of rows rather than a folder of millions.
    _rebuild_window(state)
    _redraw()
    message = "  ".join(["{0} object(s) placed, {1} performance(s) played from {2} seed(s).".format(
        placed, played, len(seeds) + len(clips))] + lines[:3])
    level = host_port.INFO if placed or played else host_port.WARNING
    _announce(context, message, level)
    if panel:
        host.panel_state(context, panel).status = message
    _redraw()


#: The row flag the map states for "something this row pulls in is a material" -- what a
#: decompile has to answer with, since a material names the program it was compiled to. A
#: prefab or a mesh holds no material of its own and still reaches every one it is drawn
#: with; the map walks that once per load, on either engine.
SHADED = "shaded"


def _shader_rows(state):
    """The selected rows a decompile can answer about: the ones whose dependencies reach
    a material, as the map states it -- never read off a row's name or its folder."""
    shaded = cabmap_state.cabs_where(SHADED)
    return [row for row in _selected_target_rows(state) if row["cab"] in shaded]


def _shader_ready(context, state):
    """The tab a decompile runs against, with its required options met and its bridge pointed
    at this install -- or None once it has said what is missing. Stated once so the two
    decompile buttons cannot drift on what they demand before either can start."""
    config = _ensure_active_config(state)
    blocked = _blocking_required_options(config)
    if blocked:
        _announce(context, blocked, host_port.ERROR)
        return None
    _sync_bridge_to_tab(config)
    return config


def _shader_output(context, state):
    """Where a decompile writes, or "" once it has said there is nowhere yet."""
    output = host_port.current().absolute_path(state.shader_output) if state.shader_output else ""
    if not output:
        _announce(context, "State a Shader Folder first.", host_port.ERROR)
    return output


def _read_shaders(context, arguments):
    """Write out the source of every shader the selected rows reach.

    The button is the same button on every install; only the answer differs. A game whose
    engine ships no shader ASSET says how to answer for itself (``Game.shaders_of``), and
    everything else goes down the one shared road: the closure of these rows, every shader
    in it, decompiled."""
    state = state_of(context)
    config = _shader_ready(context, state)
    if config is None:
        return
    rows = _shader_rows(state)
    output = _shader_output(context, state)
    if not output:
        return

    seeds = [row["cab"] for row in rows]
    stated = Game.shaders_of(_module_game_name(config), config.engine_family)
    if stated is not None:
        produced = yield app_command.Read(lambda: stated(seeds, output), 0.8)
        _announce(context, "{0} row(s) -> {1} archive(s) in {2}".format(
            len(seeds), len(produced or []), output))
        return
    found = yield app_command.Read(
        lambda: cabmap_state.BRIDGE.export_shaders(seeds, output), 0.8)
    _announce(context, "{0} row(s) -> {1} shader(s) in {2}".format(len(seeds), found, output))


def _read_all_shaders(context, arguments):
    """Write out the source of every shader the INSTALL ships.

    The same question as the selected-rows button with nothing named to narrow it, and it
    takes the same two roads for the same reason. WHICH rows "every" is stays on the far
    side in both: the map already states what each row holds, so neither road is answered
    by this side walking the table."""
    state = state_of(context)
    config = _shader_ready(context, state)
    if config is None:
        return
    output = _shader_output(context, state)
    if not output:
        return

    stated = Game.all_shaders_of(_module_game_name(config), config.engine_family)
    if stated is not None:
        produced = yield app_command.Read(lambda: stated(output), 0.8)
        _announce(context, "whole install -> {0} archive(s) in {1}".format(
            len(produced or []), output))
        return
    found = yield app_command.Read(
        lambda: cabmap_state.BRIDGE.export_all_shaders(output), 0.8)
    _announce(context, "whole install -> {0} shader(s) in {1}".format(found, output))


def _map_ready(context):
    return state_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_target(context):
    """A map to act on and something in it the selection means."""
    return _map_ready(context) and bool(_target_cabs(state_of(context)))


def _has_shading_target(context):
    """Whether anything the selection pulls in is a material -- the only rows a decompile
    of the selection can answer about."""
    return _map_ready(context) and not _target_cabs(state_of(context)).isdisjoint(
        cabmap_state.cabs_where(SHADED))


READ_SHADERS = app_command.COMMANDS.define(
    "ruri.cabmap_shaders", "Decompile Shaders", _read_shaders,
    description=("Write out, as source, every shader the materials the selected row(s) reach "
                 "were compiled to -- a prefab or a mesh answers for every material it pulls in, "
                 "on either engine"),
    icon="NODE_MATERIAL", poll=_has_shading_target, steps=True, status_state=STATE,
    failure="Reading these shaders failed")

READ_ALL_SHADERS = app_command.COMMANDS.define(
    "ruri.cabmap_shaders_all", "Decompile All Shaders", _read_all_shaders,
    description=("Write out, as source, every shader this install ships -- no selection, "
                 "the whole map, on either engine"),
    icon="SHADERFX", poll=_map_ready, steps=True, status_state=STATE,
    failure="Reading this install's shaders failed")

IMPORT_SELECTED = app_command.COMMANDS.define(
    "ruri.import_selected", "Import Selected", _import_selected,
    description="Import every selected row as one selection",
    icon="IMPORT", poll=_has_target, steps=True, status_state=STATE,
    failure="Import failed",
    arguments=(app_state.Field("reset_scene", app_state.BOOL, False),))

REVEAL = app_command.COMMANDS.define(
    "ruri.reveal", "Open Containing Folder", _reveal,
    description="Switch to the bundle browser and open where the picked row lives",
    icon="FILE_FOLDER", poll=_map_ready, internal=True,
    arguments=(
        app_state.Field("seeds", app_state.STRING, "",
                        description="What to reveal: the picked row's payload, one seed per line"),
        app_state.Field("panel", app_state.STRING, "",
                        description="The panel state an outcome is written into")))

LOAD = app_command.COMMANDS.define(
    "ruri.load", "Load", _load,
    description="Load the picked row into the document",
    icon="IMPORT", poll=_map_ready, steps=True, internal=True,
    status_state=lambda arguments: arguments.get("panel") or STATE,
    failure="Loading failed",
    arguments=(
        app_state.Field("seeds", app_state.STRING, "",
                        description="What to load: the picked row's payload, one seed per line"),
        app_state.Field("reset_scene", app_state.BOOL, False),
        app_state.Field("panel", app_state.STRING, "",
                        description="The panel state the outcome is written into")))


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
    yield from _import_selected(context, arguments)


IMPORT_WITH_DEPENDENTS = app_command.COMMANDS.define(
    "ruri.cabmap_import_with_dependents", "Import (With Dependents)", _import_with_dependents,
    description=("Find every CAB that directly depends on the selected row(s) -- e.g. the "
                 "Prefab/Material that actually uses a mesh-only CAB -- and import "
                 "everything together in one step"),
    icon="LOOP_BACK", poll=_has_target, steps=True, status_state=STATE,
    failure="Import failed",
    arguments=(app_state.Field("reset_scene", app_state.BOOL, False),))


def _import_secondary_motion(context, arguments):
    """The browser's own way in for the hair/cloth chains a model carries.

    The roster lists the cast the game declares; a model that carries these
    settings carries them whether or not anything declares it, so the rows are the
    other way in. What the settings ARE is the game's -- the host neither knows
    them nor which games have them."""
    state = state_of(context)
    host = host_port.current()
    rig = host_port.selected_rig(context)
    cabs = [row["cab"] for row in _selected_target_rows(state)]
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
    _redraw()
    return None


def _has_target_and_rig(context):
    """Something selected to read the chains off, and an armature to write them onto."""
    return _has_target(context) and host_port.selected_rig(context) is not None


IMPORT_SECONDARY_MOTION = app_command.COMMANDS.define(
    "ruri.import_secondary_motion", "Import Cloth", _import_secondary_motion,
    description=("Write the selected model prefab's own hair/cloth/accessory chains "
                 "and their collision volumes onto the active armature, replacing "
                 "whatever it already carried"),
    icon="MOD_CLOTH", requires=host_port.Rig, poll=_has_target_and_rig)


def _discover_animations(context, arguments):
    """List the clips the selected rows' dependency closure carries, off the loaded map's own
    graph -- nothing is read or built -- so the ones worth playing can be checked first."""
    state = state_of(context)
    rows = _selected_target_rows(state)
    seeds = [row["cab"] for row in rows]
    by_cab = cabmap_state.rows_by_cab()
    clips = sorted((by_cab[cab] for cab in cabmap_state.BRIDGE.resolve_closure_cab_names(seeds)
                    if cab in by_cab and "AnimationClip" in by_cab[cab]["type_names"]),
                   key=lambda row: (_clip_folder(row["container"]).lower(), row["name"].lower()))
    state.available_clips.clear()
    for row in clips:
        item = state.available_clips.add()
        item.seed = row["cab"]
        item.name = row["name"]
        item.folder = _clip_folder(row["container"])
    state.animation_seeds = loading.seed_lines(seeds)
    state.animation_character_name = (rows[0]["name"] if len(rows) == 1
                                      else "{0} selected rows".format(len(rows)))
    _apply_animation_filter(state)
    _announce(context, "Found {0} clip(s) -- check the ones to play.".format(len(clips)) if clips
              else "No animation clips in this selection's dependency closure.")
    _redraw()
    return None


def _play_checked_animations(context, arguments):
    """Play the checked clips onto the rig in front of the user -- and where there is none, load
    the rows they were discovered from first, and play onto what that placed."""
    state = state_of(context)
    clips = [item.seed for item in state.available_clips if item.selected]
    if not clips:
        _announce(context, "No animations checked.", host_port.WARNING)
        return
    seeds = ([] if host_port.selected_rig(context) is not None
             else loading.seeds_in(state.animation_seeds))
    yield from _load_steps(context, state, seeds, clips, False, False)


def _has_checked_clips(context):
    return any(item.selected for item in state_of(context).available_clips)


DISCOVER_ANIMATIONS = app_command.COMMANDS.define(
    "ruri.discover_animations", "Discover Animations", _discover_animations,
    description="List the selected rows' animation clips from the loaded map's own dependency "
                "graph -- nothing is read or built yet",
    icon="VIEWZOOM", requires=host_port.Timeline, poll=_has_target)
PLAY_CHECKED_ANIMATIONS = app_command.COMMANDS.define(
    "ruri.play_checked_animations", "Import Checked Animations", _play_checked_animations,
    description="Play the checked clips onto the rig in front of you -- loading the character "
                "they were discovered from first when there is none",
    icon="IMPORT", requires=host_port.Timeline, poll=_has_checked_clips, steps=True,
    status_state=STATE, failure="Importing the checked animations failed")


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


def _decoder_entries(context):
    """The decoders this tab's install may be read through: every version its own product ships,
    the family's beside them, and none at all."""
    config = active_config(state_of(context))
    product = config.game_name if config is not None else ""
    family = config.engine_family if config is not None else ""
    found = list(_decoders_of(product))
    if family and family.lower() != product.lower():
        found.extend(_decoders_of(family))
    entries = [] if found else [{"separator": True,
                                 "text": "No decoder ships for {0}".format(product or "this install")}]
    for entry in found:
        label = "{0} {1}".format(entry[0], entry[1])
        if entry[2]:
            label = "{0}  ·  {1}".format(label, entry[2])
        entries.append({"text": label, "command": SET_DECODER.id,
                        "values": {"decoder_id": _decoder_id(entry)}})
    entries.append({"text": "None (plain Unity build)", "command": SET_DECODER.id,
                    "values": {"decoder_id": ""}})
    return entries


COLUMN_WIDTHS_PANEL = vocabulary.declare_popover(
    "RURI_PT_column_widths_popover", "Column Widths", draw_column_widths)
DECODER_MENU = vocabulary.declare_menu("RURI_MT_decoder", "Decoder", _decoder_entries)


#: The handful of options worth an icon on their row, and the two that only mean
#: something in front of a rig the user has selected. Everything else about them
#: -- label, tooltip, default, kind, which host gets them -- is the table's.
_OPTION_ICONS = {"retarget_face": "USER", "import_secondary_motion": "MOD_CLOTH"}
_ERROR_LINE_WIDTH = 60
_ERROR_LINE_COUNT = 8
_OPTION_NEEDS_RIG = ("retarget_face", "import_secondary_motion")
#: Options only a game that STATES the thing can offer. The host never learns
#: which games those are; it asks the registry per option.
_OPTION_ASKS_GAME = {"retarget_face": lambda name: Game.face_retarget_of(name),
                     "import_secondary_motion": lambda name: Game.secondary_motion_of(name),
                     "import_shadow_proxies": lambda name: Game.shadow_proxies_of(name)}


def draw_import_options(layout, context, state=None, answered=()):
    """Every import option this host honours, in the order the table declares
    them, less the ones the caller ``answered`` itself and so does not ask.

    Generated rather than typed out: a widget that has drifted from the key the
    pipeline reads is a switch the user flips for nothing, and that is exactly
    what a second hand-written list of the same switches produced. An option whose
    capability this host does not answer never became a field, so it is not here
    at all.

    Public because it is not the browser tab's block: a game tab that loads a
    model draws the same switches, and drawing its own hand-picked subset is how
    it comes to name an option this host does not have."""
    state = state_of(context) if state is None else state
    game_name = _active_game_name(state)
    has_rig = host_port.selected_rig(context) is not None
    for entry in kernel_options.schema():
        asks = _OPTION_ASKS_GAME.get(entry.key)
        if entry.key in answered or (asks is not None and not asks(game_name)):
            continue
        row = layout.row(align=True)
        if entry.key in _OPTION_NEEDS_RIG:
            row.active = has_rig
        row.prop(state, entry.key, icon=_OPTION_ICONS.get(entry.key, ""))


def draw(layout, context):
    """The whole browser panel: which install is in front of us, then its content.

    Written once. Blender renders it into an N-panel, Painter into a dock, and
    neither renderer decides anything about what it says."""
    from ...Kernel.bridge import bootstrap

    state = state_of(context)

    if not bootstrap.is_ready():
        error = bootstrap.last_error()
        if error:
            layout.label(text="pythonnet install failed:", icon="ERROR")
            tail = " ".join(error.split())[-_ERROR_LINE_WIDTH * _ERROR_LINE_COUNT:]
            for start in range(0, len(tail), _ERROR_LINE_WIDTH):
                layout.label(text=tail[start:start + _ERROR_LINE_WIDTH])
            layout.operator(RETRY_INSTALL.id, icon="FILE_REFRESH")
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
    config = active_config(state)
    named = config is not None and bool(config.game_name)
    blocked = not named and bool(_IDENTITY_BLOCKED[0])
    identity = top.row(align=True)
    identity.label(text=_identity_line(config),
                   icon="FILE_3D" if not blocked else "ERROR")
    identity.operator(REPROBE_INSTALL.id, text="", icon="FILE_REFRESH")
    decoder = top.row(align=True)
    # A decoder nobody could ask for is not "none" -- saying "plain Unity build"
    # while the reader is down is the panel stating a fact it does not have.
    decoder.menu(DECODER_MENU,
                 text=(config.decoder_id if config is not None and config.decoder_id
                       else "Decoder unknown" if blocked
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
    active = _active_tab(state)
    active_key = active.key if active is not None else BROWSER_TAB_ID
    tabs = layout.row(align=True)
    for key, label in _tab_bar(state):
        button = tabs.row(align=True)
        button.enabled = state.loaded or key == LOOK_TAB.key
        button.operator(SELECT_TAB.id, text=label, depress=(key == active_key)).tab = key

    if active is LOOK_TAB:
        active.draw(layout, context)
        return

    # ``enabled = False`` greys a layout out; it does NOT stop the code that fills
    # it from running. Every tab below this line is ABOUT the loaded cabmap and
    # reads the bridge to draw itself, so without one the draw has to stop here
    # rather than be painted grey while it runs anyway.
    if not state.loaded:
        layout.label(text="Build or load a cabmap to browse/import.", icon="LOCKED")
        return

    gated = layout.column()
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
    resettable = host_port.SceneGraph in host.capabilities
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
            and host_port.Rig in host.capabilities):
        gated.operator(IMPORT_SECONDARY_MOTION.id)

    # Asking what a row is MADE of and asking what it was COMPILED as are two
    # questions about the same selection, so the second one lives beside the first
    # -- and states where its answer goes, because that is per install.
    shading = gated.column(align=True)
    shading.prop(state, "shader_output")
    shading.operator(READ_SHADERS.id, icon="NODE_MATERIAL")
    shading.operator(READ_ALL_SHADERS.id, icon="SHADERFX")

    if host_port.Timeline in host.capabilities:
        _draw_animations(gated.box(), state)


#: One discovered clip: its checkbox, its name, and the game's own folder for it -- dimmed,
#: because a closure mixes a character's body library with cutscene clips that share nothing
#: but a name pattern, and the folder is what tells them apart.
_CLIP_COLUMNS = (
    vocabulary.ListColumn("", width=0.08, prop="selected"),
    vocabulary.ListColumn("name", width=0.6),
    vocabulary.ListColumn(lambda item: item.folder.rpartition("/")[2], align=vocabulary.RIGHT,
                          active=False),
)


def _draw_animations(layout, state):
    """Discover what the selection plays, check, play. The filter HIDES rather than removes, so
    the three counts differ for real reasons: a check made before the filter still plays."""
    layout.operator(DISCOVER_ANIMATIONS.id, icon="VIEWZOOM")
    if not state.available_clips:
        layout.label(text="Select a row above, then Discover Animations.", icon="INFO")
        return
    layout.label(text="Clips for: {0}".format(state.animation_character_name),
                 icon="ARMATURE_DATA")
    layout.prop(state, "animation_search", icon="VIEWZOOM", text="")
    picks = layout.row(align=True)
    picks.operator(ANIMATION_SELECT_ALL.id, text="All Shown").select = True
    picks.operator(ANIMATION_SELECT_ALL.id, text="None").select = False
    layout.list(state, "available_clips", "available_clips_active_index", _CLIP_COLUMNS,
                rows=8, identifier="animation_clips", visible_key="visible")
    total = len(state.available_clips)
    shown = sum(1 for item in state.available_clips if item.visible)
    checked = sum(1 for item in state.available_clips if item.selected)
    folders = len({item.folder for item in state.available_clips if item.folder})
    layout.label(text="{0} clip(s){1}{2} · {3} checked".format(
        total, "" if shown == total else " · {0} shown".format(shown),
        "" if folders <= 1 else " · {0} folders".format(folders), checked))
    layout.operator(PLAY_CHECKED_ANIMATIONS.id, icon="IMPORT",
                    text="Import {0} Checked Animation(s)".format(checked) if checked
                    else "Import Checked Animations")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
HANDLERS = app_state.Handlers(
    "browser", base=filtering.HANDLERS,
    get_game_root=_get_game_root, set_game_root=_set_game_root,
    get_cabmap_path=_get_cabmap_path, set_cabmap_path=_set_cabmap_path,
    get_shader_output=_get_shader_output, set_shader_output=_set_shader_output,
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
