"""The package's one rule, enforced mechanically.

Nothing here may import a DCC API. It is an easy rule to break by accident --
one ``from mathutils import Matrix`` inside a helper and the module silently
stops being importable in Painter (or in this very test run), which is exactly
how a "shared" layer rots back into two of them. So it is a test, not a
convention.

This also guards the second structural rule: package ``__init__`` files import
nothing, because ``runtime.bootstrap`` has to be importable in an interpreter
that does not have numpy yet -- installing numpy is its job.
"""

from __future__ import annotations

import ast
import os
import unittest

FORBIDDEN_ROOTS = {
    "bpy", "bmesh", "mathutils", "bpy_extras", "aud", "gpu", "bl_ui",
    "substance_painter", "PySide2", "PySide6", "shiboken2", "shiboken6",
}

PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _python_files():
    for root, dirs, files in os.walk(PACKAGE_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


def _imported_roots(path):
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node.lineno, node.module.split(".")[0]


class TestNoHostImports(unittest.TestCase):
    def test_no_module_imports_a_host_api(self):
        offenders = []
        for path in _python_files():
            for lineno, root in _imported_roots(path):
                if root in FORBIDDEN_ROOTS:
                    offenders.append("{0}:{1}: {2}".format(
                        os.path.relpath(path, PACKAGE_ROOT), lineno, root))
        self.assertEqual(offenders, [], "host API imported in the shared package:\n"
                                        + "\n".join(offenders))

    def test_package_inits_import_nothing(self):
        """bootstrap must be importable before numpy exists, so no ``__init__``
        may pull a submodule in as a side effect of ``import``."""
        for path in _python_files():
            if os.path.basename(path) != "__init__.py":
                continue
            if os.path.dirname(path).endswith("tests"):
                continue
            imports = list(_imported_roots(path))
            self.assertEqual(imports, [],
                             "{0} imports {1}".format(os.path.relpath(path, PACKAGE_ROOT),
                                                      [name for _line, name in imports]))

    def test_bootstrap_and_workspace_are_stdlib_only(self):
        """They run before the dependency install, so not even numpy."""
        for name in ("runtime/bootstrap.py", "runtime/workspace.py"):
            path = os.path.join(PACKAGE_ROOT, *name.split("/"))
            for lineno, root in _imported_roots(path):
                self.assertNotEqual(root, "numpy",
                                    "{0}:{1} imports numpy".format(name, lineno))

    def test_pythonnet_bridge_defers_its_numpy_dependency(self):
        """Same reason: claim_runtime_early() runs at host startup, possibly
        before the install has finished, so row_table (numpy) must only be
        imported inside the function that needs it."""
        path = os.path.join(PACKAGE_ROOT, "runtime", "pythonnet_bridge.py")
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), path)
        top_level = set()
        for node in tree.body:  # module scope only -- nested ones are the point
            if isinstance(node, ast.Import):
                top_level.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                top_level.update(alias.name for alias in node.names)
                if node.module:
                    top_level.add(node.module)
        self.assertNotIn("row_table", top_level)
        self.assertNotIn("numpy", top_level)


if __name__ == "__main__":
    unittest.main()
