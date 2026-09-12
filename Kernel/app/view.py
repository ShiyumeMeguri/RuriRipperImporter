"""ONE drawn list, for every list in every game in every host.

A list on screen is a VIEW of a table: this search text, these Include/Exclude
rules, this facet, sorted this way, grouped into sections, cut to what a host can
actually draw. Every one of those words is a QUESTION ABOUT DATA, and all of them
are answered on the C# side, over the very buffers the table was built from --
one vectorized sweep, the shared rule evaluator, an allocation-free sort, and the
status line already worded. What crosses back is the ANSWER: the lines to draw,
in the order to draw them, with their section headers already in place.

This side reads cells out of that answer. It does not filter, sort, join, count,
group, truncate or format anything -- and it cannot, because a View hands out no
column, only cells. That is the whole reason a cast of eighteen thousand switches
as fast as one of fifty: the eighteen thousand never come over.

WHICH COLUMN ANSWERS WHAT is not stated here either. A column carries its own
role (name / id / detail / section / facet) declared in C# where the table is
built, so a decoder that grows a column lights it up with no edit on this side,
and no game module repeats what its own tables already say.

Nothing here imports a host.
"""

from __future__ import annotations

from . import filtering
from . import layout as app_layout
from . import state as app_state
from .state import Field, Schema
from ...RuriRipperPyBridge.runtime import column_table
from ...RuriRipperPyBridge.session import cabmap_state

#: The facet entry that means "do not narrow". Mirrors View.EveryFacet.
EVERY = "*"

#: What a column ANSWERS, for a list this side publishes. Re-exported so a panel
#: states its columns without reaching past the kernel into the bridge.
LABEL = column_table.LABEL
KEY = column_table.KEY
DETAIL = column_table.DETAIL
GROUP = column_table.GROUP
FACET = column_table.FACET
NAMED = column_table.NAMED
SHIPPED = column_table.SHIPPED
PAYLOAD = column_table.PAYLOAD

#: How many lines a host will materialize for one list. The ONE budget, because
#: there is one list. It is not a limit on what can be FOUND -- the view always
#: reports how many matched and says so on the status line -- it is how many host
#: list items are worth building for a person who reads the first screenful and
#: narrows. Blender needs a real collection element per line to give the list its
#: native scrollbar and keyboard navigation, and that element is what costs.
WINDOW = 4000


class View:
    """The composed answer for one list. Cells only; no columns, no computation."""

    __slots__ = ("_native", "_rows", "_facets", "_label", "_key", "_detail", "_group",
                 "payload_column")

    def __init__(self, native):
        self._native = native
        self._rows = column_table.ColumnTable.from_pinned(native.PackedRows)
        self._facets = column_table.ColumnTable.from_pinned(native.PackedFacets)
        self._label = self._rows.first_named(column_table.LABEL)
        self._key = self._rows.first_named(column_table.KEY)
        self._detail = self._rows.first_named(column_table.DETAIL)
        #: What loading this row needs -- the column the decoder marked as the
        #: payload, whatever that build happens to call it.
        self.payload_column = self._rows.first_named(column_table.PAYLOAD)
        #: Whether the install has anything behind a row. A view that was asked
        #: to show unshipped rows anyway still knows which they are, so they can
        #: be drawn as what they are rather than silently dropped.
        self.shipped_column = self._rows.first_named(column_table.SHIPPED)
        self._group = "is_group"

    @classmethod
    def open(cls, table, facet="", search="", rules=None, note="", shipped_only=True,
             sort_column="", sort_direction=0, window=WINDOW):
        return cls(cabmap_state.BRIDGE.open_view(
            table, facet=facet, query=search, rules=rules, note=note,
            shipped_only=shipped_only, sort_column=sort_column,
            sort_direction=sort_direction, window=window))

    def close(self):
        native, self._native = self._native, None
        self._rows.close()
        self._facets.close()
        if native is not None:
            native.Dispose()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # -- what one drawn line says ------------------------------------------
    def __len__(self):
        return self._rows.row_count

    def text(self, row, column=""):
        return self._rows.cell(row, column or self._label)

    def label(self, row):
        return self._rows.cell(row, self._label)

    def key(self, row):
        return self._rows.cell(row, self._key) if self._key else ""

    def detail(self, row):
        return self._rows.cell(row, self._detail) if self._detail else ""

    def is_group(self, row):
        return bool(self._rows.cell(row, self._group))

    def shipped(self, row):
        if not self.shipped_column:
            return True
        stated = self._rows.cell(row, self.shipped_column)
        return bool(stated) and stated != "0"

    def has(self, column):
        return column in self._rows.names

    # -- what the list as a whole says -------------------------------------
    @property
    def summary(self):
        return str(self._native.Summary)

    @property
    def matched(self):
        return int(self._native.Matched)

    def index_of_key(self, key):
        """Where the row carrying ``key`` is drawn now, or -1 when this view does
        not show it. A selection's identity is its key, never its position: the
        row at position 7 before a keystroke is a different thing after one."""
        return int(self._native.IndexOfKey(str(key or "")))

    def facet_items(self):
        """The facet switch's entries, worded on the other side. Empty when this
        table has nothing to narrow by -- a list with one kind draws no switch."""
        return [(self._facets.cell(row, "id"),
                 self._facets.cell(row, "label"),
                 self._facets.cell(row, "detail"))
                for row in range(self._facets.row_count)]

    def fields(self):
        """The rule editor's vocabulary: the columns this view actually carries,
        under the names the decoder gave them, the displayed name first because a
        new rule starts on the first field."""
        titled = [(name, title) for name, title in zip(self._rows.names, self._rows.titles)
                  if name != self._group]
        titled.sort(key=lambda pair: 0 if pair[0] == self._label else 1)
        return tuple(titled)


#: One drawn line's SEAT in the host's list. It carries no content -- the view
#: does -- only which line of the view this seat shows, which never changes: seat
#: i is always line i. So the seats are a POOL that grows to the largest list the
#: session has drawn and is never rewritten again; a keystroke changes how many
#: seats are a line, not what any seat says.
VIEW_ROW = Schema("ViewRow", """One seat in a host list bound to a view.""", (
    Field("row", app_state.INT, -1),
))


class Bound:
    """One panel's live view, and the seats that draw it.

    A panel holds one of these instead of a list of records. Opening a view
    replaces the old one (and releases its pinned buffers); the seats stay.
    """

    __slots__ = ("key", "view", "table")

    def __init__(self, key):
        self.key = key
        self.view = None
        self.table = None

    def close(self):
        view, self.view = self.view, None
        if view is not None:
            view.close()

    def open(self, table, state, standing=(), **query):
        """Re-ask the C# side for this list, then seat it.

        Every word of the question -- the search text, the rules, the facet --
        goes over as it was typed; nothing is evaluated here.

        ``standing`` are the panel's OWN constraints, in the same rule vocabulary
        the user's are in: "this half of the tab is the streaming maps", "these
        are the places of the map on screen". They ride alongside the user's rules
        through the one evaluator rather than being a second kind of narrowing
        with its own meaning of ``is``."""
        self.table = table
        chosen = self.selected_key(state)
        self.close()
        if table is None:
            return None
        self.view = View.open(table, facet=_facet(state), search=getattr(state, "search", ""),
                              rules=list(getattr(state, "filter_rules", ())) + list(standing),
                              **query)
        self.seat(state, chosen)
        return self.view

    def publish(self, columns, rows, roles, state, **query):
        """Hand the kernel a list THIS side found, then draw it as a view.

        Some lists have no table behind them because nothing read them out of a
        table: a display stage is discovered by parsing the assets the game ships,
        a story unit by walking what a cutscene references. Those rows still get
        the one search, the one rule evaluator, the one sort and the one section
        pass -- they are published as a table first, rather than growing a second,
        worse list implementation on this side.

        ``columns`` are spelled as anywhere else ("name", "count#",
        "name|Displayed Name"); ``roles`` says positionally what each answers."""
        cabmap_state.BRIDGE.open_host_table(self.key, columns, rows, roles)
        return self.open(self.key, state, **query)

    def seat(self, state, chosen=""):
        """Grow the seat pool to cover the view and put the cursor back on the
        row carrying ``chosen``. The cursor is an IDENTITY: the row at position
        seven before a keystroke is a different thing after one."""
        seats = state.rows
        while len(seats) < len(self.view):
            made = seats.add()
            made.row = len(seats) - 1
        state.active_index = self.view.index_of_key(chosen)

    def selected_key(self, state):
        row = state.active_index
        if self.view is None or not 0 <= row < len(self.view):
            return ""
        return self.view.key(row)

    def selected(self, state):
        """The picked line's number, or -1 when the pick is a header or nothing."""
        row = state.active_index
        if self.view is None or not 0 <= row < len(self.view) or self.view.is_group(row):
            return -1
        return row

    def value(self, state, column=""):
        """One column of the picked line, "" when nothing usable is picked."""
        row = self.selected(state)
        return "" if row < 0 else self.view.text(row, column)

    def payload(self, state):
        """What loading the picked row needs, in the build's own words."""
        return "" if self.view is None else self.value(state, self.view.payload_column)

    def keys(self):
        """Every drawn line's key, headers skipped -- what a command that acts on
        the WHOLE list (import this window, not this row) is handed."""
        if self.view is None:
            return []
        return [self.view.key(row) for row in range(len(self.view))
                if not self.view.is_group(row)]

    def picked(self, state):
        """The picked line as the two things a command ever asks of it -- what the
        game calls it and what the game keys it by -- or None when the pick is a
        section header or nothing. Two cell reads, not a record: there is no copy
        of the row on this side to go stale."""
        row = self.selected(state)
        return None if row < 0 else Picked(self, row)


    # -- what a description reads ------------------------------------------
    def cell(self, seat, column=""):
        return "" if self.view is None else self.view.text(seat.row, column)

    def column(self, name="", **stated):
        return app_layout.ListColumn(key=lambda seat: self.cell(seat, name), **stated)

    def is_group(self, seat):
        return self.view is not None and self.view.is_group(seat.row)

    def shipped(self, seat):
        """Whether this install has anything behind the line in this seat."""
        return self.view is None or self.view.shipped(seat.row)

    @property
    def count(self):
        return 0 if self.view is None else len(self.view)

    @property
    def summary(self):
        return "Refresh to read this list." if self.view is None else self.view.summary

    def facet_items(self):
        """The facet switch's entries, or none when there is nothing to narrow by."""
        return [] if self.view is None else self.view.facet_items()

    def facet_choices(self, _state=None, _context=None):
        """What the host's enum field is allowed to hold. An enum with no entries
        is not a thing a host can draw, so a list with nothing to narrow by still
        offers the one choice it has."""
        return self.facet_items() or [(EVERY, "All", "")]

    def fields(self):
        return (("label", "Name"),) if self.view is None else self.view.fields()


class Picked:
    """The line the user is on. Reads through to the view, so it cannot go stale
    and there is no copy of the row on this side."""

    __slots__ = ("_bound", "row")

    def __init__(self, bound, row):
        self._bound = bound
        self.row = row

    @property
    def key(self):
        return self._bound.view.key(self.row)

    @property
    def label(self):
        return self._bound.view.label(self.row)

    @property
    def shipped(self):
        return self._bound.view.shipped(self.row)

    @property
    def payload(self):
        return self.cell(self._bound.view.payload_column)

    def cell(self, column=""):
        return self._bound.view.text(self.row, column)


def _facet(state):
    return getattr(state, "facet", "") or EVERY


def draw_head(bound, layout, state, refresh_id):
    """The one line every view-backed list opens with: which facet, and re-read.

    The facet is a MENU, not a row of buttons: a build that files its rows under
    a dozen kinds would otherwise push the list itself off the panel. It is drawn
    only where the view says there is a choice to make."""
    if len(bound.facet_items()) > 1:
        layout.prop(state, "facet", text="")
    filtering.draw_search_row(layout, state, extra_operator=(refresh_id, "FILE_REFRESH"))


def draw_list(bound, layout, state, columns, identifier, rows=10, group_column=None):
    """The list and what it came to, drawn the same way for every game."""
    layout.list(state, "rows", "active_index", columns, rows=rows, identifier=identifier,
                group_key=bound.is_group, group_column=group_column or bound.column(),
                visible_count=bound.count)
    layout.label(text=bound.summary, icon="INFO")
