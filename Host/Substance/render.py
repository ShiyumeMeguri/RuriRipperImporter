"""Render a kernel layout description with Qt, and invoke kernel commands.

The Blender renderer is a pass-through because the vocabulary is Blender's own;
this one has real work to do, because Qt is retained-mode and the description is
immediate-mode. The answer is to rebuild the widget tree whenever the
description changes and to keep the widgets keyed by their position in it, so a
redraw is cheap and nothing accumulates.

Everything this file decides is about HOW to show something in Qt. What is shown
is the kernel's description, identical to the one Blender renders -- the two
panels are the same panel.
"""

from __future__ import annotations

import traceback

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from ...Kernel.app import command as app_command
from ...Kernel.app import layout as app_layout
from ...Kernel.app import state as app_state

#: Blender icon names that map onto something Qt ships. Icons are advisory: an
#: unmapped one simply is not drawn, which costs nothing a caption does not
#: already say.
_ICONS = {
    "ERROR": QtWidgets.QStyle.SP_MessageBoxCritical,
    "INFO": QtWidgets.QStyle.SP_MessageBoxInformation,
    "QUESTION": QtWidgets.QStyle.SP_MessageBoxQuestion,
    "FILE_FOLDER": QtWidgets.QStyle.SP_DirIcon,
    "FILE_3D": QtWidgets.QStyle.SP_FileIcon,
    "FILE_REFRESH": QtWidgets.QStyle.SP_BrowserReload,
    "FILE_TICK": QtWidgets.QStyle.SP_DialogSaveButton,
    "IMPORT": QtWidgets.QStyle.SP_ArrowDown,
    "TRASH": QtWidgets.QStyle.SP_TrashIcon,
    "X": QtWidgets.QStyle.SP_DialogCloseButton,
    "ADD": QtWidgets.QStyle.SP_FileDialogNewFolder,
    "HOME": QtWidgets.QStyle.SP_DirHomeIcon,
    "LOCKED": QtWidgets.QStyle.SP_DialogCancelButton,
    "VIEWZOOM": QtWidgets.QStyle.SP_FileDialogContentsView,
}

_ALIGNMENTS = {
    app_layout.LEFT: Qt.AlignLeft | Qt.AlignVCenter,
    app_layout.CENTER: Qt.AlignHCenter | Qt.AlignVCenter,
    app_layout.RIGHT: Qt.AlignRight | Qt.AlignVCenter,
}


def _icon(widget, name):
    role = _ICONS.get(name or "")
    return widget.style().standardIcon(role) if role is not None else None


#: How narrow a control may be drawn before the panel stops shrinking it -- about
#: a checkbox indicator, which is the narrowest thing that still reads as a
#: control. It is a floor per CONTROL, so a line's floor is its controls': the
#: browser's deepest breadcrumb row (15 of them) then floors at 240px, inside the
#: 326px a docked panel has. Wider, and that one row alone put the panel over.
_NARROWEST = 16


class _Elided:
    """A control the panel may draw NARROWER THAN ITS OWN CAPTION.

    Qt sizes a button, a tool button, a checkbox or a field's caption from its
    text and then refuses to go below it, and a column is as wide as the widest
    floor in it -- so one long caption anywhere puts a floor under the whole dock.
    Painter cannot honour that floor: it gives the dock the width the user left it
    at and clips the rest, taking the row list's own scrollbar off screen with it,
    and a focus change then slides the panel sideways with no bar to bring it
    back. Measured on the browser tab of an Endfield install: the panel demanded
    930px inside a 326px dock.

    Blender's layout engine shortens the label instead, which is why the same
    description fits its N-panel at any width. This is that, in Qt's vocabulary:
    the control still ASKS for its whole caption, so nothing shrinks while there
    is room, settles for whatever width it is given, and shortens the text it
    draws to fit -- with the whole caption still in the tooltip."""

    #: Answers for a style that measures the widget while it is still being
    #: built, before there is a caption to measure against.
    _caption = ""
    _chrome = 0

    def bind_caption(self, caption):
        self._caption = caption or ""
        # Everything that is NOT the text -- frame, icon, checkbox indicator, the
        # style's own padding -- measured once against the full caption, so it is
        # the style's number rather than one written down here.
        self._chrome = max(0, super().sizeHint().width()
                           - self.fontMetrics().horizontalAdvance(self._caption))
        policy = self.sizePolicy()
        policy.setHorizontalPolicy(QtWidgets.QSizePolicy.Preferred)
        self.setSizePolicy(policy)
        if self._caption and not self.toolTip():
            self.setToolTip(self._caption)
        self._fit()

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(self._chrome + self.fontMetrics().horizontalAdvance(self._caption))
        return hint

    def minimumSizeHint(self):
        return QtCore.QSize(_NARROWEST, super().sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        room = max(0, self.width() - self._chrome)
        shown = self.fontMetrics().elidedText(self._caption, Qt.ElideRight, room)
        if shown != self.text():
            super().setText(shown)


class _Button(_Elided, QtWidgets.QPushButton):
    def __init__(self, caption=""):
        super().__init__()
        self.bind_caption(caption)


class _ToolButton(_Elided, QtWidgets.QToolButton):
    def __init__(self, caption=""):
        super().__init__()
        self.bind_caption(caption)


class _Check(_Elided, QtWidgets.QCheckBox):
    def __init__(self, caption=""):
        super().__init__()
        self.bind_caption(caption)


class _Caption(_Elided, QtWidgets.QLabel):
    """A field's own name, beside the editor. Wrapping is for a MESSAGE -- a
    caption that wraps pushes the control it names down a line."""

    def __init__(self, caption=""):
        super().__init__()
        self.bind_caption(caption)


class _Flow(QtWidgets.QLayout):
    """A row that wraps onto the next line when it runs out of width.

    This is what the description means by a grid: "a row divides the width it has
    between however many tabs there are, so every tab gets narrower as more open
    until each is a few clipped characters" is the reason the tab bar asks for one.
    Qt ships no wrapping box layout, so a grid used to be rendered as a plain row
    -- which is exactly the shape the kernel said not to use, and which put the
    whole tab bar's width into the panel's floor.

    ``columns`` fixes how many go on a line, as Blender's grid_flow does; 0 fits
    as many as the width allows."""

    def __init__(self, columns=0, spacing=3):
        super().__init__()
        self._items = []
        self._columns = max(0, int(columns))
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    # -- the five QLayout has to have --------------------------------------
    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def addLayout(self, layout):
        """Qt only puts this on the box layouts, and a container in a description
        is a layout rather than a widget."""
        self.addItem(layout)

    # -- geometry ----------------------------------------------------------
    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QtCore.QRect(0, 0, width, 0), measure_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, measure_only=False)

    def sizeHint(self):
        width = height = 0
        for item in self._items:
            hint = item.sizeHint()
            width += hint.width() + self.spacing()
            height = max(height, hint.height())
        return QtCore.QSize(max(0, width - self.spacing()), height)

    def minimumSize(self):
        size = QtCore.QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect, measure_only):
        """Place the items and answer the height it took."""
        spacing = self.spacing()
        left, top, line_height = rect.x(), rect.y(), 0
        per_line = (self._columns or 0)
        width = (max(_NARROWEST, (rect.width() - spacing * (per_line - 1)) // per_line)
                 if per_line else 0)
        on_line = 0
        for item in self._items:
            hint = item.sizeHint()
            take = width or min(hint.width(), rect.width())
            if on_line and (on_line >= per_line if per_line
                            else left + take > rect.right() + 1):
                left, top, on_line = rect.x(), top + line_height + spacing, 0
                line_height = 0
            if not measure_only:
                item.setGeometry(QtCore.QRect(left, top, take, hint.height()))
            left += take + spacing
            line_height = max(line_height, hint.height())
            on_line += 1
        return top + line_height - rect.y()


class _Rows(QtWidgets.QTableWidget):
    """A row list, laid out to the width the panel actually has.

    The columns are the DESCRIPTION's, resolved the one way both hosts resolve
    them: each factor is a share of what is left where that column starts, and
    the last one takes the remainder (``ListColumn.width_of``, which Blender
    renders as nested splits). Because the shares are of the viewport, the list
    always fits it -- what scrolls is the rows, inside the list, which is the
    only scrolling a panel this narrow can afford.

    It used to size itself instead: one stretched column and the rest at content
    width. A row whose Source is a full archive path then asked for 3838px of
    columns inside a 904px viewport, so the list scrolled sideways, the name
    column went off screen, and the panel's own scrollbar went with it."""

    def __init__(self, columns, state):
        super().__init__(0, len(columns))
        self._factors = [column.width_of(state) for column in columns]
        header = self.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        header.setStretchLastSection(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._spread()

    def _spread(self):
        header = self.horizontalHeader()
        left = self.viewport().width()
        for position, factor in enumerate(self._factors[:-1]):
            width = max(_NARROWEST, int(round(left * factor)))
            header.resizeSection(position, width)
            left = max(0, left - width)
        if self._factors:
            header.resizeSection(len(self._factors) - 1, max(_NARROWEST, left))


class Renderer:
    """Turns one description into widgets under ``root_layout``.

    ``invoke`` is how a command reaches the host: it runs the command and then
    asks for a redraw, which is what makes the description immediate-mode from
    the panel author's point of view even though Qt is not."""

    def __init__(self, host_widget, context, invoke, on_error=None):
        self.host_widget = host_widget
        self.context = context
        self.invoke = invoke
        self.on_error = on_error or (lambda label, detail: None)

    # -- entry -------------------------------------------------------------
    def draw(self, description, target):
        for child in description.children:
            self._node(child, target, enabled=description.enabled)

    # -- nodes -------------------------------------------------------------
    def _node(self, node, target, enabled=True):
        kind, spec = node.kind, node.spec
        enabled = enabled and node.enabled

        if kind in (app_layout.COLUMN, app_layout.BOX):
            container = self._container(QtWidgets.QVBoxLayout, node, target, boxed=kind == app_layout.BOX)
        elif kind == app_layout.GRID:
            container = self._container(
                lambda: _Flow(spec["columns"]), node, target)
        elif kind in (app_layout.ROW, app_layout.SPLIT):
            container = self._container(QtWidgets.QHBoxLayout, node, target)
        elif kind == app_layout.LABEL:
            self._label(spec, target, enabled)
            return
        elif kind == app_layout.SEPARATOR:
            line = QtWidgets.QFrame()
            line.setFrameShape(QtWidgets.QFrame.HLine)
            line.setFrameShadow(QtWidgets.QFrame.Plain)
            target.addWidget(line)
            return
        elif kind == app_layout.PROP:
            self._prop(spec, target, enabled)
            return
        elif kind == app_layout.OPERATOR:
            self._operator(spec, target, enabled)
            return
        elif kind == app_layout.MENU:
            self._menu(spec, target, enabled)
            return
        elif kind == app_layout.POPOVER:
            self._popover(spec, target, enabled)
            return
        elif kind == app_layout.PROGRESS:
            self._progress(spec, target)
            return
        elif kind == app_layout.LIST:
            self._list(spec, target, enabled)
            return
        else:
            raise TypeError("no Qt rendering for layout node {0!r}".format(kind))

        for child in node.children:
            self._node(child, container, enabled=enabled)

    def _container(self, layout_class, node, target, boxed=False):
        made = layout_class()
        made.setContentsMargins(0, 0, 0, 0)
        made.setSpacing(3)
        if boxed or node.alert:
            frame = QtWidgets.QFrame()
            frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
            frame.setLayout(made)
            made.setContentsMargins(4, 4, 4, 4)
            if node.alert:
                # Blender paints an alert layout red; the same statement in Qt
                # is a red border, from the palette rather than a written-in
                # colour so it follows whatever theme Painter runs.
                frame.setStyleSheet(
                    "QFrame {{ border: 1px solid {0}; }}".format(
                        self.host_widget.palette().color(
                            QtGui.QPalette.BrightText).name()))
            target.addWidget(frame)
        else:
            target.addLayout(made)
        return made

    def _label(self, spec, target, enabled):
        widget = QtWidgets.QLabel(spec["text"])
        widget.setEnabled(enabled)
        widget.setWordWrap(True)
        icon = _icon(self.host_widget, spec["icon"])
        if icon is not None:
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            mark = QtWidgets.QLabel()
            mark.setPixmap(icon.pixmap(14, 14))
            row.addWidget(mark)
            row.addWidget(widget, 1)
            target.addLayout(row)
            return
        target.addWidget(widget)

    # -- properties --------------------------------------------------------
    def _prop(self, spec, target, enabled):
        bag, key = spec["state"], spec["key"]
        field = bag.schema.field(key)
        label = spec["text"] if spec["text"] is not None else field.label or key
        widget = self._editor(bag, key, field, spec)
        # A field with a getter and no setter is an observation of live state --
        # there is nothing for the user to type into it.
        writable = bool(field.setter) or not field.getter
        widget.setEnabled(enabled and writable)
        if field.description:
            widget.setToolTip(field.description)

        if field.kind == app_state.BOOL and not spec["toggle"]:
            target.addWidget(widget)
            return
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if label:
            caption = _Caption(label)
            caption.setEnabled(enabled)
            row.addWidget(caption)
        row.addWidget(widget, 1)
        if field.subtype in (app_state.DIRECTORY, app_state.FILE):
            row.addWidget(self._browse_button(bag, key, field, widget))
        target.addLayout(row)

    def _editor(self, bag, key, field, spec):
        value = getattr(bag, key)
        if field.kind == app_state.BOOL:
            widget = _Check(
                spec["text"] if spec["text"] is not None else (field.label or key))
            widget.setChecked(bool(value))
            widget.toggled.connect(lambda state: self._write(bag, key, bool(state)))
            return widget
        if field.kind == app_state.INT:
            widget = QtWidgets.QSpinBox()
            widget.setRange(-999999 if field.minimum is None else field.minimum,
                            999999 if field.maximum is None else field.maximum)
            widget.setValue(int(value))
            widget.valueChanged.connect(lambda number: self._write(bag, key, int(number)))
            return widget
        if field.kind == app_state.FLOAT:
            widget = QtWidgets.QDoubleSpinBox()
            widget.setRange(-999999.0 if field.minimum is None else field.minimum,
                            999999.0 if field.maximum is None else field.maximum)
            widget.setSingleStep(0.05)
            widget.setValue(float(value))
            widget.valueChanged.connect(lambda number: self._write(bag, key, float(number)))
            return widget
        if field.kind == app_state.ENUM:
            widget = QtWidgets.QComboBox()
            for identifier, caption, _description in bag.enum_items(key):
                widget.addItem(caption or identifier, identifier)
            position = widget.findData(value)
            widget.setCurrentIndex(position if position >= 0 else 0)
            widget.currentIndexChanged.connect(
                lambda _index, w=widget: self._write(bag, key, w.currentData()))
            return widget
        widget = QtWidgets.QLineEdit(str(value))
        if field.live:
            widget.textChanged.connect(lambda text: self._write(bag, key, text))
        else:
            widget.editingFinished.connect(
                lambda w=widget: self._write(bag, key, w.text()))
        return widget

    def _browse_button(self, bag, key, field, editor):
        button = _ToolButton("...")

        def pick():
            if field.subtype == app_state.DIRECTORY:
                chosen = QtWidgets.QFileDialog.getExistingDirectory(
                    self.host_widget, field.label or key, str(getattr(bag, key) or ""))
            else:
                chosen, _filter = QtWidgets.QFileDialog.getOpenFileName(
                    self.host_widget, field.label or key, str(getattr(bag, key) or ""))
            if chosen:
                self._write(bag, key, chosen)

        button.clicked.connect(pick)
        return button

    def _write(self, bag, key, value):
        try:
            setattr(bag, key, value)
        except Exception:
            self.on_error("setting " + key, traceback.format_exc())
            return
        self.invoke(None, {})

    # -- commands ----------------------------------------------------------
    def _operator(self, spec, target, enabled):
        command = app_command.COMMANDS.get(spec["command"])
        text = spec["text"] if spec["text"] is not None else command.label
        button = _Button(text)
        icon = _icon(self.host_widget, spec["icon"])
        if icon is not None:
            button.setIcon(icon)
        if not text and icon is not None:
            button.setMaximumWidth(28)
        button.setCheckable(bool(spec["depress"]))
        button.setChecked(bool(spec["depress"]))
        button.setEnabled(enabled and command.poll(self.context))
        if command.description:
            button.setToolTip(command.description)
        values = dict(spec["values"])
        button.clicked.connect(
            lambda: self.invoke(command, dict(values, **_modifiers(command))))
        target.addWidget(button)

    def _menu(self, spec, target, enabled):
        """A Blender menu is a dropdown of commands; Qt's nearest is a tool
        button with a popup menu. The entries come from the same place Blender's
        do -- the menu id names a command group in the registry."""
        button = _ToolButton(spec["text"] or "")
        button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        button.setEnabled(enabled)
        menu = QtWidgets.QMenu(button)
        first = True
        for entry in _menu_entries(spec["menu"], self.context):
            if entry.get("separator"):
                # A section caption, exactly what Blender draws for the same row.
                if not first:
                    menu.addSeparator()
                menu.addAction(entry["text"]).setEnabled(False)
                first = False
                continue
            first = False
            action = menu.addAction(entry["text"])
            command = app_command.COMMANDS.get(entry["command"])
            values = dict(entry.get("values", {}))
            action.triggered.connect(
                lambda _checked=False, c=command, v=values: self.invoke(c, v))
        button.setMenu(menu)
        target.addWidget(button)

    def _popover(self, spec, target, enabled):
        """Blender's popover is a panel opened from a button. Qt gets the same:
        a button that opens the described panel in a small dialog."""
        button = _ToolButton(spec["text"] or "")
        icon = _icon(self.host_widget, spec["icon"])
        if icon is not None:
            button.setIcon(icon)
        button.setEnabled(enabled)
        button.clicked.connect(lambda: self._open_popover(spec["panel"]))
        target.addWidget(button)

    def _open_popover(self, panel_id):
        describe = _POPOVERS.get(panel_id)
        if describe is None:
            return
        dialog = QtWidgets.QDialog(self.host_widget)
        dialog.setWindowTitle(panel_id)
        body = QtWidgets.QVBoxLayout(dialog)
        self.draw(app_layout.describe(describe, self.context), body)
        dialog.exec()

    def _progress(self, spec, target):
        bar = QtWidgets.QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(round(spec["factor"] * 100)))
        bar.setFormat(spec["text"] or "%p%")
        target.addWidget(bar)

    # -- lists -------------------------------------------------------------
    def _list(self, spec, target, enabled):
        columns = spec["columns"]
        bag = spec["state"]
        records = getattr(bag, spec["collection"])
        visible_key = spec["visible_key"]
        table = _Rows(columns, bag)
        table.setHorizontalHeaderLabels([column.label for column in columns])
        table.horizontalHeader().setVisible(any(column.label for column in columns))
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setShowGrid(False)
        table.setFrameShape(QtWidgets.QFrame.NoFrame)
        table.setWordWrap(False)
        table.setEnabled(enabled)

        group_key = spec["group_key"]
        # A pooled list draws exactly the lines the view says it has; the rest of
        # the pool is not a line at all.
        drawn = spec["visible_count"]
        for index, record in enumerate(records):
            if 0 <= drawn <= index:
                break
            if visible_key and not getattr(record, visible_key, True):
                continue
            row = table.rowCount()
            table.insertRow(row)
            if group_key and (group_key(record) if callable(group_key)
                              else getattr(record, group_key, False)):
                item = QtWidgets.QTableWidgetItem(spec["group_column"].text(record))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setData(Qt.UserRole, ("group", index))
                table.setItem(row, 0, item)
                if len(columns) > 1:
                    table.setSpan(row, 0, 1, len(columns))
                continue
            for position, column in enumerate(columns):
                widget = self._cell_widget(column, record, index)
                if widget is not None:
                    table.setCellWidget(row, position, widget)
                    continue
                item = QtWidgets.QTableWidgetItem(column.text(record))
                item.setData(Qt.UserRole, ("row", index))
                if column.align in _ALIGNMENTS:
                    item.setTextAlignment(_ALIGNMENTS[column.align])
                if not column.is_active(record) or not column.is_enabled(record):
                    item.setForeground(self.host_widget.palette().brush(
                        QtGui.QPalette.Disabled, QtGui.QPalette.WindowText))
                table.setItem(row, position, item)
            if getattr(record, "selected", False):
                table.selectRow(row)

        table.setMinimumHeight(max(spec["rows"], 6) * 20)
        table.itemClicked.connect(lambda item: self._row_clicked(spec, item))
        table.itemDoubleClicked.connect(lambda item: self._row_activated(spec, item))
        target.addWidget(table, 1)

    def _cell_widget(self, column, record, index):
        """A cell the user OPERATES rather than reads, or None for plain text."""
        if column.prop:
            field = record.schema.field(column.prop)
            widget = self._editor(record, column.prop, field,
                                  {"text": column.text(record) or "", "toggle": False})
            widget.setEnabled(column.is_enabled(record))
            return widget
        if column.command and (column.values is None or column.arguments(record)):
            command = app_command.COMMANDS.get(column.command)
            button = _ToolButton(column.text(record) or command.label)
            button.setEnabled(column.is_enabled(record) and command.poll(self.context))
            values = column.arguments(record)
            button.clicked.connect(lambda: self.invoke(command, values))
            return button
        return None

    def _row_clicked(self, spec, item):
        kind, index = item.data(Qt.UserRole)
        if kind == "group":
            return
        setattr(spec["state"], spec["index_key"], index)
        if not spec["on_click"]:
            # Selecting IS the change: what the selection gates was decided when
            # the panel was described, so a list with no click command still has
            # to ask for the repaint every other write asks for (see _write).
            self.invoke(None, {})
            return
        command = app_command.COMMANDS.get(spec["on_click"])
        self.invoke(command, dict({spec["click_argument"]: index},
                                  **_modifiers(command)))

    def _row_activated(self, spec, item):
        kind, index = item.data(Qt.UserRole)
        if kind != "group" or not spec["group_command"]:
            return
        record = getattr(spec["state"], spec["collection"])[index]
        values = spec["group_values"]
        values = {} if values is None else dict(values(record))
        if not values:
            return
        self.invoke(app_command.COMMANDS.get(spec["group_command"]), values)


#: Popover bodies, by the id a description names. Registered where the popover's
#: content is declared, so the renderer never learns what is in one.
_POPOVERS = {}
#: Menu entries, by the id a description names -- a callable (context) -> rows of
#: {"text", "command", "values"}.
_MENUS = {}


def _modifiers(command):
    """The two selection modifiers, off the keyboard state at the moment of the
    click. Qt reports them per event; this is the same reading Blender's invoke()
    takes from its own event, under the kernel's names for them."""
    if not command.modifiers:
        return {}
    held = QtWidgets.QApplication.keyboardModifiers()
    return {app_command.EXTEND: bool(held & Qt.ControlModifier),
            app_command.RANGE: bool(held & Qt.ShiftModifier)}


def register_popover(panel_id, describe):
    _POPOVERS[panel_id] = describe


def register_menu(menu_id, entries):
    _MENUS[menu_id] = entries


def _menu_entries(menu_id, context):
    build = _MENUS.get(menu_id)
    return list(build(context)) if build is not None else []
