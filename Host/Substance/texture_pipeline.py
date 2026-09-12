# -*- coding: utf-8 -*-
"""Execute a MaterialPlan's texture jobs: decode the source image, split/rebuild
the channels Painter needs, and write the results into the project cache.

Decoding and encoding both go through Qt's own image codecs (PySide6 ships with
Painter, so this adds no dependency and runs at C++ speed); the per-pixel maths
in between is numpy. Nothing here shells out, and nothing depends on Pillow.

The channel semantics are ported from BuildSPInputs.py unchanged -- same
splits, same Unity normal decode, same Smoothness->Roughness inversion -- so
the EndField_Uber shader consuming the results sees byte-identical inputs to
the old offline pipeline.
"""

from __future__ import annotations

import json
import os

import numpy as np

from PySide6 import QtCore, QtGui

from . import unity_material


# ---------------------------------------------------------------------------
# Qt <-> numpy
# ---------------------------------------------------------------------------
def _qimage_to_rgba(image):
    """QImage -> (h, w, 4) uint8, honouring the image's own scanline padding
    (bytesPerLine is NOT guaranteed to be width*4)."""
    image = image.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    width, height = image.width(), image.height()
    stride = image.bytesPerLine()
    buffer = bytes(image.constBits())[:stride * height]
    rows = np.frombuffer(buffer, dtype=np.uint8).reshape(height, stride)
    return np.ascontiguousarray(rows[:, :width * 4].reshape(height, width, 4))


def decode_image(data_or_path):
    """Decode raw image bytes or a file path into an (h, w, 4) uint8 array."""
    image = QtGui.QImage()
    if isinstance(data_or_path, (bytes, bytearray, memoryview)):
        ok = image.loadFromData(QtCore.QByteArray(bytes(data_or_path)))
    else:
        ok = image.load(str(data_or_path))
    if not ok or image.isNull():
        return None
    return _qimage_to_rgba(image)


def _save(array, path):
    """Write an (h, w) / (h, w, 3) / (h, w, 4) uint8 array as PNG."""
    array = np.ascontiguousarray(array, dtype=np.uint8)
    height, width = array.shape[0], array.shape[1]
    if array.ndim == 2:
        payload = array.tobytes()
        image = QtGui.QImage(payload, width, height, width,
                             QtGui.QImage.Format.Format_Grayscale8)
    elif array.shape[2] == 3:
        payload = array.tobytes()
        image = QtGui.QImage(payload, width, height, width * 3,
                             QtGui.QImage.Format.Format_RGB888)
    else:
        payload = array.tobytes()
        image = QtGui.QImage(payload, width, height, width * 4,
                             QtGui.QImage.Format.Format_RGBA8888)
    # QImage does not copy the buffer it was constructed over, and `payload`
    # dies with this frame -- copy() detaches into Qt-owned storage before the
    # (asynchronous-free but still post-return) save touches it.
    image = image.copy()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return bool(image.save(path, "PNG"))


# ---------------------------------------------------------------------------
# Decode operations (ported from BuildSPInputs)
# ---------------------------------------------------------------------------
def reconstruct_normal(rgba, flip_green=False, use_ag=True, channels=(0, 1)):
    """Unity stores tangent-space normals in two channels and drops Z, which is
    why such a texture looks yellow/olive; Painter needs a full RGB normal.

    Unity decode (identical to EndField_Uber.glsl):
        X = R * A     (UnpackNormalmapRGorAG: packednormal.x *= packednormal.w)
        Y = G
        Z = sqrt(1 - X^2 - Y^2)
    Output: R = X, G = Y, B = (Z + 1) / 2   (flat = 128,128,255).

    ``channels`` picks which two source channels hold X and Y -- (0, 1) for a
    normal Unity RG/AG map, (2, 3) for the specular half of a hair
    _SplitNormalMap. ``use_ag`` must be False for the split map: its alpha is
    the spec normal's Y, never an AG multiplier.
    """
    data = rgba.astype(np.float32) / 255.0
    x_raw = data[..., channels[0]]
    y_raw = data[..., channels[1]]
    alpha = data[..., 3] if rgba.shape[2] >= 4 else np.ones_like(x_raw)

    x = x_raw * alpha if use_ag else x_raw
    y = (1.0 - y_raw) if flip_green else y_raw

    xn = x * 2.0 - 1.0
    yn = y * 2.0 - 1.0
    zn = np.sqrt(np.clip(1.0 - (xn * xn + yn * yn), 0.0, 1.0))

    out = np.empty(rgba.shape[:2] + (3,), np.float32)
    out[..., 0] = x
    out[..., 1] = y
    out[..., 2] = (zn + 1.0) * 0.5
    return np.clip(out * 255.0 + 0.5, 0.0, 255.0).astype(np.uint8)


def normal_already_standard(rgba):
    """A non-empty blue channel means the map is already a standard OpenGL
    normal (some exports ship pre-decoded) -- copy it rather than decoding a
    second time."""
    if rgba.shape[2] < 3:
        return False
    blue = rgba[..., 2].astype(np.float32)
    return bool(blue.std() > 8.0 and blue.mean() > 100.0)


_OPS = {
    unity_material.OP_COPY_RGB:   lambda a: a[..., :3],
    unity_material.OP_COPY_RGBA:  lambda a: a,
    unity_material.OP_CHANNEL_R:  lambda a: a[..., 0],
    unity_material.OP_CHANNEL_G:  lambda a: a[..., 1],
    unity_material.OP_CHANNEL_B:  lambda a: a[..., 2],
    unity_material.OP_CHANNEL_A:  lambda a: a[..., 3],
    # Roughness = 1 - Smoothness (Unity keeps smoothness in alpha).
    unity_material.OP_INVERT_A:   lambda a: (255 - a[..., 3].astype(np.int16)).astype(np.uint8),
}


_CHANNEL_INDEX = {"r": 0, "g": 1, "b": 2, "a": 3}


def apply_op(op, rgba, flip_green=False):
    simple = _OPS.get(op)
    if simple is not None:
        return np.ascontiguousarray(simple(rgba))
    if op == unity_material.OP_NORMAL_UNITY:
        if normal_already_standard(rgba):
            return np.ascontiguousarray(rgba[..., :3])
        return reconstruct_normal(rgba, flip_green=flip_green, use_ag=True, channels=(0, 1))
    if op == unity_material.OP_NORMAL_SPLIT_RG:
        return reconstruct_normal(rgba, flip_green=flip_green, use_ag=False, channels=(0, 1))
    if op == unity_material.OP_NORMAL_SPLIT_BA:
        return reconstruct_normal(rgba, flip_green=flip_green, use_ag=False, channels=(2, 3))
    # Manifest-driven segment ops: "extract:<channels>" packs the named source
    # channels, in order, into the output's first components; "invert:<channels>"
    # is the same extract with 255-x applied. The channel letters come from the
    # generator's projection manifest, so the vocabulary lives in one place.
    for prefix, invert in (("extract:", False), ("invert:", True)):
        if op.startswith(prefix):
            picks = [_CHANNEL_INDEX[c] for c in op[len(prefix):]]
            data = rgba[..., picks]
            if invert:
                data = (255 - data.astype(np.int16)).astype(np.uint8)
            if data.shape[2] == 2:
                pad = np.zeros(data.shape[:2] + (1,), np.uint8)
                data = np.concatenate([data, pad], axis=2)
            return np.ascontiguousarray(data[..., 0] if data.shape[2] == 1 else data)
    raise ValueError("unknown texture op: {0}".format(op))


# ---------------------------------------------------------------------------
# Source resolution + execution
# ---------------------------------------------------------------------------
class TextureBaker:
    """Runs texture jobs against one asset database, writing into one folder.

    Decoded source images and produced files are both memoized: a texture bound
    by several materials is decoded once, and an identical (source, operation)
    pair is written once and shared -- Painter imports the file by path, so the
    same file simply becomes the same project resource.
    """

    INDEX_NAME = "_index.json"
    _VERSION_KEY = "__bake_version__"

    def __init__(self, db, out_dir, flip_green=False, force=False):
        self.db = db
        self.out_dir = out_dir
        self.flip_green = flip_green
        self.force = force
        self._source_cache = {}
        self._output_cache = {}
        self.warnings = []
        self.written = 0
        self.reused = 0
        if not force:
            self._load_index()

    def _index_path(self):
        return os.path.join(self.out_dir, self.INDEX_NAME)

    def _load_index(self):
        """Restore the (source texture, operation) -> file mapping from the last
        run. Without it a re-import is not deterministic: a texture shared by
        several materials gets written under whichever material happened to
        claim it first, so a second run would bake fresh near-duplicates under
        the OTHER materials' names and Painter's shelf would slowly fill with
        copies of the same ramp."""
        try:
            with open(self._index_path(), "r", encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return
        if not isinstance(stored, dict):
            return
        # A bake-rules change must invalidate what is on disk. The cheap reuse
        # path below matches on FILE NAME (<material>__<target>.png), which
        # carries no version, so without this a rules change is silently masked
        # by stale files and looks like the fix did not work.
        if stored.get(self._VERSION_KEY) != unity_material.TextureJob.BAKE_VERSION:
            self.force = True
            return
        for key, path in stored.items():
            if key == self._VERSION_KEY:
                continue
            if isinstance(path, str) and os.path.isfile(path):
                self._output_cache[key] = path

    def flush(self):
        """Persist the mapping. Call once after every material has been run."""
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            payload = dict(self._output_cache)
            payload[self._VERSION_KEY] = unity_material.TextureJob.BAKE_VERSION
            with open(self._index_path(), "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=1, ensure_ascii=False)
        except OSError:
            pass

    def _source(self, guid):
        if guid in self._source_cache:
            return self._source_cache[guid]

        # Two places an image can come from, and BOTH are tried: bridge mode
        # hands over the asset's own exported bytes in whatever container
        # AssetRipper chose (png/tga/exr/...), while a disk database resolves the
        # guid to a file. Trying them in turn rather than stopping at the first
        # that returns SOMETHING matters, because the bridge answers for every
        # guid -- an asset that is not an exported image comes back as its YAML
        # (or shader source) bytes, which decode to nothing. Stopping there left
        # the file on disk unread and reported the texture as undecodable.
        attempts = []
        texture_bytes = getattr(self.db, "texture_bytes", None)
        if callable(texture_bytes):
            attempts.append(("bridge", texture_bytes(guid)))
        path = self.db.resolve_guid(guid)
        if path and os.path.isfile(path):
            attempts.append(("file", path))

        rgba = None
        for _origin, payload in attempts:
            if payload is None:
                continue
            rgba = decode_image(payload)
            if rgba is not None:
                break
        if rgba is None:
            # Name the texture the way the game does; the guid is only who it is
            # when the closure genuinely has no path for it.
            asset_name = getattr(self.db, "asset_name", None)
            label = (asset_name(guid) if callable(asset_name) else None) or guid[:8]
            self.warnings.append("texture {0} could not be decoded ({1})".format(
                label, _describe_sources(attempts) or "no source at all"))
        self._source_cache[guid] = rgba
        return rgba

    def run(self, material_name, jobs):
        """Execute one material's jobs. Returns {target: absolute path} for
        whichever jobs produced a file."""
        produced = {}
        for job in jobs:
            key = job.cache_key()
            cached = self._output_cache.get(key)
            if cached is not None:
                produced[job.target] = cached
                self.reused += 1
                continue
            path = os.path.join(self.out_dir,
                                "{0}__{1}.png".format(_safe(material_name), _safe(job.target)))
            if not self.force and os.path.isfile(path):
                self._output_cache[key] = path
                produced[job.target] = path
                self.reused += 1
                continue
            rgba = self._source(job.guid)
            if rgba is None:
                continue
            try:
                array = apply_op(job.op, rgba, flip_green=self.flip_green)
            except Exception as exc:
                self.warnings.append("{0}.{1}: {2}".format(material_name, job.target, exc))
                continue
            if not _save(array, path):
                self.warnings.append("{0}.{1}: could not write {2}".format(
                    material_name, job.target, path))
                continue
            self._output_cache[key] = path
            produced[job.target] = path
            self.written += 1
        return produced


# Container signatures, so a texture that fails to decode says WHAT it was
# rather than only that it failed. Qt reads png/jpg/bmp/gif/tga; a payload that
# turns out to be YAML or shader source is an asset that was never an image,
# and a dds/ktx one is a container Qt has no codec for -- different problems
# with different fixes, and the message now tells them apart.
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"DDS ", "dds"),
    (b"\xabKTX", "ktx"),
    (b"v/1\x01", "exr"),
    (b"%YAML", "yaml, not an image"),
)


def _describe_sources(attempts):
    """One phrase per source that was tried, naming what it actually held."""
    parts = []
    for origin, payload in attempts:
        if payload is None:
            parts.append("{0}: nothing".format(origin))
            continue
        if isinstance(payload, str):
            parts.append("{0}: {1}".format(origin, os.path.basename(payload)))
            continue
        head = bytes(payload[:8])
        kind = next((name for magic, name in _SIGNATURES if head.startswith(magic)),
                    "unknown 0x" + head[:4].hex())
        parts.append("{0}: {1} bytes of {2}".format(origin, len(payload), kind))
    return "; ".join(parts)


_UNSAFE = set('<>:"/\\|?*')


def _safe(name):
    return "".join("_" if c in _UNSAFE or ord(c) < 32 else c for c in str(name)).strip() or "unnamed"
