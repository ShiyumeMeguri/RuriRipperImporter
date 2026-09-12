"""Endfield's SkeletalMorph facial system, read out of its MonoBehaviour assets.

The game drives a face through named **ctrl drivers** rather than through
transform curves: a facial asset carries, per channel, a list of
``(_ctrlName, weight)`` -- either a constant weight (a pose) or a whole
``AnimationCurve`` of weight over time (a morph animation).  Body clips carry
nothing facial at all (their humanoid muscles stop at "Jaw Close"), so this
family is the only source of expression animation there is.

Three shapes exist, told apart by the fields present rather than by script guid
(a guid table would be one more thing to keep in sync with the game):

    pose     ``_pose`` with per-channel ``_<name>Value<side>`` weight lists
    emotion  a pose plus blink / micro-expression / phoneme-set references
    anim     per-channel ``_<name>Curve<side>`` lists of ctrl -> AnimationCurve
    lipsync  ``_lipSyncMap``: phoneme-set index -> the poses for A/E/I/O/U/...

Channel names are NOT enumerated here.  Whatever ``_*Curve*`` / ``_*Value*``
keys an asset carries become its channels, so a game update that adds one
arrives as data.  ``channel_label`` derives the display name from the key.

Host-agnostic on purpose (same rule as the rest of RuriRipperPyBridge): no bpy, no
Qt.  Curves are ``clip_curves.Channel`` objects, so a host samples morph
weights through exactly the same vectorized Hermite evaluator it already uses
for transform curves.
"""

from __future__ import annotations

import re

import numpy as np

_LABEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def channel_label(channel_key):
    """``_browCurveL`` -> ``Brow L``, ``_mouthCurve`` -> ``Mouth``,
    ``_lEarCurve`` -> ``L Ear``. Derived, never tabulated -- an unknown
    channel from a future game build still reads as something sensible."""
    stem = channel_key.lstrip("_")
    stem = stem.replace("Curve", " ").replace("Value", " ")
    words = []
    for chunk in stem.split():
        words.extend(w for w in _LABEL_SPLIT_RE.split(chunk) if w)
    return " ".join(word[:1].upper() + word[1:] for word in words) or stem


def ctrl_group(ctrl_name):
    """The ctrl's own family prefix (``brow_happy_l_L_ctrl`` -> ``brow``).
    Used to group drivers that a pose spreads across L/R channels."""
    head = ctrl_name.split("_", 1)[0]
    return head.lower() if head else ctrl_name.lower()


class MorphDriver:
    """One ctrl inside one channel: a constant weight, a curve, or both when
    an asset carries a pose and an animation for the same ctrl."""

    __slots__ = ("ctrl", "channel", "value", "curve")

    def __init__(self, ctrl, channel, value=None, curve=None):
        self.ctrl = ctrl
        self.channel = channel
        self.value = value
        self.curve = curve

    def weight_at(self, time):
        """The driver's weight at ``time`` seconds -- the curve when there is
        one, the constant otherwise, 0.0 when there is neither."""
        if self.curve is not None:
            return float(self.curve.sample(np.asarray([time], dtype=np.float64))[0, 0])
        return float(self.value) if self.value is not None else 0.0

    def __repr__(self):
        kind = "curve" if self.curve is not None else "value"
        return f"MorphDriver({self.ctrl!r}, {self.channel!r}, {kind})"


class MorphAsset:
    """One parsed SkeletalMorph asset: its kind, its channels, and the drivers
    in them. ``guid`` is the closure key it was loaded under."""

    __slots__ = ("guid", "name", "kind", "duration", "flags", "channels",
                 "references", "phoneme_sets")

    def __init__(self, guid, name, kind):
        self.guid = guid
        self.name = name
        self.kind = kind
        self.duration = 0.0
        self.flags = {}          # _bIsAdditiveClip / _bIsOverride / _bIsPauseAutoBlink / ...
        self.channels = {}       # channel key -> [MorphDriver]
        self.references = {}     # field name -> guid or [guid] (blink anim, micro-expression poses)
        self.phoneme_sets = {}   # lipsync only: set index -> [guid | None] per phoneme slot

    def drivers(self):
        for channel_key in sorted(self.channels):
            for driver in self.channels[channel_key]:
                yield driver

    def ctrl_names(self):
        return sorted({driver.ctrl for driver in self.drivers()})

    def is_animated(self):
        return any(driver.curve is not None for driver in self.drivers())

    def __repr__(self):
        return (f"MorphAsset({self.name!r}, {self.kind}, "
                f"{len(self.channels)} channel(s), {len(self.ctrl_names())} ctrl(s))")


# ── the avatar: what a ctrl actually MOVES on one character ───────────────────
#
# The tables themselves are read by the hook off the game's own typed asset
# (``endfield.morph.avatars``/``.ctrls``/``.bones``/``.shaderparams``); what
# lives here is only the shape a host holds them in.
#
# A face at weights w is, per bone:
#     base + sum(w_i * delta_i)
# with rotation in Euler degrees and scale deltas defaulting to 0 (= unchanged).


class MorphBoneDelta:
    """One bone's TRS contribution, either a base pose or a ctrl's delta."""

    __slots__ = ("bone_id", "bone_name", "position", "rotation", "scale")

    def __init__(self, bone_id, bone_name, position, rotation, scale):
        self.bone_id = bone_id
        self.bone_name = bone_name
        self.position = position
        self.rotation = rotation
        self.scale = scale

    def __repr__(self):
        return f"MorphBoneDelta({self.bone_name or self.bone_id})"


class MorphAvatar:
    """One character's ctrl -> bone-delta table."""

    __slots__ = ("guid", "name", "tag_id", "bone_names", "base_pose", "mappings", "shader_params")

    def __init__(self, guid, name, tag_id=0):
        self.guid = guid
        self.name = name
        # The game's own character <-> avatar-table join: a character data asset's
        # SkeletalMorphComponentData carries this same tagId. Identity, not a name.
        self.tag_id = tag_id
        self.bone_names = []      # boneID -> bone name
        self.base_pose = {}       # boneID -> MorphBoneDelta
        self.mappings = {}        # ctrl name -> [MorphBoneDelta]
        self.shader_params = {}   # ctrl name -> {"param": str, "default": float}

    def ctrl_names(self):
        return sorted(self.mappings)

    def bones_touched(self):
        return {delta.bone_name for deltas in self.mappings.values() for delta in deltas
                if delta.bone_name}

    def __repr__(self):
        return (f"MorphAvatar({self.name!r}, {len(self.bone_names)} bone(s), "
                f"{len(self.mappings)} ctrl(s))")


def sample_weights(asset, time):
    """Every ctrl of ``asset`` at ``time`` seconds -> {ctrl: weight}. A ctrl
    appearing in more than one channel takes the largest magnitude, which is
    what an additive L/R split means when both sides name the same ctrl."""
    weights = {}
    for driver in asset.drivers():
        weight = driver.weight_at(time)
        previous = weights.get(driver.ctrl)
        if previous is None or abs(weight) > abs(previous):
            weights[driver.ctrl] = weight
    return weights
