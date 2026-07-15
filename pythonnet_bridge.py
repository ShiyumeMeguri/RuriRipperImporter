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

# The addon preference (Edit > Preferences > Add-ons > RuriRipperImporter > "Ruri-RipperHook
# Bin Dir", see RuriRipperImporterPreferences in __init__.py) points DIRECTLY at the bin dir that
# has to contain both Ruri.RipperHook.dll and Ruri.RipperHook.CLI.runtimeconfig.json -- typically
# "<repo>/AssetRipper/Source/0Bins/AssetRipper/Debug". No repo-root-relative derivation, no
# Release-vs-Debug guessing: one directory, set explicitly, nothing hardcoded here. Pushed into
# this module via set_repo_root() at register() time and whenever the preference changes.
# RURI_RIPPERHOOK_BIN remains as an escape hatch for headless/CLI use with no bpy preferences UI.
_runtime_set = False
_bridge_type = None
_repo_root_override = None  # set via set_repo_root() -- see __init__.py's AddonPreferences


def set_repo_root(path):
    """Called by __init__.py.register() (and the preferences panel's update callback) with the
    user-configured bin dir. Takes priority over RURI_RIPPERHOOK_BIN."""
    global _repo_root_override
    _repo_root_override = (path or "").strip() or None


def _dll_dir():
    d = _repo_root_override or os.environ.get("RURI_RIPPERHOOK_BIN")
    if not d:
        raise RuntimeError(
            "No Ruri-RipperHook bin dir configured. Set it in Blender's Edit > Preferences > "
            "Add-ons > RuriRipperImporter > \"Ruri-RipperHook Bin Dir\" (the folder containing "
            "Ruri.RipperHook.dll, e.g. AssetRipper/Source/0Bins/AssetRipper/Debug), or set the "
            "RURI_RIPPERHOOK_BIN environment variable.")
    if not os.path.isfile(os.path.join(d, "Ruri.RipperHook.dll")):
        raise RuntimeError(f"Ruri.RipperHook.dll not found in configured bin dir: {d}")
    if not os.path.isfile(os.path.join(d, "Ruri.RipperHook.CLI.runtimeconfig.json")):
        raise RuntimeError(
            f"Ruri.RipperHook.CLI.runtimeconfig.json not found next to the DLL in: {d} -- "
            "build Source/Ruri.RipperHook.CLI/Ruri.RipperHook.CLI.csproj (a Release build that "
            "only ran the GUI/core projects has the DLL but not this file).")
    return d


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


class _StaticTypeProxy:
    """Wraps a `System.Type` obtained via reflection (Assembly.GetType) so `.SomeMethod(*args)`
    still works as if it were a normal pythonnet-imported class.

    Root cause (confirmed against pythonnet's actual source, not guessed): pythonnet's
    `from Namespace import Class` only works for a type if AssemblyManager.ScanAssembly's
    `Assembly.GetExportedTypes()` call succeeded for the WHOLE containing assembly first
    (AssemblyManager.cs GetTypes()) -- and that call throws FileNotFoundException (silently
    swallowed, returning zero types for the ENTIRE assembly) if ANY exported type anywhere in
    Ruri.RipperHook.dll can't resolve one of its own dependencies, even ones having nothing to
    do with RipperBlenderBridge. clr.AddReference() itself still succeeds (the assembly file
    loads fine), so _ensure_runtime() falls back to Assembly.GetType(fullName) -- a single-type,
    much narrower reflection lookup that isn't affected by that whole-assembly scan failure.

    But a raw reflected System.Type crosses into Python as a plain object exposing Type's OWN
    instance API (.Name, .GetMethod(), ...) -- NOT as the callable class it describes (that
    special wrapping, ReflectedClrType, is pythonnet's import-hook machinery specifically,
    confirmed in Converter.ToPython: a Type value takes the generic CLRObject.GetReference
    path, not ReflectedClrType.GetOrCreate). So `RipperBlenderBridge.ListAvailableHooks()`
    fails with AttributeError. This proxy makes `.SomeMethod(*args)` dispatch through
    `GetMethod(name).Invoke(None, args)` (static: no target instance) instead, sidestepping
    pythonnet's class-wrapping entirely -- pure .NET reflection, unaffected by any of the above.
    """

    def __init__(self, clr_type):
        self._clr_type = clr_type

    def __getattr__(self, name):
        method = self._clr_type.GetMethod(name)
        if method is None:
            raise AttributeError(f"{self._clr_type.FullName} has no method '{name}'")

        def call(*args):
            import System
            arg_array = System.Array[System.Object](list(args)) if args else None
            try:
                return method.Invoke(None, arg_array)
            except Exception as exc:
                # MethodInfo.Invoke wraps any exception the target method itself throws in a
                # System.Reflection.TargetInvocationException -- unwrap it so callers (and
                # _report_exception's `type(exc).__name__`) see the real underlying exception
                # (DirectoryNotFoundException, etc.), not just "TargetInvocationException" for
                # every possible C#-side error.
                inner = getattr(exc, "InnerException", None)
                if inner is not None:
                    raise inner from exc
                raise

        return call


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
    import System

    dll_path = os.path.join(dll_dir, "Ruri.RipperHook.dll")
    clr.AddReference(dll_path)
    assembly = next((a for a in System.AppDomain.CurrentDomain.GetAssemblies()
                     if str(a.GetName().Name) == "Ruri.RipperHook"), None)
    if assembly is None:
        raise RuntimeError(
            f"Ruri.RipperHook.dll (loaded from {dll_path}) is not among "
            "AppDomain.CurrentDomain.GetAssemblies() after AddReference() -- the load itself failed.")

    # Diagnostic only, never fatal: if Ruri.RipperHook.dll has a type somewhere that can't
    # resolve one of its own dependencies, THIS is what silently empties AssemblyManager's
    # namespace scan for the whole assembly (see _StaticTypeProxy's doc comment) -- surface
    # exactly which dependency so the real fix (getting it into the bin dir) is findable,
    # without blocking on it, since Assembly.GetType() below doesn't need this to succeed.
    try:
        assembly.GetExportedTypes()
    except Exception as exc:
        missing = getattr(exc, "FileName", None) or getattr(exc, "Message", None) or str(exc)
        print(f"[RuriRipper] Ruri.RipperHook.dll: not every exported type resolves cleanly "
              f"({type(exc).__name__}: {missing}) -- this is why `from Ruri.RipperHook...import` "
              "doesn't work and the reflection fallback is needed; harmless if the fallback "
              "below still finds RipperBlenderBridge.")

    bridge_type = assembly.GetType("Ruri.RipperHook.Bridge.RipperBlenderBridge")
    if bridge_type is None:
        raise RuntimeError(
            "Ruri.RipperHook.dll loaded, but has no Ruri.RipperHook.Bridge.RipperBlenderBridge type -- "
            "rebuild Source/Ruri.RipperHook/Ruri.RipperHook.csproj against the latest source.")
    _bridge_type = _StaticTypeProxy(bridge_type)


def list_available_hooks():
    """Every hook id (e.g. "EndField_1.3.3") compiled into the loaded Ruri.RipperHook.dll, straight
    from RipperBlenderBridge.ListAvailableHooks() -- no RipperBridge session (Initialize with chosen
    hook ids) required first, since this only boots the CLR runtime and loads the DLL, then reflects
    over its already-loaded hook types. This is what the Hook picker in cabmap_panel.py populates its
    checkbox list from instead of a hardcoded/free-text id."""
    _ensure_runtime()
    return [str(h) for h in _bridge_type.ListAvailableHooks()]


def _string_array(strings):
    """pythonnet does not auto-marshal a plain Python list to
    IEnumerable<string>/string[] -- build a real System.String[] explicitly."""
    import System
    return System.Array[System.String](list(strings))


def _as_root_list(vfs_roots):
    """VFS-root parameters accept either one path (str) or a priority-ordered
    list of paths -- normalize to a list so callers don't have to remember
    to wrap a single root themselves."""
    return [vfs_roots] if isinstance(vfs_roots, str) else list(vfs_roots)


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

    def resolve_cabs_for_paths(self, container_paths):
        """Resolve addressable container paths (e.g. discover_scene_placements'
        asset_path values) to the CAB names that host them. Paths with no
        match are silently skipped -- compare len(input) to len(result) to
        check coverage. Requires a loaded cabmap."""
        if self._map is None:
            raise RuntimeError("No cabmap loaded -- call load_cab_map()/build_cab_map() first.")
        return [str(c) for c in self._bridge.ResolveCabsForPaths(self._map, _string_array(container_paths))]

    def resolve_closure_cab_names(self, cab_names):
        """Pure in-memory dependency-closure CAB-name enumeration for the
        given seed CABs -- no VFS decrypt, no AssetRipper export, just the
        already-loaded cabmap's own dependency graph (CabMap.
        ResolveClosureCabNames). Pair with enumerate_rows()' own type_names
        (already loaded per CAB) to answer "does this prefab's closure
        include an AnimationClip" without resolving/exporting anything.
        Requires a loaded cabmap."""
        if self._map is None:
            raise RuntimeError("No cabmap loaded -- call load_cab_map()/build_cab_map() first.")
        return [str(c) for c in self._bridge.ResolveClosureCabNames(self._map, _string_array(cab_names))]

    def enumerate_vfs_files(self, vfs_roots, block_type_filter=None):
        """Every file recorded in every .blc manifest across vfs_roots (a
        path, or a priority-ordered list of paths -- e.g. [Persistent/VFS,
        StreamingAssets/VFS], see the C# doc comments on EnumerateVfsFiles/
        BuildMergedFileIndex for why a hot-update overlay root and the base
        client root normally both need to be passed together), of ANY block
        type (not just Unity-CAB-shaped entries). Returns plain dicts
        (file_name/file_name_hash/block_type/length/chk_path). Independent of
        load_cab_map() -- only needs Initialize() (an active session) to have
        run."""
        filter_arg = _string_array(block_type_filter) if block_type_filter else None
        return [
            {
                "file_name": f.FileName,
                "file_name_hash": int(f.FileNameHash),
                "block_type": f.BlockType,
                "length": int(f.Length),
                "chk_path": f.ChkPath,
            }
            for f in self._bridge.EnumerateVfsFiles(_string_array(_as_root_list(vfs_roots)), filter_arg)
        ]

    def extract_vfs_file(self, vfs_roots, file_name):
        """Raw decrypted bytes of one VFS-packed file, by its exact original
        name (as returned by enumerate_vfs_files' file_name). Tries vfs_roots
        in priority order with fallback -- a hot-update overlay can list a
        file it never duplicated because that patch didn't change it (see
        ExtractFirstAvailable's C# doc comment)."""
        return bytes(self._bridge.ExtractVfsFile(_string_array(_as_root_list(vfs_roots)), file_name))

    def enumerate_scene_maps(self, vfs_roots):
        """Every distinct map name with streaming-chunk data across vfs_roots."""
        return [str(m) for m in self._bridge.EnumerateSceneMaps(_string_array(_as_root_list(vfs_roots)))]

    def diagnose_schema_drift(self, vfs_roots, map_name):
        """Binary/vtable-level schema-drift report (list of str lines) for
        map_name's streaming chunks -- flags any FlatBuffers table type
        where the live game data declares more fields than the currently-
        compiled (1.2.4-era) bindings know how to read. See
        EndfieldSceneBridge.DiagnoseSchemaDrift's C# doc comment."""
        return [str(line) for line in
                self._bridge.DiagnoseSchemaDrift(_string_array(_as_root_list(vfs_roots)), map_name)]

    def discover_scene_placements(self, vfs_roots, map_name):
        """Every mesh-bearing entity placement for map_name's streaming chunks
        -- plain dicts (asset_path/asset_hash/entity_name/source_chunk/
        has_transform/px..sz/material_asset_paths). material_asset_paths is
        the SAME hash-LUT source as asset_path (FBPropertyAssetData,
        AssetType==1 instead of ==2) -- the entity's own real material(s),
        not a naming-convention guess. Cheap: no dependency closure resolved,
        no CAB loaded -- see DiscoverScenePlacements' C# doc comment."""
        return [
            {
                "asset_path": p.AssetPath,
                "asset_hash": int(p.AssetHash),
                "entity_name": p.EntityName,
                "source_chunk": p.SourceChunk,
                "has_transform": bool(p.HasTransform),
                "px": float(p.Px), "py": float(p.Py), "pz": float(p.Pz),
                "qx": float(p.Qx), "qy": float(p.Qy), "qz": float(p.Qz), "qw": float(p.Qw),
                "sx": float(p.Sx), "sy": float(p.Sy), "sz": float(p.Sz),
                "material_asset_paths": [str(m) for m in p.MaterialAssetPaths],
            }
            for p in self._bridge.DiscoverScenePlacements(_string_array(_as_root_list(vfs_roots)), map_name)
        ]

    def import_cabs(self, cab_names):
        """Resolve cab_names' dependency closure, load it, export it in-memory, and return
        (documents, textures, roots, seed_roots): documents/textures are plain Python dicts keyed
        by lowercase guid (str -> str Unity-YAML text, str -> bytes PNG); roots is the list of
        guids that are the actual importable (.prefab) top-level assets; seed_roots is
        {cab_name: guid} for each requested cab_names entry that resolved to its own asset --
        resolved bridge-side directly through the cabmap's own CAB/addressable-path identity
        (RipperBlenderBridge.Partition/NormalizeExportPath), NOT by matching display names, so a
        caller never needs its own name-matching heuristic to figure out which of `roots`
        corresponds to which requested CAB (a single seed's closure routinely resolves to more
        than one root .prefab, e.g. a co-resolved portrait/uimodel variant)."""
        if self._map is None:
            raise RuntimeError("No cabmap loaded -- call load_cab_map()/build_cab_map() first.")
        cab_names = list(cab_names)
        result = self._bridge.ImportCabs(self._map, _string_array(cab_names))
        # .NET IReadOnlyDictionary crosses into Python as an iterable of
        # KeyValuePair (no dict-like .items()) -- iterate and pull .Key/.Value.
        documents = {str(kvp.Key).lower(): str(kvp.Value) for kvp in result.Documents}
        textures = {str(kvp.Key).lower(): bytes(kvp.Value) for kvp in result.Textures}
        roots = [str(g).lower() for g in result.Roots]
        seed_roots = {str(kvp.Key): str(kvp.Value).lower() for kvp in result.SeedRoots}
        return documents, textures, roots, seed_roots
