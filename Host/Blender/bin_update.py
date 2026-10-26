"""Keeping the RipperHook bin folder current from inside Blender.

One button in the add-on preferences downloads the newest build and points the
preference at it; the same check runs once when the add-on starts, and again
whenever booting the reader fails, because a missing or stale reader is the
failure a newer build most often cures. Everything here is the host half:
:class:`Kernel.bridge.bin_update.Job` does the work on a thread, and this module
applies its result on Blender's main thread, where the preferences are safe to write.
"""

from __future__ import annotations

import bpy

from ...Kernel.bridge import bin_update, pythonnet_bridge

POLL_SECONDS = 0.25

_job = None


def running():
    return _job is not None and not _job.finished


def message():
    return _job.message if _job is not None else ""


def failed():
    return _job is not None and bool(_job.error)


def _redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def _apply(preferences, replace_custom):
    if preferences is None or _job.error or not _job.folder:
        return
    current = preferences.ripperhook_repo
    if current == _job.folder or not (replace_custom or not current or bin_update.is_managed(current)):
        return
    preferences.ripperhook_repo = _job.folder
    print("[RuriRipper] RipperHook bin dir -> {0}".format(_job.folder))


def start(replace_custom):
    """Begin a check-and-install unless one is already running. ``replace_custom``
    is whether a path the user typed themselves may be replaced: the button says
    yes, the automatic checks only ever move a path this updater made."""
    global _job
    if running():
        return
    from . import _preferences
    preferences = _preferences()
    _job = bin_update.Job(preferences.ripperhook_repo if preferences is not None else "")

    def poll():
        _redraw()
        if not _job.finished:
            return POLL_SECONDS
        _apply(_preferences(), replace_custom)
        _redraw()
        return None

    bpy.app.timers.register(poll, first_interval=POLL_SECONDS)


class RURI_OT_UpdateRipperHook(bpy.types.Operator):
    bl_idname = "ruri_ripper.update_ripperhook"
    bl_label = "Download / Update RipperHook"
    bl_description = "Download the newest RipperHook build and set the bin dir to it"

    def execute(self, context):
        start(True)
        return {"FINISHED"}


def _automatic():
    from . import _preferences
    preferences = _preferences()
    if preferences is None or preferences.ripperhook_auto_update:
        start(False)


def register():
    bpy.utils.register_class(RURI_OT_UpdateRipperHook)

    def on_boot_failure(exception):
        print("[RuriRipper] reader failed to boot ({0}); checking for a newer RipperHook".format(exception))
        bpy.app.timers.register(_automatic, first_interval=0)

    pythonnet_bridge.set_failure_listener(on_boot_failure)
    bpy.app.timers.register(_automatic, first_interval=0)


def unregister():
    pythonnet_bridge.set_failure_listener(None)
    bpy.utils.unregister_class(RURI_OT_UpdateRipperHook)
