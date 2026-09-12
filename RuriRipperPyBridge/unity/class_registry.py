"""All-version Unity class id <-> name registry.

Backed by ``class_registry.json`` (merged from 1398 Unity TypeTreeDump class
tables).  Unity class ids are stable across versions while names occasionally get
renamed, so the importer dispatches on the numeric id from the ``!u!<id>`` header
and uses this registry to map between ids and any historical name.

Degrades gracefully: if the JSON is missing, lookups return None and callers fall
back to matching on the class name written in the file.
"""

from __future__ import annotations

import json
import os

_DATA = None


def _load():
    global _DATA
    if _DATA is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "class_registry.json")
        try:
            with open(path, encoding="utf-8") as handle:
                _DATA = json.load(handle)
        except (OSError, ValueError):
            _DATA = {"canonical": {}, "id_to_names": {}, "name_to_id": {}}
    return _DATA


def canonical_name(class_id):
    """Current (reference-version) name for a numeric class id, or None."""
    if class_id is None:
        return None
    return _load()["canonical"].get(str(class_id))


def names_for_id(class_id):
    """Every historical name a class id has had."""
    if class_id is None:
        return []
    return _load()["id_to_names"].get(str(class_id), [])


def id_for_name(name):
    """Numeric class id for any historical class name, or None."""
    if name is None:
        return None
    cid = _load()["name_to_id"].get(name)
    return int(cid) if cid is not None else None


def is_loaded():
    return bool(_load()["name_to_id"])
