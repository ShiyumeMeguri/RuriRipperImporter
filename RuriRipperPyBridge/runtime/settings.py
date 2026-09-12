"""A small persisted settings store, for hosts that have no preference system.

Blender has AddonPreferences and keeps its one machine-specific path there.
Painter has nothing equivalent for plugins, so its settings are a JSON file --
and that file's mechanics (defaults, unknown-key rejection, versioned repair,
thread safety, not corrupting itself on a crash) are not Painter-specific at
all. This is that mechanism; the KEYS and their meanings stay with the host that
owns them.

Two properties worth calling out:

* **Writes are atomic.** Serialise to a sibling temp file, then ``os.replace``.
  A settings file is read at startup and written on every widget change; a
  crash or a power cut mid-write must not leave a truncated JSON that then
  silently resets every preference the user ever set.
* **Migration is by explicit key reset, not by "clear everything".** A settings
  file written by an older, buggy version can hold values that were never
  deliberate (Painter's v1 panel connected its change signals before populating
  the widgets, so loading a file wrote every option back as off). Bumping
  ``version_key`` resets exactly the declared ``volatile_keys`` -- typed paths,
  selections and anything else the user actually entered survive.
"""

from __future__ import annotations

import json
import os
import threading


class JsonSettings:
    """Defaults-backed JSON settings. Keys not in ``defaults`` are rejected on
    both read and write, so a stale file can never inject unknown state."""

    def __init__(self, path, defaults, volatile_keys=(), version_key="settings_version"):
        self._path = path
        self._defaults = dict(defaults)
        self._volatile_keys = tuple(volatile_keys)
        self._version_key = version_key
        self._lock = threading.RLock()
        self._cache = None

    @property
    def path(self):
        return self._path

    @property
    def defaults(self):
        return dict(self._defaults)

    # -- load / save --------------------------------------------------------

    def load(self):
        with self._lock:
            if self._cache is not None:
                return self._cache
            data = dict(self._defaults)
            repaired = False
            try:
                with open(self._path, "r", encoding="utf-8") as handle:
                    stored = json.load(handle)
                if isinstance(stored, dict):
                    for key, value in stored.items():
                        if key in self._defaults:
                            data[key] = value
                    repaired = self._migrate(data, stored)
            except (OSError, ValueError):
                pass  # absent or unreadable -> defaults, and rewritten on first save
            self._cache = data
        if repaired:
            self.save()
        return self._cache

    def _migrate(self, data, stored):
        """Reset the volatile block when the stored file predates the current
        version. Returns whether anything changed."""
        if not self._version_key or self._version_key not in self._defaults:
            return False
        current = self._defaults[self._version_key]
        if stored.get(self._version_key, 1) >= current:
            return False
        for key in self._volatile_keys:
            data[key] = self._defaults[key]
        data[self._version_key] = current
        return True

    def save(self):
        data = self.load()
        directory = os.path.dirname(os.path.abspath(self._path))
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            return False
        temp = self._path + ".tmp"
        try:
            with self._lock:
                with open(temp, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, indent=2, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, self._path)
        except OSError:
            try:
                os.unlink(temp)
            except OSError:
                pass
            return False
        return True

    # -- access -------------------------------------------------------------

    def get(self, key, default=None):
        return self.load().get(key, self._defaults.get(key, default))

    def set_many(self, **values):
        """Assign known keys; saves once if anything actually changed. Returns
        whether it did."""
        data = self.load()
        changed = False
        with self._lock:
            for key, value in values.items():
                if key not in self._defaults:
                    continue
                if data.get(key) != value:
                    data[key] = value
                    changed = True
        if changed:
            self.save()
        return changed

    def subset(self, keys, coerce=None):
        """{key: value} for a group of keys -- e.g. the import options a builder
        takes. ``coerce`` (bool, float, ...) is applied to every value."""
        data = self.load()
        out = {}
        for key in keys:
            value = data.get(key, self._defaults.get(key))
            out[key] = coerce(value) if coerce is not None else value
        return out

    def reset(self, keys=None):
        """Put keys (default: all) back to their declared defaults and save."""
        with self._lock:
            data = self.load()
            for key in (self._defaults if keys is None else keys):
                if key in self._defaults:
                    data[key] = self._defaults[key]
        self.save()

    def reload(self):
        """Drop the in-memory cache so the next access re-reads the file."""
        with self._lock:
            self._cache = None
