"""Find a roster character's weapons in the existing bundle browser."""
import json

from ...Kernel import host as host_port
from ...Kernel.app import attachments, browser as app_browser, cast_panel, command
from ...Kernel.bridge import cabmap_state
from . import datasets


def selection(context):
    from . import roster
    browser = roster.browser_of(context)
    if cabmap_state.BRIDGE is None or not browser.loaded or browser.active_tab != roster.SPEC_KEY:
        return None
    state = roster.state_of(context)
    entry = roster.BOUND.picked(state)
    if (state.pane != cast_panel.ACTOR or entry is None or not entry.shipped
            or entry.cell("kind") not in (roster.CHARACTERS, roster.UI_MODELS)):
        return None
    return entry if entry.payload else None


def can_find(context):
    from . import roster
    return (selection(context) is not None and not roster.state_of(context).loading
            and not roster.browser_of(context).loading)


def find(context, arguments):
    from . import roster
    state = roster.state_of(context)
    entry = selection(context)
    if entry is None:
        state.status = "Select a character in the roster first."
        return {"CANCELLED"}
    host = host_port.current()
    seed, basis = entry.payload, host.basis
    asked = cabmap_state.ACTIVE, cabmap_state.ROWS, cabmap_state.BRIDGE
    # Discovery belongs to the picked roster row, even in an empty scene or
    # while a different character's rig is selected. Resolve the actual target
    # only when the user explicitly asks to equip, never to browse candidates.
    table = yield command.Read(lambda: datasets.weapon_bindings(seed, "", "[]", basis), 0.8)
    try:
        now = selection(context)
        if (any(a is not b for a, b in zip(asked, (cabmap_state.ACTIVE, cabmap_state.ROWS, cabmap_state.BRIDGE)))
                or now is None or now.payload != seed):
            state.status = "Character or install changed. Click Find Weapons again."
            return {"CANCELLED"}
        rows = [table.row(i) for i in range(len(table))]
        if not rows:
            state.status = "This character has no declared weapon candidates."
            return {"CANCELLED"}
        # Same public navigation as Find Animations. The backend supplies the
        # exact CAB rules and binding facts; Python never constructs a seed.
        app_browser.show_rules(context, json.loads(rows[0]["rules"]))
        attachments.open_for(rows,
                             resolver=lambda target, paths, target_basis: datasets.weapon_bindings(seed, target, paths, target_basis),
                             plan=lambda row: datasets.weapon_animation_plan(row["actor_seed"], row["seed"]))
        cabmap_state.clear_selection()
        app_browser.state_of(context).active_index = -1
        app_browser.state_of(context).cursor_cab = ""
        app_browser.state_of(context).status = "Choose a weapon: ordinary Append or Append to Character."
    finally:
        table.close()


FIND = command.COMMANDS.define(
    "ruri.endfield_find_weapons", "Find Weapons", find,
    description="Show the selected roster character's weapon names and resources; an existing rig is needed only to equip",
    icon="OUTLINER_OB_MESH", poll=can_find, requires=host_port.RigAttachment,
    steps=True, status_state="ruri_roster", settle=lambda context, result: result,
    failure="Weapon query failed")
