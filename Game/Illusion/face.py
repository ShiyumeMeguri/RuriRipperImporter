"""What this family's heads state about a face, as data.

A head states its face as PATTERNS: per channel (eyebrow / eyes / mouth), a list
of (closed key, open key) pairs, and an expression picks one pattern per channel
plus how open it is. Resolving that into "which blend shape of which mesh is at
what value" is this game's arithmetic; setting them is the host's
(:meth:`Kernel.host.Host.drive_blend_shapes`), because which object carries a mesh
and which of its keys is index N is a fact about the application.

There is no panel here. Browsing a model's expression vocabulary is the ONE cast
panel's Face pane, which asks the engine what the meshes themselves are named with
(``Kernel.app.cast_panel``); what is left here is this family's own arithmetic,
which that pane does not need and the cross-game expression IR does.

Nothing here imports a host.
"""

from __future__ import annotations

import json

from ...Kernel import host as host_port
from . import datasets

#: Where a built character remembers which prefab carried its head. Kept on the
#: rig itself so the face can still be driven in a later session.
HEAD_KEY = "ruri_illusion_head"

CHANNELS = ("eyebrow", "eyes", "mouth")


# ---------------------------------------------------------------------------
# What the head states
# ---------------------------------------------------------------------------
def remember(rig, bundle, asset):
    """Note on the rig which prefab carried its head."""
    if rig is not None and bundle and asset:
        host_port.current().rig_memory(rig)[HEAD_KEY] = json.dumps(
            {"bundle": bundle, "asset": asset}, separators=(",", ":"))


def head_of(rig):
    """``(bundle, asset)`` of the head this rig was built from, or ("", "").

    The stored form stays exactly what it has always been: a rig stamped by an
    earlier version is still a character whose face has to keep working."""
    if rig is None:
        return "", ""
    raw = host_port.current().rig_memory(rig).get(HEAD_KEY)
    if not raw:
        return "", ""
    try:
        stored = json.loads(raw)
    except ValueError:
        return "", ""
    return str(stored.get("bundle") or ""), str(stored.get("asset") or "")


def table(rig):
    """The head's pattern table, as ``{channel: [{target, patterns[(close, open)]}]}``.
    Read from the hook by the head the rig remembers."""
    bundle, asset = head_of(rig)
    if not bundle or not asset:
        return {}
    rows = datasets.rows(datasets.FACE_PATTERNS, bundle=bundle, asset=asset)
    built = {}
    for row in rows:
        channel = str(row["channel"])
        target = str(row["target"])
        targets = built.setdefault(channel, {})
        pairs = targets.setdefault(target, [])
        pattern = int(float(row["pattern"]))
        while len(pairs) <= pattern:
            pairs.append((-1, -1))
        pairs[pattern] = (int(float(row["close"])), int(float(row["open"])))
    return {channel: [{"target": target, "patterns": pairs} for target, pairs in targets.items()]
            for channel, targets in built.items()}


def pattern_count(face_table, channel):
    targets = (face_table or {}).get(channel) or ()
    return min((len(entry["patterns"]) for entry in targets), default=0)


def expressions(personality):
    """The named expressions one personality has, plus the shared set every
    personality falls back on (the sheets the game files under negative keys)."""
    found = []
    listed = datasets.table(datasets.EXPRESSIONS)
    if listed is None:
        return found
    for index in range(len(listed)):
        own = datasets.number(listed, index, "personality")
        if own == personality or own < 0:
            found.append({name: listed.cell(index, name) for name in listed.names})
    return found


#: The blend rate each channel actually shows at rest, as a share of its ceiling.
#: The game's own resting drive: the mouth rests closed, the other two open.
_RESTING_DRIVE = {"eyebrow": 1.0, "eyes": 1.0, "mouth": 0.0}


def resting_openness(ceilings):
    """The blend rate each channel actually shows at rest, from its stored ceiling:
    ``Lerp(0, ceiling, drive)`` with the game's own resting drive."""
    return {channel: float(ceilings.get(channel, 1.0)) * drive
            for channel, drive in _RESTING_DRIVE.items()}


def default_openness():
    return resting_openness({})


def expression_patterns(row):
    """One ``face.expressions`` row split into what :func:`weights` takes. The
    row's three open values are the ceilings the command sets, so they become a
    pose through :func:`resting_openness`. A pattern of -1 is one the expression
    does not touch, and is left out rather than driven to nothing."""
    patterns = {}
    ceilings = {}
    for channel in CHANNELS:
        pattern = int(float(row.get(channel, -1)))
        if pattern >= 0:
            patterns[channel] = pattern
        ceilings[channel] = float(row.get(channel + "Open", 1.0))
    return patterns, resting_openness(ceilings)


# Extra channels an expression row states outside the three pattern channels.
# They are single scalars rather than pattern indices, so the IR carries them
# under their own names and a contract maps them (or not) like anything else.
SCALAR_CHANNELS = ("blush", "tears", "highlight")


def expression_ir(row):
    """One ``face.expressions`` row as the cross-game IR (see Kernel.face_ir).

    Each driven channel contributes its pattern at the openness rate, plus the
    same pattern's shut end at the complement -- that pair IS what the game
    blends, so dropping the shut end would quietly lose every half-lidded and
    half-open expression. Untouched channels (-1) contribute nothing, which is
    what makes a viseme row a mouth-only statement rather than a whole face.
    """
    from ...Kernel import face_ir

    patterns, _resting = expression_patterns(row)
    ir = {}
    for channel, pattern in patterns.items():
        # The row's own ceiling, NOT resting_openness: the resting drive is a
        # runtime parameter (a mouth is shut until the character speaks, which
        # is why its drive is 0), so folding it in here would state every
        # expression as mouth-closed and lose the whole channel.
        rate = max(0.0, min(1.0, float(row.get(channel + "Open", 1.0))))
        ir[face_ir.ir_key(channel, pattern)] = rate
        if rate < 1.0:
            ir[face_ir.ir_key(channel, pattern, closed=True)] = 1.0 - rate
    for channel in SCALAR_CHANNELS:
        value = float(row.get(channel, 0.0) or 0.0)
        if abs(value) > 1e-6:
            ir[channel] = value
    # Gaze is an enumerated direction, not a magnitude, so it is keyed like a
    # pattern -- a row that states ONLY a gaze (the 視線 entries) is a complete
    # statement about where the eyes point and says nothing about the face.
    look = int(float(row.get("eyesLook", -1) or -1))
    if look >= 0:
        ir[face_ir.ir_key("eyesLook", look)] = 1.0
    return ir


# ---------------------------------------------------------------------------
# The arithmetic: patterns -> which blend shape sits at what
# ---------------------------------------------------------------------------
def weights(face_table, patterns, openness):
    """``{mesh name: {blend shape index: value}}`` for one expression.

    Only the driven channel's OWN keys are zeroed. A head's three channels drive
    different keys of the same mesh, and clearing everything would undo whichever
    was applied last."""
    found = {}
    for channel, targets in (face_table or {}).items():
        if channel not in patterns:
            continue
        pattern = int(patterns[channel])
        rate = max(0.0, min(1.0, float(openness.get(channel, 1.0))))
        for entry in targets:
            pairs = entry["patterns"]
            if not 0 <= pattern < len(pairs):
                continue
            values = found.setdefault(str(entry["target"]).lower(), {})
            for close, opened in pairs:
                for index in (close, opened):
                    if index >= 0:
                        values[index] = 0.0
            close, opened = pairs[pattern]
            if close >= 0:
                values[close] = 1.0 - rate
            if opened >= 0:
                values[opened] = rate
    return found


def cleared(face_table):
    """Every key any channel of this head drives, back to zero."""
    found = {}
    for targets in (face_table or {}).values():
        for entry in targets:
            values = found.setdefault(str(entry["target"]).lower(), {})
            for close, opened in entry["patterns"]:
                for index in (close, opened):
                    if index >= 0:
                        values[index] = 0.0
    return found

