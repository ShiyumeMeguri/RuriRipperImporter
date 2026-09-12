"""Install this toolchain's binary dependencies into a private, ABI-keyed
folder on first use.

A DCC's bundled Python ships almost nothing; the bridge needs pythonnet (the
``clr`` CoreCLR host, whose clr_loader -> cffi dependency is a compiled
extension) and numpy (mesh decode / image math). Four things this has to get
right, each of which is a real failure that has actually happened:

* **Never import ``clr`` to check for it.** A bare ``import clr`` picks -- and
  permanently locks in -- a default CLR runtime, which on Windows means .NET
  Framework, which cannot load a net10.0 assembly. Probing therefore goes
  through ``importlib.util.find_spec`` only, and the CoreCLR claim happens
  exactly once, in ``pythonnet_bridge``.

* **``sys.executable`` is not always an interpreter.** Inside Substance Painter
  it is the APPLICATION exe, so ``sys.executable -m pip`` cannot work. Real
  interpreters are looked for next to ``sys.prefix``/``sys.base_prefix`` (Painter
  points PYTHONHOME at ``resources/pythonsdk``), with an in-process ``runpy``
  pip run as the last resort. Under Blender ``sys.executable`` IS a real
  interpreter and is used directly.

* **The interpreter that RUNS pip need not have the ABI wheels are needed for.**
  Painter ships both python311.dll and python313.dll while its own
  ``pythonsdk/python.exe`` reports 3.13; installing with that exe's defaults
  fetches wheels for the wrong ABI, and a cp313 cffi/numpy is simply not
  loadable by a live cp311 interpreter. Every install therefore passes the LIVE
  interpreter's ``--python-version``/``--abi``/``--platform`` explicitly,
  together with ``--only-binary=:all: --target``, which makes resolution
  independent of whichever interpreter happens to run pip.

* **Install only what is actually missing.** Blender ships numpy; reinstalling
  it into a private directory that then shadows the host's own copy is both a
  waste and a risk. The requirement list is per-module, and only the specs whose
  module cannot be found are passed to pip.

The target folder is keyed by that same ABI tag, so a host upgrade that changes
the embedded interpreter simply starts a fresh, correct folder next to the old
one instead of silently loading incompatible binaries.

Stdlib only, by construction: this module has to be importable in an
interpreter that has none of the things it installs.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
import sysconfig
import threading

from . import workspace

# Reloading this module resets the install/probe guards below while the worker
# thread they track is still running, so a host's script reload has to skip it.
HOLDS_PROCESS_STATE = True

# clr-loader < 0.2.10 mis-parses .NET 10.x version strings ("10.0" -> "10..0"),
# throwing FrameworkMissingFailure when booting CoreCLR (fixed upstream by
# pythonnet/clr-loader#104) -- pin past that fix rather than trusting a stale
# cached "latest". pythonnet>=3.1.0 is the first line with clean CPython 3.13
# wheels.
DEFAULT_REQUIREMENTS = (
    ("clr", "pythonnet>=3.1.0"),
    ("pythonnet", "pythonnet>=3.1.0"),
    ("clr_loader", "clr-loader>=0.3.1"),
    ("numpy", "numpy>=1.24"),
)

_state_lock = threading.Lock()
_ready = False
_error = None
_install_thread = None
_requirements = DEFAULT_REQUIREMENTS
_target_override = None
_log = [print]


def configure(requirements=None, target_dir=None, report_fn=None):
    """Host setup. ``requirements`` is a sequence of (module, pip spec) pairs;
    ``target_dir`` overrides the ABI-keyed workspace folder entirely (rarely
    wanted -- configure ``workspace`` instead)."""
    global _requirements, _target_override
    if requirements is not None:
        _requirements = tuple(tuple(item) for item in requirements)
    if target_dir is not None:
        _target_override = (target_dir or "").strip() or None
    if report_fn is not None:
        set_reporter(report_fn)


def set_reporter(report_fn):
    _log[0] = report_fn


def _report(message):
    try:
        _log[0](message)
    except Exception:
        pass  # a broken reporter must never take the install down with it


# --------------------------------------------------------------------------
# Private site-packages
# --------------------------------------------------------------------------
def abi_tag():
    """cp<major><minor>-<platform> for the LIVE interpreter -- the identity the
    installed wheels have to match."""
    major, minor = sys.version_info[:2]
    platform = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    return "cp{0}{1}-{2}".format(major, minor, platform)


def runtime_dir():
    """``<workspace>/runtime/<abi tag>`` -- where the private wheels live."""
    if _target_override:
        return _target_override
    return workspace.subdir("runtime", abi_tag())


def activate():
    """Put the private folder on sys.path (idempotent). Safe to call before the
    install has happened -- a missing folder is simply not added."""
    target = runtime_dir()
    if os.path.isdir(target) and target not in sys.path:
        sys.path.insert(0, target)
    return target


# --------------------------------------------------------------------------
# Probing
# --------------------------------------------------------------------------
def _findable(name):
    """``find_spec(name)`` without the crash: a module already in sys.modules --
    however it got there, which for pythonnet's ``clr`` can be outside normal
    import machinery -- counts as present, and find_spec raises ValueError
    rather than returning cleanly for those."""
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ValueError, ModuleNotFoundError, ImportError):
        return False


def missing_specs():
    """Ordered, de-duplicated pip specs for the requirements whose module is not
    importable. Empty means everything is already available."""
    specs = []
    for module, spec in _requirements:
        if _findable(module):
            continue
        if spec not in specs:
            specs.append(spec)
    return specs


def probe():
    """Whether every dependency is importable, WITHOUT importing ``clr`` for the
    first time (see the module docstring)."""
    activate()
    return not missing_specs()


def is_ready():
    with _state_lock:
        return _ready


def last_error():
    with _state_lock:
        return _error


# --------------------------------------------------------------------------
# Install
# --------------------------------------------------------------------------
def _interpreter_candidates():
    """Real python executables, most-likely first. The host's own
    ``sys.executable`` is only accepted when it genuinely looks like an
    interpreter, which under Painter it does not."""
    exe = "python.exe" if os.name == "nt" else "python3"
    bases = [sys.prefix, sys.base_prefix, sys.exec_prefix]
    # Painter's sys.executable is the application; its bundled interpreter sits
    # in resources/pythonsdk next to it. Derived, never hardcoded -- and only a
    # fallback for the case where PYTHONHOME did not make sys.prefix point there
    # already.
    app_dir = os.path.dirname(os.path.abspath(sys.executable or ""))
    if app_dir:
        bases.append(os.path.join(app_dir, "resources", "pythonsdk"))
    found = []
    for base in bases:
        for candidate in (os.path.join(base, exe),
                          os.path.join(base, "bin", exe),
                          os.path.join(base, "Scripts", exe)):
            if os.path.isfile(candidate) and candidate not in found:
                found.append(candidate)
    own = os.path.basename(sys.executable or "").lower()
    if own.startswith("python") and os.path.isfile(sys.executable):
        if sys.executable not in found:
            found.insert(0, sys.executable)
    return found


def _pip_arguments(target, specs):
    """Explicit-ABI, wheels-only, into-target install -- see the module
    docstring for why the ABI can never be left to the running interpreter."""
    major, minor = sys.version_info[:2]
    platform = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    return [
        "install", "--no-cache-dir", "--no-warn-script-location", "--upgrade",
        "--target", target,
        "--only-binary=:all:",
        "--python-version", "{0}.{1}".format(major, minor),
        "--implementation", "cp",
        "--abi", "cp{0}{1}".format(major, minor),
        "--platform", platform,
    ] + list(specs)


def _no_window():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_external_pip(target, specs):
    last = None
    for interpreter in _interpreter_candidates():
        # Belt-and-suspenders: every shipped interpreter here has pip, but a
        # from-source build might not have it wired up yet.
        try:
            subprocess.run([interpreter, "-m", "ensurepip", "--default-pip"],
                           check=False, capture_output=True, text=True,
                           creationflags=_no_window())
        except OSError:
            pass
        try:
            result = subprocess.run(
                [interpreter, "-m", "pip"] + _pip_arguments(target, specs),
                check=False, capture_output=True, text=True,
                creationflags=_no_window())
        except OSError as exc:
            last = "{0}: {1}".format(interpreter, exc)
            continue
        if result.returncode == 0:
            _report(result.stdout[-2000:])
            return True, None
        last = "{0} exited {1}: {2}".format(interpreter, result.returncode,
                                            (result.stderr or result.stdout)[-2000:])
    return False, last or "no python interpreter found next to sys.prefix"


def _run_inprocess_pip(target, specs):
    """Last resort when no interpreter executable exists: run the pip the host
    already ships, inside this very interpreter. Only ever a --target install
    (never a self-upgrade), which is the shape that does not disturb the running
    pip's own state."""
    import runpy

    argv = sys.argv
    sys.argv = ["pip"] + _pip_arguments(target, specs)
    try:
        runpy.run_module("pip", run_name="__main__")
    except SystemExit as exc:
        code = exc.code or 0
        if code:
            return False, "in-process pip exited {0}".format(code)
    except Exception as exc:  # pragma: no cover - pip internals
        return False, "in-process pip failed: {0}".format(exc)
    finally:
        sys.argv = argv
    return True, None


def _install(specs):
    target = runtime_dir()
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        return False, "cannot create {0}: {1}".format(target, exc)
    ok, err = _run_external_pip(target, specs)
    if not ok:
        _report("[RuriRipper] external pip unavailable ({0}) -- trying in-process pip.".format(err))
        ok, err = _run_inprocess_pip(target, specs)
    if ok:
        activate()
        importlib.invalidate_caches()
    return ok, err


def _finish(ok, err, on_ready):
    global _ready, _error
    ready_now = ok and probe()
    with _state_lock:
        _ready = ready_now
        _error = None if ready_now else (
            err or "dependencies still not importable after install: {0}".format(
                ", ".join(missing_specs())))
    if ready_now:
        _report("[RuriRipper] runtime ready.")
        _call_on_ready(on_ready)
    else:
        _report("[RuriRipper] runtime install failed: {0}".format(_error))
    return ready_now


def _call_on_ready(on_ready):
    """Hook for whatever must happen the moment the dependencies exist -- in
    practice claiming CoreCLR, which cannot be done before pythonnet is
    importable and must not wait for the user's first click. Best-effort: the
    authoritative attempt still happens on first real bridge use."""
    if on_ready is None:
        return
    try:
        on_ready()
    except Exception as exc:
        _report("[RuriRipper] post-install hook skipped: {0}".format(exc))


def ensure_async(report_fn=None, on_ready=None):
    """Kick the install (if needed) onto a daemon worker so a first-time
    ~10-60s pip run never freezes the host's UI. Idempotent: a call while one is
    already running, or after one has succeeded, is a no-op."""
    global _install_thread
    if report_fn is not None:
        set_reporter(report_fn)
    with _state_lock:
        if _ready or _install_thread is not None:
            return

    def _worker():
        global _install_thread, _ready
        try:
            if probe():
                with _state_lock:
                    _ready = True
                _call_on_ready(on_ready)
                return
            specs = missing_specs()
            _report("[RuriRipper] installing {0} into {1} ...".format(
                ", ".join(specs), runtime_dir()))
            ok, err = _install(specs)
            _finish(ok, err, on_ready)
        finally:
            with _state_lock:
                _install_thread = None

    _install_thread = threading.Thread(
        target=_worker, name="RuriRipperRuntimeInstall", daemon=True)
    _install_thread.start()


def ensure_blocking(report_fn=None, on_ready=None):
    """Synchronous variant, for a headless script or the moment a user presses
    a button that needs the bridge and wants a definite yes/no."""
    global _ready
    if report_fn is not None:
        set_reporter(report_fn)
    if probe():
        with _state_lock:
            _ready = True
        _call_on_ready(on_ready)
        return True
    specs = missing_specs()
    _report("[RuriRipper] installing {0} ...".format(", ".join(specs)))
    ok, err = _install(specs)
    return _finish(ok, err, on_ready)
