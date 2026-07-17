"""Columnar cabmap row store -- the python half of the zero-materialization
pipeline (C# side: CabTable/RCM4 + RipperBlenderBridge.EnumerateTablePacked).

The classic shape (237k python dicts x 6 keys) cost ~0.4s to build and ~0.2s
per search keystroke. Here the table IS the raw column buffers: one decoded
big string + one numpy offsets array per string column, numpy arrays for the
numeric ones. Nothing per-row exists until someone actually looks at a row:

- display strings (leaf name, joined container list, type names) derive on
  demand -- the browser window only ever shows ~500 rows;
- quick-search runs str.find over the big strings (C speed) and maps hit
  positions back to row ids with numpy searchsorted;
- ``table[i]`` / iteration yield RowView, a dict-compatible lazy view, so
  every legacy consumer (and test script) written against list-of-dicts rows
  keeps working unchanged.
"""

from __future__ import annotations

import numpy as np

# Mirrors RipperBlenderBridge.MaxContainerJoinChars -- the joined Container
# display string is capped with an explicit "…(+N more names)" tail.
_MAX_CONTAINER_JOIN_CHARS = 16384

_ROW_KEYS = ("cab", "name", "container", "type_names", "source", "deps")


class RowView:
    """Dict-compatible lazy view of one row. Supports the exact access shapes
    the legacy list-of-dicts consumers use: row["cab"], row.get("name"), and
    equality against a plain dict of the same six keys."""

    __slots__ = ("_table", "_index")

    def __init__(self, table, index):
        self._table = table
        self._index = index

    def __getitem__(self, key):
        return self._table.field(self._index, key)

    def get(self, key, default=None):
        if key in _ROW_KEYS:
            return self._table.field(self._index, key)
        return default

    def keys(self):
        return _ROW_KEYS

    def __contains__(self, key):
        return key in _ROW_KEYS

    def __eq__(self, other):
        if isinstance(other, (RowView, dict)):
            return all(self[k] == other[k] for k in _ROW_KEYS)
        return NotImplemented

    def __repr__(self):
        return f"<Row {self._table.cab(self._index)}>"


class _StringColumn:
    """One string column: a single big str + int64 char-offset array. Values
    are slices; searching is str.find over the big string with hits filtered
    to those that do not straddle a value boundary."""

    __slots__ = ("big", "offsets", "_lower")

    def __init__(self, big, offsets):
        self.big = big
        self.offsets = offsets  # (n+1,) int64 char offsets
        self._lower = None

    @classmethod
    def from_blob(cls, blob_bytes, offsets_bytes):
        byte_offsets = np.frombuffer(offsets_bytes, dtype="<i4").astype(np.int64)
        big = blob_bytes.decode("utf-8")
        if len(big) == len(blob_bytes):
            # Pure ASCII (the real-data common case): byte offsets ARE char offsets.
            return cls(big, byte_offsets)
        # Non-ASCII somewhere: rebuild char offsets by decoding per value once.
        parts = [blob_bytes[byte_offsets[i]:byte_offsets[i + 1]].decode("utf-8")
                 for i in range(len(byte_offsets) - 1)]
        char_offsets = np.zeros(len(byte_offsets), dtype=np.int64)
        np.cumsum([len(p) for p in parts], out=char_offsets[1:])
        return cls("".join(parts), char_offsets)

    def __len__(self):
        return len(self.offsets) - 1

    def value(self, index):
        return self.big[self.offsets[index]:self.offsets[index + 1]]

    def lower(self):
        if self._lower is None:
            self._lower = self.big.lower()
        return self._lower

    def find_value_ids(self, needle_lower):
        """Ids of every value containing needle_lower (case-insensitive),
        as a sorted unique numpy array."""
        haystack = self.lower()
        length = len(needle_lower)
        positions = []
        position = haystack.find(needle_lower)
        while position != -1:
            positions.append(position)
            position = haystack.find(needle_lower, position + 1)
        if not positions:
            return np.empty(0, dtype=np.int64)
        starts = np.asarray(positions, dtype=np.int64)
        start_ids = np.searchsorted(self.offsets, starts, side="right") - 1
        end_ids = np.searchsorted(self.offsets, starts + length - 1, side="right") - 1
        within = start_ids == end_ids  # a hit straddling two values is not a match in either
        return np.unique(start_ids[within])


class RowTable:
    """The whole cabmap row set, columnar. len()/iteration/indexing yield
    RowView for drop-in compatibility with the old list-of-dicts ROWS."""

    __slots__ = ("count", "cabs", "sources", "paths", "path_starts",
                 "class_flat", "class_starts", "deps", "class_names",
                 "_asset_bundle_id", "_cab_to_index", "_sort_cache")

    def __init__(self, count, cabs, sources, paths, path_starts,
                 class_flat, class_starts, deps, class_names):
        self.count = count
        self.cabs = cabs                # _StringColumn (count)
        self.sources = sources          # _StringColumn (count)
        self.paths = paths              # _StringColumn (total container paths)
        self.path_starts = path_starts  # (count+1,) int64 -> rows of `paths`
        self.class_flat = class_flat    # (total,) int32
        self.class_starts = class_starts  # (count+1,) int64
        self.deps = deps                # (count,) int32
        self.class_names = class_names  # {class_id: name}
        self._asset_bundle_id = next(
            (i for i, n in class_names.items() if n == "AssetBundle"), 142)
        self._cab_to_index = None
        self._sort_cache = {}

    @classmethod
    def from_dicts(cls, rows):
        """Build a table from plain row dicts -- the constructor test
        harnesses use to fabricate small tables without a bridge session.
        Container/type columns are synthesized from the display fields
        (single path = the container string; one class name per comma
        segment), which round-trips exactly for the simple rows tests use."""
        count = len(rows)
        def column(values):
            offsets = np.zeros(count + 1, dtype=np.int64)
            np.cumsum([len(v) for v in values], out=offsets[1:])
            return _StringColumn("".join(values), offsets)

        cabs = column([r["cab"] for r in rows])
        sources = column([r.get("source", "") for r in rows])
        path_values = [r.get("container", "") for r in rows]
        paths = column(path_values)
        path_starts = np.arange(count + 1, dtype=np.int64)

        class_names = {}
        class_flat = []
        class_starts = np.zeros(count + 1, dtype=np.int64)
        next_id = 1
        for i, r in enumerate(rows):
            names = [n.strip() for n in (r.get("type_names") or "").split(",") if n.strip()]
            for name in names:
                found = next((cid for cid, cname in class_names.items() if cname == name), None)
                if found is None:
                    found = next_id
                    class_names[found] = name
                    next_id += 1
                class_flat.append(found)
            class_starts[i + 1] = len(class_flat)
        deps = np.asarray([int(r.get("deps", 0)) for r in rows], dtype=np.int32)
        return cls(count, cabs, sources, paths, path_starts,
                   np.asarray(class_flat, dtype=np.int32), class_starts, deps, class_names)

    @classmethod
    def from_packed(cls, packed):
        """Build from RipperBlenderBridge.EnumerateTablePacked's DTO -- a few
        buffer copies and two big-string decodes, nothing per-row."""
        count = int(packed.Count)
        cabs = _StringColumn.from_blob(bytes(packed.CabBlob), bytes(packed.CabOffsets))
        sources = _StringColumn.from_blob(bytes(packed.SourceBlob), bytes(packed.SourceOffsets))
        paths = _StringColumn.from_blob(bytes(packed.PathBlob), bytes(packed.PathOffsets))
        path_starts = np.frombuffer(bytes(packed.PathStarts), dtype="<i4").astype(np.int64)
        class_flat = np.frombuffer(bytes(packed.ClassFlat), dtype="<i4")
        class_starts = np.frombuffer(bytes(packed.ClassStarts), dtype="<i4").astype(np.int64)
        deps = np.frombuffer(bytes(packed.DependencyCounts), dtype="<i4")
        class_names = {}
        for line in str(packed.ClassIdNames).splitlines():
            class_id, _, name = line.partition("=")
            if class_id:
                class_names[int(class_id)] = name
        return cls(count, cabs, sources, paths, path_starts,
                   class_flat, class_starts, deps, class_names)

    # ── row field access (lazy derivation, parity with the C# DTO builders) ──

    def cab(self, index):
        return self.cabs.value(index)

    def source(self, index):
        return self.sources.value(index)

    def container_path_count(self, index):
        return int(self.path_starts[index + 1] - self.path_starts[index])

    def container_path(self, index, path_index):
        return self.paths.value(int(self.path_starts[index]) + path_index)

    def name(self, index):
        path_count = self.container_path_count(index)
        if path_count == 0:
            return ""
        first = self.container_path(index, 0)
        leaf = first.rsplit("/", 1)[-1]
        return f"{leaf} (+{path_count - 1})" if path_count > 1 else leaf

    def container(self, index):
        # Character-exact port of RipperBlenderBridge.JoinContainerPaths: the
        # separator is appended BEFORE the overflow check, so a capped row's
        # "…(+N more names)" tail sits after a separator, and the cap compares
        # against the separator-inclusive running length.
        path_count = self.container_path_count(index)
        pieces = []
        length = 0
        for p in range(path_count):
            if p > 0:
                pieces.append("  |  ")
                length += 5
            path = self.container_path(index, p)
            if length + len(path) > _MAX_CONTAINER_JOIN_CHARS:
                pieces.append(f"…(+{path_count - p} more names)")
                break
            pieces.append(path)
            length += len(path)
        return "".join(pieces)

    def type_names(self, index):
        start = int(self.class_starts[index])
        end = int(self.class_starts[index + 1])
        names = [self.class_names.get(int(cid), str(int(cid)))
                 for cid in self.class_flat[start:end]
                 if int(cid) != self._asset_bundle_id]
        return ", ".join(names) if names else "AssetBundle"

    def field(self, index, key):
        if key == "cab":
            return self.cab(index)
        if key == "name":
            return self.name(index)
        if key == "container":
            return self.container(index)
        if key == "type_names":
            return self.type_names(index)
        if key == "source":
            return self.source(index)
        if key == "deps":
            return int(self.deps[index])
        raise KeyError(key)

    # ── container protocol (legacy list-of-dicts compatibility) ──────────────

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        return RowView(self, index)

    def __iter__(self):
        for index in range(self.count):
            yield RowView(self, index)

    def cab_to_index(self):
        if self._cab_to_index is None:
            offsets = self.cabs.offsets
            big = self.cabs.big
            self._cab_to_index = {big[offsets[i]:offsets[i + 1]]: i
                                  for i in range(self.count)}
        return self._cab_to_index

    # ── quick search (columnar) ───────────────────────────────────────────────

    def search_mask(self, query):
        """Row mask for the quick-search box: case-insensitive substring over
        the same fields the legacy per-row match used (name / container /
        source / type). Container/name matching runs over the RAW container
        paths -- the display join's " | " separators, its 16KB cap tail and
        the derived "(+N)" suffix were never meaningful search targets (and
        the cap made overlong rows partially unsearchable)."""
        needle = query.strip().lower()
        mask = np.zeros(self.count, dtype=bool)
        if not needle:
            mask[:] = True
            return mask

        # source column: value id == row id
        mask[self.sources.find_value_ids(needle)] = True

        # container paths: path row -> entry row
        path_ids = self.paths.find_value_ids(needle)
        if len(path_ids):
            row_ids = np.searchsorted(self.path_starts, path_ids, side="right") - 1
            mask[np.unique(row_ids)] = True

        # type names: query -> matching class ids -> rows carrying one. The
        # AssetBundle id is excluded here because the display column SKIPS it
        # (a "Transform, GameObject" row never showed the word AssetBundle,
        # so it must not match "assetbundle" now either).
        matching_ids = [cid for cid, cname in self.class_names.items()
                        if needle in cname.lower() and cid != self._asset_bundle_id]
        if matching_ids:
            hits = np.isin(self.class_flat, matching_ids)
            positions = np.flatnonzero(hits)
            if len(positions):
                row_ids = np.searchsorted(self.class_starts, positions, side="right") - 1
                mask[np.unique(row_ids)] = True
        # Rows whose class list is empty or AssetBundle-only DISPLAY as the
        # literal "AssetBundle" -- keep those searchable by that word.
        if needle in "assetbundle":
            non_bundle_cumulative = np.concatenate(
                [[0], np.cumsum(self.class_flat != self._asset_bundle_id)])
            non_bundle_per_row = (non_bundle_cumulative[self.class_starts[1:]]
                                  - non_bundle_cumulative[self.class_starts[:-1]])
            mask[non_bundle_per_row == 0] = True

        return mask

    def sort_values(self, column):
        """Materialized per-row sort keys for one column, cached -- built on
        the first header click, numpy-cheap for deps, one derivation pass for
        the string columns."""
        cached = self._sort_cache.get(column)
        if cached is None:
            if column == "deps":
                cached = self.deps
            elif column == "name":
                cached = [self.name(i) for i in range(self.count)]
            elif column == "container":
                cached = [self.container(i) for i in range(self.count)]
            elif column == "type_names":
                cached = [self.type_names(i) for i in range(self.count)]
            elif column == "source":
                cached = [self.source(i) for i in range(self.count)]
            else:
                cached = [self.cab(i) for i in range(self.count)]
            self._sort_cache[column] = cached
        return cached
