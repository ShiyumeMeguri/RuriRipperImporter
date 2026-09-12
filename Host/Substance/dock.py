"""The RuriRipper dock: a renderer for the kernel's panel descriptions.

There is no layout in this file. What the panel says -- the install tab bar, the
identity line, the decoder, the source-option form, the cabmap fields, the
browser with its breadcrumbs and sort row and rule filter, the import options and
actions, and every game tab this host can offer -- is described once in the
kernel and rendered here exactly as Blender renders it. The two panels are the
same panel.

What IS here is Painter's half of that: a dock widget, a rebuild when the
description changes (Qt is retained-mode and the description is not), and the
driver for a command that says its work as steps.

THE STEP CONTRACT IS THE SAME ONE BLENDER DRIVES. A ``Read`` is what may leave
the main thread -- it is bridge traffic and plain Python by construction -- and
the generator ITSELF is advanced on the main thread, because everything between
reads is where a host API is touched. Getting that backwards would mean the two
hosts walk the same generator under different rules, which is exactly the
divergence one description exists to prevent.
"""

from __future__ import annotations

import traceback

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from ...Kernel.app import browser, command as app_command
from ...Kernel.app import layout as app_layout
from ...Kernel.app import filtering
from . import render


class _Read(QtCore.QRunnable):
    """Runs ONE cross-boundary read off the main thread and hands the result back.

    One read at a time, never the whole generator: the bridge is a single session
    and the code between reads builds things, so both have to stay where they
    belong.

    A runnable on Qt's own pool rather than a QThread this file creates: then the
    thread belongs to Qt for its whole life, and no driver of ours can be holding
    the last reference to one that is still running -- which Qt ends the PROCESS
    over, not an exception."""

    def __init__(self, fn, report):
        super().__init__()
        self._fn = fn
        self._report = report

    def run(self):
        try:
            self._report(self._fn(), None)
        except Exception:
            self._report(None, traceback.format_exc())


class _Steps(QtCore.QObject):
    """Drives one command's step generator: reads on a worker, everything else
    here, and the panel alive throughout.

    A QObject, and built on the main thread, because THAT is the only thing that
    decides where a queued connection is delivered. As a plain Python object this
    had no thread of its own, so Qt ran the completion slot straight from the
    worker: the driver then waited on its own thread and destroyed it while it was
    still running, and Painter did not raise -- it died, on the first Load CAB.
    Measured rather than argued (the plain shape reports "resume IS the worker
    thread: True"; this one reports "resume on main thread: True")."""

    read_done = QtCore.Signal(object, object)

    def __init__(self, dock, one, steps, pool):
        super().__init__()
        self.dock = dock
        self.command = one
        self.steps = steps
        self.pool = pool
        self.read_done.connect(self.resume)

    def start(self):
        self.advance(None)

    def advance(self, sent):
        try:
            step = self.steps.send(sent)
        except StopIteration as stop:
            self.finish(stop.value)
            return
        except Exception:
            self.fail(traceback.format_exc())
            return
        if step.progress is not None:
            self.dock.set_progress(self.command.label, step.progress)
        if isinstance(step, app_command.Read):
            self.offload(step.fn)
            return
        # A Mark is a checkpoint: let the panel repaint, then carry on.
        self.dock.rebuild()
        QtCore.QTimer.singleShot(0, lambda: self.advance(None))

    def offload(self, fn):
        self.pool.start(_Read(fn, self.read_done.emit))

    @QtCore.Slot(object, object)
    def resume(self, result, error):
        if error is not None:
            self.fail(error)
            return
        self.advance(result)

    def finish(self, result):
        try:
            self.command.settle(None, result)
        except Exception:
            self.fail(traceback.format_exc())
            return
        self.dock.set_progress("", None)
        self.dock.finished(self)

    def fail(self, detail):
        self.dock.set_progress("", None)
        self.dock.report_error(self.command.failure, detail)
        self.dock.finished(self)


class RuriRipperDock(QtWidgets.QWidget):
    """One dock, rebuilt from the description whenever it changes."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RuriRipper")
        # Painter keys the dock's saved position/geometry off objectName, and turns
        # windowIcon into the side-strip quick-access button -- without one the dock
        # has no way back once it is closed.
        self.setObjectName("RuriRipperImporter")
        self.setWindowIcon(_dock_icon())
        self.setMinimumWidth(240)
        self._running = []
        #: A repaint has been asked for and will happen on the next turn. See
        #: rebuild(): never inside the signal that asked.
        self._repaint_queued = False
        # One read at a time, by construction: the bridge IS a single session, so a
        # pool that could run two would be describing something this host does not
        # have. Parented to the dock, so its own destructor is what waits for a read
        # still in flight when the panel goes away.
        self._pool = QtCore.QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._renderer = render.Renderer(self, None, self._invoke, self.report_error)

        # The scroll area fills the dock edge to edge on purpose. Painter themes
        # through an application stylesheet, not a palette, so a bare widget in a
        # dock paints nothing of its own and the dock's blue accent shows through --
        # which is what a margin here, or a status label outside this scroll area,
        # used to put on screen. A QScrollArea is a class that stylesheet dresses,
        # so everything inside it comes out in Painter's own panel colour.
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        # Every control in the panel can be drawn narrower than its caption, so
        # the description fits whatever width the dock has and this bar stays
        # away. It is AsNeeded rather than AlwaysOff because a panel that somehow
        # still does not fit has to SAY so: hidden, the area still scrolls (Qt
        # scrolls it itself to reveal whatever takes focus), which is how the
        # dock ended up showing the middle of its own panel with nothing on
        # screen to scroll it back.
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        outer.addWidget(self._scroll, 1)
        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)

        self._apply_style()
        self.rebuild()

    # -- rendering ---------------------------------------------------------
    def rebuild(self):
        """Ask for a repaint, on the NEXT turn of the event loop.

        Never inside the signal that asked for it. A rebuild replaces the whole
        widget tree, and the widget whose click asked for the repaint is in that
        tree -- Qt carries on touching it after the slot returns (released(),
        repaint, click state), so deleting it there is a use-after-free. Queued
        and coalesced, which also collapses the two requests one click makes: the
        command body redraws through the port, and the invoker redraws again when
        the command returns."""
        if self._repaint_queued:
            return
        self._repaint_queued = True
        QtCore.QTimer.singleShot(0, self._rebuild_now)

    def _rebuild_now(self):
        """Re-describe the panel and rebuild the widgets under it.

        A whole rebuild rather than a diff: the description is small, Qt builds it
        in microseconds, and a diff would be a second model of what is on screen --
        which is the thing this design exists to avoid."""
        self._repaint_queued = False
        body = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(body)
        column.setContentsMargins(6, 6, 6, 6)
        column.setSpacing(4)
        try:
            self._renderer.draw(app_layout.describe(browser.draw, None), column)
        except Exception:
            self.report_error("drawing the panel", traceback.format_exc())
        column.addStretch(1)
        column.addWidget(self._status)
        self._scroll.setWidget(body)

    def set_status(self, message):
        self._status.setText(message or "")

    def set_progress(self, label, factor):
        """The one line a command in flight shows. The bar itself is part of the
        description (the panel draws its own state's loading fields); this is the
        status line under the dock, which Blender's status bar answers to."""
        if factor is None:
            self.set_status("")
            return
        self.set_status("{0}... {1:.0f}%".format(label, factor * 100.0))

    # -- commands ----------------------------------------------------------
    def _invoke(self, one, arguments):
        """Run a command, then repaint. ``None`` means a property was written --
        the state already changed and only the screen is behind."""
        if one is None:
            self.rebuild()
            return
        try:
            result = one.run(None, dict(arguments))
        except Exception:
            self.report_error(one.label, traceback.format_exc())
            self.rebuild()
            return
        if one.steps and result is not None and hasattr(result, "send"):
            task = _Steps(self, one, result, self._pool)
            self._running.append(task)
            task.start()
            return
        self.rebuild()

    def finished(self, task):
        if task in self._running:
            self._running.remove(task)
        self.rebuild()

    def report_error(self, label, detail):
        self.set_status("{0} failed -- see the log.".format(label))
        import substance_painter.logging
        substance_painter.logging.error("[RuriRipper] {0}\n{1}".format(label, detail))

    # -- chrome ------------------------------------------------------------
    def _apply_style(self):
        """Flatten the leftover Qt-default chrome: sunken frames, bevelled headers,
        grid lines.

        Not one colour is written here, and that is the fix rather than an
        omission. Painter themes through an application stylesheet; the palette a
        widget carries in one of its docks is still Qt's default light one, so
        reading colours out of it -- which this used to do -- painted the table
        headers light grey with black text in the middle of a dark panel. Left
        alone, the host's own stylesheet dresses these classes."""
        self.setStyleSheet(
            "QToolButton { border: none; padding: 3px 2px; }"
            "QHeaderView::section { border: none; padding: 3px 4px; }"
            "QTableWidget { border: none; }"
            "QTableWidget::item { padding: 1px 4px; }"
            "QScrollArea { border: none; }")


def _dock_icon():
    """The dock's window icon, which Painter turns into the quick-access button in
    the right-hand side strip. Drawn here rather than shipped as a bitmap so it
    stays crisp at every strip size and needs no binary asset in the package."""
    icon = QtGui.QIcon()
    for size in (16, 20, 24, 32, 48, 64):
        pixmap = QtGui.QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        inset = max(1.0, size * 0.09)
        stroke = max(1.0, size / 14.0)
        # Monochrome outline + glyph, matching Painter's own strip icons.
        pen = QtGui.QPen(QtGui.QColor(226, 226, 226))
        pen.setWidthF(stroke)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        radius = size * 0.22
        painter.drawRoundedRect(
            QtCore.QRectF(inset, inset, size - 2 * inset, size - 2 * inset), radius, radius)
        font = painter.font()
        font.setPixelSize(max(7, int(size * 0.56)))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QtCore.QRectF(0, 0, size, size), Qt.AlignCenter, "R")
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def register_surfaces():
    """The popovers and menus the descriptions name. Blender registers these as
    real Panel/Menu classes; Qt opens the same bodies as a dialog and a popup."""
    render.register_popover(filtering.RULES_PANEL, filtering.draw_rules)
    render.register_popover(browser.COLUMN_WIDTHS_PANEL, browser.draw_column_widths)
    render.register_menu(browser.DECODER_MENU, _decoder_entries)
    render.register_menu(filtering.QUICK_FILTER_MENU, _quick_filter_entries)


def _decoder_entries(context):
    """The decoders THIS tab's install may be read through: every version its own
    product ships, plus the family one, plus none at all -- the same list Blender's
    decoder menu draws, off the same reader."""
    state = browser.state_of(context)
    config = browser._active_config(state)
    product = config.game_name if config is not None else ""
    family = config.engine_family if config is not None else ""
    found = list(browser._decoders_of(product))
    if family and family.lower() != product.lower():
        found = found + list(browser._decoders_of(family))
    entries = []
    for entry in found:
        label = "{0} {1}".format(entry[0], entry[1])
        if entry[2]:
            label = "{0}  ·  {1}".format(label, entry[2])
        entries.append({"text": label, "command": browser.SET_DECODER.id,
                        "values": {"decoder_id": browser._decoder_id(entry)}})
    entries.append({"text": "None (plain Unity build)",
                    "command": browser.SET_DECODER.id, "values": {"decoder_id": ""}})
    return entries


def _quick_filter_entries(context):
    """Include/Exclude x every field, for the row the list on screen has
    selected -- whichever list that is."""
    return filtering.quick_filter_menu_entries(context)
