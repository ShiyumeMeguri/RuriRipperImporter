"""Per-game session isolation and the module-level view proxy -- host-free, no
CLR: a fake RowTable stands in for the bridge's enumerate_table so the whole
browse/select/reset surface can be driven over several games at once."""

from __future__ import annotations

import unittest

from ..session import cabmap_state


class _FakeRows:
    """The subset of row_table.RowTable the session's tree/selection touch."""

    def __init__(self, entries):
        # entries: (cab, name, [container_path, ...])
        self._entries = list(entries)
        self._index = {cab: i for i, (cab, _n, _p) in enumerate(self._entries)}

    def __len__(self):
        return len(self._entries)

    def cab(self, i):
        return self._entries[i][0]

    def name(self, i):
        return self._entries[i][1]

    def container_path_count(self, i):
        return len(self._entries[i][2])

    def container_path(self, i, p):
        return self._entries[i][2][p]

    def cab_to_index(self):
        return self._index

    def __getitem__(self, i):
        cab, name, paths = self._entries[i]
        return {"cab": cab, "name": name, "container": "  |  ".join(paths)}


_EF_ROWS = _FakeRows([
    ("cab-ef-a", "pelica_body", ["chr/pelica/body"]),
    ("cab-ef-b", "pelica_face", ["chr/pelica/face"]),
    ("cab-ef-c", "endmin", ["chr/endmin/body"]),
])
_KK_ROWS = _FakeRows([
    ("cab-kk-a", "chara_head", ["chara/head"]),
])


def _load_into_active(rows):
    """Stand in for load_rows() without a bridge: seed the active session's ROWS
    and rebuild its folder tree at the root."""
    cabmap_state.ACTIVE.ROWS = rows
    cabmap_state.ACTIVE._ROWS_BY_CAB = None
    cabmap_state.clear_selection()
    cabmap_state._build_tree(())


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
        cabmap_state.apply_filter("pelica")
        self.assertEqual(cabmap_state.VISIBLE, [])  # BRIDGE is None -> empty, no crash

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
