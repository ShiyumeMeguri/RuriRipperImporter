"""Bringing the toolchain up inside a host -- once, for every host.

Both plugins used to open with the same twenty lines in a different order and a
different comment: point the workspace somewhere writable, push the machine's
RipperHook bin dir and the extra decoder assemblies into the bridge, declare
which image containers this application can decode, claim the process-wide CLR
runtime before anything else in the process triggers its own ``import clr``, and
kick off the dependency install without blocking the UI.

None of that is host-specific. What IS host-specific is the four answers, and a
driver states those by being a :class:`Kernel.host.Host`. So the sequence lives
here and the drivers call it.

Order is not incidental:

1. The workspace has to be known before the bootstrap can find (or install) the
   private runtime folder, and before any settings file is read.
2. The bin dir has to be pushed before the early CoreCLR claim, because the claim
   resolves ``Ruri.RipperHook.CLI.runtimeconfig.json`` relative to it.
3. The claim has to happen before any OTHER plugin in this application gets a
   chance to ``import clr``, which on Windows defaults to .NET Framework and
   would lock out the net10.0 assembly for the rest of the session -- pythonnet
   allows exactly one runtime per process.
4. The dependency install runs asynchronously, because a first run can take
   10-60 seconds and neither application may freeze for it. ``on_ready`` closes
   the one window step 3 cannot: pythonnet becoming importable only partway
   through the session, after that claim already no-opped.
"""

from __future__ import annotations

from ..RuriRipperPyBridge.runtime import bootstrap as _runtime_bootstrap
from ..RuriRipperPyBridge.runtime import pythonnet_bridge, workspace
from . import host as host_port


def configure():
    """Bring the bound driver's process up. Returns the host.

    Separate from :func:`Kernel.host.bind`, which a driver does at import: an
    application's settings are not readable until its own lifecycle has run far
    enough (Blender's AddonPreferences class has to be registered first), while
    the identity a driver publishes -- its name and its capabilities -- is
    needed the moment anything in the kernel is touched."""
    host = host_port.current()

    workspace.configure(host.workspace_dir())
    _runtime_bootstrap.activate()

    pythonnet_bridge.set_bin_dir(host.bin_dir())
    pythonnet_bridge.set_bin_dir_hint(host.bin_dir_hint())
    pythonnet_bridge.set_texture_formats(tuple(host.texture_containers()))

    try:
        pythonnet_bridge.claim_runtime_early()
    except Exception as exc:  # best effort -- _ensure_runtime() retries for real on first use
        host.log(host_port.INFO, "early CoreCLR claim skipped: {0}".format(exc))

    _runtime_bootstrap.ensure_async(
        report_fn=lambda message: host.log(host_port.INFO, message),
        on_ready=pythonnet_bridge.claim_runtime_early)
    return host


def republish_paths():
    """Re-push the paths after the user edits them mid-session. The values are
    read back off the bound host, so a driver only has to say WHERE its user
    typed them once."""
    host = host_port.current()
    pythonnet_bridge.set_bin_dir(host.bin_dir())
