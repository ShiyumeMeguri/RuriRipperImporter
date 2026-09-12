"""One driver per application. Exactly one of them is live in any process.

Which one is not configured and not guessed: it is read off what the
interpreter this code was imported INTO can import. An application embeds its
own Python and its own API module, so ``bpy`` present means this is Blender and
``substance_painter`` present means this is Painter -- the most direct fact
available, with nothing to keep in step.

Two present at once, or neither, is an error rather than a preference order. A
preference order is how a plugin ends up half-loaded in a host it was never
meant for, reporting a missing attribute three stages later.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys

#: Driver folder name -> the module whose presence proves that application.
DRIVERS = {
    "Blender": "bpy",
    "Substance": "substance_painter",
}


def _present(name):
    """Whether this interpreter has that application's module.

    ``sys.modules`` first, and not as a shortcut: inside a host, its own API
    module is ALREADY imported, and asking the import system to find a spec for
    something already loaded is the less direct question -- it raises outright
    for a module injected without one."""
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def detect():
    """The name of the driver for the application this is running inside."""
    present = sorted(name for name, probe in DRIVERS.items() if _present(probe))
    if len(present) == 1:
        return present[0]
    if not present:
        raise RuntimeError(
            "RuriRipperImporter is running outside every application it has a driver for. "
            "Expected one of: " + ", ".join(
                "{0} ({1})".format(name, probe) for name, probe in sorted(DRIVERS.items())))
    raise RuntimeError(
        "two host applications are importable at once ({0}) -- the driver cannot be "
        "read off the interpreter any more".format(", ".join(present)))


def load():
    """Import the detected driver package. Importing it binds it
    (:func:`Kernel.host.bind`); nothing else here does."""
    return importlib.import_module("." + detect(), __name__)
