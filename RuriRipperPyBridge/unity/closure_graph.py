"""The loaded closure's object graph, decoded from the bridge's columnar blob
(ClosureGraphBlob.cs): one row per asset -- identity (collection name, PathID),
ClassID, m_Name -- plus every resolvable PPtr reference as a (from, field, to)
edge. Built C#-side after Process and before Export, so it also covers generated
assets (reconstructed AnimatorStateMachines/States), and it exists on EVERY
import -- a scan_cabs call is just an import that exports nothing.

This is the topology surface: which clip does a state play, which avatar does a
template name, which controller owns which states -- all answerable without
serializing or parsing a single YAML document. Identity is the game's own
deterministic coordinates; key(i) strings feed straight back into
import_cabs(export_asset_keys=...) to materialize exactly those assets."""

from __future__ import annotations

import json


class ClosureGraph:
    def __init__(self, count, names, class_ids, path_ids, collection_of,
                 collection_names, field_names, edge_from, edge_to, edge_field):
        self.count = count
        self._names = names
        self._class_ids = class_ids
        self._path_ids = path_ids
        self._collection_of = collection_of
        self._collection_names = collection_names
        self._field_names = field_names
        self._edge_from = edge_from
        self._edge_to = edge_to
        self._edge_field = edge_field
        self._out_edges = None

    @classmethod
    def from_blob(cls, meta_json, payload):
        if not meta_json:
            return cls(0, [], [], [], [], [], [], [], [], [])
        meta = json.loads(meta_json)
        sections = meta["Sections"]
        view = memoryview(payload)

        def texts(section_key, expected):
            offset, length = sections[section_key]
            if expected == 0:
                return []
            parts = bytes(view[offset:offset + length]).decode("utf-8").split("\x00")
            if len(parts) != expected:
                raise ValueError("graph blob section %r: %d strings, expected %d"
                                 % (section_key, len(parts), expected))
            return parts

        def numbers(section_key, fmt):
            offset, length = sections[section_key]
            return view[offset:offset + length].cast(fmt)

        count = meta["AssetCount"]
        return cls(
            count,
            texts("names", count),
            numbers("classIds", "i"),
            numbers("pathIds", "q"),
            numbers("collectionOf", "i"),
            texts("collectionNames", meta["CollectionCount"]),
            texts("fieldNames", meta["FieldCount"]),
            numbers("edgeFrom", "i"),
            numbers("edgeTo", "i"),
            numbers("edgeField", "i"),
        )

    def name(self, index):
        return self._names[index]

    def class_id(self, index):
        return self._class_ids[index]

    def path_id(self, index):
        return self._path_ids[index]

    def collection_name(self, index):
        return self._collection_names[self._collection_of[index]]

    def key(self, index):
        """The asset's deterministic identity, in the exact wire form
        import_cabs(export_asset_keys=...) takes."""
        return "%s|%d" % (self.collection_name(index), self._path_ids[index])

    def indices_of_class(self, class_id):
        return [i for i in range(self.count) if self._class_ids[i] == class_id]

    def find(self, class_id, name):
        """Every index whose class and exact m_Name both match."""
        return [i for i in range(self.count)
                if self._class_ids[i] == class_id and self._names[i] == name]

    def out_edges(self, index):
        """[(field name, target index), ...] -- the asset's own references."""
        if self._out_edges is None:
            adjacency = [[] for _ in range(self.count)]
            for position in range(len(self._edge_from)):
                adjacency[self._edge_from[position]].append(position)
            self._out_edges = adjacency
        return [(self._field_names[self._edge_field[p]], self._edge_to[p])
                for p in self._out_edges[index]]

    def reachable(self, start, expand_class_ids, collect_class_id):
        """Indices of ``collect_class_id`` assets reachable from ``start``,
        expanding only through assets whose class is in ``expand_class_ids``
        (the start expands unconditionally). Restricting expansion is what keeps
        a walk semantically scoped -- e.g. controller -> state machines -> states
        without leaking across state transitions."""
        found = []
        seen = {start}
        frontier = [start]
        while frontier:
            current = frontier.pop()
            for _field, target in self.out_edges(current):
                if target in seen:
                    continue
                seen.add(target)
                target_class = self._class_ids[target]
                if target_class == collect_class_id:
                    found.append(target)
                if target_class in expand_class_ids:
                    frontier.append(target)
        return found
