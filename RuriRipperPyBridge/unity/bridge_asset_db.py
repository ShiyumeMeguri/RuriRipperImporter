"""Bridge-mode AssetDatabase: resolves guids against an in-memory pythonnet
closure (RipperBlenderBridge.ImportCabs' single guid -> bytes Assets dict)
instead of scanning a directory of .meta files. Same duck-typed surface as
asset_db.AssetDatabase (load_guid, load_file, resolve_guid, resolve_ref), plus
texture_bytes/all_guids/clip_curves for the bridge-only callers -- every consumer
that sticks to the shared surface (hierarchy, mesh_decoder, prefab, material,
and each host's own mesh/material builders) works unchanged against either
database, which is what lets one importer serve both a folder of extracted
YAML and a live cabmap closure.

The closure hands over one dict of raw bytes and nothing else: the bridge does
not sort payloads by kind, because the caller asking for a guid already knows
whether it wants YAML or an image. Decoding therefore happens here, on demand,
and an exporter that starts writing TGA or EXR where it used to write PNG needs
no change on either side of the bridge.
"""

from __future__ import annotations

from . import clip_curves as clip_curves_module
from . import unity_yaml


class BridgeAssetDatabase:
    """Backed by GUID-keyed dicts already pulled into memory by the pythonnet
    bridge. Each guid is parsed at most once and memoized, same caching
    behaviour as the disk AssetDatabase's file cache."""

    def __init__(self, assets, clip_curve_blobs=None, mesh_blobs=None, asset_paths=None,
                 texture_srgb=None):
        # assets: dict[guid_lower] -> the asset's exported bytes, any format
        # clip_curve_blobs: dict[guid_lower] -> (meta_json, payload_bytes) --
        #   the bridge's zero-parse AnimationClip curve payloads (see
        #   RipperBridge.clip_curves_by_guid / ClipCurveBlob.cs)
        # mesh_blobs: dict[guid_lower] -> (meta_json, payload_bytes) -- the mesh
        #   counterpart (RipperBridge.mesh_blobs_by_guid / MeshRawBlob.cs)
        # asset_paths: dict[guid_lower] -> the exported path AssetRipper wrote
        #   (real file name + extension) -- every asset's display identity
        #   (RipperBridge.asset_paths_by_guid)
        self._assets = assets
        self._texture_srgb = texture_srgb or {}
        self._mesh_blobs = mesh_blobs or {}
        self._asset_paths = asset_paths or {}
        self._file_cache = {}  # guid -> UnityFile
        self._text_cache = {}  # guid -> decoded text, or None when the bytes aren't text
        self._clip_curve_blobs = clip_curve_blobs or {}
        self._clip_curve_cache = {}  # guid -> ClipCurves

    def mesh_blob(self, guid):
        """The raw (meta_json, payload_bytes) MeshRawBlob for a Mesh guid, or
        None when this closure carries no blob for it (older bridge, a mesh the
        blob builder declined, or simply not a mesh)."""
        if not guid:
            return None
        return self._mesh_blobs.get(guid.lower())

    def clip_curves(self, guid):
        """The zero-parse ClipCurves for an AnimationClip guid (the single
        bridge-mode clip surface; the disk AssetDatabase's same-named method
        runs the raw-text parser instead) -- or None when this closure carries
        no blob for it. A selected clip with no blob cannot be imported and is
        reported, never re-parsed from YAML."""
        if not guid:
            return None
        guid = guid.lower()
        cached = self._clip_curve_cache.get(guid)
        if cached is not None:
            return cached
        blob = self._clip_curve_blobs.get(guid)
        if blob is None:
            return None
        clip = clip_curves_module.ClipCurves.from_blob(blob[0], blob[1])
        self._clip_curve_cache[guid] = clip
        return clip

    def _text(self, guid):
        """The asset's bytes as text, or None when they are not text at all (an
        image, a mesh blob, ...). Decoding here rather than in the bridge is what
        keeps the closure format-agnostic; the result is memoized either way, so a
        closure-wide scan pays one failed decode per binary asset, not one per look."""
        if guid in self._text_cache:
            return self._text_cache[guid]
        data = self._assets.get(guid)
        text = None
        if data is not None:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = None
        self._text_cache[guid] = text
        return text

    def load_guid(self, guid):
        if not guid:
            return None
        guid = guid.lower()
        cached = self._file_cache.get(guid)
        if cached is not None:
            return cached
        text = self._text(guid)
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
        opaque lookup key a host's texture stage uses to fetch bytes (see
        texture_bytes). Returns None when the guid isn't in the closure at all."""
        if not guid:
            return None
        guid = guid.lower()
        return guid if guid in self._assets else None

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

    def texture_bytes(self, key):
        """The bridge-mode image source: the asset's exported bytes for a guid,
        in whatever container AssetRipper chose for it (png/tga/exr/...). Presence
        of this method (vs. the disk AssetDatabase, which lacks it) is what a
        host's texture stage branches on to pick the in-memory decode path."""
        if not key:
            return None
        return self._assets.get(key.lower())

    def texture_is_srgb(self, guid):
        """Whether this texture's own bytes are sRGB-encoded, as the asset states it, or None
        for a guid that states nothing (not a texture, or a closure without the channel).
        The caller must not substitute a guess for None: a slot's semantics and a file name
        are both measured to disagree with the asset."""
        if not guid:
            return None
        return self._texture_srgb.get(guid.lower())

    def asset_path(self, guid):
        """The exported path the closure wrote for a guid (real name +
        container extension), or None for a guid the closure does not hold."""
        if not guid:
            return None
        return self._asset_paths.get(guid.lower())

    def asset_name(self, guid):
        """The exported file name without its extension -- the asset's own
        m_Name-derived display name, what any host should label it with."""
        path = self.asset_path(guid)
        if not path:
            return None
        leaf = path.replace("\\", "/").rsplit("/", 1)[-1]
        stem = leaf.rsplit(".", 1)[0] if "." in leaf else leaf
        return stem or None

    def clips(self):
        """Every AnimationClip this closure holds, as {guid, name, size_bytes, path}.

        The curve blobs ARE the answer: a guid the bridge built curves for is a clip, and one it
        did not is not. ``size_bytes`` is the payload the host would decode, which is the cost
        that actually falls on it -- the exported YAML for these clips is not written at all."""
        found = []
        for guid, blob in self._clip_curve_blobs.items():
            found.append({"guid": guid,
                          "name": self.asset_name(guid) or guid,
                          "size_bytes": len(blob[1]) if blob and len(blob) > 1 else 0,
                          "path": self.asset_path(guid) or ""})
        return found

    def all_guids(self):
        """Every guid present in the closure -- for closure-wide scans (e.g.
        loose-AnimationClip gathering, the bridge equivalent of the disk
        importer's folder walk). Non-text assets are in here too; a scan that
        sniffs raw_text simply gets None for them."""
        return self._assets.keys()

    def raw_text(self, guid):
        """Unparsed YAML text for a guid, without going through load_guid's
        unity_yaml.parse_text (and its memoizing cache). Lets a caller peek
        at a document (e.g. the cheap class/name sniff animation-clip discovery
        does) without paying a full parse for every candidate -- some
        AnimationClip documents in a character's closure run to 100+MB, and
        most closure guids aren't clips at all. None for a non-text asset."""
        if not guid:
            return None
        return self._text(guid.lower())
