"""The inputs a generated shader reads, and what changed in them between two generations of its products.

A material's inputs are its record: parameter values and bound images, kept under the names the shader's manifest
declares. Replacing one generation of shaders with the next leaves every record as it is; only a name the new
generation no longer declares can leave a value unread. This module lists those names -- declared by the
generation that runs, absent from the one installed -- beside the names that are new, so the user can say which
new name carries each old one, and applies that choice to the records of the materials the file owns.

Where a record keeps what is the material's own self-description (``ruri_shading``), not a table copied here:
which buckets hold values, how a bucket's key spells the shader's name, and which bucket holds the images.
"""

from __future__ import annotations

import difflib
import json
import os

MANIFEST_FILE = "RuriManifest.json"
SHADING = "ruri_shading"
PARAMETER = "param"
SLOT = "slot"

_INSTALLED = {}


def summarize(manifest):
    """One stack's manifest as the names a material can carry: per part the parameter names (with their kind)
    and the image slots it samples. Level images are the world's, not a material's, and are left out."""
    parts = {}
    for part, spec in (manifest.get("parts") or {}).items():
        fetches = list(spec.get("fetches") or [])
        for zone in spec.get("zones") or []:
            fetches.extend(zone.get("fetches") or [])
        fetches.extend((spec.get("outline") or {}).get("fetches") or [])
        parts[part] = {
            "params": {row[0]: row[1] for row in spec.get("params") or []},
            "slots": sorted({fetch["slot"] for fetch in fetches if not fetch.get("env")}),
        }
    return {"stamp": manifest["stamp"], "blend": manifest["blend"], "parts": parts}


def loaded(package):
    """The declarations of the stacks ``package`` registered in this session, by panel key."""
    return {stack.PANEL_KEY: summarize(stack.m) for stack in package.STACKS}


def manifest_path(package):
    return os.path.join(os.path.dirname(os.path.abspath(package.__file__)), MANIFEST_FILE)


def installed(package):
    """The declarations of the products on disk beside ``package``, by panel key. Read again only when the file
    changed."""
    path = manifest_path(package)
    status = os.stat(path)
    signature = (status.st_mtime_ns, status.st_size)
    held = _INSTALLED.get(path)
    if held is None or held[0] != signature:
        with open(path, encoding="utf-8") as stream:
            document = json.load(stream)
        held = (signature, {entry["names"]["panel_key"]: summarize(entry) for entry in document["stacks"]})
        _INSTALLED[path] = held
    return held[1]


def removed_parts(before, after):
    """The parts the running generation declares that the installed one no longer does, as (panel key, part)."""
    return [(key, part) for key, old in before.items() if key in after
            for part in old["parts"] if part not in after[key]["parts"]]


class Change:
    """One name the installed generation dropped, the new names that could carry it, and how many materials hold
    a value under it."""

    __slots__ = ("key", "part", "kind", "name", "candidates", "carriers", "linked")

    def __init__(self, key, part, kind, name, candidates, carriers, linked):
        self.key = key
        self.part = part
        self.kind = kind
        self.name = name
        self.candidates = candidates
        self.carriers = carriers
        self.linked = linked


def _spelling(template, name):
    """The record key a bucket keeps ``name`` under: the template spells a key into the shader's name, so the
    key is the name with the template's own prefix and suffix taken off. None when ``name`` is not spelled so."""
    head, _marker, tail = template.partition("{0}")
    if not name.startswith(head) or not name.endswith(tail) or len(name) < len(head) + len(tail):
        return None
    return name[len(head):len(name) - len(tail)]


def _image_group(material):
    description = material.get(SHADING)
    return description["images"]["group"] if description is not None else None


def _buckets(material):
    description = material.get(SHADING)
    if description is None:
        return {}
    return {bucket: str(template) for bucket, template in description["values"].items()}


def holds(material, kind, name):
    """Whether the material's record keeps a value or an image under the shader name ``name``."""
    if kind == SLOT:
        group = _image_group(material)
        table = material.get(group) if group else None
        return table is not None and name in table
    for bucket, template in _buckets(material).items():
        key = _spelling(template, name)
        table = material.get(bucket)
        if key is not None and table is not None and key in table:
            return True
    return False


def _form(templates, name):
    """Which buckets keep ``name`` under a spelling of their own (a tiling row is its slot's name plus a suffix): two
    names can stand for one another only when they spell into the same buckets."""
    return frozenset(bucket for bucket, template in templates.items()
                     if template != "{0}" and _spelling(template, name) is not None)


def changes(before, after, carriers, linked):
    """What the installed generation (``after``) dropped from the running one (``before``): per stack and part
    each parameter name and each image slot only the running generation declares, with the new names that could
    carry it -- the same kind, spelled into the same buckets -- closest spelling first. ``carriers(key, part)``
    and ``linked(key, part)`` give the materials of a part the file owns and the ones it links; names nobody
    holds a value under are left out."""
    found = []
    for key, old in before.items():
        new = after.get(key)
        if new is None:
            continue
        for part, declared in old["parts"].items():
            now = new["parts"].get(part)
            if now is None:
                continue
            owners = list(carriers(key, part))
            links = list(linked(key, part))
            templates = _buckets((owners + links)[0]) if owners or links else {}
            for kind, was, is_now in ((PARAMETER, declared["params"], now["params"]),
                                      (SLOT, declared["slots"], now["slots"])):
                for name in was:
                    if name in is_now:
                        continue
                    fresh = [one for one in is_now if one not in was and
                             (kind == SLOT or (is_now[one] == was[name] and _form(templates, one) == _form(templates, name)))]
                    fresh.sort(key=lambda one: -difflib.SequenceMatcher(None, name, one).ratio())
                    owned = sum(1 for material in owners if holds(material, kind, name))
                    held = sum(1 for material in links if holds(material, kind, name))
                    if owned or held:
                        found.append(Change(key, part, kind, name, fresh, owned, held))
    return found


def rename(material, kind, old, new):
    """Move the value or image the record keeps under ``old`` to the key that spells ``new``. A key already
    present under the new name is the user's, and stays. Returns whether the record changed."""
    if kind == SLOT:
        group = _image_group(material)
        table = material.get(group) if group else None
        if table is None or old not in table or new in table:
            return False
        moved = table.to_dict()
        moved[new] = moved.pop(old)
        material[group] = moved
        return True
    changed = False
    for bucket, template in _buckets(material).items():
        table = material.get(bucket)
        was, now = _spelling(template, old), _spelling(template, new)
        if table is None or was is None or now is None or was not in table or now in table:
            continue
        moved = table.to_dict()
        moved[now] = moved.pop(was)
        material[bucket] = moved
        changed = True
    return changed
