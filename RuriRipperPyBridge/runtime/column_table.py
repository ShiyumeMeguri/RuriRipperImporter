"""Generic columnar table -- the python half of Ruri.Data's ColumnTable.

The C# side does not COPY anything over: it pins the very arrays the table was
built from and hands over their addresses, and this side maps them (numpy over
the pinned address, no copy at all). A column is one shape everywhere -- a run of
bytes plus, for the sliced kinds, a row offsets array -- so there is one reader
here rather than one per kind.

Nothing per row is materialized either, so a 138k-row localization table and a
31-row roster cost the same per-row price: zero until someone asks for a cell.
Decoding happens per CELL, never over the whole blob, which is what lets a view
draw thirty rows out of eighteen thousand without touching the other 18,321.

Carries no notion of what a column MEANS in a panel -- that is the column's own
``role``, stated in C# where the table is built (see Ruri.RipperHook.Tables.
ColumnRole). Which table and which arguments to ask for is the caller's
declaration; see the game modules.

The pinned buffers live until ``close()``. Everything this hands out either
copies (``values``) or is a plain python value (``cell``), so nothing a caller
keeps can outlive the pin and read freed memory.
"""

from __future__ import annotations

import ctypes

import numpy as np

#: Mirrors Ruri.RipperHook.Tables.ColumnRole. What a column ANSWERS, so a panel
#: never has to restate "which column is the name" on this side.
LABEL = 1 << 0
KEY = 1 << 1
DETAIL = 1 << 2
GROUP = 1 << 3
FACET = 1 << 4
NAMED = 1 << 5
SHIPPED = 1 << 6
PAYLOAD = 1 << 7

TEXT = "text"
BLOB = "blob"
INTEGER = "int"
REAL = "real"

_SCALAR = {INTEGER: np.int64, REAL: np.float64}


def _mapped(address, length):
    """A numpy view over C#-pinned memory -- no copy, no ownership."""
    if not address or length <= 0:
        return np.empty(0, dtype=np.uint8)
    return np.frombuffer((ctypes.c_ubyte * length).from_address(address), dtype=np.uint8)


class ColumnTable:
    """One projected table, mapped straight onto the C# side's own buffers."""

    __slots__ = ("handle", "name", "row_count", "names", "titles", "roles",
                 "_pinned", "_kinds", "_data", "_offsets", "_scalars", "_text_cache")

    def __init__(self, pinned):
        self._pinned = pinned
        self.handle = str(pinned.Handle)
        self.name = str(pinned.Name)
        self.row_count = int(pinned.RowCount)
        self.names = [str(name) for name in pinned.Names]
        #: What a person should see each column called, worded on the other side.
        self.titles = [str(title) for title in pinned.Titles]
        self.roles = [int(role) for role in pinned.Roles]
        self._kinds = [str(kind) for kind in pinned.Kinds]
        self._data = []
        self._offsets = []
        self._scalars = []
        self._text_cache = {}
        for index in range(len(self.names)):
            data = _mapped(pinned.Addresses[index * 2], pinned.Lengths[index * 2])
            offsets = _mapped(pinned.Addresses[index * 2 + 1], pinned.Lengths[index * 2 + 1])
            self._data.append(data)
            self._offsets.append(offsets.view(np.int32))
            scalar = _SCALAR.get(self._kinds[index])
            self._scalars.append(None if scalar is None else data.view(scalar))

    @classmethod
    def from_pinned(cls, pinned):
        return cls(pinned)

    def close(self):
        """Release the C# pins. A closed table answers nothing -- every reader
        goes through the mapped arrays, which are dropped here first, so a use
        after close is an ordinary python error rather than freed memory."""
        pinned, self._pinned = self._pinned, None
        self._data = []
        self._offsets = []
        self._scalars = []
        self._text_cache = {}
        if pinned is not None:
            pinned.Dispose()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def index_of(self, column):
        try:
            return self.names.index(column)
        except ValueError:
            raise KeyError(f"table {self.name!r} has no column {column!r}; has {self.names}") from None

    def kind(self, column):
        return self._kinds[self.index_of(column)]

    def role(self, column):
        return self.roles[self.index_of(column)]

    def named(self, role):
        """Every column carrying ``role``, in table order -- which is the order a
        fallback chain is stated in."""
        return [name for name, carried in zip(self.names, self.roles) if carried & role == role]

    def first_named(self, role):
        carrying = self.named(role)
        return carrying[0] if carrying else ""

    def cell(self, row, column):
        index = self.index_of(column)
        return self._at(index, row)

    def _at(self, index, row):
        scalars = self._scalars[index]
        if scalars is not None:
            return scalars[row].item()
        offsets = self._offsets[index]
        raw = self._data[index][offsets[row]:offsets[row + 1]].tobytes()
        return raw if self._kinds[index] == BLOB else raw.decode("utf-8")

    def values(self, column):
        """Whole column, as values this side OWNS -- text as a python list (built
        once, cached), scalars as a numpy copy. A copy because the caller may
        outlive the pinned buffers; a panel never asks for this at all (it draws
        a view, which reads cells)."""
        index = self.index_of(column)
        scalars = self._scalars[index]
        if scalars is not None:
            return scalars.copy()
        cached = self._text_cache.get(index)
        if cached is None:
            cached = [self._at(index, row) for row in range(self.row_count)]
            self._text_cache[index] = cached
        return cached

    def row(self, index):
        return {name: self._at(column, index) for column, name in enumerate(self.names)}

    def __len__(self):
        return self.row_count
