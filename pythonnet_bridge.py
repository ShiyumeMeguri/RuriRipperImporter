"""In-process pythonnet bridge into Ruri.RipperHook.dll: boots a CoreCLR
runtime inside Blender's own process (via pythonnet_bootstrap having already
installed pythonnet) and exposes thin Python wrappers over
Ruri.RipperHook.Bridge.RipperBlenderBridge. Everything crossing the CLR/Python
boundary out of this module is plain data (str/bytes/dict/list) -- callers
elsewhere in the addon never touch `clr`/.NET objects directly.
"""

from __future__ import annotations

import os
import sys

# Known build output locations (Release preferred; falls back to Debug so this
# works against a dev build without requiring a Release rebuild first). Override
# with the RURI_RIPPERHOOK_BIN environment variable if the repo lives elsewhere.
_DEFAULT_DLL_DIRS = [
    r"D:\Ruri\Git\FractalTools\Ruri-RipperHook\AssetRipper\Source\0Bins\AssetRipper\Release",
    r"D:\Ruri\Git\FractalTools\Ruri-RipperHook\AssetRipper\Source\0Bins\AssetRipper\Debug",
]

_runtime_set = False
_bridge_type = None


def _dll_dir():
    override = os.environ.get("RURI_RIPPERHOOK_BIN")
    candidates = ([override] if override else []) + _DEFAULT_DLL_DIRS
    for d in candidates:
        if d and os.path.isfile(os.path.join(d, "Ruri.RipperHook.dll")):
            return d
    raise RuntimeError(
        "Ruri.RipperHook.dll not found. Build Source/Ruri.RipperHook/Ruri.RipperHook.csproj, "
        "or set the RURI_RIPPERHOOK_BIN environment variable to its output directory. "
        f"Looked in: {candidates}")


def _ensure_runtime():
    """Boot CoreCLR (once per Blender session -- it cannot be re-pointed or
    unloaded once set) and load Ruri.RipperHook.dll."""
    global _runtime_set, _bridge_type
    if _bridge_type is not None:
        return
    dll_dir = _dll_dir()

    if not _runtime_set:
        from clr_loader import get_coreclr
        from pythonnet import set_runtime
        # Reuse the CLI's own runtimeconfig.json (Microsoft.NETCore.App +
        # Microsoft.AspNetCore.App only -- confirmed the core DLL needs no
        # Microsoft.WindowsDesktop.App, that's GUI-only) rather than
        # authoring a new one; it's built right next to the DLL already.
        runtime_config = os.path.join(dll_dir, "Ruri.RipperHook.CLI.runtimeconfig.json")
        if not os.path.isfile(runtime_config):
            raise RuntimeError(f"Missing runtimeconfig.json next to the DLL: {runtime_config}")
        set_runtime(get_coreclr(runtime_config=runtime_config))
        _runtime_set = True

    if dll_dir not in sys.path:
        sys.path.append(dll_dir)
    import clr
    clr.AddReference("Ruri.RipperHook")
    from Ruri.RipperHook.Bridge import RipperBlenderBridge
    _bridge_type = RipperBlenderBridge


def _string_array(strings):
    """pythonnet does not auto-marshal a plain Python list to
    IEnumerable<string>/string[] -- build a real System.String[] explicitly."""
    import System
    return System.Array[System.String](list(strings))


class RipperBridge:
    """One bridge session: Initialize once with the target game's hook id(s),
    then Build/Load a cabmap, browse rows, and pull a selection into memory.
    Call from one thread at a time -- the underlying C# side is written for a
    single active session per the CLI's own model (see RipperBlenderBridge's
    doc comments on GameFileLoader/GameBundleHook static state)."""

    def __init__(self, hook_ids):
        _ensure_runtime()
        self._bridge = _bridge_type
        self._bridge.Initialize(_string_array(hook_ids))
        self._map = None

    @property
    def has_map(self):
        return self._map is not None

    def build_cab_map(self, game_root, out_path):
        """Scan game_root and write a fresh cabmap to out_path. Returns 0 on success."""
        return int(self._bridge.BuildCabMap(game_root, out_path))

    def load_cab_map(self, cab_map_path):
        """Load an existing cabmap file; must be called (or build_cab_map) before
        enumerate_rows()/import_cabs()."""
        self._map = self._bridge.LoadCabMap(cab_map_path)

    def enumerate_rows(self):
        """Every CAB in the loaded map, as plain dicts (Name/Container/TypeNames/
        Source/DependencyCount) -- the N-panel browser's backing data."""
        if self._map is None:
            raise RuntimeError("No cabmap loaded -- call load_cab_map()/build_cab_map() first.")
        return [
            {
                "cab": row.Cab,
                "name": row.Name,
                "container": row.Container,
                "type_names": row.TypeNames,
                "source": row.Source,
                "deps": int(row.DependencyCount),
            }
            for row in self._bridge.EnumerateRows(self._map)
        ]

    def import_cabs(self, cab_names):
        """Resolve cab_names' dependency closure, load it, export it in-memory,
        and return (documents, textures, roots): documents/textures are plain
        Python dicts keyed by lowercase guid (str -> str Unity-YAML text, str ->
        bytes PNG); roots is the list of guids that are the actual importable
        (.prefab) top-level assets."""
        if self._map is None:
            raise RuntimeError("No cabmap loaded -- call load_cab_map()/build_cab_map() first.")
        result = self._bridge.ImportCabs(self._map, _string_array(cab_names))
        # .NET IReadOnlyDictionary crosses into Python as an iterable of
        # KeyValuePair (no dict-like .items()) -- iterate and pull .Key/.Value.
        documents = {str(kvp.Key).lower(): str(kvp.Value) for kvp in result.Documents}
        textures = {str(kvp.Key).lower(): bytes(kvp.Value) for kvp in result.Textures}
        roots = [str(g).lower() for g in result.Roots]
        return documents, textures, roots
