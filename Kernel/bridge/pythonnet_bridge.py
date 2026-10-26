"""In-process pythonnet bridge into Ruri.RipperHook.dll: boots a CoreCLR runtime
inside the host application's own process (``bootstrap`` having already
installed pythonnet) and exposes thin Python wrappers over
Ruri.RipperHook.BlenderBridge.RipperBlenderBridge. Everything crossing the CLR/Python
boundary out of this module is plain data (str/bytes/dict/list) -- nothing above
it ever touches `clr`/.NET objects directly, which is what keeps the bridge one
implementation rather than one per host.

The bin dir -- the folder that has to contain BOTH Ruri.RipperHook.dll and
Ruri.RipperHook.CLI.runtimeconfig.json, typically ``<checkout>/Source/0Bins/Release``
-- is the one genuinely machine-specific value here. No repo-root-relative
derivation, no configuration guessing, nothing hardcoded: it is resolved, in
order, from

1. ``set_bin_dir(path)`` -- what the host pushed in (Blender: its
   AddonPreferences; Painter: its settings file), which is the normal case;
2. ``$RURI_RIPPERHOOK_BIN`` -- the headless/CLI escape hatch, no UI needed.
"""

from __future__ import annotations

import os
import sys

# A claimed CoreCLR runtime can never be re-claimed in this process, so reloading
# this module would only desync the guards below from what is really loaded.
HOLDS_PROCESS_STATE = True

_runtime_set = False
_bridge_type = None
_bin_dir_override = None
_failure_listener = None
_bin_dir_hint = ("Set it in the RuriRipper panel, or set the RURI_RIPPERHOOK_BIN "
                 "environment variable.")


_texture_formats = ()


def set_texture_formats(extensions):
    """Declare which image containers THIS host can decode, lowercase extensions
    in preference order ("png", "jpeg", ...). Every texture whose natural
    (game-authored) container is in the set crosses the bridge byte-identical to
    a disk export; one that is not is encoded into the first entry instead.
    Never declaring anything means raw everything -- the unopinionated truth --
    which is exactly right for a host like Blender that decodes tga/exr natively.
    The capability is the host's own data: the producer holds no format opinion."""
    global _texture_formats
    _texture_formats = tuple((extension or "").strip().lower()
                             for extension in (extensions or ()) if (extension or "").strip())


def set_bin_dir(path):
    """Push the user-configured bin dir in. Takes priority over RURI_RIPPERHOOK_BIN;
    an empty value clears it again."""
    global _bin_dir_override
    _bin_dir_override = (path or "").strip() or None


def set_bin_dir_hint(hint):
    """One sentence telling THIS host's user where that path is configured --
    it goes into the error raised when none is set."""
    global _bin_dir_hint
    _bin_dir_hint = hint


def _configured_bin_dir():
    return _bin_dir_override or (os.environ.get("RURI_RIPPERHOOK_BIN") or "").strip() or None


def _dll_dir():
    d = _configured_bin_dir()
    if not d:
        raise RuntimeError(
            "No Ruri-RipperHook bin dir configured (the folder containing "
            "Ruri.RipperHook.dll, e.g. <checkout>/Source/0Bins/Release). " + _bin_dir_hint)
    if not os.path.isfile(os.path.join(d, "Ruri.RipperHook.dll")):
        raise RuntimeError(f"Ruri.RipperHook.dll not found in configured bin dir: {d}")
    if not os.path.isfile(os.path.join(d, "Ruri.RipperHook.CLI.runtimeconfig.json")):
        raise RuntimeError(
            f"Ruri.RipperHook.CLI.runtimeconfig.json not found next to the DLL in: {d} -- "
            "build Source/Ruri.RipperHook.CLI/Ruri.RipperHook.CLI.csproj (a Release build that "
            "only ran the GUI/core projects has the DLL but not this file).")
    return d


def reader_folder():
    """The folder the reader is loaded from, checked -- or a raise saying exactly
    what is wrong with it.

    Public because a host whose only place to keep that path is its own settings
    file has to be able to ASK whether the value it stored still works, and put
    the answer on screen. Without it, a path that went stale looks to the user
    like "this folder is no install"."""
    return _dll_dir()


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
    CLR runtime per process, ever -- if ANYTHING else in this host session
    (a Blender profile can have dozens of add-ons, a Painter install several
    plugins; a lazily-triggered `import clr` in any of them defaults to .NET
    Framework on Windows) claims a runtime before we do, our net10.0 DLL can
    never load under it. "Already loaded" is only safe to swallow when what's
    already bound is a CoreCLR-family runtime (our own earlier claim -- e.g. a
    second register() in this process after Blender's Reload Scripts, which
    resets this module's own globals via importlib.reload but can't un-claim
    the real process-wide runtime -- or anything else CoreCLR-compatible); if
    it's .NET Framework, swallowing the error here would just defer the real
    failure to a much more confusing spot later (clr.AddReference silently not
    registering the assembly's namespaces, surfacing as "No module named
    'Ruri'" at the unrelated from-import line) -- fail loudly and specifically
    right here instead.

    The runtime starts as the application that lives in the reader folder. A
    runtime-config start names no application, so hostfxr leaves
    APP_CONTEXT_BASE_DIRECTORY empty, and a library that finds its native half
    beside the application (Silk.NET's loader for spirv-cross, which the shader
    decompiler emits through) finds nothing -- every shader export failed. The
    base is stated here, the one value ``dotnet <app>.dll`` would have set."""
    global _runtime_set
    if _runtime_set:
        return
    from clr_loader import get_coreclr
    from pythonnet import set_runtime
    application_base = os.path.join(os.path.dirname(runtime_config), "")
    try:
        set_runtime(get_coreclr(runtime_config=runtime_config,
                                properties={"APP_CONTEXT_BASE_DIRECTORY": application_base}))
    except RuntimeError as exc:
        if "already been loaded" not in str(exc):
            raise
        bound, bound_kind = _bound_runtime_kind()
        if bound_kind and "netfx" in bound_kind.lower():
            raise RuntimeError(
                "A .NET Framework runtime is already loaded in this host process "
                f"({bound_kind}, {bound!r}) -- pythonnet allows only one CLR runtime per "
                "process, and .NET Framework cannot load Ruri.RipperHook.dll (targets "
                "net10.0). Something imported `clr` (or called pythonnet.load()) before "
                "claim_runtime_early() got the chance to claim CoreCLR. Restart the "
                "application with this plugin loading first; if it keeps happening, "
                "another plugin/add-on in this profile is the culprit and needs to be "
                "identified."
            ) from exc
        # Bound to something else CoreCLR-compatible (most likely: our own
        # earlier claim_runtime_early() in this same process) -- fine.
    _runtime_set = True


def claim_runtime_early():
    """Call at plugin/add-on start (not lazily on first bridge use) to win the
    single-runtime-per-process race as early as structurally possible --
    before the user has clicked anything that might trigger some other
    plugin's own lazy pythonnet/CLR usage. Best-effort/silent: if pythonnet
    isn't installed yet or the DLL isn't built yet, this is a no-op and
    _ensure_runtime() will do the real work (and raise a real error if
    appropriate) on first actual bridge use instead. bootstrap's on_ready hook
    covers the remaining window: the case where pythonnet only becomes
    importable partway through the session, after this call already no-opped."""
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

            def coerce(value):
                # Boxing straight into Object[] keeps Python scalars as PyInt/
                # PyFloat wrappers, which MethodInfo.Invoke then can't bind to an
                # Int32/Double parameter ("Object of type 'Python.Runtime.PyInt'
                # cannot be converted...") -- pythonnet only runs its numeric
                # conversion when the TARGET type is known, and Object gives it
                # nothing to aim at. Convert scalars explicitly. bool first:
                # it's an int subclass in Python.
                if isinstance(value, bool):
                    return System.Boolean(value)
                if isinstance(value, int):
                    return System.Int32(value)
                if isinstance(value, float):
                    return System.Double(value)
                if isinstance(value, (bytes, bytearray, memoryview)):
                    return _clr_byte_array(value)
                return value

            arg_array = System.Array[System.Object]([coerce(a) for a in args]) if args else None
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


def _clr_byte_array(data):
    """Python bytes -> System.Byte[] in one memcpy. Element-wise Array[Byte](seq)
    is a per-byte Python loop -- seconds on a multi-MB curve payload -- so copy
    through Marshal.Copy from the bytes object's own buffer instead."""
    import ctypes

    import System
    import System.Runtime.InteropServices as Interop

    raw = bytes(data)
    array = System.Array.CreateInstance(System.Byte, len(raw))
    if raw:
        address = ctypes.cast(ctypes.c_char_p(raw), ctypes.c_void_p).value
        Interop.Marshal.Copy(System.IntPtr(address), array, 0, len(raw))
    return array


def set_failure_listener(listener):
    """Called with the exception whenever booting the reader fails, before it
    propagates -- a host uses it to look for a newer build of the reader."""
    global _failure_listener
    _failure_listener = listener


def _ensure_runtime():
    try:
        _boot_runtime()
    except Exception as exc:
        if _failure_listener is not None:
            _failure_listener(exc)
        raise


def _boot_runtime():
    """Boot CoreCLR (once per host process -- it cannot be re-pointed or
    unloaded once set, whether that "once" was this call or an earlier
    claim_runtime_early()) and load Ruri.RipperHook.dll."""
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

    bridge_type = assembly.GetType("Ruri.RipperHook.BlenderBridge.RipperBlenderBridge")
    if bridge_type is None:
        raise RuntimeError(
            "Ruri.RipperHook.dll loaded, but has no Ruri.RipperHook.BlenderBridge.RipperBlenderBridge type -- "
            "rebuild Source/Ruri.RipperHook/Ruri.RipperHook.csproj against the latest source.")
    _bridge_type = _StaticTypeProxy(bridge_type)
    # Every decoder beyond the kernel's own -- the Unreal one is the module today --
    # is declared by its build in Modules/*.module beside the kernel, because kernel
    # and modules build into one folder. Nothing above this line names a decoder, and
    # no host carries a path to one: what is in the bin dir is what can be read.
    _bridge_type.LoadDeclaredModules()


def list_decoders():
    """Every game decoder compiled into the loaded Ruri.RipperHook.dll, as
    ``[(product, version, engine_version), ...]`` -- product then version, newest
    version first within a product.

    A decoder's ``product`` IS the Unity productName of the builds it reads and its
    id is ``product_version``, so a host joins an install to a decoder by string
    equality and never carries a mapping table. Host FEATURES are not in this list:
    they are the host's own compile-time fact (RipperBlenderBridge.HostFeatures),
    not something a user picks to make a game readable.

    Needs only the runtime -- no session, no cabmap."""
    _ensure_runtime()
    flat = [str(value) for value in _bridge_type.ListDecoders()]
    return [tuple(flat[i:i + 3]) for i in range(0, len(flat) - 2, 3)]


def read_install(game_root, source_options=None):
    """What the players under ``game_root`` say they are:
    ``[{"data_folder", "company", "product", "game_version", "engine_version",
    "engine", "is_project"}, ...]``.

    A Unity build is read off two small files per player and nothing else --
    app.info for the names the build publishes, globalgamemanagers for its own
    bundleVersion and the Unity version its serialized header states. A build on
    another engine is answered by that engine's own probe in the kernel, which may
    need ``source_options`` (an archive key, say) to open its files at all -- they
    are pushed first (see set_source_options). Either version is "" when the build
    states none, which is a fact about the install and not an error. ``engine`` is
    the engine FAMILY the build runs on ("Unity", "UnrealEngine", ...) -- what a
    family-wide decoder and an engine-level panel are selected by. ``is_project``
    marks the ONE player the install IS -- an install routinely ships several (a
    game, its VR build, its studio).

    Needs only the runtime: this answers "which game is this folder" before anything
    is loaded and without any decoder active."""
    _ensure_runtime()
    if source_options is not None:
        set_source_options(source_options)
    flat = [str(value) for value in _bridge_type.ReadInstall(str(game_root or ""))]
    players = []
    for i in range(0, len(flat) - 6, 7):
        players.append({
            "data_folder": flat[i],
            "company": flat[i + 1],
            "product": flat[i + 2],
            "game_version": flat[i + 3],
            "engine_version": flat[i + 4],
            "engine": flat[i + 5],
            "is_project": flat[i + 6] == "1",
        })
    return players


def resolve_decoder(product, game_version, engine_version, engine=""):
    """The decoder id this install reads through, or "" when none applies (a plain
    Unity build needs no decoder). The rule lives in the kernel (HookCatalog.Resolve):
    the product's own decoders first, then the one declared for its engine family,
    so every host resolves alike."""
    _ensure_runtime()
    return str(_bridge_type.ResolveDecoder(
        str(product or ""), str(game_version or ""), str(engine_version or ""),
        str(engine or "")))


def set_source_options(options):
    """State how the install is to be READ beyond its folder: ``{name: value}`` of
    whatever the active decoder needs to open its files (an archive key, an engine
    version, a schema file). The kernel carries them verbatim; which names exist is
    what the decoder publishes as a dataset, so a host draws its form from that and
    never spells a name itself. Pushed before a probe, a build or a load; pushing the
    same values again is free."""
    _ensure_runtime()
    flat = []
    for name, value in sorted((options or {}).items()):
        flat.append(str(name))
        flat.append("" if value is None else str(value))
    _bridge_type.SetSourceOptions(_string_array(flat))


def set_locale(locale):
    """State the language the HOST is showing its user, as that host names it
    ("en_US", "ja_JP", "zh_CN"). The application's own UI language, never the
    machine's: a decoder that joins a roster to its text reads it off the session,
    so switching the application's language switches every roster and nothing else
    has to be told. Pushing the same value again is free."""
    _ensure_runtime()
    _bridge_type.SetLocale(str(locale or ""))


def _string_array(strings):
    """pythonnet does not auto-marshal a plain Python list to
    IEnumerable<string>/string[] -- build a real System.String[] explicitly."""
    import System
    return System.Array[System.String](list(strings))


def _int_array(values):
    """Same story for int[] -- see _string_array."""
    import System
    return System.Array[System.Int32]([int(v) for v in values])


def _flat_rules(rules):
    """Include/Exclude rules in the five-strings-per-rule wire form both search
    entry points take (field, relation, value, action, enabled). One encoder,
    because the cabmap browser and every projected/host table are read by the
    SAME C# rule evaluator -- a second copy here is how the two quietly stop
    agreeing. ``rules`` is any iterable of objects exposing
    .field/.relation/.value/.action/.enabled (Blender's rule PropertyGroup is
    one). None when there are no rules at all, which is what the C# side reads
    as "no rule stage"."""
    flat = []
    for rule in (rules or ()):
        flat.extend((str(rule.field), str(rule.relation), str(rule.value),
                     str(rule.action), "1" if rule.enabled else "0"))
    return _string_array(flat) if flat else None


def _wire(value):
    """One argument value in the spelling the kernel reads it back in.

    Python and the kernel agree on every finite number's shortest round-trip text
    and disagree on exactly three words: Python writes ``inf``/``-inf``/``nan``
    where the kernel's own number format writes ``Infinity``/``-Infinity``/
    ``NaN``. That is not a corner case -- an unbounded world rect (import this
    whole map, rather than one place in it) IS stated as two infinities, and the
    kernel rejected it as "must be a number".

    The kernel declares the format because it declares the parameter; converting
    at this boundary is what keeps one spelling instead of two parsers."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if value != value:
            return "NaN"
        if value == _INFINITY:
            return "Infinity"
        if value == -_INFINITY:
            return "-Infinity"
    return str(value)


_INFINITY = float("inf")


def _named_args(args):
    """Dataset arguments in the wire form the kernel binds by NAME: a flat
    ``[name, value, name, value, ...]``. A value that is a list or tuple repeats
    its name once per entry, which is exactly how a list-kind parameter (roots,
    scene states, column specs) is stated -- so order inside one name is kept and
    no separator is invented to smuggle several values through one string."""
    flat = []
    for name, value in args.items():
        if isinstance(value, (list, tuple, set, frozenset)):
            for entry in value:
                flat.extend((str(name), _wire(entry)))
            continue
        flat.extend((str(name), _wire(value)))
    return _string_array(flat)


class RipperBridge:
    """One bridge session: Initialize once with the target install's decoder id,
    then Build/Load a cabmap, browse rows, and pull a selection into memory.
    Call from one thread at a time -- the underlying C# side is written for a
    single active session per the CLI's own model (see RipperBlenderBridge's
    doc comments on GameFileLoader/GameBundleHook static state).

    ONE decoder, never a set: the process reads one game at a time, and which
    features run is the host's own declaration on the other side of the bridge."""

    def __init__(self, decoder_id, game_root, source_options=None):
        _ensure_runtime()
        self._bridge = _bridge_type
        # How the install is read beyond its folder (see set_source_options), kept
        # per session so switching installs restates the values that install was
        # opened with. Pushed BEFORE Initialize: a decoder opens its source on apply.
        self._source_options = dict(source_options or {})
        set_source_options(self._source_options)
        self._bridge.Initialize(str(decoder_id or ""), str(game_root or ""))
        self._decoder_id = str(decoder_id or "")
        self._game_root = str(game_root or "")
        # install key -> the source options that install's cabmap was built/loaded
        # under, restated by use_session alongside its decoder and root.
        self._options_by_key = {}
        # install key -> the folder that install's datasets read, captured when its
        # cabmap loaded. use_session re-opens the session on it, so a dataset never
        # has to be told where the game is installed.
        self._roots_by_key = {}
        self._map = None
        # Per-INSTALL cabmap slots, keyed by whatever identity the host files an
        # install under (see cabmap_state.GameSession.key). maps_by_key is that key
        # -> loaded map handle; _decoder_by_key is that key -> the decoder id that
        # read it. use_session(key) flips self._map between them and, when the
        # decoder differs, reinitializes onto it. Several installs can be loaded at
        # once (a CabMapHandle is a pure value object, safe to hold many of); only
        # one decoder is ever active, which is what use_session switches.
        self.maps_by_key = {}
        self._decoder_by_key = {}

    @property
    def decoder_id(self):
        """The decoder this session was last (re)Initialize()d with -- see reinitialize()."""
        return self._decoder_id

    @property
    def game_root(self):
        """The install this session is open on. The hook that decodes it declares
        which folders under it hold content (Session.DeclareLayout), so nothing
        above this line ever spells an install layout."""
        return self._game_root

    def reinitialize(self, decoder_id, game_root=None, source_options=None):
        """Re-apply a (possibly different) decoder onto this SAME session, preserving
        self._map -- unlike constructing a fresh RipperBridge, this does not
        drop an already-loaded cabmap. Safe/idempotent on the C# side (RipperBlenderBridge.
        Initialize -> RuriHook.ApplyHooks diffs the desired hook set against the currently
        active one and only enables/disables the delta), so this is cheap even when the decoder
        is unchanged. Callers should still skip the call when it is, to avoid the log spam
        ApplyHooks prints per hook transition."""
        root = self._game_root if game_root is None else str(game_root or "")
        if source_options is not None:
            self._source_options = dict(source_options)
        set_source_options(self._source_options)
        self._bridge.Initialize(str(decoder_id or ""), root)
        self._decoder_id = str(decoder_id or "")
        self._game_root = root
        # The per-install slots restate what an install was LAST opened with, not what
        # it was opened with when its cabmap loaded: a value stated afterwards (a schema
        # file set once the form warned it was missing) has to survive switching away
        # from the install and back, so every slot at this root follows the values just
        # applied. Without this, use_session restated the stale snapshot and the kernel
        # silently lost the value the moment the tab was re-selected.
        for key, slot_root in self._roots_by_key.items():
            if slot_root == root:
                self._decoder_by_key[key] = self._decoder_id
                self._options_by_key[key] = dict(self._source_options)

    @property
    def source_options(self):
        """The source options this session was last opened with -- see set_source_options."""
        return dict(self._source_options)

    @property
    def has_map(self):
        return self._map is not None

    def build_cab_map(self, game_root, out_path):
        """Scan game_root and write a fresh cabmap to out_path. Returns 0 on success."""
        return int(self._bridge.BuildCabMap(game_root, out_path))

    def load_cab_map(self, cab_map_path, key=None):
        """Load an existing cabmap file; must be called (or build_cab_map) before
        enumerate_rows()/import_cabs(). key=None just points the current _map at it
        (the single-install path, unchanged). Passing an install key ALSO registers
        the map in this session's per-install slot, capturing the decoder that read
        it, so use_session(key) can switch back to it later without reloading."""
        handle = self._bridge.LoadCabMap(cab_map_path)
        self._map = handle
        if key is not None:
            self.maps_by_key[key] = handle
            self._decoder_by_key[key] = self._decoder_id
            self._roots_by_key[key] = self._game_root
            self._options_by_key[key] = dict(self._source_options)

    def rename_session(self, old_key, new_key):
        """Refile every per-install slot from ``old_key`` to ``new_key`` -- the
        already-loaded cabmap, its decoder and its root move together, so an
        install that only just learned its own name keeps the map it paid for."""
        if old_key == new_key:
            return
        for slot in (self.maps_by_key, self._decoder_by_key, self._roots_by_key,
                     self._options_by_key):
            if old_key in slot:
                slot[new_key] = slot.pop(old_key)

    def use_session(self, key):
        """Select a previously loaded install's cabmap (load_cab_map(path, key=...))
        as the active _map and, when that install's decoder differs from what is
        active, reinitialize onto it -- one game's decoder replaces another's,
        matching the upstream's one-decoder-at-a-time rule. No-op on the hook side
        when the target's decoder is already active, so repeated selects of the same
        install are cheap. enumerate_table/search only need the _map switch;
        import/game_data go through the freshly selected decoder."""
        if key not in self.maps_by_key:
            raise RuntimeError(
                f"No cabmap loaded for install {key!r} -- call load_cab_map(path, key={key!r}) first.")
        self._map = self.maps_by_key[key]
        decoder = self._decoder_by_key.get(key, self._decoder_id)
        root = self._roots_by_key.get(key, self._game_root)
        options = self._options_by_key.get(key, self._source_options)
        if decoder != self._decoder_id or root != self._game_root or options != self._source_options:
            self.reinitialize(decoder, root, options)

    #: The loaded map's own rows, as published. Every search and every folder listing
    #: indexes into these row ids, so whoever asked for them keeps the table it got.
    def enumerate_table(self):
        """The loaded cabmap as a column_table.ColumnTable, its display words
        already decided on the other side. One interop crossing, the buffers
        pinned rather than copied, nothing materialized per row."""
        return self.game_data("core.rows")

    def folder_children(self, folder):
        """One virtual folder's child folders and how many rows are under each."""
        return self.game_data("core.folders.children", folder=str(folder))

    def folder_files(self, folder):
        """The rows listed IN one virtual folder, as int32 row ids straight out of the reader's
        bytes -- a root folder can hold millions."""
        import numpy as np
        return np.frombuffer(self.game_data_blob("core.folders.files", folder=str(folder)), dtype="<i4")

    def folder_exists(self, folder):
        """Whether a remembered folder still exists in THIS map."""
        table = self.game_data("core.folders.exists", folder=str(folder))
        return bool(len(table)) and bool(int(float(table.cell(0, "exists"))))

    def folder_of(self, row_index, query="", folder=""):
        """(folder, leaf name) for one row as it is CURRENTLY being shown."""
        table = self.game_data("core.folders.of", row=int(row_index), query=str(query),
                               folder=str(folder))
        if not len(table):
            return "", ""
        return str(table.cell(0, "folder")), str(table.cell(0, "leaf"))

    def _ordered(self, ids, sort_column, sort_direction):
        """Row ids in the order a display column puts them (direction 1 up, 2 down), or in load
        order when none is sorted -- ordered by the reader's own engine over its own ranks, never
        here."""
        import numpy as np
        payload = np.ascontiguousarray(ids, dtype="<i4").tobytes()
        return np.frombuffer(self.game_data_blob(
            "core.rows.order", payload=payload, column=str(sort_column or ""),
            direction=int(sort_direction)), dtype="<i4")

    def search_table(self, table, query, rules=None, sort_column="name", sort_direction=0):
        """Quick search + Include/Exclude rules + sort over one cabmap's rows (the
        table enumerate_table gave), matched on the reader's own engine. Returns the
        visible row ids as a numpy int32 array, already sorted."""
        import numpy as np
        payload = self.search_data_table(table, query or "", rules)
        ids = np.frombuffer(bytes(payload), dtype="<i4")
        return self._ordered(ids, sort_column, sort_direction)

    def sort_rows(self, row_ids, sort_column, sort_direction):
        """Sort an explicit row-id subset (the folder view's listing) by a
        display column -- same order semantics as search_table."""
        return self._ordered(row_ids, sort_column, sort_direction)

    def _loaded(self):
        if self._map is None:
            raise RuntimeError("No cabmap loaded -- call load_cab_map()/build_cab_map() (or use_session(key) "
                               "to select a loaded game's map) first.")
        return self._map

    def resolve_cabs_for_paths(self, container_paths):
        """The archives that hold these addressable paths. Paths nothing hosts
        are simply absent from the answer."""
        wanted = [str(path) for path in container_paths if str(path)]
        if not wanted:
            return []
        found = []
        for path in wanted:
            table = self.game_data("core.select", query=path)
            for index in range(len(table)):
                cab = str(table.cell(index, "cab"))
                if cab and cab not in found:
                    found.append(cab)
        return found

    def resolve_closure_cab_names(self, cab_names):
        """Every archive the given ones reach, the whole closure."""
        seeds = [str(name) for name in cab_names if str(name)]
        if not seeds:
            return []
        reached = list(seeds)
        for seed in seeds:
            table = self.game_data("core.deps", query=seed, direction="forward", depth=-1)
            for index in range(len(table)):
                cab = str(table.cell(index, "cab"))
                if cab and cab not in reached:
                    reached.append(cab)
        return reached

    def find_direct_dependents(self, cab_names):
        """The archives that depend ON these, one hop back."""
        seeds = [str(name) for name in cab_names if str(name)]
        if not seeds:
            return []
        found = []
        for seed in seeds:
            table = self.game_data("core.deps", query=seed, direction="reverse", depth=0)
            for index in range(len(table)):
                cab = str(table.cell(index, "cab"))
                if cab and cab not in found and cab not in seeds:
                    found.append(cab)
        return found

    def search_data_table(self, table_or_handle, query, rules=None):
        """Row ids of a table matching ``query`` AND every enabled Include/
        Exclude rule -- run by the SAME vectorized C# engine and the SAME rule
        evaluator the cabmap browser uses, over the very buffers the table was
        built from. Nothing is matched on this side.

        Takes either a column_table.ColumnTable (from query_data_table) or a
        bare handle string (from open_host_table): both name a table the engine
        already holds. ``rules`` is the same duck type search_table takes, with
        each rule's field naming a COLUMN of that table. Returns a numpy int32
        array."""
        import numpy as np
        handle = getattr(table_or_handle, "handle", table_or_handle)
        payload = self._bridge.SearchDataTable(str(handle), query or "", _flat_rules(rules))
        return np.frombuffer(bytes(payload), dtype=np.int32)

    def export_shaders(self, seed_cabs, output_dir):
        """The shader sources behind a selection, written beside the map."""
        table = self.game_data("core.shaders", seed=list(seed_cabs), output=str(output_dir))
        return len(table)

    def export_all_shaders(self, output_dir):
        """Every shader this install carries."""
        table = self.game_data("core.shaders.all", output=str(output_dir))
        return len(table)

    def open_host_table(self, handle, columns, rows, roles=()):
        """Publish a list THIS side assembled as a searchable table, so it gets
        the same vectorized search and the same rules as every other list
        instead of a hand-rolled substring scan.

        ``rows`` is an iterable of per-row sequences, one value per column, in
        ``columns`` order; everything is sent as text and a column spelled with a
        trailing ``#`` is parsed back to a number on arrival. ``roles`` states
        positionally what each column ANSWERS (see column_table's LABEL/KEY/...),
        which is what lets a list this side assembled be drawn by the same view
        engine as one the hook published. Re-publishing under the same handle
        replaces it, which is what a refreshed list wants. Needs no cabmap and
        no game hook -- searching is a core capability. Returns the handle."""
        flat = []
        for row in rows:
            values = list(row)
            if len(values) != len(columns):
                raise ValueError(f"row has {len(values)} value(s) for {len(columns)} column(s)")
            flat.extend("" if value is None else str(value) for value in values)
        return str(self._bridge.OpenHostTable(str(handle), _string_array(list(columns)),
                                              _int_array(list(roles)) if roles else None,
                                              _string_array(flat)))

    def game_data(self, dataset_id, cancellation=None, **args):
        """One published dataset, as a column_table.ColumnTable.

        This is the ONE way data crosses: whatever is read -- a parts catalog, a
        scene list, a build plan, a face's pattern table, the session's own
        description -- arrives in the same columnar shape, already searchable under
        ``table.handle`` (pass it to search_data_table) and cached on the C# side by
        (id, args). Arguments are passed BY NAME (``game_data(id, map="map01",
        sceneState=[1, 2])``): the kernel validates them against what that dataset
        declares, so a wrong name fails loudly here instead of silently shifting
        every later argument along. Nothing is parsed on this side.

        ``core.datasets`` is the one id worth knowing: it lists every dataset this
        session publishes, with the role a caller binds it by."""
        import System.Threading
        from . import column_table
        token = cancellation if cancellation is not None \
            else getattr(System.Threading.CancellationToken, "None")
        return column_table.ColumnTable.from_pinned(self._bridge.GameDataTable(
            self._map, str(dataset_id), _named_args(args), token))

    def open_view(self, table_or_handle, facet="", query="", rules=None, note="",
                  shipped_only=True, sort_column="", sort_direction=0, window=0,
                  ordered=False, label_column="", group_column=""):
        """One drawn list, composed ENTIRELY on the C# side: quick search, the
        Include/Exclude rules, the facet, the shipped test, the sort, the group
        headers, the window and the status line all happen there, over the very
        buffers the table was built from.

        What comes back is the answer, not the ingredients: a View whose rows are
        the lines to draw in the order to draw them. This side reads cells out of
        it and does not filter, sort, join, count or format anything -- which is
        what makes a list of eighteen thousand cost the same as one of fifty.

        Which column answers what (the name, the id, the facet, the section) is
        not stated here: it is the column's own role, declared in C# where the
        table is built."""
        handle = getattr(table_or_handle, "handle", table_or_handle)
        return self._bridge.OpenView(str(handle), str(facet), query or "", _flat_rules(rules),
                                     str(note), bool(shipped_only), str(sort_column),
                                     int(sort_direction), int(window), bool(ordered),
                                     str(label_column), str(group_column))

    def game_data_blob(self, dataset_id, payload=None, cancellation=None, **args):
        """One published dataset whose answer is bytes rather than rows.

        ``payload`` is what the CALLER brings that the reader cannot derive --
        a performance to restate, an image to re-encode. A dataset that needs
        nothing brought gets nothing."""
        import System.Threading
        token = (cancellation if cancellation is not None
                 else getattr(System.Threading.CancellationToken, "None"))
        return bytes(self._bridge.GameDataBlob(
            self._map, str(dataset_id), _named_args(args),
            bytes(payload or b""), token))

    def release_statement(self, **args):
        """The selection these statement arguments asked about has been placed: the reader
        lets go of the flattening its tables were cut from, and hands the memory back.

        In this order. This side's wrappers over the reader's objects -- the array each
        texture crossed as -- keep those objects alive until pythonnet's finalizer releases
        them, and it batches that until two hundred have queued up; released first, the
        reader's collection right after finds them unreferenced instead of walking a heap
        whose largest objects are still held by handles nobody wants any more."""
        import gc

        from Python.Runtime import Finalizer
        gc.collect()
        Finalizer.Instance.Collect()
        self._bridge.ReleaseStatement(self._map, _named_args(args))
