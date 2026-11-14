"""Reader-authored animation chains, queried and performed through shared commands."""

from .. import host as host_port
from ..bridge import cabmap_state
from . import browser, command, loading, schemas
from . import state as app_state
from .state import Field, Schema


ROW = Schema("AnimationChainRow", "One related animation in the reader's order.", (
    Field("identifier", app_state.STRING, ""),
    Field("label", app_state.STRING, ""),
    Field("seed", app_state.STRING, ""),
    Field("group", app_state.STRING, ""),
    Field("group_label", app_state.STRING, ""),
    Field("selected", app_state.BOOL, False),
    Field("status", app_state.STRING, ""),
    Field("detail", app_state.STRING, ""),
))

FORM = Schema("AnimationChain", "The animation relation query currently in front of the user.", (
    Field("owner_seed", app_state.STRING, "", transient=True),
    Field("install_key", app_state.STRING, "", transient=True),
    Field("map_identity", app_state.STRING, "", transient=True),
    Field("clip_seed", app_state.STRING, "", transient=True),
    Field("group", app_state.STRING, "", transient=True),
    Field("generation", app_state.INT, 0, transient=True),
    Field("status", app_state.STRING, ""),
    Field("rows", app_state.COLLECTION, element=ROW),
), include=(schemas.LOADING_STATE,))


def _map_identity():
    return str(id(cabmap_state.ROWS))


def _selected(state):
    return [row.identifier for row in state.rows
            if state.group and row.group == state.group and row.selected and row.status == "ready" and row.seed]


def _groups(state):
    return {row.group: row.group_label for row in state.rows}


class Panel:
    def __init__(self, identifier, state_name, owner, related, sequence, notes=()):
        self.id = identifier
        self.state_name = state_name
        self.owner = owner
        self.related = related
        self.sequence = sequence
        self.notes = notes

    def state_of(self, context):
        return host_port.current().panel_state(context, self.state_name)

    def valid(self, context, state):
        active = cabmap_state.ACTIVE
        if (active is None or cabmap_state.BRIDGE is None or not browser.state_of(context).loaded
                or active.key != state.install_key or _map_identity() != state.map_identity):
            return False
        return self.owner(context) == state.owner_seed

    def snapshot(self, context, state):
        return (cabmap_state.ACTIVE, cabmap_state.ROWS, cabmap_state.BRIDGE,
                state.owner_seed, state.clip_seed, browser.state_of(context).animation_selected_seed,
                state.group, state.generation)

    def open(self, context, seed):
        state = self.state_of(context)
        state.owner_seed = seed
        state.install_key = cabmap_state.ACTIVE.key
        state.map_identity = _map_identity()
        state.generation += 1
        state.clip_seed = ""
        state.group = ""
        state.rows.clear()
        state.status = "Select an animation, then click Scan NLA."

    def select(self, context, seed):
        state = self.state_of(context)
        state.generation += 1
        state.rows.clear()
        state.clip_seed = ""
        state.group = ""
        if not self.valid(context, state):
            state.status = ""
        elif seed:
            state.status = "Click Scan NLA to find related animations."
        else:
            state.status = "Select an animation, then click Scan NLA."

    def can_scan(self, context):
        state = self.state_of(context)
        browser_state = browser.state_of(context)
        return (self.valid(context, state) and bool(browser_state.animation_selected_seed)
                and not state.loading and not browser_state.loading)

    def can_import(self, context):
        state = self.state_of(context)
        return (self.valid(context, state) and not state.loading and bool(_selected(state))
                and state.clip_seed == browser.state_of(context).animation_selected_seed
                and host_port.selected_rig(context) is not None)

    def can_select_path(self, context):
        state = self.state_of(context)
        browser_state = browser.state_of(context)
        return (self.valid(context, state) and bool(state.rows)
                and state.clip_seed == browser_state.animation_selected_seed
                and not state.loading and not browser_state.loading)

    def draw(self, layout, context):
        state = self.state_of(context)
        if not self.valid(context, state):
            return
        box = layout.box()
        scan = box.row()
        scan.enabled = self.can_scan(context)
        scan.operator(SCAN.id, text="Scan NLA", icon="VIEWZOOM").panel = self.id
        if state.status:
            box.label(text=state.status, icon="INFO")
        if state.clip_seed != browser.state_of(context).animation_selected_seed:
            return
        groups = _groups(state)
        if len(groups) > 1:
            paths = box.column(align=True)
            paths.enabled = self.can_select_path(context)
            for group, label in groups.items():
                choice = paths.operator(SELECT_PATH.id, text=label, depress=state.group == group)
                choice.panel, choice.group = self.id, group
        if not state.group:
            return
        for item in state.rows:
            if item.group != state.group:
                continue
            line = box.row(align=True)
            line.enabled = item.status == "ready" and bool(item.seed)
            line.prop(item, "selected", text=item.label)
            if item.detail and item.status != "ready":
                box.label(text=item.detail, icon="INFO")
        if state.rows:
            for note in self.notes:
                box.label(text=note)
            action = box.row()
            action.enabled = self.can_import(context)
            action.operator(IMPORT.id, text="Import NLA", icon="IMPORT").panel = self.id

    def register(self, module):
        host_port.current().register_state(self.state_name, FORM, app_state.Handlers(self.id))
        browser.ANIMATION_SECTIONS.add(self, key=self.id, module=module)

    def unregister(self, module):
        browser.ANIMATION_SECTIONS.remove(self, key=self.id, module=module)
        host_port.current().unregister_state(self.state_name)


def _panel(arguments):
    return browser.ANIMATION_SECTIONS.get(arguments["panel"])


def _read_related(panel, owner, seed):
    try:
        return panel.related(owner, seed), None
    except Exception as error:
        return None, error


def _scan(context, arguments):
    panel = _panel(arguments)
    state = panel.state_of(context)
    seed = browser.state_of(context).animation_selected_seed
    if not seed or not panel.valid(context, state):
        return {"CANCELLED"}
    state.generation += 1
    state.clip_seed = seed
    state.group = ""
    state.rows.clear()
    asked = panel.snapshot(context, state)
    owner = state.owner_seed
    state.status = "Reading related animations."
    rows, error = yield command.Read(lambda: _read_related(panel, owner, seed), 0.5)
    current = panel.valid(context, state) and asked == panel.snapshot(context, state)
    if error is not None:
        if current:
            state.status = str(error)
        raise error
    if not current:
        return
    for row in rows:
        item = state.rows.add()
        item.identifier = row["id"]
        item.label = row["label"]
        item.seed = row["seed"]
        item.group = row["group"]
        item.group_label = row["group_label"]
        item.status = row["status"]
        item.detail = row["detail"]
        item.selected = bool(int(row["selected"])) and item.status == "ready" and bool(item.seed)
    groups = _groups(state)
    if len(groups) == 1:
        state.group = next(iter(groups))
    state.status = ("Choose an animation path." if len(groups) > 1
                    else "" if rows else "No native animation chain for this selection.")
    host_port.current().redraw()


def _import(context, arguments):
    panel = _panel(arguments)
    state = panel.state_of(context)
    if (not panel.valid(context, state) or not _selected(state)
            or state.clip_seed != browser.state_of(context).animation_selected_seed
            or host_port.selected_rig(context) is None):
        return {"CANCELLED"}
    asked = panel.snapshot(context, state)
    selected = _selected(state)
    rig = host_port.selected_rig(context)
    owner, clip = state.owner_seed, state.clip_seed
    sequence = yield command.Read(lambda: panel.sequence(owner, clip, selected), 0.5)
    if (not panel.valid(context, state) or asked != panel.snapshot(context, state)
            or selected != _selected(state) or rig != host_port.selected_rig(context)):
        state.status = "Selection or target changed. Import the current selection again."
        return {"CANCELLED"}
    if not sequence:
        raise ValueError("The reader returned no sequence for the checked animations.")
    seeds = list(dict.fromkeys(row["seed"] for row in sequence))
    played, lines = loading.perform(context, seeds, rig=rig,
                                    options=browser.as_options(browser.state_of(context)),
                                    sequence=sequence)
    state.status = "{0} animation(s) arranged. {1}".format(played, " ".join(lines))
    host_port.current().redraw()


def _select_path(context, arguments):
    panel = _panel(arguments)
    if not panel.can_select_path(context):
        return {"CANCELLED"}
    state = panel.state_of(context)
    group = arguments["group"]
    if group not in _groups(state):
        raise ValueError("The selected animation path is not in the current scan.")
    if state.group != group:
        state.group = group
        state.generation += 1
        state.status = ""
        host_port.current().redraw()


SELECT_PATH = command.COMMANDS.define(
    "ruri.select_animation_chain_path", "Select Animation Path", _select_path,
    description="Choose one of the reader's animation paths without changing its order",
    requires=host_port.Timeline,
    poll=lambda context: any(panel.can_select_path(context) for panel in browser.ANIMATION_SECTIONS),
    internal=True, arguments=(Field("panel", app_state.STRING, ""), Field("group", app_state.STRING, "")))


SCAN = command.COMMANDS.define(
    "ruri.scan_animation_chain", "Scan NLA", _scan,
    description="Read the selected animation's native NLA relations",
    icon="VIEWZOOM", requires=host_port.Timeline,
    poll=lambda context: any(panel.can_scan(context) for panel in browser.ANIMATION_SECTIONS),
    steps=True, internal=True, arguments=(Field("panel", app_state.STRING, ""),),
    status_state=lambda arguments: _panel(arguments).state_name,
    failure="Reading native animation relations failed")

IMPORT = command.COMMANDS.define(
    "ruri.import_animation_chain", "Import NLA", _import,
    description="Arrange the checked native animations in the reader's fixed order",
    icon="IMPORT", requires=host_port.Timeline,
    poll=lambda context: any(panel.can_import(context) for panel in browser.ANIMATION_SECTIONS),
    steps=True, internal=True, arguments=(Field("panel", app_state.STRING, ""),),
    status_state=lambda arguments: _panel(arguments).state_name,
    failure="Importing the native animation chain failed")
