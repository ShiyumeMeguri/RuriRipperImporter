"""Per-game session isolation and the module-level view proxy -- host-free, no
CLR: a fake RowTable stands in for the bridge's enumerate_table so the whole
browse/select/reset surface can be driven over several games at once."""

from __future__ import annotations

import unittest

from ..session import cabmap_state


class _FakeRows:
    """The subset of column_table.ColumnTable the session's selection touches."""

    def __init__(self, entries):
        # entries: (cab, name, [container_path, ...])
        self._entries = list(entries)

    def __len__(self):
        return len(self._entries)

    @property
    def row_count(self):
        return len(self._entries)

    def values(self, column):
        return [self.cell(index, column) for index in range(len(self._entries))]

    def cell(self, index, column):
        return self.row(index)[column]

    def row(self, index):
        cab, name, paths = self._entries[index]
        return {"cab": cab, "name": name, "container": "  |  ".join(paths),
                "paths": list(paths)}


class _FakeBridge:
    """What the kernel answers about one map's folder tree. The tree itself is
    built there; these tests only check that the session ASKS it and keeps what
    comes back per game."""

    #: No map is loaded under any key here, so activate() only switches this
    #: side's view -- which is exactly the case these tests are about.
    maps_by_key = {}

    def __init__(self, rows):
        self._rows = rows

    def _tree(self):
        folders = {}
        for index in range(len(self._rows)):
            for path in self._rows.row(index)["paths"]:
                segments = [part for part in path.split("/") if part]
                for depth in range(len(segments)):
                    parent = "/".join(segments[:depth])
                    folders.setdefault(parent, {}).setdefault(segments[depth], [])
                folders.setdefault("/".join(segments[:-1]), {}).setdefault(
                    segments[-1], []).append(index)
        return folders

    def folder_exists(self, folder):
        return folder in self._tree() or folder == ""

    def folder_children(self, folder):
        tree = self._tree()
        names = sorted(name for name in tree.get(folder, {})
                       if (folder + "/" + name if folder else name) in tree)
        return _FakeFolders([(name, len(tree[folder][name])) for name in names])

    def folder_files(self, folder):
        tree = self._tree()
        found = []
        for files in tree.get(folder, {}).values():
            found.extend(files)
        return _FakeIds(sorted(found))

    def rename_session(self, old_key, new_key):
        """Nothing is loaded here, so renaming a slot is a no-op."""

    def search_table(self, query, rules, sort_column, sort_direction):
        """Every row matches: what this side does with the ids is the subject,
        not the matching, which is the kernel's."""
        return _FakeIds(range(len(self._rows)))

    def folder_of(self, row_index, query="", folder=""):
        paths = self._rows.row(row_index)["paths"]
        if not paths:
            return "", ""
        segments = [part for part in paths[0].split("/") if part]
        return "/".join(segments[:-1]), segments[-1]


class _FakeFolders:
    def __init__(self, pairs):
        self._pairs = pairs

    @property
    def row_count(self):
        return len(self._pairs)

    def cell(self, row, column):
        return self._pairs[row][0 if column == "name" else 1]


class _FakeIds(list):
    def tolist(self):
        return list(self)


_EF_ROWS = _FakeRows([
    ("cab-ef-a", "pelica_body", ["chr/pelica/body"]),
    ("cab-ef-b", "pelica_face", ["chr/pelica/face"]),
    ("cab-ef-c", "endmin", ["chr/endmin/body"]),
])
_KK_ROWS = _FakeRows([
    ("cab-kk-a", "chara_head", ["chara/head"]),
])


def _load_into_active(rows):
    """Stand in for load_rows(): seed the active session's ROWS and point it at
    the root, with a bridge that answers about the folders those rows are in."""
    cabmap_state.BRIDGE = _FakeBridge(rows)
    cabmap_state.ACTIVE.ROWS = rows
    cabmap_state.ACTIVE._CAB_INDEX = None
    cabmap_state.clear_selection()
    cabmap_state.browse_dir(())


class TestSessionIsolation(unittest.TestCase):
    def setUp(self):
        self._saved_active = cabmap_state.ACTIVE
        self._saved_bridge = cabmap_state.BRIDGE
        cabmap_state.BRIDGE = None
        self._games = ("__test_ef__", "__test_kk__")

    def tearDown(self):
        cabmap_state.ACTIVE = self._saved_active
        cabmap_state.BRIDGE = self._saved_bridge
        for game in self._games:
            cabmap_state.SESSIONS.pop(game, None)

    def test_sessions_are_distinct_objects(self):
        a = cabmap_state.session_for("__test_ef__")
        b = cabmap_state.session_for("__test_kk__")
        self.assertIsNot(a, b)
        self.assertIs(cabmap_state.session_for("__test_ef__"), a)

    def test_active_key_follows_activate(self):
        cabmap_state.activate("__test_ef__")
        self.assertEqual(cabmap_state.active_key(), "__test_ef__")
        cabmap_state.activate("__test_kk__")
        self.assertEqual(cabmap_state.active_key(), "__test_kk__")

    def test_decoder_game_is_apart_from_the_install_key(self):
        # Two installs of one title: distinct sessions, one game.
        cabmap_state.activate("__test_ef__", "Endfield")
        self.assertEqual(cabmap_state.active_game(), "Endfield")
        cabmap_state.activate("__test_kk__", "Endfield")
        self.assertEqual(cabmap_state.active_key(), "__test_kk__")
        self.assertEqual(cabmap_state.active_game(), "Endfield")
        self.assertEqual(cabmap_state.game_of("__test_ef__"), "Endfield")

    def test_rename_carries_the_session_over(self):
        cabmap_state.activate("__test_ef__", "Endfield")
        _load_into_active(_EF_ROWS)
        cabmap_state.rename("__test_ef__", "__test_kk__")
        self.assertEqual(cabmap_state.active_key(), "__test_kk__")
        self.assertEqual(cabmap_state.active_game(), "Endfield")
        self.assertEqual(len(cabmap_state.ROWS), 3)
        self.assertNotIn("__test_ef__", cabmap_state.SESSIONS)

    def test_rows_and_tree_do_not_bleed_between_games(self):
        cabmap_state.activate("__test_ef__")
        _load_into_active(_EF_ROWS)
        cabmap_state.browse_dir(("chr", "pelica"))
        self.assertEqual(len(cabmap_state.ROWS), 3)
        self.assertEqual(cabmap_state.CURRENT_DIR, ("chr", "pelica"))
        self.assertEqual(len(cabmap_state.VISIBLE), 2)  # body + face leaves

        cabmap_state.activate("__test_kk__")
        _load_into_active(_KK_ROWS)
        self.assertEqual(len(cabmap_state.ROWS), 1)
        self.assertEqual(cabmap_state.CURRENT_DIR, ())

        # EF session is exactly as it was left.
        cabmap_state.activate("__test_ef__")
        self.assertEqual(len(cabmap_state.ROWS), 3)
        self.assertEqual(cabmap_state.CURRENT_DIR, ("chr", "pelica"))
        self.assertEqual(len(cabmap_state.VISIBLE), 2)

    def test_selection_is_per_session(self):
        cabmap_state.activate("__test_ef__")
        _load_into_active(_EF_ROWS)
        cabmap_state.SELECTED_CABS.add("cab-ef-a")
        cabmap_state.set_select_anchor(0)

        cabmap_state.activate("__test_kk__")
        _load_into_active(_KK_ROWS)
        self.assertEqual(cabmap_state.SELECTED_CABS, set())
        self.assertIsNone(cabmap_state.SELECT_ANCHOR)
        cabmap_state.SELECTED_CABS.add("cab-kk-a")

        cabmap_state.activate("__test_ef__")
        self.assertEqual(cabmap_state.SELECTED_CABS, {"cab-ef-a"})
        self.assertEqual(cabmap_state.SELECT_ANCHOR, 0)
        self.assertEqual(cabmap_state.selected_cabs(), ["cab-ef-a"])

    def test_set_select_anchor_writes_active(self):
        cabmap_state.activate("__test_ef__")
        _load_into_active(_EF_ROWS)
        cabmap_state.set_select_anchor(2)
        self.assertEqual(cabmap_state.ACTIVE.SELECT_ANCHOR, 2)
        self.assertEqual(cabmap_state.SELECT_ANCHOR, 2)

    def test_reset_clears_only_the_active_session(self):
        cabmap_state.activate("__test_ef__")
        _load_into_active(_EF_ROWS)
        cabmap_state.SELECTED_CABS.add("cab-ef-a")
        cabmap_state.activate("__test_kk__")
        _load_into_active(_KK_ROWS)

        cabmap_state.activate("__test_ef__")
        cabmap_state.reset()
        self.assertEqual(len(cabmap_state.ROWS), 0)
        self.assertEqual(cabmap_state.SELECTED_CABS, set())
        # The other game and the bridge singleton are untouched.
        self.assertEqual(len(cabmap_state.SESSIONS["__test_kk__"].ROWS), 1)

    def test_apply_filter_with_no_bridge_clears_visible(self):
        cabmap_state.activate("__test_ef__")
        _load_into_active(_EF_ROWS)
        cabmap_state.BRIDGE = None  # nothing to ask -> nothing shown, and no crash
        cabmap_state.apply_filter("pelica")
        self.assertEqual(cabmap_state.VISIBLE, [])

    def test_unknown_module_attribute_raises(self):
        with self.assertRaises(AttributeError):
            _ = cabmap_state.NOT_A_SESSION_FIELD


class TestProxyInvariant(unittest.TestCase):
    """The 12 session-field names must never be real module attributes, or the
    normal-lookup would find the stale module value and __getattr__ (which only
    fires on a miss) would never proxy to the active session."""

    def test_session_fields_are_not_shadowed_at_module_scope(self):
        shadowed = [name for name in cabmap_state._SESSION_FIELDS
                    if name in vars(cabmap_state)]
        self.assertEqual(shadowed, [], f"session fields shadow the proxy: {shadowed}")

    def test_bridge_stays_a_real_module_attribute(self):
        # BRIDGE is process-wide, NOT a session field -- it must be a plain module
        # global (found by normal lookup), never proxied.
        self.assertIn("BRIDGE", vars(cabmap_state))
        self.assertNotIn("BRIDGE", cabmap_state._SESSION_FIELDS)


if __name__ == "__main__":
    unittest.main()
