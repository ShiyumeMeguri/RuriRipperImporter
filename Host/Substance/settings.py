"""What Painter stores between sessions: WHICH keys exist and what they mean.

The store itself (defaults, unknown-key rejection, versioned repair, atomic
writes) is ``RuriRipperPyBridge.runtime.settings`` -- none of that is
Painter-specific. What is Painter-specific is the key set below, and where the
workspace lives: the user's Painter resources directory, never inside this
package, which is a checkout that gets replaced wholesale.

The IMPORT OPTIONS are not listed here. They come from the one schema
(``Kernel.options``), filtered to what this host can honour, so the panel's
widgets, the store's defaults and what the pipeline reads are the same
declaration. Blender keeps the equivalent in its own AddonPreferences and
operator properties, generated from that same schema; only the storage differs,
because only the storage is a fact about the application.
"""

from __future__ import annotations

import os

from ...Kernel import host as host_port
from ...Kernel import options as kernel_options
from ...RuriRipperPyBridge.runtime import settings as _settings

#: Painter's own state -- paths the user types, what the install published about
#: itself, and how the dock was last left.
_PAINTER_DEFAULTS = {
    # Folder that directly contains Ruri.RipperHook.dll AND
    # Ruri.RipperHook.CLI.runtimeconfig.json, e.g.
    # <Ruri-RipperHook checkout>/Source/0Bins/Release -- the one folder every
    # reader builds into, kernel and decoder modules alike.
    "ripperhook_bin": "",
    # What the panels remember between sessions, by the name each state is filed
    # under: {"cabmap": {...}}. The CONTENTS are the kernel's -- every field it
    # marks remembered, install tabs and all -- and this is only where Painter
    # keeps them, which is the half that is about Painter. Blender's equivalent
    # is the .blend.
    #
    # It replaces the flat game_root/cabmap_path/decoder_id/game_name this file
    # used to hold: those were one install's worth of a panel that now browses
    # several, and nothing had read them since the tabs arrived. There is no
    # reader for them -- an install is typed once more and remembered from then
    # on, which is a second of work against a migration path nobody could test.
    "panels": {},
    # WHICH dock was put into the right-hand strip -- the objectName, not a bool.
    #
    # Painter keys a dock's saved area and geometry by objectName, so a record
    # that the dock "has been placed" is only true OF THAT NAME. Stored as a
    # bool, it went stale the moment the merged plugin renamed its dock: the
    # early-out still fired, Painter had no geometry saved under the new name,
    # and the panel came up floating in the middle of the screen. Storing the
    # name makes the record self-invalidating -- there is no version to remember
    # to bump, because the thing that changed IS the key.
    "dock_placed_for": "",
    # Bumped when a stored file has to be repaired rather than trusted. Version 1
    # was written by a panel that connected each widget's change signal BEFORE
    # populating it from the settings, so the first setChecked() during load fired
    # a save of the still-empty widget row. Version 2 predates the mandatory
    # Unity -> glTF UV V flip. Bumping this resets exactly the option block;
    # typed paths and the decoder choice are the user's own input and survive.
    #
    # Retired option keys need no bump at all: JsonSettings drops any key absent
    # from the defaults on read, so the ones the single schema replaced
    # ("lod0_only", "keep_unity_uv_origin") disappear on the next save by
    # themselves.
    "settings_version": 4,
}


def _option_keys():
    return tuple(entry.key for entry in kernel_options.schema(host_port.current().capabilities))


_DEFAULTS = dict(_PAINTER_DEFAULTS,
                 **kernel_options.defaults(host_port.current().capabilities))

#: Keys whose stored value is only meaningful once the panel has deliberately
#: written it. Everything the user types (paths, decoder choice) is preserved
#: across a migration; the options are reset to the schema's defaults.
_OPTION_KEYS = _option_keys()


def plugin_dir():
    """The package root -- three levels up from this driver module."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def user_resources_dir():
    """<Painter user resources>/python -- the parent of the plugins folder this
    package was loaded from."""
    return os.path.dirname(os.path.dirname(plugin_dir()))


def workspace_dir():
    """Everything this plugin generates (settings, installed runtime, model and
    texture cache) lives under one folder outside the package."""
    path = os.path.join(user_resources_dir(), "RuriRipperWorkspace")
    os.makedirs(path, exist_ok=True)
    return path


def cache_dir():
    path = os.path.join(workspace_dir(), "cache")
    os.makedirs(path, exist_ok=True)
    return path


_STORE = _settings.JsonSettings(
    os.path.join(workspace_dir(), "settings.json"), _DEFAULTS, _OPTION_KEYS)


def load():
    return _STORE.load()


def get(key, default=None):
    return _STORE.get(key, default)


def set_many(**values):
    return _STORE.set_many(**values)


def save():
    return _STORE.save()


def remember_panel(name, values):
    """Keep what a panel says it is worth reopening with, under its own name.

    One key for every panel rather than a key per field: what a panel remembers
    is the kernel's schema talking, and a settings file that named those fields
    would be a second copy of it -- the kind that still lists an install's folder
    a year after the panel started holding several."""
    stored = dict(_STORE.get("panels", {}))
    if stored.get(name) == values:
        return False
    stored[name] = values
    return _STORE.set_many(panels=stored)


def recalled_panel(name):
    """What that panel was last left holding, or {} for a first run."""
    return dict(_STORE.get("panels", {})).get(name) or {}


def import_options():
    """The option dict the pipeline consumes -- the schema's keys, this host's
    subset, with whatever the user last set."""
    return {key: _STORE.get(key, _DEFAULTS[key]) for key in _OPTION_KEYS}
