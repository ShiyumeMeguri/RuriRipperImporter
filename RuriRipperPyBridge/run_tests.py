"""Run the package's self-tests with no host application involved.

    python run_tests.py [-v]

Works whatever this checkout's folder is named (each host picks its own
submodule path), because the package is imported by that folder name and the
tests only ever use relative imports.
"""

from __future__ import annotations

import os
import sys
import unittest


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    parent, package = os.path.split(here)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    __import__(package)  # fail loudly here rather than inside discovery
    suite = unittest.defaultTestLoader.discover(
        os.path.join(here, "tests"), pattern="test_*.py", top_level_dir=parent)
    verbosity = 2 if "-v" in argv else 1
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
