"""Where everything this toolchain generates is allowed to live.

One rule: never inside the plugin/add-on checkout. A checkout is replaced
wholesale by a ``git pull`` (and in Painter's case may sit in a read-only app
install), so an installed runtime, a settings file or a texture cache written
there is data waiting to be deleted. Nothing here is hardcoded either -- a host
declares its own root, and if it declares none the OS's own per-user data
directory is used.

Resolution order:

1. ``configure(path)`` -- what the host passed in (Painter points this at
   ``<user resources>/python/RuriRipperWorkspace``, so everything stays with the
   user profile rather than the app install).
2. ``$RURI_RIPPER_WORKSPACE`` -- the headless/CI escape hatch.
3. The platform user-data directory: ``%LOCALAPPDATA%\\RuriRipper`` on Windows,
   ``~/Library/Application Support/RuriRipper`` on macOS, ``$XDG_DATA_HOME``
   (or ``~/.local/share``) ``/RuriRipper`` elsewhere.
"""

from __future__ import annotations

import os
import sys

ENV_VAR = "RURI_RIPPER_WORKSPACE"
_APP_DIR_NAME = "RuriRipper"

_configured = None


def configure(path):
    """Point the workspace at a host-chosen directory (None/"" clears it)."""
    global _configured
    _configured = (path or "").strip() or None


def configured():
    return _configured


def platform_default():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, _APP_DIR_NAME)


def root(create=True):
    """The workspace root, created on demand."""
    path = _configured or (os.environ.get(ENV_VAR) or "").strip() or platform_default()
    path = os.path.abspath(path)
    if create:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
    return path


def subdir(*names, **kwargs):
    """A directory under the workspace root, created on demand."""
    create = kwargs.pop("create", True)
    if kwargs:
        raise TypeError("unexpected keyword arguments: {0}".format(sorted(kwargs)))
    path = os.path.join(root(create=create), *names)
    if create:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
    return path
