"""Closure-wide discovery: finding assets by class and name across a database.

A resolved cabmap closure is a flat bag of guid -> YAML text with no index of
its own, so a scan here is built on ``peek_class_and_name``, a bounded-prefix
sniff rather than a parse: scanning a whole closure costs O(number of documents)
instead of O(total closure bytes).

Clips are the exception, deliberately: the database STATES which of its assets
are clips (``clips()``). Sniffing them meant their text had to be written at
all, and for one character that text measured 793 MB. Asking beats sniffing
wherever the source can answer.

Works against either database (disk or bridge closure) -- both expose
``all_guids`` / ``raw_text`` / ``load_guid`` / ``clips``.
"""

from __future__ import annotations

import os
import re

_CLASS_HEADER_RE = re.compile(r"^---\s+!u!\d+\s+&-?\d+(?:\s+stripped)?\s*$", re.MULTILINE)
_NAME_FIELD_RE = re.compile(r"^\s*m_Name:\s*(.*?)\s*$", re.MULTILINE)
# Unity always writes the class name + m_Name within the first few hundred
# bytes of a document; 4KB is slack, not a guess at the limit.
_PEEK_CHARS = 4096


def peek_class_and_name(text):
    """(class_name, m_Name) read off a bounded prefix of a document's raw text,
    without a full unity_yaml parse. Either may be None."""
    prefix = text[:_PEEK_CHARS]
    header = _CLASS_HEADER_RE.search(prefix)
    if header is None:
        return None, None
    rest = prefix[header.end():].lstrip("\r\n")
    line_end = rest.find("\n")
    class_line = rest if line_end == -1 else rest[:line_end]
    class_name = class_line.split(":", 1)[0].strip() or None
    name_match = _NAME_FIELD_RE.search(prefix)
    name = name_match.group(1).strip() or None if name_match else None
    return class_name, name


def collect_guids(obj, out):
    """Recursively collect every guid referenced anywhere in a parsed
    structure, into the set ``out``."""
    if isinstance(obj, dict):
        guid = obj.get("guid")
        if isinstance(guid, str) and len(guid) == 32:
            out.add(guid)
        for value in obj.values():
            collect_guids(value, out)
    elif isinstance(obj, list):
        for value in obj:
            collect_guids(value, out)


def prefab_display_name(prefab, fallback="UnityModel"):
    """A prefab's own display name: its first GameObject's m_Name."""
    root = prefab.first("GameObject")
    if root is not None:
        name = root.data.get("m_Name")
        if name:
            return str(name)
    return fallback


def name_index(db, class_name):
    """{lowercased m_Name -> guid} over every document of one class in the
    closure.

    Lowercased on purpose: an addressable path resolved through the game's hash
    LUT is consistently all-lowercase while the asset's own m_Name keeps its
    authored casing, so this is the only join that actually matches. The cabmap
    only maps container path -> CAB name (not -> guid) and one CAB can host
    several named sub-objects (a multi-object FBX), which is why a name index is
    needed at all."""
    index = {}
    for guid in db.all_guids():
        text = db.raw_text(guid)
        if text is None:
            continue
        found_class, found_name = peek_class_and_name(text)
        if found_class == class_name and found_name:
            index[found_name.lower()] = guid
    return index


def material_name_index(db):
    """{lowercased Material m_Name -> guid} -- see ``name_index``. Joins a scene
    placement's own material_asset_paths (the entity's real material hash,
    resolved through the same StringPathHash LUT as its mesh) to a guid."""
    return name_index(db, "Material")


def prefab_name_index(db, roots):
    """{lowercased prefab display name -> guid} over a closure's own top-level
    .prefab guid list (ImportCabs' ``roots``).

    Mirrors the name-based join above, but keyed off each root's display name
    rather than a peeked m_Name, since ``roots`` is already the small,
    pre-filtered set of prefab-classed top-level assets -- no full-closure scan
    needed."""
    index = {}
    for guid in roots:
        prefab_file = db.load_guid(guid)
        if prefab_file is None:
            continue
        name = prefab_display_name(prefab_file, fallback="")
        if name:
            index[name.lower()] = guid
    return index


# ---------------------------------------------------------------------------
# AnimationClip discovery
# ---------------------------------------------------------------------------
def discover_clip_refs(db):
    """Lightweight metadata ({guid, name, size_bytes, path}) for every AnimationClip the
    closure holds. ``path`` is where the export placed it, i.e. the game's own folder for that
    clip -- what a browser groups a few hundred clips by.

    The DATABASE states which of its assets are clips (``clips()``); nothing here sniffs a class
    name off a megabyte of text. That is what lets a clip's curves cross as a blob and its YAML
    never be written at all. Nothing is decoded either: browsing what is available must not pay
    to decode every clip up front, and that cost belongs to whichever clips the user picks.
    Sorted by name for a stable browser listing."""
    refs = sorted(db.clips(), key=lambda r: r["name"].lower())
    return refs


def clip_paths_from_controller(db, controller_file):
    """De-duplicated absolute ``.anim`` paths for every clip a controller
    references, at any depth (disk mode only -- guids resolve to real paths).

    An Animator controller reaches its clips through ``m_Motion`` on states and
    through blend-tree children; collecting every guid and keeping the ones that
    resolve to a clip file captures them all without guessing at the structure.
    """
    paths = []
    seen = set()
    guids = set()
    for doc in controller_file.documents:
        collect_guids(doc.data, guids)
    for guid in guids:
        path = db.resolve_guid(guid)
        if not path:
            continue
        absolute = os.path.abspath(path)
        key = absolute.lower()
        if key not in seen and key.endswith(".anim") and os.path.isfile(absolute):
            seen.add(key)
            paths.append(absolute)
    return paths
