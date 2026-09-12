"""Project-aware asset resolution for Unity imports.

Unity references assets across files by ``{fileID, guid, type}``.  The guid is
declared in the sibling ``.meta`` file of the target asset.  This module:

* finds the project's ``Assets`` directory by walking up from an asset path,
* builds a guid -> asset-path index by scanning ``.meta`` files (lazily, and
  scoped so large projects do not pay for a full scan unless a reference misses),
* caches parsed Unity files so each asset is parsed at most once.
"""

from __future__ import annotations

import os
import re

from . import unity_yaml

_SRGB_PATTERN = re.compile(r"sRGBTexture:\s*(\d+)")

_GUID_RE = re.compile(r"^guid:\s*([0-9a-fA-F]{32})\s*$")


def find_assets_dir(start_path):
    """Return the nearest enclosing ``Assets`` directory, or None."""
    path = os.path.abspath(start_path)
    if os.path.isfile(path):
        path = os.path.dirname(path)
    while True:
        if os.path.basename(path) == "Assets" and os.path.isdir(path):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        # Also accept a child Assets dir (project root passed in).
        candidate = os.path.join(path, "Assets")
        if os.path.isdir(candidate):
            return candidate
        path = parent


class AssetDatabase:
    """Resolves guids to paths and caches parsed Unity files."""

    def __init__(self, primary_dir, assets_dir=None):
        self.primary_dir = os.path.abspath(primary_dir)
        self.assets_dir = os.path.abspath(assets_dir) if assets_dir else None
        self._guid_to_path = {}
        self._scanned_dirs = set()
        self._file_cache = {}
        self._texture_srgb = {}
        # The folder containing the imported asset is always cheap to scan.
        self._scan_dir(self.primary_dir)

    # -- guid index ----------------------------------------------------------

    def _scan_dir(self, directory):
        directory = os.path.abspath(directory)
        if directory in self._scanned_dirs or not os.path.isdir(directory):
            return
        self._scanned_dirs.add(directory)
        for dirpath, _dirnames, filenames in os.walk(directory):
            for name in filenames:
                if not name.endswith(".meta"):
                    continue
                meta_path = os.path.join(dirpath, name)
                guid = self._read_meta_guid(meta_path)
                if guid and guid not in self._guid_to_path:
                    self._guid_to_path[guid] = meta_path[:-5]  # strip ".meta"

    @staticmethod
    def _read_meta_guid(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8", errors="replace") as handle:
                for _ in range(12):
                    line = handle.readline()
                    if not line:
                        break
                    m = _GUID_RE.match(line.strip())
                    if m:
                        return m.group(1).lower()
        except OSError:
            return None
        return None

    def resolve_guid(self, guid):
        """Return the asset path for a guid, scanning wider on a miss."""
        if not guid:
            return None
        guid = guid.lower()
        path = self._guid_to_path.get(guid)
        if path:
            return path
        # Miss: escalate the scan to the full Assets tree once.
        if self.assets_dir and self.assets_dir not in self._scanned_dirs:
            self._scan_dir(self.assets_dir)
            return self._guid_to_path.get(guid)
        return None

    def raw_text(self, guid):
        """Unparsed text for a guid, the same question the closure-backed
        database answers -- so a caller that only wants to peek at a document
        (a shader asset's ``Shader "..."`` head line, say) works the same
        whichever input source is feeding it. None for a non-text asset."""
        path = self.resolve_guid(guid)
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except (OSError, UnicodeDecodeError):
            return None

    def texture_is_srgb(self, guid):
        """Whether this texture's own bytes are sRGB-encoded, read off the asset's own .meta
        (TextureImporter.sRGBTexture, which the exporter wrote from Texture2D.m_ColorSpace),
        or None for a guid whose meta states nothing. The bridge database answers the same
        question from the closure; both read the asset, neither guesses from a slot or a name."""
        path = self.resolve_guid(guid)
        if not path:
            return None
        cached = self._texture_srgb.get(path)
        if cached is not None:
            return cached[0]
        stated = None
        try:
            with open(path + ".meta", "r", encoding="utf-8", errors="replace") as handle:
                match = _SRGB_PATTERN.search(handle.read())
            if match:
                stated = match.group(1) != "0"
        except OSError:
            stated = None
        self._texture_srgb[path] = (stated,)
        return stated

    # -- parsed file cache ---------------------------------------------------

    def load_file(self, path):
        path = os.path.abspath(path)
        cached = self._file_cache.get(path)
        if cached is None:
            cached = unity_yaml.parse_file(path)
            self._file_cache[path] = cached
        return cached

    def load_guid(self, guid):
        path = self.resolve_guid(guid)
        if path and os.path.isfile(path):
            return self.load_file(path)
        return None

    def clip_curves(self, guid):
        """The clip's ``clip_curves.ClipCurves`` via the single raw-text parser
        (``ClipCurves.from_yaml_text`` -- the disk-mode twin of the bridge's
        zero-parse blob carrier; both databases expose the same surface, so
        callers never branch on the mode). None when the guid resolves to no
        readable .anim file. A malformed clip raises instead of importing
        silently wrong curves."""
        from . import clip_curves as clip_curves_module
        path = self.resolve_guid(guid)
        if not path or not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return clip_curves_module.ClipCurves.from_yaml_text(handle.read())

    def clips(self):
        """Every AnimationClip this project holds, as {guid, name, size_bytes, path} -- the
        disk-mode twin of BridgeAssetDatabase.clips. A folder of extracted assets states a clip
        by its own extension, so nothing is read to answer."""
        self._scan_dir(self.assets_dir or self.primary_dir)
        found = []
        for guid, path in self._guid_to_path.items():
            if not path.lower().endswith(".anim"):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            found.append({"guid": guid,
                          "name": os.path.splitext(os.path.basename(path))[0],
                          "size_bytes": size,
                          "path": path})
        return found

    def raw_text(self, guid):
        """Unparsed YAML text for a guid -- disk-mode twin of
        BridgeAssetDatabase.raw_text (same contract: None on a miss), so
        callers that stash/peek raw documents (e.g. persisting the working
        Avatar onto an armature) work identically in both modes."""
        path = self.resolve_guid(guid)
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return None

    def resolve_ref(self, ref):
        """Resolve a {fileID, guid} reference to (UnityDocument, path) or (None, None)."""
        if not isinstance(ref, dict):
            return None, None
        guid = ref.get("guid")
        file_id = ref.get("fileID")
        if not guid:
            return None, None
        unity_file = self.load_guid(guid)
        if not unity_file:
            return None, None
        doc = unity_file.get(file_id) if file_id is not None else None
        if doc is None and unity_file.documents:
            doc = unity_file.documents[0]
        return doc, unity_file.path
