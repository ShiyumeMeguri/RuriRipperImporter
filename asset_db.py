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

try:
    from . import unity_yaml
except ImportError:  # standalone (non-package) testing
    import unity_yaml

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

    def raw_text(self, guid):
        """Unparsed YAML text for a guid -- disk-mode twin of
        BridgeAssetDatabase.raw_text (same contract: None on a miss), so
        callers that stash/peek raw documents (e.g. prefab_importer's
        _stamp_avatar_on_armature persisting the working Avatar onto the
        armature) work identically in both modes."""
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
