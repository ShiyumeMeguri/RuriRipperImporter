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

Host-agnostic on purpose (same rule as the rest of ruri_pybridge): no bpy, no
Qt.  Curves are ``clip_curves.Channel`` objects, so a host samples morph
weights through exactly the same vectorized Hermite evaluator it already uses
for transform curves.
"""

from __future__ import annotations

import re
import zlib

import numpy as np

from ...ruri_pybridge.unity import clip_curves

CTRL_KEY = "_ctrlName"
CURVE_KEY = "_curve"
VALUE_KEY = "_value"
POSE_KEY = "_pose"
LIPSYNC_KEY = "_lipSyncMap"

# The one anchor into the game's own asset layout: every asset of this family
# lives under this VFS folder. Everything else (which kinds exist, which
# channels, which ctrls) is derived from what is actually there.
VFS_MARKER = "/skeletalmorph/"

_CURVE_CHANNEL_RE = re.compile(r"^_.*Curve.*$")
_VALUE_CHANNEL_RE = re.compile(r"^_.*Value.*$")
# The game's Hungarian boolean prefix is "_b" followed by a capitalised word
# ("_bIsAdditiveClip"). Matching a bare "_b" would swallow "_blinkAnim" and
# "_blinkInterval", which are a reference and a number.
_FLAG_RE = re.compile(r"^_b[A-Z]")
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


def _curve_from_entry(entry):
    """One ``_curve`` block -> a 1-D clip_curves.Channel, or None when it holds
    no keyframes at all (the game ships plenty of empty ctrl slots)."""
    keys = ((entry or {}).get(CURVE_KEY) or {}).get("m_Curve") or []
    if not keys:
        return None
    times = np.empty(len(keys), dtype=np.float64)
    values = np.empty((len(keys), 1), dtype=np.float64)
    in_slopes = np.empty((len(keys), 1), dtype=np.float64)
    out_slopes = np.empty((len(keys), 1), dtype=np.float64)
    for index, key in enumerate(keys):
        times[index] = _number(key.get("time"))
        values[index, 0] = _number(key.get("value"))
        in_slopes[index, 0] = _number(key.get("inSlope"))
        out_slopes[index, 0] = _number(key.get("outSlope"))
    # Channel.sample assumes ascending times (searchsorted); the game writes
    # them in order, but a sort here costs nothing and removes the assumption.
    order = np.argsort(times, kind="stable")
    return clip_curves.Channel(entry.get(CTRL_KEY, ""), times[order], values[order],
                               in_slopes[order], out_slopes[order],
                               attribute=entry.get(CTRL_KEY, ""))


def _number(token):
    try:
        return float(token)
    except (TypeError, ValueError):
        return 0.0


def _rid(token):
    """A [SerializeReference] id, kept EXACT. These run past 4e18, well beyond
    what a float64 mantissa holds, so routing one through _number silently
    collapses neighbouring ids onto the same value -- and neighbouring ids are
    exactly what an avatar's mapping list is made of, so every ctrl would bind
    to some other ctrl's bones with nothing to show for it."""
    try:
        return int(token)
    except (TypeError, ValueError):
        return None


def _driver_list(entries, channel_key):
    """A ``_browCurveL``/``_browValueL``-shaped list -> [MorphDriver]. Entries
    without a ctrl name are skipped: an unnamed driver binds to nothing."""
    drivers = []
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        ctrl = entry.get(CTRL_KEY)
        if not ctrl:
            continue
        value = entry.get(VALUE_KEY)
        drivers.append(MorphDriver(ctrl, channel_key,
                                   value=_number(value) if value is not None else None,
                                   curve=_curve_from_entry(entry)))
    return drivers


def _looks_like_driver_list(value):
    """An EMPTY list still counts: the game ships every channel on every asset
    and simply leaves the unused ones empty, so dropping them would lose the
    difference between "this channel is unused here" and "this asset has no
    such channel" -- which is exactly what a host's channel grouping shows."""
    if not isinstance(value, list):
        return False
    return not value or (isinstance(value[0], dict) and CTRL_KEY in value[0])


def _reference_guid(ref):
    if isinstance(ref, dict):
        guid = ref.get("guid")
        return guid.lower() if isinstance(guid, str) and guid else None
    return None


def parse(data, guid="", name=""):
    """Parse one MonoBehaviour body (the dict ``unity_yaml`` produced) into a
    MorphAsset, or None when it is not a SkeletalMorph asset at all.

    Kind comes from the fields present, so the four shapes are told apart
    without a script-guid table:
      - ``_lipSyncMap``                     -> lipsync
      - any ``_*Curve*`` driver list        -> anim
      - ``_pose`` + ``_microExpressionPoses`` -> emotion
      - ``_pose`` alone                     -> pose
    """
    if not isinstance(data, dict):
        return None
    name = name or (data.get("m_Name") or "")

    if LIPSYNC_KEY in data:
        asset = MorphAsset(guid, name, "lipsync")
        _parse_lipsync(asset, data.get(LIPSYNC_KEY))
        return asset

    curve_channels = {key: value for key, value in data.items()
                      if _CURVE_CHANNEL_RE.match(key) and _looks_like_driver_list(value)}
    pose = data.get(POSE_KEY)
    has_pose = isinstance(pose, dict)
    # Empty channels are kept (see _looks_like_driver_list) but cannot by
    # themselves identify an asset: a MonoBehaviour of some other type with a
    # field merely NAMED like a channel and holding an empty list would
    # otherwise be claimed as a morph animation.
    if not has_pose and not any(entries for entries in curve_channels.values()):
        return None

    kind = "anim" if curve_channels else ("emotion" if "_microExpressionPoses" in data else "pose")
    asset = MorphAsset(guid, name, kind)
    asset.duration = _number(data.get("_duration"))

    for key, value in curve_channels.items():
        asset.channels[key] = _driver_list(value, key)
    if has_pose:
        for key, value in pose.items():
            if _VALUE_CHANNEL_RE.match(key) and _looks_like_driver_list(value):
                asset.channels.setdefault(key, []).extend(_driver_list(value, key))
            elif _FLAG_RE.match(key):
                asset.flags[key] = bool(_number(value))

    for key, value in data.items():
        if _FLAG_RE.match(key):
            asset.flags[key] = bool(_number(value))
        elif isinstance(value, dict) and _reference_guid(value):
            asset.references[key] = _reference_guid(value)
        elif isinstance(value, list) and value and isinstance(value[0], dict) and "guid" in value[0]:
            asset.references[key] = [_reference_guid(item) for item in value]

    if not asset.duration and asset.is_animated():
        asset.duration = max((driver.curve.last_time() for driver in asset.drivers()
                              if driver.curve is not None), default=0.0)
    return asset


def _parse_lipsync(asset, lipsync_map):
    """``_lipSyncMap`` is Unity's serialized dictionary: parallel ``_keyData``
    (a hex blob of little-endian int32 keys) and ``_valueData`` (one ``_poses``
    list per key). The keys are the phoneme-set ids an emotion selects with
    ``_phonemePoseLipsType``."""
    if not isinstance(lipsync_map, dict):
        return
    keys = _int32_blob(lipsync_map.get("_keyData"))
    values = lipsync_map.get("_valueData") or []
    for index, entry in enumerate(values):
        set_id = keys[index] if index < len(keys) else index
        poses = (entry or {}).get("_poses") or []
        asset.phoneme_sets[set_id] = [_reference_guid(ref) for ref in poses]


def _int32_blob(hex_text):
    """``0100000002000000`` -> [1, 2]. Unity writes serialized-dictionary key
    blobs as a flat hex string of little-endian int32s.

    An all-digit blob parses as a NUMBER on the way in (the YAML scalar
    converter cannot know it is meant as hex), which silently eats its leading
    zeros -- 0100000002000000 arrives as 100000002000000. Re-padding to the
    next multiple of 8 hex digits restores them exactly, because every entry is
    one int32 = 8 digits wide."""
    if not isinstance(hex_text, str):
        if isinstance(hex_text, int) and not isinstance(hex_text, bool):
            digits = "%d" % hex_text
            hex_text = digits.rjust(-(-len(digits) // 8) * 8, "0")
        else:
            return []
    stripped = "".join(hex_text.split())
    try:
        raw = bytes.fromhex(stripped)
    except ValueError:
        return []
    usable = len(raw) - (len(raw) % 4)
    return np.frombuffer(raw[:usable], dtype="<i4").tolist()


# ── the avatar: what a ctrl actually MOVES on one character ───────────────────
#
# SkeletalMorphAvatarDataSO. This is the table the whole system hangs off, and
# it only exists in an export at all when AssetRipper's [SerializeReference]
# support is enabled -- its polymorphic entries live in a ManagedReferencesRegistry,
# and AssetRipper drops the entire MonoBehaviour structure without it (a 700 KB
# asset arrives as a 416-byte shell, silently).
#
#   data.allBoneNames[boneID]         the bone a delta targets
#   data.morphMappingNames[i]         the ctrl name
#   data.morphMappingCfgs[i].rid   ->  references.RefIds[rid].data.bones
#                                     = that ctrl's per-bone TRS DELTA
#   data.basePoseConfig               each bone's base TRS, weight 0
#
# A face at weights w is therefore, per bone:
#     base + sum(w_i * delta_i)
# with rotation in Euler degrees and scale deltas defaulting to 0 (= unchanged).

AVATAR_MAPPING_CLASS = "SkeletalMorphMappingData"
# Cheap text sniff that tells an avatar apart from the animation/pose assets
# before paying for a YAML parse -- the field is unique to this schema.
AVATAR_MARKER = "morphMappingNames"


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


def _vector(value, default=0.0):
    if not isinstance(value, dict):
        return (default, default, default)
    return (_number(value.get("x", default)), _number(value.get("y", default)),
            _number(value.get("z", default)))


def bone_name_hash(name):
    """The identity the avatar keys its bones by: a SIGNED CRC32 of the bone
    name. Verified against pelica's table -- all 85 basePoseConfig hashes are
    exactly crc32 of the matching allBoneNames entry.

    ``boneID`` is NOT that identity and must not be used as one: it indexes some
    larger rig (values run past the end of allBoneNames), so treating it as an
    index silently resolves half the deltas to no bone at all."""
    return _to_signed32(zlib.crc32(name.encode("utf-8")))


def _to_signed32(value):
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def _bone_delta(entry, name_by_hash):
    bone_id = int(_number(entry.get("boneID", -1)))
    name = name_by_hash.get(int(_number(entry.get("boneNameHash", 0))), "")
    return MorphBoneDelta(bone_id, name,
                          _vector(entry.get("position")),
                          _vector(entry.get("rotation")),
                          _vector(entry.get("scale")))


def parse_avatar(data, guid="", name=""):
    """Parse a SkeletalMorphAvatarDataSO body, or None when it is not one (or
    when it arrived as an empty shell, which is what a missing
    [SerializeReference] pass produces -- caught here rather than surfacing as
    a character whose every ctrl silently does nothing)."""
    if not isinstance(data, dict):
        return None
    payload = data.get("data")
    if not isinstance(payload, dict) or "morphMappingNames" not in payload:
        return None

    tag = data.get("tag")
    tag_id = _number(tag.get("tagId", 0)) if isinstance(tag, dict) else 0
    avatar = MorphAvatar(guid, name or (data.get("m_Name") or ""), int(tag_id))
    avatar.bone_names = [str(n) for n in (payload.get("allBoneNames") or [])]
    name_by_hash = {bone_name_hash(name): name for name in avatar.bone_names}
    for entry in payload.get("basePoseConfig") or ():
        if isinstance(entry, dict):
            delta = _bone_delta(entry, name_by_hash)
            avatar.base_pose[delta.bone_id] = delta

    by_rid = {}
    references = data.get("references")
    for entry in (references or {}).get("RefIds") or ():
        if isinstance(entry, dict) and "rid" in entry:
            rid = _rid(entry.get("rid"))
            if rid is not None:
                by_rid[rid] = entry

    _bind_rid_list(avatar, payload.get("morphMappingNames"), payload.get("morphMappingCfgs"),
                   by_rid, name_by_hash)
    _bind_shader_params(avatar, payload.get("shaderParamNames"), payload.get("shaderParams"),
                        by_rid)
    return avatar


def _bind_rid_list(avatar, names, configs, by_rid, name_by_hash):
    """``morphMappingNames`` and ``morphMappingCfgs`` are PARALLEL arrays -- the
    Unity convention for a serialized dictionary split into keys and values.
    Each config is a [SerializeReference] handle resolved through RefIds."""
    names = list(names or ())
    configs = list(configs or ())
    for index, ctrl in enumerate(names):
        if index >= len(configs) or not isinstance(configs[index], dict):
            continue
        reference = by_rid.get(_rid(configs[index].get("rid")))
        payload = (reference or {}).get("data")
        if not isinstance(payload, dict):
            continue
        avatar.mappings[str(ctrl)] = [_bone_delta(bone, name_by_hash)
                                      for bone in payload.get("bones") or ()
                                      if isinstance(bone, dict)]


def _bind_shader_params(avatar, names, configs, by_rid):
    """The same parallel-array shape for the ctrls that drive a MATERIAL rather
    than geometry (a screen-faced robot's emotion atlas frame, for one)."""
    names = list(names or ())
    configs = list(configs or ())
    for index, ctrl in enumerate(names):
        if index >= len(configs) or not isinstance(configs[index], dict):
            continue
        reference = by_rid.get(_rid(configs[index].get("rid")))
        payload = (reference or {}).get("data")
        if not isinstance(payload, dict):
            continue
        avatar.shader_params[str(ctrl)] = {
            "param": str(payload.get("_paramName") or ""),
            "default": _number(payload.get("defaultValue")),
        }


def parse_avatar_document(document, guid=""):
    if document is None:
        return None
    return parse_avatar(getattr(document, "data", None), guid=guid)


def parse_document(document, guid=""):
    """Parse a ``unity_yaml.UnityDocument`` (a MonoBehaviour) -- the shape a
    host gets back from ``asset_db.load_guid(guid).first("MonoBehaviour")``."""
    if document is None:
        return None
    return parse(getattr(document, "data", None), guid=guid)


def kind_from_container_path(path):
    """The asset's kind as the GAME files it: the folder segment directly
    under the family root. ``.../skeletalmorphanim/emotion/x.asset`` -> the
    ``emotion`` bucket, ``.../skeletalmorphcfg/girl/x.asset`` -> ``cfg``.

    Used for the cheap, no-import discovery pass (the row table only knows
    container paths); ``parse`` re-derives the real kind from the fields once
    an asset is actually loaded. Returns "" for a path outside the family."""
    lowered = (path or "").lower()
    marker = lowered.find(VFS_MARKER)
    if marker < 0:
        return ""
    segments = [s for s in lowered[marker + len(VFS_MARKER):].split("/") if s]
    if len(segments) < 2:
        return ""
    root = segments[0]
    return "cfg" if root.endswith("cfg") else segments[1]


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
