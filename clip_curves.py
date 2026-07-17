"""The one canonical in-memory form of a Unity AnimationClip's curves.

Two producers, one consumer surface:

- ``ClipCurves.from_blob``  -- the bridge fast path. RipperBlenderBridge hands
  each exported clip across as a small JSON index plus ONE float32 payload
  (see Ruri.RipperHook's ClipCurveBlob.cs); every array below is a zero-parse
  ``numpy.frombuffer`` view of that payload. This replaces re-parsing the same
  numbers out of 80+MB of YAML text (measured 15.5s for one battle clip).
- ``ClipCurves.from_document`` -- the YAML path (disk-mode .anim files, or a
  bridge clip whose blob build failed), ingesting the parsed key dicts in one
  tight loop per curve.

Everything downstream (animation_builder's bake, the humanoid muscle bake,
path repair, humanoid detection) consumes Channels and their vectorized
``sample`` -- nobody walks YAML key dicts per frame anymore.
"""

from __future__ import annotations

import json
import re

import numpy as np

_KIND_DIMENSIONS = {"pos": 3, "rot": 4, "scale": 3, "euler": 3, "float": 1}

# ── raw-text fast path (disk .anim files) ─────────────────────────────────────
#
# A big humanoid clip is 80+MB of YAML whose bytes are ~99% keyframe numbers in
# a rigidly regular shape (grounded against real Unity/AssetRipper output, see
# from_yaml_text). Extracting them with compiled regexes + numpy's C-level
# string->float conversion skips the generic YAML parser's per-line python
# work entirely; every entry self-checks its keyframe count and any mismatch
# raises, so callers fall back to the full parser rather than import a
# silently truncated curve.

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
        """Disk fast path: extract the curves straight out of raw AnimationClip
        YAML text with compiled regexes + numpy string->float conversion --
        the generic YAML parser spends seconds building per-key dicts this
        never touches. Raises ValueError on ANY structural surprise (keyframe
        count mismatch, no AnimationClip header) so the caller falls back to
        the full parser; it never returns a silently truncated clip."""
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
        for section in _CURVE_SECTION.finditer(text):
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
        return clip

    @classmethod
    def from_document(cls, data):
        """YAML path: one tight ingestion loop per curve over the parsed key
        dicts (the m_Curve entries), straight into arrays."""
        clip = cls()
        clip.name = data.get("m_Name") or "Clip"
        clip.sample_rate = float(data.get("m_SampleRate") or 60.0)
        settings = data.get("m_AnimationClipSettings") or {}
        clip.start_time = float(settings.get("m_StartTime") or 0.0)
        clip.stop_time = float(settings.get("m_StopTime") or 0.0)
        clip.keep_position_xz = bool(settings.get("m_KeepOriginalPositionXZ", True))
        clip.keep_position_y = bool(settings.get("m_KeepOriginalPositionY", True))
        clip.keep_orientation = bool(settings.get("m_KeepOriginalOrientation", True))

        for field, target, components in (
                ("m_RotationCurves", clip.rotations, ("x", "y", "z", "w")),
                ("m_PositionCurves", clip.positions, ("x", "y", "z")),
                ("m_ScaleCurves", clip.scales, ("x", "y", "z")),
                ("m_EulerCurves", clip.eulers, ("x", "y", "z"))):
            for entry in data.get(field) or []:
                target.append(_channel_from_entry(entry, components))
        for entry in data.get("m_FloatCurves") or []:
            channel = _channel_from_entry(entry, ("v",))
            channel.attribute = entry.get("attribute") or ""
            raw_class = entry.get("classID")
            channel.class_id = int(raw_class) if isinstance(raw_class, (int, float)) else 0
            clip.floats.append(channel)
        return clip


def _channel_from_entry(entry, components):
    """One m_Curve key-dict list -> a Channel, single pass. Semantics match
    the legacy per-key reader exactly: dict values read per component with 0.0
    defaults, scalar values land in component 0, non-numeric slopes are 0.0,
    keys sorted by time."""
    keys = (entry.get("curve") or {}).get("m_Curve") or []
    key_count = len(keys)
    dimensions = len(components)
    times = np.empty(key_count, dtype=np.float64)
    values = np.zeros((key_count, dimensions), dtype=np.float64)
    in_slopes = np.zeros((key_count, dimensions), dtype=np.float64)
    out_slopes = np.zeros((key_count, dimensions), dtype=np.float64)

    for i, key in enumerate(keys):
        times[i] = key.get("time", 0.0) or 0.0
        value = key.get("value")
        in_slope = key.get("inSlope")
        out_slope = key.get("outSlope")
        if isinstance(value, dict):
            in_is_dict = isinstance(in_slope, dict)
            out_is_dict = isinstance(out_slope, dict)
            for j, component in enumerate(components):
                values[i, j] = value.get(component, 0.0) or 0.0
                if in_is_dict:
                    in_slopes[i, j] = in_slope.get(component, 0.0) or 0.0
                if out_is_dict:
                    out_slopes[i, j] = out_slope.get(component, 0.0) or 0.0
        else:
            values[i, 0] = value or 0.0
            if isinstance(in_slope, (int, float)):
                in_slopes[i, 0] = in_slope
            if isinstance(out_slope, (int, float)):
                out_slopes[i, 0] = out_slope

    if key_count > 1:
        order = np.argsort(times, kind="stable")
        if np.any(order != np.arange(key_count)):
            times = times[order]
            values = values[order]
            in_slopes = in_slopes[order]
            out_slopes = out_slopes[order]

    return Channel(entry.get("path") or "", times, values, in_slopes, out_slopes)
