"""Bridge-mode AssetDatabase: resolves guids against an in-memory pythonnet
closure (RipperBlenderBridge.ImportCabs' Documents/Textures dicts) instead of
scanning a directory of .meta files. Same duck-typed surface as
asset_db.AssetDatabase (load_guid, load_file, resolve_guid, resolve_ref), plus
png_bytes/all_guids for the bridge-only callers (material_builder,
prefab_importer's clip gathering) -- every existing builder that only ever
calls the shared surface (hierarchy, mesh_decoder, mesh_builder,
armature_builder, animation_builder, humanoid_retarget) works unchanged
against either database.
"""

from __future__ import annotations

try:
    from . import unity_yaml
except ImportError:  # standalone (non-package) testing
    import unity_yaml


class BridgeAssetDatabase:
    """Backed by GUID-keyed dicts already pulled into memory by the pythonnet
    bridge. Each guid is parsed at most once and memoized, same caching
    behaviour as the disk AssetDatabase's file cache."""

    def __init__(self, documents, textures, bridge=None):
        # documents: dict[guid_lower] -> Unity YAML text (str)
        # textures: dict[guid_lower] -> raw PNG bytes
        self._documents = documents
        self._textures = textures
        self._bridge = bridge  # optional: fetch_guid() fallback on a closure miss
        self._file_cache = {}  # guid -> UnityFile

    def load_guid(self, guid):
        if not guid:
            return None
        guid = guid.lower()
        cached = self._file_cache.get(guid)
        if cached is not None:
            return cached
        text = self._documents.get(guid)
        if text is None and self._bridge is not None:
            text = self._bridge.fetch_guid(guid)
        if text is None:
            return None
        unity_file = unity_yaml.UnityFile(guid, unity_yaml.parse_text(text, guid))
        self._file_cache[guid] = unity_file
        return unity_file

    def load_file(self, key):
        """Signature parity with AssetDatabase.load_file(path) -- key is a guid here."""
        return self.load_guid(key)

    def resolve_guid(self, guid):
        """No filesystem path concept in bridge mode: the guid itself is the
        opaque lookup key material_builder uses to fetch texture bytes (see
        png_bytes). Returns None when the guid isn't in the closure at all."""
        if not guid:
            return None
        guid = guid.lower()
        return guid if (guid in self._documents or guid in self._textures) else None

    def resolve_ref(self, ref):
        """Resolve a {fileID, guid} reference to (UnityDocument, guid) or (None, None).
        Mirrors asset_db.AssetDatabase.resolve_ref exactly, minus disk."""
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

    def png_bytes(self, key):
        """material_builder's bridge-mode image source: raw PNG bytes for a guid.
        Presence of this method (vs. the disk AssetDatabase, which lacks it) is
        what material_builder branches on to pick the in-memory decode path."""
        if not key:
            return None
        return self._textures.get(key.lower())

    def all_guids(self):
        """Every document guid present in the closure -- for closure-wide scans
        (e.g. prefab_importer's loose-AnimationClip gathering, the bridge
        equivalent of the disk importer's folder walk)."""
        return self._documents.keys()
