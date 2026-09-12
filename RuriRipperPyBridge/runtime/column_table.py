"""Generic columnar table -- the python half of Ruri.Data's ColumnTable.

The C# side hands over the columns exactly as it built them: a UTF-8 blob plus
an offsets array for text, a native array for the scalars. Nothing per row is
materialized here either, so a 138k-row localization table and a 31-row roster
cost the same per-row price: zero until someone asks for a cell.

Carries no notion of what a column means. Which table, which columns and which
joins to ask for is the caller's declaration -- see the game modules.
"""

from __future__ import annotations

import numpy as np


class ColumnTable:
    """One projected table: ``columns`` in the order C# emitted them, column 0
    always being the row's own key."""

    __slots__ = ("handle", "name", "row_count", "names", "_kinds", "_blobs", "_offsets", "_text_cache")

    def __init__(self, name, row_count, names, kinds, blobs, offsets, handle=""):
        self.handle = handle
        self.name = name
        self.row_count = row_count
        self.names = names
        self._kinds = kinds
        self._blobs = blobs
        self._offsets = offsets
        self._text_cache = {}

    @classmethod
    def from_packed(cls, dto):
        names = [str(n) for n in dto.Names]
        kinds = [str(k) for k in dto.Kinds]
        blobs = []
        offsets = []
        for index, kind in enumerate(kinds):
            raw = bytes(dto.Blobs[index])
            if kind == "text":
                # Strict on purpose: a lenient decode would insert replacement
                # characters and silently shift every offset after them, which
                # shows up as wrong cell contents rather than as an error.
                blobs.append(raw.decode("utf-8"))
                offsets.append(_utf16_offsets(raw, np.frombuffer(bytes(dto.Offsets[index]), dtype=np.int32)))
            elif kind == "blob":
                # Payload bytes stay bytes: a blob column carries geometry, curve
                # or pose payloads whose shape only the reader of that column knows.
                blobs.append(raw)
                offsets.append(np.frombuffer(bytes(dto.Offsets[index]), dtype=np.int32).astype(np.int64))
            elif kind == "int":
                blobs.append(np.frombuffer(raw, dtype=np.int64))
                offsets.append(None)
            elif kind == "real":
                blobs.append(np.frombuffer(raw, dtype=np.float64))
                offsets.append(None)
            else:
                raise ValueError(f"unknown column kind {kind!r} for column {names[index]!r}")
        return cls(str(dto.Name), int(dto.RowCount), names, kinds, blobs, offsets, str(dto.Handle))

    def index_of(self, column):
        try:
            return self.names.index(column)
        except ValueError:
            raise KeyError(f"table {self.name!r} has no column {column!r}; has {self.names}") from None

    def kind(self, column):
        return self._kinds[self.index_of(column)]

    def cell(self, row, column):
        index = self.index_of(column)
        if self._kinds[index] in ("text", "blob"):
            start, end = self._offsets[index][row], self._offsets[index][row + 1]
            return self._blobs[index][start:end]
        return self._blobs[index][row].item()

    def values(self, column):
        """Whole column. Text comes back as a python list (built once, cached);
        scalars stay a numpy view over the original buffer."""
        index = self.index_of(column)
        if self._kinds[index] not in ("text", "blob"):
            return self._blobs[index]
        cached = self._text_cache.get(index)
        if cached is None:
            blob, offsets = self._blobs[index], self._offsets[index]
            cached = [blob[offsets[i]:offsets[i + 1]] for i in range(self.row_count)]
            self._text_cache[index] = cached
        return cached

    def row(self, index):
        return {name: self.cell(index, name) for name in self.names}

    def __len__(self):
        return self.row_count


def _utf16_offsets(blob, byte_offsets):
    """C# counts a text column's offsets in UTF-8 bytes; python slices the
    decoded str by code points. Convert once, so slicing a cell is O(1) and
    never re-decodes."""
    if len(byte_offsets) == 0:
        return np.zeros(1, dtype=np.int64)
    # Bytes that start a UTF-8 code point; a prefix count of those is exactly
    # the code-point index of that byte offset.
    starts = (np.frombuffer(blob, dtype=np.uint8) & 0xC0) != 0x80 if len(blob) else np.zeros(0, dtype=bool)
    prefix = np.zeros(len(blob) + 1, dtype=np.int64)
    if len(blob):
        np.cumsum(starts, out=prefix[1:])
    return prefix[byte_offsets.astype(np.int64)]
