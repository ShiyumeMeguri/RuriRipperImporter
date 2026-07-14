"""Auto-install pythonnet (the `clr` bridge to .NET) into Blender's bundled
Python on first use. Blender ships no pip-installed packages by default, and
calling in-process into Ruri.RipperHook.dll needs pythonnet's CoreCLR host.

register() should call ensure_pythonnet_async() so a first-time ~10-60s pip
install doesn't freeze Blender's UI; gate cabmap/import operators on
is_ready(). Headless scripts (the CLI self-loop) should call
ensure_pythonnet_blocking() instead, since there's no UI to freeze and a
script wants a definite yes/no before proceeding.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading

# clr-loader < 0.2.10 mis-parses .NET 10.x version strings ("10.0" -> "10..0"),
# throwing FrameworkMissingFailure when booting CoreCLR (fixed upstream by
# pythonnet/clr-loader#104) -- pin past that fix rather than trusting a stale
# cached "latest". pythonnet>=3.1.0 is the first line with clean CPython 3.13
# wheels (Blender 5.1's bundled interpreter).
_PYTHONNET_SPEC = "pythonnet>=3.1.0"
_CLR_LOADER_SPEC = "clr-loader>=0.3.1"

_state_lock = threading.Lock()
_ready = False
_error = None
_install_thread = None


def is_ready():
    with _state_lock:
        return _ready


def last_error():
    with _state_lock:
        return _error


def _findable(name):
    """importlib.util.find_spec(name) without the crash: if some OTHER
    already-loaded addon put a module into sys.modules without going through
    normal import machinery (pythonnet's own `clr` can end up this way once
    something else has called pythonnet.load()/set_runtime()), find_spec
    raises ValueError("...__spec__ is None") instead of returning cleanly.
    A module already sitting in sys.modules at all -- however it got there --
    counts as present."""
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ValueError, ModuleNotFoundError, ImportError):
        return False


def _probe():
    """Check whether pythonnet is installed WITHOUT importing `clr` for the
    first time -- a bare `import clr` has the side effect of implicitly
    picking (and locking in) a default CLR runtime, which on Windows means
    .NET Framework. pythonnet_bridge needs to be the one to set CoreCLR
    explicitly (via pythonnet.set_runtime) before `clr` is ever imported
    anywhere, or its later set_runtime(get_coreclr(...)) call fails with
    "runtime already loaded"."""
    return _findable("clr") and _findable("pythonnet") and _findable("clr_loader")


def _install(report_fn):
    try:
        # Belt-and-suspenders: Blender ships pip, but a from-source Python
        # build might not have it wired up yet.
        subprocess.run([sys.executable, "-m", "ensurepip", "--default-pip"],
                        check=False, capture_output=True, text=True)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir",
             "--no-warn-script-location", "--upgrade",
             _PYTHONNET_SPEC, _CLR_LOADER_SPEC],
            check=True, capture_output=True, text=True)
        report_fn(result.stdout[-2000:])
        return True, None
    except subprocess.CalledProcessError as exc:
        return False, f"pip install failed (exit {exc.returncode}): {exc.stderr[-2000:]}"
    except OSError as exc:
        return False, f"pip install failed: {exc}"


def _try_claim_runtime(report_fn):
    """Claim CoreCLR the moment pythonnet becomes usable -- whether it was
    already installed (this runs from register()'s synchronous
    claim_runtime_early() call moments before anyway, so this is a fast
    no-op) or just got installed by this very worker thread (the one gap
    register()'s synchronous claim can't cover, since pythonnet isn't
    importable yet at that point) -- to close the window before anything
    else in this Blender session could import `clr` first. Best-effort: any
    failure here just gets logged, since the authoritative attempt still
    happens on first real bridge use (pythonnet_bridge._ensure_runtime),
    which raises for real if this couldn't be resolved."""
    try:
        from . import pythonnet_bridge
    except ImportError:
        import pythonnet_bridge
    try:
        pythonnet_bridge.claim_runtime_early()
    except Exception as exc:
        report_fn(f"[RuriRipper] early CoreCLR claim (post-install) skipped: {exc}")


def ensure_pythonnet_async(report_fn=print):
    """Kick off the install (if needed) on a daemon worker thread. Idempotent:
    a call while one is already running or has already succeeded is a no-op."""
    global _install_thread
    with _state_lock:
        if _ready or _install_thread is not None:
            return

    def _worker():
        global _ready, _error
        if _probe():
            _try_claim_runtime(report_fn)
            with _state_lock:
                _ready = True
            return
        report_fn("[RuriRipper] pythonnet not found -- installing into Blender's bundled Python...")
        ok, err = _install(report_fn)
        ready_now = ok and _probe()
        if ready_now:
            _try_claim_runtime(report_fn)
        with _state_lock:
            _ready = ready_now
            _error = None if ready_now else (err or "pythonnet still not importable after install.")
        report_fn("[RuriRipper] pythonnet ready." if ready_now
                   else f"[RuriRipper] pythonnet install failed: {_error}")

    _install_thread = threading.Thread(target=_worker, name="RuriRipperPythonnetInstall", daemon=True)
    _install_thread.start()


def ensure_pythonnet_blocking(report_fn=print):
    """Synchronous variant for headless/CLI scripts where blocking is fine."""
    global _ready, _error
    if _probe():
        with _state_lock:
            _ready = True
        return True
    report_fn("[RuriRipper] pythonnet not found -- installing into Blender's bundled Python...")
    ok, err = _install(report_fn)
    ready_now = ok and _probe()
    with _state_lock:
        _ready = ready_now
        _error = None if ready_now else (err or "pythonnet still not importable after install.")
    if not ready_now:
        report_fn(f"[RuriRipper] pythonnet install failed: {_error}")
    return ready_now
