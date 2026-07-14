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


def _runtime_config_path():
    dll_dir = _dll_dir()
    # Reuse the CLI's own runtimeconfig.json (Microsoft.NETCore.App +
    # Microsoft.AspNetCore.App only -- confirmed the core DLL needs no
    # Microsoft.WindowsDesktop.App, that's GUI-only) rather than authoring a
    # new one; it's built right next to the DLL already.
    return dll_dir, os.path.join(dll_dir, "Ruri.RipperHook.CLI.runtimeconfig.json")


def _bound_runtime_kind():
    """Best-effort introspection of whichever runtime pythonnet already has
    set (pythonnet._RUNTIME is not public API, so this degrades to None --
    "unknown" -- rather than raising if a future pythonnet version removes
    or renames it)."""
    try:
        import pythonnet
        bound = getattr(pythonnet, "_RUNTIME", None)
    except ImportError:
        return None, None
    if bound is None:
        return None, None
    return bound, f"{type(bound).__module__}.{type(bound).__qualname__}"


def _claim_coreclr(runtime_config):
    """The one and only set_runtime() call site. pythonnet allows exactly one
    CLR runtime per process, ever -- if ANYTHING else in this Blender session
    (this profile can have dozens of addons; a lazily-triggered `import clr`
    in any of them defaults to .NET Framework on Windows) claims a runtime
    before we do, our net10.0 DLL can never load under it. "Already loaded"
    is only safe to swallow when what's already bound is a CoreCLR-family
    runtime (our own earlier claim -- e.g. a second register() in this
    process after Blender's Reload Scripts, which resets this module's own
    globals via importlib.reload but can't un-claim the real process-wide
    runtime -- or anything else CoreCLR-compatible); if it's .NET Framework,
    swallowing the error here would just defer the real failure to a much
    more confusing spot later (clr.AddReference silently not registering the
    assembly's namespaces, surfacing as "No module named 'Ruri'" at the
    unrelated from-import line) -- fail loudly and specifically right here
    instead."""
    global _runtime_set
    if _runtime_set:
        return
    from clr_loader import get_coreclr
    from pythonnet import set_runtime
    try:
        set_runtime(get_coreclr(runtime_config=runtime_config))
    except RuntimeError as exc:
        if "already been loaded" not in str(exc):
            raise
        bound, bound_kind = _bound_runtime_kind()
        if bound_kind and "netfx" in bound_kind.lower():
            raise RuntimeError(
                "A .NET Framework runtime is already loaded in this Blender process "
                f"({bound_kind}, {bound!r}) -- pythonnet allows only one CLR runtime per "
                "process, and .NET Framework cannot load Ruri.RipperHook.dll (targets "
                "net10.0). Something imported `clr` (or called pythonnet.load()) before "
                "RuriRipperImporter's register() got a chance to claim CoreCLR. Restart "
                "Blender with RuriRipperImporter enabled and nothing else touching it "
                "first; if this keeps happening, another addon in this profile is the "
                "culprit and needs to be identified."
            ) from exc
        # Bound to something else CoreCLR-compatible (most likely: our own
        # earlier claim_runtime_early() in this same process) -- fine.
    _runtime_set = True


def claim_runtime_early():
    """Call from register() (not lazily on first bridge use) to win the
    single-runtime-per-process race as early as structurally possible --
    before the user has clicked anything that might trigger some other
    addon's own lazy pythonnet/CLR usage. Best-effort/silent: if pythonnet
    isn't installed yet or the DLL isn't built yet, this is a no-op and
    _ensure_runtime() will do the real work (and raise a real error if
    appropriate) on first actual bridge use instead."""
    try:
        _, runtime_config = _runtime_config_path()
    except RuntimeError:
        return
    if not os.path.isfile(runtime_config):
        return
    try:
        _claim_coreclr(runtime_config)
    except ImportError:
        pass  # pythonnet/clr_loader not installed yet


def _ensure_runtime():
    """Boot CoreCLR (once per Blender process -- it cannot be re-pointed or
    unloaded once set, whether that "once" was this call or an earlier
    claim_runtime_early()/register()) and load Ruri.RipperHook.dll."""
    global _bridge_type
    if _bridge_type is not None:
        return
    dll_dir, runtime_config = _runtime_config_path()
    if not os.path.isfile(runtime_config):
        raise RuntimeError(f"Missing runtimeconfig.json next to the DLL: {runtime_config}")
    _claim_coreclr(runtime_config)

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
