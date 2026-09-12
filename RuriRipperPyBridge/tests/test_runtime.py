"""The runtime layer's host-free parts: workspace resolution, the settings
store, and the bootstrap's requirement arithmetic."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ..runtime import bootstrap, settings, workspace


class TestWorkspace(unittest.TestCase):
    def setUp(self):
        self._previous = workspace.configured()
        self._env = os.environ.get(workspace.ENV_VAR)
        self.tmp = tempfile.mkdtemp(prefix="ruri-ws-")

    def tearDown(self):
        workspace.configure(self._previous)
        if self._env is None:
            os.environ.pop(workspace.ENV_VAR, None)
        else:
            os.environ[workspace.ENV_VAR] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_configured_wins(self):
        os.environ[workspace.ENV_VAR] = os.path.join(self.tmp, "from-env")
        workspace.configure(os.path.join(self.tmp, "from-host"))
        self.assertEqual(workspace.root(), os.path.abspath(os.path.join(self.tmp, "from-host")))

    def test_env_var_is_the_fallback(self):
        workspace.configure(None)
        os.environ[workspace.ENV_VAR] = os.path.join(self.tmp, "from-env")
        self.assertEqual(workspace.root(), os.path.abspath(os.path.join(self.tmp, "from-env")))

    def test_platform_default_is_outside_any_checkout(self):
        workspace.configure(None)
        os.environ.pop(workspace.ENV_VAR, None)
        default = workspace.platform_default()
        self.assertTrue(os.path.isabs(default))
        package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertFalse(os.path.abspath(default).startswith(os.path.abspath(package_root)))

    def test_subdir_is_created(self):
        workspace.configure(self.tmp)
        path = workspace.subdir("runtime", "cp313")
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(path, os.path.join(os.path.abspath(self.tmp), "runtime", "cp313"))


DEFAULTS = {
    "ripperhook_bin": "",
    "decoder_id": "",
    "detail_level": 0,
    "texture_resolution": 2048,
    "settings_version": 3,
}
VOLATILE = ("detail_level", "texture_resolution")


class TestJsonSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ruri-settings-")
        self.path = os.path.join(self.tmp, "nested", "settings.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _store(self):
        return settings.JsonSettings(self.path, DEFAULTS, VOLATILE)

    def test_missing_file_yields_defaults(self):
        store = self._store()
        self.assertEqual(store.get("texture_resolution"), 2048)
        self.assertEqual(store.get("decoder_id"), "")

    def test_round_trip_creates_the_directory(self):
        store = self._store()
        self.assertTrue(store.set_many(ripperhook_bin="D:/bin", decoder_id="Endfield_1.4.4"))
        self.assertTrue(os.path.isfile(self.path))
        self.assertEqual(self._store().get("ripperhook_bin"), "D:/bin")
        self.assertEqual(self._store().get("decoder_id"), "Endfield_1.4.4")

    def test_unchanged_write_is_skipped(self):
        store = self._store()
        store.set_many(ripperhook_bin="D:/bin")
        self.assertFalse(store.set_many(ripperhook_bin="D:/bin"))

    def test_unknown_keys_are_rejected(self):
        store = self._store()
        self.assertFalse(store.set_many(not_a_setting=1))
        self.assertIsNone(store.get("not_a_setting"))

    def test_corrupt_file_falls_back_to_defaults(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{ this is not json")
        self.assertEqual(self._store().get("texture_resolution"), 2048)

    def test_migration_resets_only_the_volatile_block(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"settings_version": 1, "ripperhook_bin": "D:/typed/by/hand",
                       "decoder_id": "Endfield_1.4.4", "detail_level": 2,
                       "texture_resolution": 512}, handle)
        store = self._store()
        self.assertEqual(store.get("ripperhook_bin"), "D:/typed/by/hand")  # user input kept
        self.assertEqual(store.get("decoder_id"), "Endfield_1.4.4")        # user input kept
        self.assertEqual(store.get("detail_level"), 0)                     # reset
        self.assertEqual(store.get("texture_resolution"), 2048)            # reset
        self.assertEqual(store.get("settings_version"), 3)
        # ...and the repaired file was written back, so it only happens once.
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["settings_version"], 3)

    def test_no_migration_at_the_current_version(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"settings_version": 3, "detail_level": 2}, handle)
        self.assertEqual(self._store().get("detail_level"), 2)

    def test_save_leaves_no_temp_file_behind(self):
        store = self._store()
        store.set_many(ripperhook_bin="D:/bin")
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.path))), ["settings.json"])

    def test_subset(self):
        store = self._store()
        store.set_many(detail_level=2)
        self.assertEqual(store.subset(("detail_level",), int), {"detail_level": 2})

    def test_reset(self):
        store = self._store()
        store.set_many(detail_level=2, ripperhook_bin="D:/bin")
        store.reset(("detail_level",))
        self.assertEqual(store.get("detail_level"), 0)
        self.assertEqual(store.get("ripperhook_bin"), "D:/bin")


class TestBootstrapRequirements(unittest.TestCase):
    def tearDown(self):
        bootstrap.configure(requirements=bootstrap.DEFAULT_REQUIREMENTS)

    def test_only_missing_modules_produce_specs(self):
        bootstrap.configure(requirements=(
            ("os", "os-is-obviously-present"),
            ("a_module_that_does_not_exist_9x7", "ghost>=1.0"),
        ))
        self.assertEqual(bootstrap.missing_specs(), ["ghost>=1.0"])

    def test_specs_are_deduplicated_in_order(self):
        bootstrap.configure(requirements=(
            ("ghost_module_one_9x7", "same-wheel>=1"),
            ("ghost_module_two_9x7", "same-wheel>=1"),
            ("ghost_module_three_9x7", "other>=2"),
        ))
        self.assertEqual(bootstrap.missing_specs(), ["same-wheel>=1", "other>=2"])

    def test_everything_present_means_ready(self):
        bootstrap.configure(requirements=(("os", "os"), ("json", "json")))
        self.assertTrue(bootstrap.probe())

    def test_default_requirements_cover_the_bridge(self):
        modules = {module for module, _spec in bootstrap.DEFAULT_REQUIREMENTS}
        self.assertTrue({"clr", "pythonnet", "clr_loader", "numpy"} <= modules)

    def test_abi_tag_shape(self):
        tag = bootstrap.abi_tag()
        self.assertTrue(tag.startswith("cp"))
        self.assertIn("-", tag)

    def test_pip_arguments_pin_the_live_abi(self):
        args = bootstrap._pip_arguments("D:/target", ["numpy>=1.24"])  # noqa: SLF001
        self.assertIn("--only-binary=:all:", args)
        self.assertIn("--target", args)
        self.assertIn("--abi", args)
        self.assertEqual(args[args.index("--abi") + 1], bootstrap.abi_tag().split("-")[0])
        self.assertEqual(args[-1], "numpy>=1.24")


if __name__ == "__main__":
    unittest.main()
