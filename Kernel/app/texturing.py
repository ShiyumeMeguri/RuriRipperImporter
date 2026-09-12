"""Texture property names no layer of the role table states.

A material names its inputs whatever its author typed. The role table says what
the conventional ones mean, in layers -- the default layer is Unity's own
vocabulary, a game's folder holds that game's, and the user states the rest.
What is left over is gathered here as it is met, so a panel can list it and the
user can say what each one is, once.

In the kernel because BOTH hosts meet the same names: an unmapped property is a
fact about the game's vocabulary, not about whether the host builds node graphs
or bakes images. The panel that lists them is likewise one panel.
"""

from __future__ import annotations

#: {game: {property name: {"count", "material", "texture"}}}. Session state on
#: purpose -- it describes what THIS session's imports have met.
UNRESOLVED = {}


def record(game, name, material_name, texture_name):
    entries = UNRESOLVED.setdefault(game or "", {})
    entry = entries.get(name)
    if entry is None:
        entries[name] = {"count": 1, "material": material_name, "texture": texture_name}
    else:
        entry["count"] += 1


def unresolved_for(game):
    """The unmapped property names met for ``game``, each with how many materials
    carried it and one example material and texture, oldest first."""
    return dict(UNRESOLVED.get(game or "", {}))


def forget(game, names):
    """Drop names the user has now mapped."""
    entries = UNRESOLVED.get(game or "")
    if not entries:
        return
    for name in names:
        entries.pop(name, None)
