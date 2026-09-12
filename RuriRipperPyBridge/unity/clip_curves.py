"""The one canonical in-memory form of a Unity AnimationClip's curves.

Two producers, each the single source for its mode, one consumer surface:

- ``ClipCurves.from_blob``  -- bridge mode. RipperBlenderBridge hands each
  exported clip across as a small JSON index plus ONE float32 payload (see
  Ruri.RipperHook's ClipCurveBlob.cs); every array below is a zero-parse
  ``numpy.frombuffer`` view of that payload.
- ``ClipCurves.from_yaml_text`` -- disk mode. The only clip parser there is:
  extracted straight off raw .anim YAML text (regexes + numpy's C-level
  string->float conversion, measured ~3x faster than the generic parser).
  Self-validating: any structural surprise raises ValueError -- it never
  returns a silently truncated clip.

Exactly one of these feeds every consumer (animation_builder's bake, the
humanoid muscle bake, path repair, humanoid detection) -- there is no third
parser and no fallback path to the generic YAML parser for clips.
"""

from __future__ import annotations

import json
import re

import numpy as np

_KIND_DIMENSIONS = {"pos": 3, "rot": 4, "scale": 3, "euler": 3, "float": 1}

# ── raw-text parser (disk .anim files) ──────────────────────────────────────
#
# A big humanoid clip is 80+MB of YAML whose bytes are ~99% keyframe numbers in
# a rigidly regular shape (grounded against real Unity/AssetRipper output, see
# from_yaml_text). Extracting them with compiled regexes + numpy's C-level
# string->float conversion skips the generic YAML parser's per-line python
# work entirely. Every entry self-checks its keyframe count and any mismatch
# raises ValueError -- this is the ONLY clip parser, so a Unity format change
# fails loudly instead of importing a silently wrong curve.

_NUMBER = r"([^\s,}]+)"
# A section runs until the NEXT top-level "  m_Xxx:" key -- list entries
# ("  - curve:") also sit at 2-space indent, so the terminator must match the
# key shape specifically, not just any non-space at column 2 (grounded against
# real Unity output: every AnimationClip top-level key is m_*).
_CURVE_SECTION = re.compile(
    r"^  (m_RotationCurves|m_PositionCurves|m_ScaleCurves|m_EulerCurves|m_FloatCurves):[^\n]*\n"
    r"(.*?)(?=^  m_\w|\Z)", re.M | re.S)
_SECTION_KINDS = {"m_RotationCurves": ("rot", 4), "m_PositionCurves": ("pos", 3),
                  "m_ScaleCurves": ("scale", 3), "m_EulerCurves": ("euler", 3),
                  "m_FloatCurves": ("float", 1)}
_ENTRY_SPLIT = re.compile(r"^  - curve:", re.M)
_KEYFRAME_COUNT = re.compile(r"- serializedVersion: \d+")


def _keyframe_pattern(dimensions):
    """One keyframe's time/value/inSlope/outSlope span as a SINGLE capture
    group -- 700k small strings instead of 9M per-component ones. The numbers
    come out of the joined groups in one C pass (see the caller): label
    tokens are stripped by _LABEL_SUB (word+colon or brace/comma -- a bare
    'e' inside '1e-05' has no colon and survives) and np.fromstring parses
    the remaining pure number stream."""
    if dimensions == 1:
        vector = r"[^\s,}]+"
    else:
        components = ("x", "y", "z", "w")[:dimensions]
        vector = r"\{" + ", ".join(f"{c}: [^\\s,}}]+" for c in components) + r"\}"
    return re.compile(
        r"- serializedVersion: \d+\n"
        r" +(time: [^\s,}]+\n"
        r" +value: " + vector + r"\n"
        r" +inSlope: " + vector + r"\n"
        r" +outSlope: " + vector + r")")


_LABEL_SUB = re.compile(r"[A-Za-z]+:|[{},]")
_NUMBER_TOKEN = re.compile(r"[^\s,{}:]+")


_KEYFRAME_PATTERNS = {d: _keyframe_pattern(d) for d in (1, 3, 4)}


def _entry_metadata_line(chunk, key):
    """Value of a 4-space-indented per-entry metadata line ("    path: ...")
    or None. These lines live at the very END of an entry chunk (after the
    whole m_Curve list), so an anchored-regex forward search would rescan
    the entire multi-hundred-KB chunk per entry -- rfind starts at the end
    and lands immediately (measured: this was 3.2s of an 8.6s parse)."""
    marker = "\n    " + key + ":"
    start = chunk.rfind(marker)
    if start < 0:
        return None
    start += len(marker)
    end = chunk.find("\n", start)
    if end < 0:
        end = len(chunk)
    return chunk[start:end].strip()
def _scalar_line(text, marker, start=0, end=None):
    """Value text of the first "<marker> value" line at/after `start` -- plain
    str.find (memchr-fast) instead of a multiline regex scan: several of these
    scalars live BEHIND tens of MB of curve data, and seven anchored regex
    searches over the full text measured at 5.2s on the battle clip."""
    position = text.find(marker, start, end)
    if position < 0:
        return None
    position += len(marker)
    line_end = text.find("\n", position)
    if line_end < 0:
        line_end = len(text)
    return text[position:line_end].strip()


def _unquote(scalar):
    scalar = scalar.strip()
    if len(scalar) >= 2 and scalar[0] == "'" and scalar[-1] == "'":
        return scalar[1:-1].replace("''", "'")
    return scalar


class Channel:
    """One curve: a path (transform curves) or path+attribute (float curves),
    with (k,) times and (k, d) values/slopes, times ascending."""

    __slots__ = ("path", "attribute", "class_id", "times", "values", "in_slopes", "out_slopes")

    def __init__(self, path, times, values, in_slopes, out_slopes, attribute="", class_id=0):
        self.path = path
        self.attribute = attribute
        self.class_id = class_id
        self.times = times
        self.values = values
        self.in_slopes = in_slopes
        self.out_slopes = out_slopes

    def sample(self, sample_times):
        """Vectorized cubic-Hermite evaluation at every entry of the (n,)
        ``sample_times`` array -> (n, d). Exactly the scalar evaluator's
        semantics, per component: clamp to the first/last key value outside
        the key range, a zero-length segment returns its left key, slopes
        scale by the segment length (m0 = outSlope[i]*dt, m1 = inSlope[i+1]*dt)."""
        times = self.times
        key_count = len(times)
        n = len(sample_times)
        dimensions = self.values.shape[1]
        if key_count == 0:
            return np.zeros((n, dimensions), dtype=np.float64)
        if key_count == 1:
            return np.repeat(self.values[:1], n, axis=0)

        segment = np.clip(np.searchsorted(times, sample_times) - 1, 0, key_count - 2)
        t0 = times[segment]
        dt = times[segment + 1] - t0
        degenerate = dt <= 1e-9
        u = (sample_times - t0) / np.where(degenerate, 1.0, dt)
        u2 = u * u
        u3 = u2 * u
        h00 = 2.0 * u3 - 3.0 * u2 + 1.0
        h10 = u3 - 2.0 * u2 + u
        h01 = -2.0 * u3 + 3.0 * u2
        h11 = u3 - u2

        v0 = self.values[segment]
        v1 = self.values[segment + 1]
        m0 = self.out_slopes[segment] * dt[:, None]
        m1 = self.in_slopes[segment + 1] * dt[:, None]
        out = (h00[:, None] * v0 + h10[:, None] * m0
               + h01[:, None] * v1 + h11[:, None] * m1)

        if degenerate.any():
            out[degenerate] = v0[degenerate]
        low = sample_times <= times[0]
        if low.any():
            out[low] = self.values[0]
        high = sample_times >= times[-1]
        if high.any():
            out[high] = self.values[-1]
        return out

    def last_time(self):
        return float(self.times[-1]) if len(self.times) else 0.0


class ClipCurves:
    """A whole clip: identity/settings scalars plus per-kind Channel lists."""

    __slots__ = ("name", "sample_rate", "start_time", "stop_time",
                 "keep_position_xz", "keep_position_y", "keep_orientation",
                 "rotations", "positions", "scales", "eulers", "floats")

    def __init__(self):
        self.name = "Clip"
        self.sample_rate = 60.0
        self.start_time = 0.0
        self.stop_time = 0.0
        self.keep_position_xz = True
        self.keep_position_y = True
        self.keep_orientation = True
        self.rotations = []
        self.positions = []
        self.scales = []
        self.eulers = []
        self.floats = []

    def transform_channel_lists(self):
        return (self.rotations, self.positions, self.scales, self.eulers)

    def all_channel_lists(self):
        return (self.rotations, self.positions, self.scales, self.eulers, self.floats)

    def max_time(self):
        latest = 0.0
        for channels in self.all_channel_lists():
            for channel in channels:
                latest = max(latest, channel.last_time())
        return latest

    # ── producers ────────────────────────────────────────────────────────────

    @classmethod
    def from_blob(cls, meta_json, payload_bytes):
        """Bridge fast path: wrap the float32 payload without parsing anything.
        Slopes/values stay float32 views into ONE buffer; sample() upcasts
        per-segment gathers to float64 during the arithmetic."""
        meta = json.loads(meta_json)
        payload = np.frombuffer(payload_bytes, dtype="<f4")

        clip = cls()
        clip.name = meta.get("name") or "Clip"
        clip.sample_rate = float(meta.get("sampleRate") or 60.0)
        clip.start_time = float(meta.get("startTime") or 0.0)
        clip.stop_time = float(meta.get("stopTime") or 0.0)
        clip.keep_position_xz = bool(meta.get("keepPositionXZ", True))
        clip.keep_position_y = bool(meta.get("keepPositionY", True))
        clip.keep_orientation = bool(meta.get("keepOrientation", True))

        target = {"rot": clip.rotations, "pos": clip.positions, "scale": clip.scales,
                  "euler": clip.eulers, "float": clip.floats}
        for entry in meta["curves"]:
            kind = entry["kind"]
            dimensions = _KIND_DIMENSIONS[kind]
            key_count = entry["keys"]
            offset = entry["off"]
            times = payload[offset:offset + key_count].astype(np.float64)
            cursor = offset + key_count
            span = key_count * dimensions
            values = payload[cursor:cursor + span].reshape(key_count, dimensions)
            cursor += span
            in_slopes = payload[cursor:cursor + span].reshape(key_count, dimensions)
            cursor += span
            out_slopes = payload[cursor:cursor + span].reshape(key_count, dimensions)

            # The exporter writes keys in time order; only pay a sort when a
            # clip genuinely violates that (parity with the YAML ingester's
            # unconditional argsort).
            if key_count > 1 and np.any(np.diff(times) < 0.0):
                order = np.argsort(times, kind="stable")
                times = times[order]
                values = values[order]
                in_slopes = in_slopes[order]
                out_slopes = out_slopes[order]

            target[kind].append(Channel(entry.get("path") or "", times, values,
                                        in_slopes, out_slopes,
                                        attribute=entry.get("attr") or "",
                                        class_id=int(entry.get("classId") or 0)))
        return clip

    @classmethod
    def from_yaml_text(cls, text):
        """Disk mode: extract the curves straight out of raw AnimationClip
        YAML text with compiled regexes + numpy string->float conversion --
        the generic YAML parser spends seconds building per-key dicts this
        never touches. This is the only clip parser; it raises ValueError on
        ANY structural surprise (keyframe count mismatch, unexpected section
        shape, no AnimationClip header) rather than return a silently
        truncated or misaligned clip."""
        if "AnimationClip:" not in text:
            raise ValueError("not an AnimationClip document")

        clip = cls()
        name_value = _scalar_line(text, "\n  m_Name:")
        if name_value is not None:
            clip.name = _unquote(name_value) or "Clip"
        rate_value = _scalar_line(text, "\n  m_SampleRate:")
        if rate_value is not None:
            clip.sample_rate = float(rate_value) or 60.0
        # The settings block sits BEHIND the curve data; anchor once and read
        # its keys from the local slice.
        settings_at = text.find("\n  m_AnimationClipSettings:")
        if settings_at >= 0:
            settings_end = min(len(text), settings_at + 4096)
            start_value = _scalar_line(text, "m_StartTime:", settings_at, settings_end)
            if start_value is not None:
                clip.start_time = float(start_value)
            stop_value = _scalar_line(text, "m_StopTime:", settings_at, settings_end)
            if stop_value is not None:
                clip.stop_time = float(stop_value)
            orient_value = _scalar_line(text, "m_KeepOriginalOrientation:", settings_at, settings_end)
            if orient_value is not None and orient_value.isdigit():
                clip.keep_orientation = bool(int(orient_value))
            y_value = _scalar_line(text, "m_KeepOriginalPositionY:", settings_at, settings_end)
            if y_value is not None and y_value.isdigit():
                clip.keep_position_y = bool(int(y_value))
            xz_value = _scalar_line(text, "m_KeepOriginalPositionXZ:", settings_at, settings_end)
            if xz_value is not None and xz_value.isdigit():
                clip.keep_position_xz = bool(int(xz_value))

        target = {"rot": clip.rotations, "pos": clip.positions, "scale": clip.scales,
                  "euler": clip.eulers, "float": clip.floats}
        section_count = 0
        for section in _CURVE_SECTION.finditer(text):
            section_count += 1
            kind, dimensions = _SECTION_KINDS[section.group(1)]
            pattern = _KEYFRAME_PATTERNS[dimensions]
            for chunk in _ENTRY_SPLIT.split(section.group(2))[1:]:
                expected = chunk.count("- serializedVersion:")
                matches = pattern.findall(chunk)
                if len(matches) != expected:
                    raise ValueError(
                        f"{kind} entry keyframe mismatch: matched {len(matches)} of {expected}")
                if matches:
                    columns = 1 + 3 * dimensions
                    joined = " ".join(matches)
                    if "Infinity" in joined or "NaN" in joined:
                        # np.fromstring's sep-parser stops at non-numeric
                        # tokens; route the rare stepped-tangent clip through
                        # the per-token path (strtod handles both spellings).
                        tokens = _NUMBER_TOKEN.findall(_LABEL_SUB.sub(" ", joined))
                        raw = np.array(tokens, dtype=np.float64).reshape(len(matches), columns)
                    else:
                        raw = np.fromstring(_LABEL_SUB.sub(" ", joined),
                                            dtype=np.float64, sep=" ")
                        if raw.size != len(matches) * columns:
                            raise ValueError(
                                f"{kind} numeric stream size {raw.size} != "
                                f"{len(matches)}x{columns}")
                        raw = raw.reshape(len(matches), columns)
                    times = raw[:, 0]
                    values = raw[:, 1:1 + dimensions]
                    in_slopes = raw[:, 1 + dimensions:1 + 2 * dimensions]
                    out_slopes = raw[:, 1 + 2 * dimensions:1 + 3 * dimensions]
                    if len(times) > 1:
                        order = np.argsort(times, kind="stable")
                        if np.any(order != np.arange(len(times))):
                            times = times[order]
                            values = values[order]
                            in_slopes = in_slopes[order]
                            out_slopes = out_slopes[order]
                else:
                    times = np.empty(0, dtype=np.float64)
                    values = np.zeros((0, dimensions), dtype=np.float64)
                    in_slopes = values
                    out_slopes = values
                path_value = _entry_metadata_line(chunk, "path")
                channel = Channel(_unquote(path_value) if path_value is not None else "",
                                  times, values, in_slopes, out_slopes)
                if kind == "float":
                    attribute_value = _entry_metadata_line(chunk, "attribute")
                    if attribute_value is not None:
                        channel.attribute = _unquote(attribute_value)
                    class_value = _entry_metadata_line(chunk, "classID")
                    if class_value is not None and class_value.isdigit():
                        channel.class_id = int(class_value)
                target[kind].append(channel)
        if section_count == 0:
            # Every real AnimationClip document emits all five m_*Curves keys
            # at a fixed 2-space indent (see _CURVE_SECTION) -- zero matches
            # means the indent/key layout moved, not that the clip is empty.
            # Fail loud instead of returning a clip that silently has no curves.
            raise ValueError("no m_*Curves sections matched -- indentation or key layout changed")
        return clip


# ── humanoid solve wire form ─────────────────────────────────────────────────
#
# The muscle solve lives on the C# side (RipperBlenderBridge.SolveHumanoidClip --
# the Animator itself, as a call). What crosses is the SAME curve-blob layout as
# from_blob, in both directions: float channels out, solved transform curves
# back. These two functions are the Python half of that wire.

def humanoid_float_blob(clip):
    """The clip's float channels (blendshape channels excluded -- never muscle
    data, routinely thousands of curves) as a (meta_json, payload_bytes) curve
    blob for the C# humanoid solve. Which of them ARE muscle channels is the
    solver's own knowledge; everything else rides along and is ignored there."""
    entries = []
    parts = []
    offset = 0
    for channel in clip.floats:
        attribute = channel.attribute or ""
        if attribute.startswith("blendShape."):
            continue
        key_count = int(len(channel.times))
        if key_count == 0:
            continue
        entries.append({"kind": "float", "path": channel.path or "", "attr": attribute,
                        "classId": int(channel.class_id), "keys": key_count, "off": offset})
        parts.extend((np.ascontiguousarray(channel.times, dtype=np.float32),
                      np.ascontiguousarray(channel.values, dtype=np.float32).reshape(-1),
                      np.ascontiguousarray(channel.in_slopes, dtype=np.float32).reshape(-1),
                      np.ascontiguousarray(channel.out_slopes, dtype=np.float32).reshape(-1)))
        offset += 4 * key_count
    meta = {"name": clip.name, "sampleRate": clip.sample_rate,
            "startTime": clip.start_time, "stopTime": clip.stop_time,
            "keepPositionXZ": clip.keep_position_xz, "keepPositionY": clip.keep_position_y,
            "keepOrientation": clip.keep_orientation, "curves": entries}
    payload = np.concatenate(parts) if parts else np.empty(0, dtype=np.float32)
    return json.dumps(meta, separators=(",", ":")), payload.tobytes()


def merge_solved(clip, solved, consumed_attributes):
    """Fold a solve result back into ``clip``, in place, with the exact
    semantics the C# in-place converter applies to a typed clip: a solved curve
    OWNS its path (any existing rotation/position curve on that path is
    replaced -- muscle curves are the humanoid bones' authoritative encoding),
    and the float channels the solve consumed are dropped."""
    consumed = set(consumed_attributes)
    clip.floats = [channel for channel in clip.floats if channel.attribute not in consumed]
    for existing, incoming in ((clip.rotations, solved.rotations),
                               (clip.positions, solved.positions)):
        solved_paths = {channel.path for channel in incoming}
        existing[:] = [channel for channel in existing if channel.path not in solved_paths]
        existing.extend(incoming)
