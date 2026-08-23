"""Cross-game facial expression: the neutral middle, and nothing else.

Two games state a face in completely different terms. Koikatu drives blend
shapes through a per-head *pattern table*, so an expression is a pattern index
per channel plus an openness rate. Endfield drives BONES through named *ctrl*
drivers, each carrying a per-bone TRS delta. Neither vocabulary can be derived
from the other, and neither game should learn the other's -- so the join is data.

Three layers, and this module is only the middle one:

1. **Source adapter** -- a game turns its own expression statement into the IR:
   ``{"<channel>:<pattern>": weight}``. Lives with the game that owns those
   words (Koikatu: ``Game/Illusion/face_importer.expression_ir``). It has never
   heard of the destination.
2. **Contract** (this module) -- IR keys mapped to destination driver weights.
3. **Destination applier** -- a game takes ``{driver: weight}`` and drives its
   own rig (Endfield: the SkeletalMorph ctrl bindings). It has never heard of
   the source.

**THE CONTRACT LIVES IN THE BONE TABLE.** One pair of skeletons, one file: the
``face`` section of ``<A>To<B>.json``, beside the bone ``mappings`` that already
join those two rigs. Faces and bodies travel together and are chosen by the same
fact -- which skeleton family this rig declares -- so they are resolved by the
same code (``cross_game_retarget.resolve_retarget_spec``) out of the same file. A
second directory of face-only files would be a second lookup that can disagree
with the first about which pair is even joined.

AnimationRetarget does not read the ``face`` section and does not need to: its
readers take ``mappings``/``renames``/``settings`` and ignore everything else, so
the extra section rides along untouched through load, edit and save. That is the
whole of "no face -> bones only": a table with no ``face`` section retargets the
body and says nothing about the head.

Direction matters here, unlike the bone rows. A bone mapping is a bijection
between named bones and reads the same both ways; a face mapping is a weighted
many-to-many sum, and summing is not invertible -- so the section states which
way it runs. Nothing here has to enforce that: AnimationRetarget's composer only
carries the sections it understands across a flip or a chain (see
``AnimationRetarget.compose``), so a reversed or composed table simply arrives
without one, and ``section_of`` returning None is the caller's answer.

**A driver is a NAME, and how a rig realizes it is the rig's own business.** That
is what makes a bone-driven face and a shape-key-driven face interchangeable in
either direction: a contract never says "bone" or "shape key", it says a driver
name and a weight, and the destination resolves that name against whatever it
actually has (Endfield: ``character_panel.RigBinding``, which merges a
ShapeKeyBinding and a BoneBinding into one vocabulary, so a driver bound as both
drives both). A rig that grows shape keys for drivers it used to move with bones
needs no new code and no new table -- only the keys, named after the drivers.
"""

from __future__ import annotations

# The section of a bone table that states what happens to the FACE.
SECTION = "face"

# An IR key names the channel and the pattern the source selected. ``#closed``
# is the same pattern at the other end of its openness rate: Koikatu's pattern
# is a PAIR of shape keys (shut, open) blended by that rate, and a destination
# may well have a different driver for the shut end.
CLOSED_MARK = "#closed"


def ir_key(channel, pattern, closed=False):
    return "{0}:{1}{2}".format(channel, int(pattern), CLOSED_MARK if closed else "")


def section_of(spec):
    """The ``face`` section of a bone table, or None when it states none.

    None is the answer to "can this pair carry a face at all", and the caller
    says so rather than retargeting a body and leaving the head silently behind."""
    section = (spec or {}).get(SECTION)
    return section if isinstance(section, dict) else None


def mappings_of(spec):
    """``{IR key: [(driver, factor)]}`` from a bone table's face section, or {}.

    A row's ``to`` is either a bare driver name (factor 1.0) or a
    ``[name, factor]`` pair; several source keys legitimately push the same
    driver, so the reader keeps every row and ``convert`` sums them."""
    section = section_of(spec)
    if section is None:
        return {}
    mappings = {}
    for entry in section.get("mappings") or ():
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
    return mappings


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
