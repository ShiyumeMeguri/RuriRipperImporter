"""Cross-game facial expression: the neutral middle, and nothing else.

Two games state a face in completely different terms. Koikatu drives blend
shapes through a per-head *pattern table*, so an expression is a pattern index
per channel plus an openness rate. Endfield drives BONES through named *ctrl*
drivers, each carrying a per-bone TRS delta. Neither vocabulary can be derived
from the other, and neither game should learn the other's -- so the join is a
data file, exactly as the bone side already does it with ``<A>To<B>.json``.

Three layers, and this module is only the middle one:

1. **Source adapter** -- a game turns its own expression statement into the IR:
   ``{"<channel>:<pattern>": weight}``. Lives with the game that owns those
   words (Koikatu: ``Game/Koikatu/face_importer.expression_ir``). It has never
   heard of the destination.
2. **Contract** (this module) -- ``<Source>To<Dest>.face.json`` maps IR keys to
   destination driver weights. A new game pair is a new file, not new code.
3. **Destination applier** -- a game takes ``{driver: weight}`` and drives its
   own rig (Endfield: the SkeletalMorph ctrl bindings). It has never heard of
   the source.

Direction matters here, unlike the bone tables. A bone mapping is a bijection
between named bones and reads the same both ways; a face mapping is a weighted
many-to-many sum, and summing is not invertible -- so a table states one
direction and inverting it is refused rather than faked.
"""

from __future__ import annotations

import json
import os

FORMAT = "RuriFaceTable"
VERSION = 1
SUFFIX = ".face.json"

# An IR key names the channel and the pattern the source selected. ``#closed``
# is the same pattern at the other end of its openness rate: Koikatu's pattern
# is a PAIR of shape keys (shut, open) blended by that rate, and a destination
# may well have a different driver for the shut end.
CLOSED_MARK = "#closed"


def ir_key(channel, pattern, closed=False):
    return "{0}:{1}{2}".format(channel, int(pattern), CLOSED_MARK if closed else "")


def table_dir():
    """Where the contracts live -- beside this add-on's other presets, so a
    user editing one is in the same place they edit everything else."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "presets", "RuriFaceTable")


def table_path(source_game, dest_game):
    return os.path.join(table_dir(), "{0}To{1}{2}".format(source_game, dest_game, SUFFIX))


def find_table(source_game, dest_game):
    """(mappings, label) for source -> dest, or (None, path we looked for).

    Directional on purpose: see the module docstring.
    """
    path = table_path(source_game, dest_game)
    if not os.path.isfile(path):
        return None, path
    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    if document.get("format") != FORMAT:
        raise ValueError("{0} is not a {1} document".format(path, FORMAT))
    mappings = {}
    for entry in document.get("mappings") or ():
        key = entry.get("from")
        if not key:
            continue
        targets = []
        for target in entry.get("to") or ():
            name, factor = (target[0], float(target[1])) if isinstance(target, (list, tuple)) \
                else (target, 1.0)
            if name:
                targets.append((name, factor))
        mappings[key] = targets
    return mappings, os.path.basename(path)


def convert(expression_ir, mappings):
    """(driver weights, unmapped IR keys).

    Weights accumulate: several source channels legitimately push the same
    destination driver, and the destination side sums per bone anyway. Keys the
    contract says nothing about are returned rather than silently dropped, so a
    caller can report exactly which part of a face did not survive.
    """
    weights = {}
    unmapped = []
    for key, amount in expression_ir.items():
        if abs(amount) < 1e-6:
            continue
        targets = mappings.get(key)
        if not targets:
            unmapped.append(key)
            continue
        for name, factor in targets:
            weights[name] = weights.get(name, 0.0) + amount * factor
    return weights, unmapped


def validate(mappings, vocabulary):
    """Every driver a contract names must exist on the destination rig.

    A contract is hand-written against one character's driver list; a typo, or a
    driver only some characters carry, would otherwise be a silent no-op that
    looks exactly like "this expression does nothing". Returns the offending
    (key, driver) pairs.
    """
    known = set(vocabulary)
    problems = []
    for key, targets in mappings.items():
        for name, _factor in targets:
            if name not in known:
                problems.append((key, name))
    return problems
