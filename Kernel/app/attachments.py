"""An answer a panel opens over the bundle browser: which listed resources can be held by a rig, and how.

One row is one way to hold one resource, in ordinary dataset columns: id, label, detail, seed, cab, key, visible,
status, actor_seed and its parts. The parts, parent first, are the game's statement of what hangs from what -- a direct
mount is one part -- and cross this layer unread: each part's seed is placed through the one load path and handed to
the host to mount. An ordinary import never consults any of this.
"""
import json
from contextlib import ExitStack, closing

from .. import host as host_port
from ..bridge import cabmap_state
from . import command, loading, state as app_state

#: The status of a row asked with no rig yet; asking again against the rig answers ``READY`` or why not.
NEEDS_TARGET = "needs_target"
READY = "ready"


class Answer:
    def __init__(self, rows, resolver, plan):
        self.rows = rows
        self.map = cabmap_state.ROWS
        self.bridge = cabmap_state.BRIDGE
        self.variant = 0
        self.resolver = resolver
        self.plan = plan
        self.performing = None
        self.companions = {}
        self.labels = {}
        for row in rows:
            if row["cab"] and row["label"]:
                self.labels.setdefault(row["cab"], set()).add(row["label"])

    def stale(self):
        return self.map is not cabmap_state.ROWS or self.bridge is not cabmap_state.BRIDGE

    def picked(self, cabs):
        choices = [row for row in self.rows if len(cabs) == 1 and row["cab"] in cabs]
        return choices[self.variant % len(choices)] if choices else None

    def reason(self, context, cabs):
        if self.stale():
            return "The loaded map changed. Run Find again."
        picked = self.picked(cabs)
        if picked is None:
            return "Select one matching resource to equip. Ordinary Append remains available."
        if picked["status"] != NEEDS_TARGET:
            return "Binding is not ready: " + picked["detail"]
        rig = host_port.selected_rig(context)
        if rig is None:
            return "Select the character's existing rig to equip. Ordinary Append needs no rig."
        if host_port.current().rig_seed(rig) != picked["actor_seed"]:
            return "The selected rig is not the character this answer is about."
        return ""


def current():
    return cabmap_state.ACTIVE.attachment if cabmap_state.ACTIVE is not None else None


def clear():
    if cabmap_state.ACTIVE is not None:
        cabmap_state.ACTIVE.attachment = None


def open_for(rows, resolver, plan):
    """``resolver(target_seed, paths_json, basis)`` asks the same question against a rig, which happens only when the
    user explicitly equips; ``plan(row)`` asks which clips a row's resource owns. Both are pure reader callbacks."""
    cabmap_state.ACTIVE.attachment = Answer(rows, resolver, plan)


def resource_label(cab, fallback):
    answer = current()
    if answer is None or answer.stale():
        return fallback
    labels = answer.labels.get(cab, ())
    if not labels or set(labels) <= {fallback, fallback.rsplit(".", 1)[0]}:
        return fallback
    return "{0} · {1}".format(" / ".join(sorted(labels)), fallback)


def select_variant(context, arguments):
    answer = current()
    if answer is not None:
        answer.variant += arguments["direction"]


VARIANT = command.COMMANDS.define(
    "ruri.attachment_variant", "Attachment variant", select_variant,
    internal=True, requires=host_port.RigAttachment,
    arguments=(app_state.Field("direction", app_state.INT, 1),))


def draw(layout, context, cabs, attach_command):
    answer = current()
    if answer is None:
        return
    try:
        rig = host_port.selected_rig(context)
        name = rig.name if rig is not None else "(select a matching existing rig to equip)"
    except ReferenceError:
        name = "(target removed)"
    layout.label(text="Equip target: {0}".format(name), icon="ARMATURE_DATA")
    selected = answer.picked(cabs)
    if selected is not None:
        layout.label(text=selected["label"])
        variants = layout.row(align=True)
        variants.operator(VARIANT.id, text="", icon="TRIA_LEFT").direction = -1
        variants.label(text=selected["detail"])
        variants.operator(VARIANT.id, text="", icon="TRIA_RIGHT").direction = 1
        if not selected["visible"]:
            layout.label(text="Source visibility is hidden; imported meshes remain hidden.", icon="HIDE_ON")
    layout.label(text="Standalone Append does not equip the character.", icon="INFO")
    reason = answer.reason(context, cabs)
    if reason:
        layout.label(text=reason, icon="INFO")
    action = layout.column()
    action.enabled = not reason
    action.operator(attach_command, text="Append to Character", icon="CONSTRAINT_BONE")


def _resolved(context, cabs):
    """The picked row asked again against the rig in front of the user: (answer, row, parts, rig)."""
    answer = current()
    if answer is None:
        raise ValueError("Run Find from the panel before equipping.")
    reason = answer.reason(context, cabs)
    if reason:
        raise ValueError(reason)
    picked = answer.picked(cabs)
    host = host_port.current()
    rig = host_port.selected_rig(context)
    target, paths, basis = host.rig_seed(rig), json.dumps(host.rig_paths(rig)), host.basis
    table = yield command.Read(lambda: answer.resolver(target, paths, basis), 0.3)
    with closing(table):
        if current() is not answer or answer.stale():
            raise ValueError("The install or equipment context changed during resolution.")
        matches = [table.row(index) for index in range(len(table)) if table.cell(index, "id") == picked["id"]]
    if len(matches) != 1:
        raise ValueError("The chosen binding changed. Run Find again.")
    row = matches[0]
    if row["status"] != READY:
        raise ValueError("Binding is not ready: " + row["detail"])
    parts = json.loads(row["parts"])
    reason = host.attachment_refusal(context, rig, parts)
    if reason:
        raise ValueError(reason)
    return answer, row, parts, rig


def attach(context, cabs, options):
    """Mount the picked row, and whatever it hangs from that is not there yet, onto the rig in front of the user.
    Returns (objects placed, parents reused)."""
    answer, row, parts, owner = yield from _resolved(context, cabs)
    host = host_port.current()
    with ExitStack() as stack:
        statements = []
        for part in parts:
            stated = yield command.Read(lambda seed=part["seed"]: loading.read([seed], options), 0.6)
            statements.append(stack.enter_context(stated))
        if current() is not answer or answer.stale() or host.rig_seed(owner) != row["actor_seed"]:
            raise ValueError("The install or attachment target changed during loading.")
        placed = reused = 0
        # Nothing inside yields: what a failure takes back is this batch, never what the user added while reading.
        with host.attachment_transaction(context, owner) as session:
            for index, (part, stated) in enumerate(zip(parts, statements)):
                if session.reuse(part, index == len(parts) - 1):
                    reused += 1
                    continue
                built = loading.place(context, stated, options)
                session.accept(part, stated, built)
                placed += built.imported
    return placed, reused


def plan_clips(cabs):
    """The clips the selected row's resource owns, as (row, plan rows), and remember that a play of those clips goes
    through :func:`perform`. None when the selection is not a row of the open answer."""
    answer = current()
    if answer is None:
        return None
    picked = answer.picked(cabs)
    answer.performing = picked
    if picked is None:
        return None
    with closing(answer.plan(picked)) as table:
        rows = [table.row(index) for index in range(len(table))]
    answer.companions = {row["seed"]: row["companion_seed"] for row in rows}
    return picked, rows


def performing():
    """Whether the clips the browser lists are the ones :func:`plan_clips` planned."""
    answer = current()
    return answer is not None and answer.performing is not None


def perform(context, seeds, options):
    """Key the checked clips onto the rig the planned row mounted. The first one also plays, with its exact counterpart
    on the owner when the plan found a unique one; the rest are only built. Returns (played, lines)."""
    answer = current()
    if answer.stale():
        raise ValueError("The equipment context changed. Discover Animations again.")
    host = host_port.current()
    binding = answer.performing
    rig = host.mounted_rig(context, binding)
    total, lines = 0, []
    with host.paired_performance(context, rig, binding) as session:
        for index, seed in enumerate(seeds):
            count, said = loading.perform(context, [seed], rig=rig, options=options, activate=False)
            primary = session.capture()
            total += count
            lines.extend(said)
            if index:
                continue
            companion = None
            counterpart = answer.companions[seed]
            if counterpart and count == 1 and session.can_pair:
                count, said = loading.perform(context, [counterpart], rig=session.owner, options=options, activate=False)
                companion = session.capture()
                if count != 1:
                    raise ValueError("The companion source must yield one action.")
                total += count
                lines.extend(said)
            elif not counterpart:
                lines.append("No unique companion; the owner's current action is retained.")
            elif not session.can_pair:
                lines.append("Active NLA; the owner's current action is retained.")
            session.synchronize(primary, companion)
    return total, lines
