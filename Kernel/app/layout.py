"""One panel description, two very different UI toolkits.

The vocabulary below is deliberately Blender's own -- ``column/row/box/split/
grid_flow``, ``label/prop/operator/menu/popover/separator``, a list, and the
``enabled``/``alignment`` modifiers. Not because Blender is privileged, but
because that vocabulary turned out to be a good neutral one: it is
immediate-mode, it nests, and it says WHAT is on screen rather than how to build
a widget. Choosing it means the panel bodies that already exist port by changing
their imports rather than by being rewritten, and Blender's renderer is a
straight pass-through with nothing to go wrong in it.

A ``draw(layout, context)`` written against this builds a TREE. Each host then
walks that tree with its own renderer: Blender emits ``UILayout`` calls, Painter
builds Qt widgets. Neither renderer contains a decision about what the panel
says -- that is all here, once.

WHAT IS NOT IN THE VOCABULARY is as deliberate. There is no "raw widget" escape
hatch and no per-host branch: a panel that needs something only one host can do
has to say so as a CAPABILITY (``Kernel.host``) and be absent on the other,
because a control that exists but cannot work is worse than one that is honestly
missing.
"""

from __future__ import annotations

COLUMN = "column"
ROW = "row"
BOX = "box"
SPLIT = "split"
GRID = "grid"
LABEL = "label"
SEPARATOR = "separator"
PROP = "prop"
OPERATOR = "operator"
MENU = "menu"
POPOVER = "popover"
LIST = "list"
PROGRESS = "progress"

LEFT = "LEFT"
CENTER = "CENTER"
RIGHT = "RIGHT"
EXPAND = "EXPAND"


class Node:
    """One entry in the description. Containers carry children; leaves carry a
    spec the renderer reads."""

    __slots__ = ("kind", "spec", "children", "enabled", "active", "alignment",
                 "scale_x", "scale_y", "emboss", "alert")

    def __init__(self, kind, **spec):
        self.kind = kind
        self.spec = spec
        self.children = []
        self.enabled = True
        self.active = True
        self.alignment = ""
        self.scale_x = 0.0
        self.scale_y = 0.0
        self.emboss = ""
        #: Something in here is wrong and the user has to fix it before the
        #: thing it belongs to can work. Both hosts have a way to say that in
        #: colour, and a warning that is only worded is a warning nobody reads.
        self.alert = False

    def __repr__(self):
        return "<Node {0} ({1} children)>".format(self.kind, len(self.children))


class Arguments:
    """What ``op = layout.operator(...); op.index = 3`` writes into.

    Assignment rather than a dict argument because that is how the existing
    panels are written, and because it reads better at the call site: the line
    that places the button is the line that says what it acts on."""

    __slots__ = ("_values",)

    def __init__(self, values):
        object.__setattr__(self, "_values", values)

    def __setattr__(self, name, value):
        self._values[name] = value

    def __getattr__(self, name):
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(name)


class ListColumn:
    """One cell of a list row.

    A cell is TEXT by default -- ``key`` is the attribute to read off the record,
    or a callable that words it. It becomes something else when the row says so:

    ``prop``     a field of the RECORD, edited in place (a checkbox, a weight).
    ``command``  a button, with ``values(record)`` supplying its arguments.

    ``icon``, ``enabled`` and ``active`` may each be a constant or a callable of
    the record -- what makes a row's own state readable without a second column
    of words for it. ``enabled`` is "cannot be used", ``active`` is "has nothing
    behind it here", exactly as they mean everywhere else in the vocabulary.
    """

    __slots__ = ("key", "label", "width", "align", "prop", "command", "values",
                 "icon", "enabled", "active", "role")

    def __init__(self, key, label="", width=0.0, align="", prop="", command="",
                 values=None, icon="", enabled=None, active=None, role=None):
        #: Attribute read off the row record, or a callable (record) -> str.
        self.key = key
        self.label = label
        #: Fraction of the space LEFT after the previous column (split
        #: semantics), or 0 to take whatever remains.
        self.width = width
        self.align = align
        #: Field of the record this cell edits, instead of printing it.
        self.prop = prop
        #: Command this cell invokes, instead of printing anything.
        self.command = command
        #: (record) -> {argument: value} for that command.
        self.values = values
        self.icon = icon
        self.enabled = enabled
        self.active = active
        #: The ROLE this cell reads, for a column addressed by what it means rather
        #: than by what one build happens to call it. A list drops such a column
        #: whole when its table carries nothing in that role, so a panel shared by
        #: several builds names no build's column and shows no empty one either.
        self.role = role

    def text(self, record):
        if callable(self.key):
            return self.key(record)
        if not self.key:
            return ""
        return str(getattr(record, self.key, ""))

    def icon_of(self, record):
        return self.icon(record) if callable(self.icon) else self.icon

    def arguments(self, record):
        return dict(self.values(record)) if self.values is not None else {}

    def width_of(self, state):
        """This column's share of the width that is LEFT where it starts.

        A width may name a live panel property instead of being a constant -- the
        browser's column widths are draggable -- and WHICH it is belongs to the
        column rather than to each renderer: a host that read the constant and not
        the property would draw a list nobody could re-proportion, and one that
        guessed its own proportions (the Qt list sized itself from its content for
        a while) draws a different list from the one described."""
        if isinstance(self.width, str):
            return float(getattr(state, self.width, 0.5))
        return float(self.width or 0.5)

    def _flag(self, which, record, default=True):
        value = getattr(self, which)
        if value is None:
            return default
        return bool(value(record)) if callable(value) else bool(value)

    def is_enabled(self, record):
        return self._flag("enabled", record)

    def is_active(self, record):
        return self._flag("active", record)


class Layout:
    """The builder handed to a ``draw(layout, context)``."""

    __slots__ = ("node",)

    def __init__(self, node=None):
        self.node = node if node is not None else Node(COLUMN)

    # -- containers --------------------------------------------------------
    def _child(self, kind, **spec):
        made = Node(kind, **spec)
        made.enabled = self.node.enabled
        made.active = self.node.active
        self.node.children.append(made)
        return Layout(made)

    def column(self, align=False):
        return self._child(COLUMN, align=align)

    def row(self, align=False):
        return self._child(ROW, align=align)

    def box(self):
        return self._child(BOX)

    def split(self, factor=0.5, align=False):
        return self._child(SPLIT, factor=factor, align=align)

    def grid_flow(self, columns=0, align=False, even_columns=True):
        return self._child(GRID, columns=columns, align=align, even_columns=even_columns)

    # -- leaves ------------------------------------------------------------
    def label(self, text="", icon=""):
        self._child(LABEL, text=text, icon=icon)

    def separator(self):
        self._child(SEPARATOR)

    def prop(self, state, key, text=None, icon="", expand=False, toggle=False,
             slider=False):
        self._child(PROP, state=state, key=key, text=text, icon=icon,
                    expand=expand, toggle=toggle, slider=slider)

    def operator(self, command_id, text=None, icon="", depress=False):
        values = {}
        self._child(OPERATOR, command=command_id, text=text, icon=icon,
                    depress=depress, values=values)
        return Arguments(values)

    def menu(self, menu_id, text="", icon=""):
        self._child(MENU, menu=menu_id, text=text, icon=icon)

    def popover(self, panel_id, text="", icon=""):
        self._child(POPOVER, panel=panel_id, text=text, icon=icon)

    def progress(self, factor, text=""):
        self._child(PROGRESS, factor=factor, text=text)

    def list(self, state, collection, index_key, columns, rows=10, identifier="",
             on_click="", click_argument="index", group_key="", group_column=None,
             group_command="", group_values=None, row_action="", row_action_icon="",
             row_action_width=0.88, visible_key="", visible_count=-1):
        """A row list.

        ``columns`` are :class:`ListColumn`. ``on_click`` names the command every
        cell of a row invokes -- the whole row is one click target, so plain /
        ctrl / shift clicks land in one place with their modifiers intact, which
        is what makes multi-selection behave like a file browser.

        ``group_key`` is the record field marking a HEADER row (a folder in the
        browser, a section in the roster). Such a row is drawn as
        ``group_column`` alone -- full width, and navigating rather than
        selecting when ``group_command`` is given and ``group_values(record)``
        says what it would act on. A section that leads nowhere comes back with
        no values and is a caption.

        ``row_action`` is a small trailing button on every file row (the
        browser's "go to this row's folder"), taking the same argument the
        click command does.

        ``visible_key`` is the record field the filter already decided: a row
        it says False for is HIDDEN rather than removed, so a row ticked before
        the search box was typed into is still ticked afterwards.

        ``visible_count`` is how many of the collection's records are actually a
        line right now, for a list whose records are a POOL rather than the
        answer. A list bound to a view owns no records of its own -- the view
        says how many lines there are and what each one reads -- so the pool
        grows to the largest it has ever needed and the tail is hidden, instead
        of being torn down and rebuilt on every keystroke. -1 means every record
        is a line."""
        self._child(LIST, state=state, collection=collection, index_key=index_key,
                    columns=tuple(columns), rows=rows, identifier=identifier,
                    on_click=on_click, click_argument=click_argument,
                    group_key=group_key, group_column=group_column,
                    group_command=group_command, group_values=group_values,
                    row_action=row_action, row_action_icon=row_action_icon,
                    row_action_width=row_action_width, visible_key=visible_key,
                    visible_count=visible_count)

    # -- modifiers ---------------------------------------------------------
    def __setattr__(self, name, value):
        if name == "node":
            object.__setattr__(self, name, value)
            return
        if name not in Node.__slots__:
            raise AttributeError(
                "a layout has no {0!r} -- the vocabulary is {1}".format(
                    name, ", ".join(n for n in Node.__slots__
                                    if n not in ("kind", "spec", "children"))))
        setattr(self.node, name, value)

    def __getattr__(self, name):
        if name in Node.__slots__:
            return getattr(self.node, name)
        raise AttributeError(name)


def describe(draw, context):
    """Run a panel body and hand back what it said."""
    root = Layout()
    draw(root, context)
    return root.node
