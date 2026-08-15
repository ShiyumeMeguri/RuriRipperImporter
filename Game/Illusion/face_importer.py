"""Wear one expression on an imported character.

The whole of it is FBSBase.CalculateBlendShape with a single pattern at full
weight: the pattern's Close key gets ``(1 - openness)`` and its Open key gets
``openness``. Unity states those weights on 0..100 and Blender's shape keys run
0..1, which is the only conversion on this path.

The pattern table itself is the hook's (the ``face.patterns`` dataset), normalized to
one row per (channel, target, pattern) -- so what is left here is setting shape
key values on the meshes that rig actually has.
"""

from __future__ import annotations

import json

import bpy

from . import datasets

# Where the rig remembers which head it was built from, so the face can still be
# driven in a later session without re-resolving that.
RIG_PROPERTY = "ruri_illusion_head"

CHANNELS = ("eyebrow", "eyes", "mouth")

# What the game's own live drivers feed CalculateBlendShape when nothing is
# happening -- ``openRate`` in ``Lerp(OpenMin, OpenMax, openRate)``. The blink
# controller holds the eyes and brows fully open between blinks; the mouth is
# driven by the voice, which is silent, so it sits at OpenMin. A still frame is
# exactly that state, which is why a stored "open" value is a CEILING and not the
# openness a pose shows.
_RESTING_DRIVE = {"eyebrow": 1.0, "eyes": 1.0, "mouth": 0.0}


def stamp(armature, bundle, asset):
    """Remember which head a rig was built from."""
    if armature is not None and bundle and asset:
        armature[RIG_PROPERTY] = json.dumps({"bundle": bundle, "asset": asset},
                                            separators=(",", ":"))


def head_of(armature):
    """``(bundle, asset)`` of the head this rig was built from, or ("", "")."""
    raw = armature.get(RIG_PROPERTY) if armature is not None else None
    if not raw:
        return "", ""
    try:
        stored = json.loads(raw)
    except ValueError:
        return "", ""
    return str(stored.get("bundle") or ""), str(stored.get("asset") or "")


def table(armature):
    """The head's pattern table, as ``{channel: [{target, patterns[(close, open)]}]}``.
    Read from the hook by the head the rig remembers."""
    bundle, asset = head_of(armature)
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


def apply(armature, face_table, patterns, openness):
    """Put one expression on the rig. Returns (meshes touched, warnings)."""
    meshes = _meshes_by_name(armature)
    touched = set()
    warnings = []
    for channel, targets in (face_table or {}).items():
        if channel not in patterns:
            continue
        pattern = int(patterns[channel])
        rate = max(0.0, min(1.0, float(openness.get(channel, 1.0))))
        for entry in targets:
            obj = meshes.get(entry["target"].lower())
            if obj is None:
                warnings.append("{0}: '{1}' is not in this rig".format(channel, entry["target"]))
                continue
            pairs = entry["patterns"]
            if not 0 <= pattern < len(pairs):
                warnings.append("{0}: pattern {1} is outside this head's {2}".format(
                    channel, pattern, len(pairs)))
                continue
            if _apply_pair(obj, pairs, pattern, rate, warnings):
                touched.add(obj.name)
    return len(touched), warnings


def _apply_pair(obj, pairs, pattern, rate, warnings):
    """Zero this channel's own keys on the mesh, then raise the chosen pattern's
    two. Only the channel's OWN keys are cleared: a head's three channels drive
    different keys of the same mesh, and clearing everything would undo whichever
    was applied last."""
    keys = _shape_keys(obj)
    if not keys:
        warnings.append("{0} has no shape keys".format(obj.name))
        return False
    for close, open_index in pairs:
        for index in (close, open_index):
            if 0 <= index < len(keys):
                keys[index].value = 0.0
    close, open_index = pairs[pattern]
    if 0 <= close < len(keys):
        keys[close].value = 1.0 - rate
    if 0 <= open_index < len(keys):
        keys[open_index].value = rate
    return True


def clear(armature, face_table):
    """Put every key any channel of this head drives back to zero."""
    meshes = _meshes_by_name(armature)
    for targets in (face_table or {}).values():
        for entry in targets:
            obj = meshes.get(entry["target"].lower())
            keys = _shape_keys(obj) if obj is not None else []
            for close, open_index in entry["patterns"]:
                for index in (close, open_index):
                    if 0 <= index < len(keys):
                        keys[index].value = 0.0


def _shape_keys(obj):
    """The mesh's blend shapes in Unity's own order -- everything after Basis."""
    shape_keys = getattr(obj.data, "shape_keys", None) if obj is not None else None
    return list(shape_keys.key_blocks)[1:] if shape_keys is not None else []


def _meshes_by_name(armature):
    """Every mesh the rig drives, keyed by the name it was imported under.

    Blender uniquifies a repeated name with a ``.001`` tail, which is not part of
    the identity the game states -- and an assembled character genuinely has two
    ``o_tang`` (the head's and the separate tongue part's). Of a repeated name the
    one carrying shape keys wins, because a target of the expression system is by
    definition the one with the blend shapes on it."""
    found = {}
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        driven = obj.parent is armature or any(
            modifier.type == "ARMATURE" and modifier.object is armature
            for modifier in obj.modifiers)
        if not driven:
            continue
        key = _base_name(obj.name).lower()
        current = found.get(key)
        if current is None or (not _shape_keys(current) and _shape_keys(obj)):
            found[key] = obj
    return found


def _base_name(name):
    head, separator, tail = name.rpartition(".")
    return head if separator and tail.isdigit() and len(tail) == 3 else name


def resting_openness(ceilings):
    """The blend rate each channel actually shows at rest, from its stored ceiling:
    ``Lerp(0, ceiling, drive)`` with the game's own resting drive."""
    return {channel: float(ceilings.get(channel, 1.0)) * drive
            for channel, drive in _RESTING_DRIVE.items()}


def expression_patterns(row):
    """One ``face.expressions`` row split into what ``apply`` takes. The
    row's three open values are the ceilings the command sets, so they become a
    pose through ``resting_openness``. A pattern of -1 is one the expression does
    not touch."""
    patterns = {}
    ceilings = {}
    for channel in CHANNELS:
        pattern = int(float(row.get(channel, -1)))
        if pattern >= 0:
            patterns[channel] = pattern
        ceilings[channel] = float(row.get(channel + "Open", 1.0))
    return patterns, resting_openness(ceilings)


def default_openness():
    return resting_openness({})


# Extra channels an expression row states outside the three pattern channels.
# They are single scalars rather than pattern indices, so the IR carries them
# under their own names and a contract maps them (or not) like anything else.
SCALAR_CHANNELS = ("blush", "tears", "highlight")


def expression_ir(row):
    """One ``face.expressions`` row as the cross-game IR (see face_ir).

    Each driven channel contributes its pattern at the openness rate, plus the
    same pattern's shut end at the complement -- that pair IS what the game
    blends, so dropping the shut end would quietly lose every half-lidded and
    half-open expression. Untouched channels (-1) contribute nothing, which is
    what makes a viseme row a mouth-only statement rather than a whole face.
    """
    try:
        from ... import face_ir
    except ImportError:
        import face_ir

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
