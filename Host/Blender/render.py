"""Render a kernel layout description with Blender's own UI, and wrap kernel
commands as Blender operators.

This renderer is deliberately dull. The vocabulary the kernel offers was chosen
to BE Blender's, so almost every node is one call with the same keywords, and
there is no decision in here about what a panel says -- only about how to say it
in this application. A bug in a panel is a bug in the kernel description; a bug
here would show up in every panel at once.

Icons are advisory. Their names are Blender's own enum spellings, which are
descriptive enough to serve as the neutral vocabulary; a host that has no
equivalent draws none, because an icon is decoration and a missing one costs
nothing a caption does not already say.
"""

from __future__ import annotations

import bpy

from ...Kernel.app import layout as app_layout
from ...Kernel.app import command as app_command
from ...Kernel.app import state as app_state
from ...Kernel import host as host_port
from . import rna

_ALIGNMENTS = {app_layout.LEFT: "LEFT", app_layout.CENTER: "CENTER",
               app_layout.RIGHT: "RIGHT", app_layout.EXPAND: "EXPAND"}


def _apply_modifiers(target, node):
    if not node.enabled:
        target.enabled = False
    if not node.active:
        target.active = False
    if node.alignment:
        target.alignment = _ALIGNMENTS[node.alignment]
    if node.scale_x:
        target.scale_x = node.scale_x
    if node.scale_y:
        target.scale_y = node.scale_y
    if node.emboss:
        target.emboss = node.emboss
    if node.alert:
        target.alert = True


def _text(spec, fallback=""):
    text = spec.get("text")
    return fallback if text is None else text


def render(node, target, context):
    """Walk one description into ``target`` (a bpy UILayout)."""
    kind, spec = node.kind, node.spec

    if kind == app_layout.COLUMN:
        made = target.column(align=spec["align"])
    elif kind == app_layout.ROW:
        made = target.row(align=spec["align"])
    elif kind == app_layout.BOX:
        made = target.box()
    elif kind == app_layout.SPLIT:
        made = target.split(factor=spec["factor"], align=spec["align"])
    elif kind == app_layout.GRID:
        made = target.grid_flow(row_major=True, columns=spec["columns"],
                                even_columns=spec["even_columns"], even_rows=False,
                                align=spec["align"])
    elif kind == app_layout.LABEL:
        made = target
        target.label(text=spec["text"], icon=spec["icon"] or "NONE")
    elif kind == app_layout.SEPARATOR:
        made = target
        target.separator()
    elif kind == app_layout.PROP:
        made = target
        keywords = {"icon": spec["icon"] or "NONE"}
        if spec["text"] is not None:
            keywords["text"] = spec["text"]
        if spec["expand"]:
            keywords["expand"] = True
        if spec["toggle"]:
            keywords["toggle"] = True
        if spec["slider"]:
            keywords["slider"] = True
        target.prop(spec["state"], spec["key"], **keywords)
    elif kind == app_layout.OPERATOR:
        made = target
        keywords = {"icon": spec["icon"] or "NONE", "depress": spec["depress"]}
        if spec["text"] is not None:
            keywords["text"] = spec["text"]
        operator = target.operator(spec["command"], **keywords)
        for name, value in spec["values"].items():
            setattr(operator, name, value)
    elif kind == app_layout.MENU:
        made = target
        target.menu(spec["menu"], text=spec["text"], icon=spec["icon"] or "NONE")
    elif kind == app_layout.POPOVER:
        made = target
        target.popover(spec["panel"], text=spec["text"], icon=spec["icon"] or "NONE")
    elif kind == app_layout.PROGRESS:
        made = target
        target.progress(factor=spec["factor"], text=spec["text"], type="BAR")
    elif kind == app_layout.LIST:
        made = target
        # Filing the description is a dict write, which a draw may do; making the
        # class it needs is not (see _LIST_SPECS).
        list_id = _list_id(spec)
        _LIST_SPECS[list_id] = spec
        target.template_list(RURI_UL_described.bl_idname, list_id,
                             spec["state"], spec["collection"],
                             spec["state"], spec["index_key"], rows=spec["rows"])
    else:
        raise TypeError("no Blender rendering for layout node {0!r}".format(kind))

    if made is not target:
        _apply_modifiers(made, node)
    for child in node.children:
        render(child, made, context)


def draw(description, target, context):
    """Render a described panel (the root node's children go straight onto the
    panel's own layout, so the panel has no extra column wrapped around it)."""
    _apply_modifiers(target, description)
    for child in description.children:
        render(child, target, context)


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------
#: list id -> the description last drawn under it.
#:
#: Blender addresses a UIList by a REGISTERED class, and a description is the only
#: thing that knows a list exists -- which used to mean generating a class per list
#: and registering it the first time that list was drawn. That is exactly what a
#: draw may not do: ``bpy.data`` is read-only while the UI is drawing, so every
#: such registration raised ``cannot run in readonly state`` and Blender swallowed
#: it into the console. Not one list in this add-on drew.
#:
#: So there is ONE class, registered with everything else, and which description it
#: should draw is the id the template was given -- handed back as ``list_id``.
_LIST_SPECS = {}


def _is_group(group_key, item):
    """Whether one drawn line is a section header. A list that owns its records
    says so with a field of theirs; a list bound to a VIEW owns no records -- the
    view knows -- so the description may answer with a callable instead."""
    return group_key(item) if callable(group_key) else getattr(item, group_key, False)


def _list_id(spec):
    return spec["identifier"] or (spec["collection"] + "_list")


def _cell(target, spec, column, item, index, selected):
    """One cell, as whatever the column says it is."""
    sub = target.row(align=True)
    if column.align:
        sub.alignment = _ALIGNMENTS[column.align]
    sub.enabled = column.is_enabled(item)
    sub.active = column.is_active(item)
    icon = column.icon_of(item) or "NONE"
    if column.prop:
        # Back to normal embossing: the row as a whole may be drawn flat so a
        # click reads as a selection, but a control the user OPERATES has to
        # look like one.
        sub.emboss = "NORMAL"
        sub.prop(item, column.prop, text=column.text(item))
    elif column.command and (column.values is None or column.arguments(item)):
        sub.emboss = "NORMAL"
        operator = sub.operator(column.command, text=column.text(item), icon=icon)
        for name, value in column.arguments(item).items():
            setattr(operator, name, value)
    elif spec["on_click"]:
        operator = sub.operator(spec["on_click"], text=column.text(item),
                                icon=icon, depress=selected)
        setattr(operator, spec["click_argument"], index)
    else:
        sub.label(text=column.text(item), icon=icon)


class RURI_UL_described(bpy.types.UIList):
    """Every list this add-on draws, from the description filed under its id."""

    bl_idname = "RURI_UL_described"

    def draw_item(self, context, target, data, item, icon, active_data,
                  active_property, index):
        spec = _LIST_SPECS[self.list_id]
        group_key = spec["group_key"]
        if group_key and _is_group(group_key, item):
            group_column = spec["group_column"]
            group_values = spec["group_values"]
            row = target.row(align=True)
            values = ({} if group_values is None else dict(group_values(item)))
            if spec["group_command"] and values:
                operator = row.operator(
                    spec["group_command"], text=group_column.text(item),
                    icon=group_column.icon_of(item) or "FILE_FOLDER")
                for name, value in values.items():
                    setattr(operator, name, value)
            else:
                row.enabled = False
                row.label(text=group_column.text(item),
                          icon=group_column.icon_of(item) or "OUTLINER_COLLECTION")
            return

        row = target.row(align=True)
        selected = bool(getattr(item, "selected", False))
        if spec["on_click"]:
            # Every text cell is the SAME click command, so the whole row is one
            # target and plain / ctrl / shift clicks all land in one place with
            # their modifiers intact -- which is what makes selection behave like a
            # file browser. Flat until selected, then a solid highlight bar.
            row.emboss = "NONE_OR_STATUS"

        # Nested splits, exactly as Blender means them: each factor is a fraction
        # of what is LEFT after the previous column, and content added to a split
        # lands in its left cell while splitting it again lands in the right. The
        # last column takes whatever remains.
        columns = spec["columns"]
        row_action = spec["row_action"]
        current = row
        for position, column in enumerate(columns):
            if position < len(columns) - 1:
                current = current.split(factor=column.width_of(data), align=True)
                _cell(current, spec, column, item, index, selected)
            elif row_action:
                tail = current.split(factor=spec["row_action_width"], align=True)
                _cell(tail, spec, column, item, index, selected)
                action = tail.operator(row_action, text="",
                                       icon=spec["row_action_icon"] or "FILE_FOLDER")
                setattr(action, spec["click_argument"], index)
            else:
                _cell(current, spec, column, item, index, selected)

    def filter_items(self, context, data, propname):
        """Filtering already happened against the game's own fields rather than
        the drawn string. A list that HIDES rather than removes says so with a
        field of its own, and this is where that verdict is applied."""
        spec = _LIST_SPECS[self.list_id]
        records = getattr(data, propname)
        drawn = spec["visible_count"]
        if drawn >= 0:
            # A pooled list: the view already said how many lines there are, so
            # the tail is hidden rather than removed -- two C-level list builds,
            # not a python pass over four thousand records on every redraw.
            drawn = min(drawn, len(records))
            return [self.bitflag_filter_item] * drawn + [0] * (len(records) - drawn), []
        visible_key = spec["visible_key"]
        if not visible_key:
            return [], []
        return [self.bitflag_filter_item if getattr(record, visible_key, True) else 0
                for record in records], []


def unregister_lists():
    """Drop the descriptions drawn so far. The class itself is registered and
    dropped with every other one this driver owns."""
    _LIST_SPECS.clear()


# ---------------------------------------------------------------------------
# Commands -> operators
# ---------------------------------------------------------------------------
def _handlers():
    return app_state.Handlers("render")


def operator_class(command):
    """A real bpy Operator wrapping a kernel command, keeping the command's id as
    the bl_idname so every existing keymap entry and menu reference resolves."""
    module, _, name = command.id.partition(".")
    annotations = {}
    for field in command.arguments:
        annotations[field.key] = rna.property_for(field, _ArgumentSchema(command),
                                                 _handlers(), {})

    def arguments_of(operator):
        return {field.key: getattr(operator, field.key) for field in command.arguments}

    def execute(self, context):
        result = command.run(context, arguments_of(self))
        if result is None:
            return {"FINISHED"}
        return result

    namespace = {
        "bl_idname": command.id,
        "bl_label": command.label,
        "bl_description": command.description or command.label,
        "bl_options": ({"INTERNAL"} if command.internal else
                      {"REGISTER", "UNDO"} if command.undo else {"REGISTER"}),
        "__annotations__": annotations,
        "RURI_COMMAND": command,
    }
    if command._poll is not None:
        namespace["poll"] = classmethod(lambda cls, context: command.poll(context))

    if command.modifiers:
        # invoke() is the only place Blender hands over the event; execute() is
        # still the whole body, so a keymap entry or a script that states the
        # modifiers itself runs the identical path.
        def invoke(self, context, event):
            setattr(self, app_command.EXTEND, bool(event.ctrl))
            setattr(self, app_command.RANGE, bool(event.shift))
            return self.execute(context)

        namespace["invoke"] = invoke

    if not command.steps:
        namespace["execute"] = execute
        return type("RURI_OT_" + name, (bpy.types.Operator,), namespace)

    # A command that says its work as steps is driven MODALLY here -- reads on a
    # worker, builds between ticks -- so the window stays alive. The same
    # generator runs inline when there is no window (a script, a headless build),
    # which is what keeps the two from diverging.
    from ...Kernel import host as host_port
    from . import step_loader

    def load_steps(self, context):
        return command.run(context, arguments_of(self))

    def status(self, context):
        name = command.status_state_for(arguments_of(self))
        if not name:
            return None
        return host_port.current().panel_state(context, name)

    def settle(self, context, result):
        outcome = command.settle(context, result)
        return outcome if outcome is not None else {"FINISHED"}

    def execute_inline(self, context):
        return settle(self, context, step_loader.run(load_steps(self, context)))

    namespace.update({"load_steps": load_steps, "status": status, "settle": settle,
                      "execute": execute_inline, "failure": command.failure})
    return type("RURI_OT_" + name, (step_loader.ModalSteps, bpy.types.Operator), namespace)


class _ArgumentSchema:
    """Just enough of a schema for rna's error messages to name the command."""

    __slots__ = ("name",)

    def __init__(self, command):
        self.name = command.id


#: Command id -> the Operator class generated for it. One per process; a command
#: declared anywhere gets its wrapper from the sweep below, so no module has to
#: remember to register one.
_COMMAND_OPERATORS = {}


def register_commands():
    """Wrap every declared command as a Blender operator, and register the ones
    this host can offer -- plus the one UIList every described list draws through.

    Called after the game registry has run, because that is when every command
    exists. A command whose capability this host does not answer gets no operator
    at all -- the control that would place it is absent for the same reason."""
    bpy.utils.register_class(RURI_UL_described)
    capabilities = host_port.current().capabilities
    for one in app_command.COMMANDS.available(capabilities):
        if one.id in _COMMAND_OPERATORS:
            continue
        made = operator_class(one)
        bpy.utils.register_class(made)
        _COMMAND_OPERATORS[one.id] = made
    return _COMMAND_OPERATORS


def unregister_commands():
    for made in reversed(list(_COMMAND_OPERATORS.values())):
        try:
            bpy.utils.unregister_class(made)
        except RuntimeError:
            pass
    _COMMAND_OPERATORS.clear()
    try:
        bpy.utils.unregister_class(RURI_UL_described)
    except RuntimeError:
        pass
