"""Optional browser binding context; normal Append never consults it.

The answer uses ordinary dataset columns: cab, seed, key, bone_path, offset,
visible, status, actor_seed, label and detail. No seed or game rule is interpreted.
"""
import json

from .. import host as host_port
from ..bridge import cabmap_state
from . import command, state as app_state


class Answer:
    def __init__(self, rig, rows, resolver=None):
        self.rig = rig
        self.rows = [dict(row, offset=json.loads(row["offset"])) for row in rows]
        self.map = cabmap_state.ROWS
        self.bridge = cabmap_state.BRIDGE
        self.variant = 0
        self.resolver = resolver
        self.labels = {}
        for row in self.rows:
            label = row.get("label", "")
            if row.get("cab") and label:
                self.labels.setdefault(row["cab"], set()).add(label)

    def target(self, context):
        return host_port.selected_rig(context) if self.resolver else self.rig

    def choices(self, cabs):
        return [row for row in self.rows if len(cabs) == 1 and row["cab"] in cabs]

    def picked(self, cabs):
        choices = self.choices(cabs)
        return choices[self.variant % len(choices)] if choices else None

    def reason(self, context, cabs):
        if self.map is not cabmap_state.ROWS or self.bridge is not cabmap_state.BRIDGE:
            return "The loaded map changed. Run Find again."
        if host_port.RigAttachment not in host_port.current().capabilities:
            return "This host has no rig attachment capability."
        picked = self.picked(cabs)
        if picked is None:
            return "Select one matching resource to equip. Ordinary Append remains available."
        if picked["status"] not in ("ready", "needs_target"):
            return "Binding is not ready: " + picked["detail"]
        if self.resolver is not None:
            rig = self.target(context)
            if rig is None:
                return "Select the roster character's existing rig to equip. Ordinary Append needs no rig."
            if host_port.current().rig_seed(rig) != picked["actor_seed"]:
                return "The selected rig does not match the roster character used by Find Weapons."
            if picked["status"] == "needs_target":
                return ""
        return host_port.current().validate_attachment(context, self.target(context), picked)


def current():
    return cabmap_state.ACTIVE.attachment if cabmap_state.ACTIVE is not None else None


def clear():
    if cabmap_state.ACTIVE is not None:
        cabmap_state.ACTIVE.attachment = None


def open_for(rig, rows, resolver=None):
    """Optional resolver(target_seed, source_paths_json, basis) returns a table.

    It is a pure reader callback: no host API or game decisions in this layer.
    Resolution is deferred until the explicit equipment command is invoked.
    """
    cabmap_state.ACTIVE.attachment = Answer(rig, rows, resolver)


def resource_label(cab, fallback):
    answer = current()
    if answer is None or answer.map is not cabmap_state.ROWS or answer.bridge is not cabmap_state.BRIDGE:
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


def draw(layout, context, cabs, import_command):
    answer = current()
    if answer is None:
        return
    box = layout.box()
    try:
        rig = answer.target(context)
        name = rig.name if rig is not None else "(select a matching existing rig to equip)"
    except ReferenceError:
        name = "(target removed)"
    box.label(text="Equip target: {0}".format(name), icon="ARMATURE_DATA")
    selected = answer.picked(cabs)
    if selected is not None:
        box.label(text=selected["label"])
        variants = box.row(align=True)
        variants.operator(VARIANT.id, text="", icon="TRIA_LEFT").direction = -1
        variants.label(text=selected["detail"])
        variants.operator(VARIANT.id, text="", icon="TRIA_RIGHT").direction = 1
        if not selected["visible"]:
            box.label(text="Source visibility is hidden; imported meshes remain hidden.", icon="HIDE_ON")
    box.label(text="Standalone Append does not equip the character.", icon="INFO")
    reason = answer.reason(context, cabs)
    if reason:
        box.label(text=reason, icon="INFO")
    action = box.column()
    action.enabled = not reason
    op = action.operator(import_command, text="Append to Character", icon="CONSTRAINT_BONE")
    op.attach_to_target = True
    op.reset_scene = False


def prepare(context, cabs):
    answer = current()
    if answer is None:
        raise ValueError("Run Find from the character roster before equipping.")
    reason = answer.reason(context, cabs)
    if reason:
        raise ValueError(reason)
    binding = answer.picked(cabs)
    # The browser answer is shared across commands. Keep the clicked target in
    # this generator's own state, never in a field another prepare can overwrite.
    rig = answer.target(context)
    if answer.resolver is not None:
        host = host_port.current()
        target, paths, basis = host.rig_seed(rig), json.dumps(host.rig_paths(rig)), host.basis
        table = yield command.Read(lambda: answer.resolver(target, paths, basis), 0.3)
        try:
            if (current() is not answer or answer.map is not cabmap_state.ROWS
                    or answer.bridge is not cabmap_state.BRIDGE):
                raise ValueError("The install or equipment context changed during resolution.")
            matches = [table.row(i) for i in range(len(table)) if table.cell(i, "id") == binding["id"]]
            if len(matches) != 1:
                raise ValueError("The chosen binding changed. Run Find Weapons again.")
            binding = dict(matches[0], offset=json.loads(matches[0]["offset"]))
        finally:
            table.close()
        reason = host.validate_attachment(context, rig, binding)
        if reason:
            raise ValueError(reason)
    return answer, binding, rig
