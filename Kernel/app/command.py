"""What a panel can be told to do, declared once for every host.

A Blender operator is three things bundled: an identity the UI references, a set
of typed arguments the button writes into, and a body. Only the last is about
the work; the first two are how Blender happens to spell "a button with some
values attached". Painter spells it as a signal and a closure.

So a command declares the identity and the arguments here, next to the body, and
each host wraps it: Blender generates a real ``bpy.types.Operator`` (keeping the
same ``bl_idname``, so every existing keymap entry and menu reference still
resolves), Painter calls ``run`` directly.

LONG WORK IS SAID, NOT SCHEDULED. A body that would block returns a generator of
steps instead of doing the work inline -- ``Read`` for what crosses the bridge
(slow, touches no host API) and ``Mark`` for a checkpoint worth redrawing at.
The host drives that generator its own way: Blender modally, Painter on a worker
thread. Both walk the SAME generator, which is the property a second "async
version" of a load would destroy.
"""

from __future__ import annotations

from contextlib import contextmanager

from .. import extensions

#: The registry IS process state. A development reload re-executes modules in
#: sys.modules order, which puts a module BEFORE the ones it imports -- so a
#: reload that recreated this table would wipe every command the modules above it
#: had just re-registered, and the sweep that wraps them would find nothing.
HOLDS_PROCESS_STATE = True

# An opt-in lease for a command that prepares/replaces the shared reader map.
# With no lease the historical command poll/run paths are unchanged.
_EXCLUSIVE = None


def exclusive_active():
    return _EXCLUSIVE is not None


def exclusive_allows(command_id):
    return _EXCLUSIVE is None or command_id in _EXCLUSIVE


@contextmanager
def exclusive(owner, allowed=()):
    """Exclude other commands until a reader lifecycle operation has settled."""
    global _EXCLUSIVE
    if _EXCLUSIVE is not None:
        raise RuntimeError("A reader lifecycle operation is already running.")
    _EXCLUSIVE = frozenset((owner, *allowed))
    try:
        yield
    finally:
        _EXCLUSIVE = None


class Read:
    """A step that is a pure cross-boundary read -- bridge traffic and plain
    Python, never a host API. The driver runs ``fn`` (inline, or on a worker
    thread) and sends the result back into the generator, so one sequence serves
    both. ``progress`` is a 0..1 hint for the bar, ignored by an inline driver."""

    __slots__ = ("fn", "progress", "cancel")

    def __init__(self, fn, progress=None, cancel=None):
        self.fn = fn
        self.progress = progress
        self.cancel = cancel


class Mark:
    """A checkpoint between main-thread chunks: move the bar and let the panel
    redraw. It carries NO text on purpose -- the one loading line a panel shows is
    the hook's own console output, read live, never a string composed here from
    what the loader thinks it is doing (which would be a second, drifting source
    for what the hook already prints accurately)."""

    __slots__ = ("progress",)

    def __init__(self, progress=None):
        self.progress = progress


#: The arguments a command that asks for ``modifiers`` is given, filled by the
#: host from the click that invoked it. Declared here so both hosts and every body
#: that reads them agree on the names, and so the two are impossible to spell
#: differently in two places.
EXTEND = "extend"
RANGE = "range"


def modifier_fields():
    from . import state as app_state
    return (
        app_state.Field(EXTEND, app_state.BOOL, False,
                        description="Add to what is already selected rather than replacing it"),
        app_state.Field(RANGE, app_state.BOOL, False,
                        description="Take everything between the anchor and here"),
    )


class Command:
    """One thing a panel can invoke."""

    __slots__ = ("id", "label", "description", "icon", "arguments", "_run",
                 "_poll", "requires", "internal", "steps", "status_state",
                 "_settle", "failure", "source", "modifiers", "cancel_message")

    def __init__(self, id, label, run, description="", icon="", arguments=(),
                 poll=None, requires=None, internal=False,
                 steps=False, status_state="", settle=None, failure="",
                 modifiers=False, cancel_message=""):
        #: The identity the layout references, and the bl_idname Blender keeps.
        self.id = id
        self.label = label
        self.description = description
        self.icon = icon
        #: Whether the host fills EXTEND/RANGE from the click that invoked this.
        #: They are real arguments, appended here, so a caller that means one
        #: (a keymap entry, a script) states it the same way as any other.
        self.modifiers = modifiers
        #: :class:`Kernel.app.state.Field` per argument the button writes.
        self.arguments = tuple(arguments) + (modifier_fields() if modifiers else ())
        if any(field.sensitive or field.transient for field in self.arguments):
            raise ValueError("transient input belongs to panel memory, not command arguments")
        self._run = run
        self._poll = poll
        #: Host capability this needs, or None. A host that cannot answer never
        #: shows the control -- it is not disabled, it is absent.
        self.requires = requires
        #: Reachable only from the control that places it -- kept out of a
        #: host's own command search, where it would be meaningless without
        #: the arguments its button supplies.
        self.internal = internal
        #: run() hands back a generator of Read/Mark rather than doing the work.
        #: The host drives it -- modally in Blender, on a worker in Painter.
        self.steps = steps
        #: Panel state carrying loading/load_line/progress while that runs.
        #: WHICH panel's progress and status a stepped run writes into: a name, or
        #: a callable of the arguments for a command SEVERAL panels share -- the
        #: button says which one pressed it, so one command can serve them all
        #: rather than being defined once per panel.
        self.status_state = status_state
        self._settle = settle
        #: How to word a failure, in this command's own terms.
        self.failure = failure or ("{0} failed".format(label))
        self.cancel_message = cancel_message
        #: Which module declared it -- what tells a reload apart from a genuine
        #: collision.
        self.source = getattr(run, "__module__", "")

    def poll(self, context):
        if not exclusive_allows(self.id):
            return False
        return True if self._poll is None else bool(self._poll(context))

    def run(self, context, arguments):
        """Do the work, or hand back a generator of steps for the host to drive."""
        if not exclusive_allows(self.id):
            raise RuntimeError("Wait for the reader lifecycle operation to finish.")
        return self._run(context, self.filled(arguments))

    def filled(self, arguments):
        """Every declared argument: the caller's value where it gave one, and the
        declared default where it did not.

        A button states the arguments it MEANS and no more -- "Import (Append)"
        says reset_scene, and has no opinion about the name filter a roster entry
        uses. What the rest are is declared right here, next to the body that
        reads them, and this is where that declaration is applied.

        It has to be applied here because the two hosts arrive with different
        dicts otherwise: Blender's wrapper reads its operator's typed properties,
        which Blender itself had already defaulted, while Painter hands over the
        click's own values. The same button then reached the same body with
        different keys, and a body reading one the button never stated raised
        KeyError on one host only.

        An argument this command does not declare is an error rather than a
        passenger: it is a layout naming something the body will never read, which
        is a button that silently does not do what it says."""
        values = {field.key: field.default for field in self.arguments}
        unknown = sorted(name for name in arguments if name not in values)
        if unknown:
            raise TypeError("command {0!r} has no argument {1} -- it declares {2}".format(
                self.id, ", ".join(repr(name) for name in unknown),
                ", ".join(repr(field.key) for field in self.arguments) or "none"))
        values.update(arguments)
        return values

    def status_state_for(self, arguments):
        """The panel whose progress and status this run writes into."""
        return (self.status_state(arguments) if callable(self.status_state)
                else self.status_state)

    def settle(self, context, result):
        """What to do with what the steps returned. Nothing, unless declared."""
        return self._settle(context, result) if self._settle is not None else None

    def __repr__(self):
        return "<Command {0}>".format(self.id)


def draw_progress(layout, state):
    """The one way a panel shows a steps command in flight: a bar carrying the
    hook's own newest console line.

    The line is the hook's output, trimmed from the FRONT for display only, which
    keeps the asset it names in view instead of the long path in front of it.

    Returns whether anything was drawn, so a panel can lay out around it."""
    if state is None or not getattr(state, "loading", False):
        return False
    box = layout.box()
    box.progress(factor=getattr(state, "progress", 0.0),
                 text=_trimmed(getattr(state, "load_line", "")))
    return True


def _trimmed(text, width=48):
    text = (text or "").strip()
    if not text:
        return "\u2026"
    return text if len(text) <= width else "\u2026" + text[-width:]


class Registry:
    """Every command, by id: the ``commands`` extension point with the command's
    own verbs over it, so a layout can name a command without importing whatever
    defines it. A reload and a collision mean here exactly what they mean for
    everything else that registers (:mod:`Kernel.extensions`)."""

    def __init__(self):
        self._point = extensions.point("commands", "Every command, by id.")

    def add(self, command):
        return self._point.add(command, key=command.id, module=command.source)

    def define(self, id, label, run, **spec):
        return self.add(Command(id, label, run, **spec))

    def get(self, id):
        found = self._point.get(id)
        if found is None:
            raise KeyError(
                "no command {0!r} -- a layout naming one that does not exist is a "
                "button that would do nothing".format(id))
        return found

    def available(self, capabilities):
        """The commands a host with these capabilities can offer."""
        return tuple(command for command in self._point
                     if command.requires is None or command.requires in capabilities)

    def __iter__(self):
        return iter(self._point)

    def __len__(self):
        return len(self._point)


#: The one registry. A command is registered where it is defined, and named from
#: a layout by its id alone.
COMMANDS = Registry()
