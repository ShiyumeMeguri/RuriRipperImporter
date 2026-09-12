"""ONE cast browser, for every game that ships a cast.

Every game files its cast differently and every game answers the same four
questions about a row: what is it called, what is the game's own id for it, which
of the game's own kinds is it, and what does loading it need. So the BROWSER is one
thing -- a kind switch, a search line with the shared rule editor behind it, a list,
a status line -- and what differs per game is stated as data: which columns those
four answers live in, and what the buttons under the list do.

A game module declares a :class:`Cast`, fills it with a column table its own decoder
produced, and draws whatever belongs under the list itself. Nothing here knows a
game: the kinds on the switch are the ones the ROWS carry, in the words the build
used, so a build that invents a kind shows it with no edit and a build with only one
kind draws no switch at all.

Searching is the same engine every other list here uses: the text and the rules go
to C# over the buffers the table was built from, and this side receives row ids and
reads cells. That is why a cast of eighteen thousand filters as fast as one of fifty.

Nothing here imports a host.
"""

from __future__ import annotations

from . import filtering
from . import layout as app_layout
from . import state as app_state
from .state import Field, Schema
from ...RuriRipperPyBridge.session import cabmap_state

#: The switch entry that means "do not narrow by kind". A game whose rows carry no
#: kind column never draws the switch, so the value is only ever seen by one that
#: does.
EVERY = "*"

CAST_ENTRY = Schema("CastEntry", """One drawn line: either a group header or a cast
member.""", (
    Field("label", app_state.STRING, ""),
    Field("key", app_state.STRING, ""),
    Field("group", app_state.STRING, ""),
    Field("detail", app_state.STRING, ""),
    Field("payload", app_state.STRING, ""),
    Field("shipped", app_state.BOOL, True),
    Field("is_group", app_state.BOOL, False),
    Field("row_index", app_state.INT, -1),
))


def _columns(table, name):
    """The values behind one stated column name, or a tuple of them for a name a
    game states as a fallback chain (the first of several columns that has a value
    is what a row is CALLED, which is how a build that names only some of its rows
    still reads as a list of names)."""
    if not name or table is None:
        return ()
    names = name if isinstance(name, tuple) else (name,)
    return tuple(table.values(one) for one in names if one in table.names)


def _pick(columns, index):
    for values in columns:
        if values[index]:
            return values[index]
    return ""


class Cast:
    """Which columns of one game's cast table answer the browser's four questions.

    A column named here that the table does not carry is simply absent -- the
    browser draws one fewer thing rather than failing -- so a game states only what
    it has, and a decoder that grows a column lights it up with no edit here.
    """

    def __init__(self, key, label="label", identifier="key", detail="detail",
                 group="", named="", shipped="", kind="", payload="", cap=None,
                 labels=None):
        self.key = key
        #: What to call a column in the rule editor where its own name is not the
        #: word a person uses for it. Anything absent reads as its own name.
        self.labels = labels or {}
        self.label = label
        self.identifier = identifier
        self.detail = detail
        self.group = group
        self.named = named
        self.shipped = shipped
        self.kind = kind
        self.payload = payload
        self.cap = cap or cabmap_state.CAST_CAP

    def column(self, table, name):
        """The values of one of this cast's columns, or None when the table has none."""
        return table.values(name) if name and not isinstance(name, tuple) \
            and name in table.names else None

    @property
    def first_label(self):
        return self.label[0] if isinstance(self.label, tuple) else self.label

    def fields(self, table):
        """The rule editor's vocabulary: the columns the LOADED table actually has,
        the displayed name first because a new rule starts on the first field."""
        leading = self.first_label
        if table is None:
            return ((leading, self.labels.get(leading, "Name")),)
        names = sorted(table.names, key=lambda name: 0 if name == leading else 1)
        return tuple((name, self.labels.get(name, name.replace("_", " ").title()))
                     for name in names)

    def kinds(self, table):
        """Every kind the rows carry, most populous first, with how many carry it."""
        values = self.column(table, self.kind)
        if values is None:
            return []
        counted = {}
        for value in values:
            counted[value] = counted.get(value, 0) + 1
        return sorted(counted.items(), key=lambda pair: (-pair[1], pair[0]))

    def kind_items(self, table):
        """The switch entries: every kind, plus one that narrows by none."""
        counted = self.kinds(table)
        if len(counted) < 2:
            return [(EVERY, "All", "Everything this install ships")]
        return [(EVERY, "All", "{0} row(s)".format(sum(count for _kind, count in counted)))] + [
            (kind or EVERY, kind or "Unfiled", "{0} row(s)".format(count))
            for kind, count in counted]


def fill(state, cast, table, note="", shipped_only=True, matched=None):
    """Rebuild the drawn line list from one cast table.

    The selection is the cast member, not the row number: refilling this list (a
    keystroke, a rule edit, a kind switch) must not hand the buttons below a
    different one.

    ``shipped_only`` drops the rows the game ships nothing for, which is the honest
    default -- offering a button that cannot do anything is a lie -- and is the one
    thing a panel overrides, because a game whose catalog names more than the
    install downloaded lets a person SEE what they are missing.
    """
    chosen = filtering.selected_key(state)
    state.entries.clear()
    if table is None:
        state.status = "Refresh to read the cast out of this install's own tables."
        return
    if matched is None:
        matched = cabmap_state.BRIDGE.search_data_table(table, state.search.strip(),
                                                        state.filter_rules)
    labels = _columns(table, cast.label)
    identifiers = cast.column(table, cast.identifier)
    details = cast.column(table, cast.detail)
    groups = cast.column(table, cast.group)
    payloads = cast.column(table, cast.payload)
    kinds = cast.column(table, cast.kind)
    # A row the game ships nothing for gets no button, so it is not drawn --
    # offering one would be a lie. Which rows those are is the cast's own column;
    # a cast whose every row is loadable has no such column.
    shipped = cast.column(table, cast.shipped)
    # The ones the game actually NAMES come first: a label falls back to the row's
    # own id when the game names it nothing, and ids are ASCII while names are not,
    # so plain alphabetical order buries every named row under the unnamed ones.
    named = cast.column(table, cast.named)
    wanted = (state.kind or EVERY) if kinds is not None else EVERY

    def keep(index):
        if shipped_only and shipped is not None and not shipped[index]:
            return False
        return wanted == EVERY or (kinds[index] or EVERY) == wanted

    order = sorted((int(index) for index in matched if keep(int(index))),
                   key=lambda index: (0 if named is not None and named[index] else 1,
                                      groups[index] if groups is not None else "",
                                      _pick(labels, index)))
    drawn = order[:cast.cap]
    counts = {}
    if groups is not None:
        for index in drawn:
            counts[groups[index]] = counts.get(groups[index], 0) + 1

    current = None
    for position, index in enumerate(drawn):
        group = groups[index] if groups is not None else ""
        if group and group != current:
            current = group
            header = state.entries.add()
            header.label = "{0}  ({1})".format(current, counts[current])
            header.group = current
            header.is_group = True
        entry = state.entries.add()
        entry.label = _pick(labels, index)
        entry.key = str(identifiers[index]) if identifiers is not None else ""
        entry.group = group
        entry.detail = str(details[index]) if details is not None else ""
        entry.payload = payloads[index] if payloads is not None else ""
        entry.shipped = bool(shipped[index]) if shipped is not None else True
        entry.row_index = position
    state.status = "{0} of {1} row(s){2}{3}".format(
        len(order), table.row_count,
        " · " + note if note else "",
        "" if len(order) == len(drawn) else
        " · showing {0}, narrow your search to see the rest".format(len(drawn)))
    filtering.restore_selection(state, chosen)


def selected(state):
    """The picked cast member, or None when the pick is a group header or nothing."""
    if 0 <= state.active_index < len(state.entries):
        entry = state.entries[state.active_index]
        if not entry.is_group:
            return entry
    return None


def draw_head(layout, state, cast, table, refresh_id):
    """The one line every cast browser opens with: which kind, and re-read.

    The kind is a MENU, not a row of buttons: a build that files its cast under a
    dozen kinds would otherwise push the list itself off the panel. It is drawn only
    where there is a choice to make.
    """
    if len(cast.kinds(table)) > 1:
        layout.prop(state, "kind", text="")
    filtering.draw_search_row(layout, state, extra_operator=(refresh_id, "FILE_REFRESH"))


def draw_list(layout, state, columns, identifier, rows=10, group_column=None):
    """The list and what it came to, drawn the same way for every game."""
    layout.list(state, "entries", "active_index", columns, rows=rows,
                identifier=identifier, group_key="is_group",
                group_column=group_column or app_layout.ListColumn("label"))
    layout.label(text=state.status, icon="INFO")
