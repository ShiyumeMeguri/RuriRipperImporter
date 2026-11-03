"""Names a USD tree gives to what a source states.

A source names its objects however it likes: spaces, brackets, dots, text in any script. A prim
needs an identifier and a file needs a name every file system accepts, and two things the source
calls the same still need two names where they are looked up. USD itself answers what an
identifier is, so no character table is kept here.
"""

from __future__ import annotations

import functools
import hashlib

from pxr import Sdf

#: Names a file system reserves whatever the extension.
_RESERVED = frozenset(
    ["con", "prn", "aux", "nul"] + ["com{0}".format(number) for number in range(1, 10)]
    + ["lpt{0}".format(number) for number in range(1, 10)])

#: Longest name a prim, a folder or a file gets, so a nested path stays inside what tools accept.
LIMIT = 48


@functools.lru_cache(maxsize=None)
def _starts(character):
    return Sdf.Path.IsValidIdentifier(character)


@functools.lru_cache(maxsize=None)
def _continues(character):
    return Sdf.Path.IsValidIdentifier("_" + character)


def identifier(text, limit=LIMIT):
    """``text`` as a prim name: each run of characters USD does not allow in one becomes a single
    underscore, one at either end is dropped, a name that cannot start with its first character gets an
    underscore in front, and one longer than ``limit`` keeps its start and a digest of the whole."""
    text = text or ""
    pieces = []
    for character in text:
        if _continues(character):
            pieces.append(character)
        elif pieces and pieces[-1] != "":
            pieces.append("")
    while pieces and pieces[-1] == "":
        pieces.pop()
    name = "".join(piece or "_" for piece in pieces)
    if not name or not _starts(name[0]):
        name = "_" + name
    if len(name) > limit:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        name = name[:limit - 9] + "_" + digest
    return name


def file_stem(text, limit=LIMIT):
    """``text`` as a file or folder name: an identifier that is also not one a file system reserves."""
    name = identifier(text, limit)
    return name + "_" if name.lower() in _RESERVED else name


class Namer:
    """Hands out a name once per scope, comparing without regard to case because the file system does."""

    def __init__(self):
        self._taken = {}

    def claim(self, scope, wanted):
        taken = self._taken.setdefault(scope, set())
        name = wanted
        number = 1
        while name.lower() in taken:
            number += 1
            name = "{0}_{1}".format(wanted, number)
        taken.add(name.lower())
        return name

    def holds(self, scope, name):
        return name.lower() in self._taken.get(scope, ())
