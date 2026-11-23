"""The engine must run on Python 3.7 (Buster). bench/verify also runs every test under a 3.7
interpreter when one is at hand; this check runs everywhere, on 3.8 or later."""
import ast
import os
import re
import sys
import unittest

from tests.helpers import REPO

# Library calls newer than 3.7 that the syntax check cannot see.
NEWER = re.compile(r"\.removeprefix\(|\.removesuffix\(|functools\.cache\b|from functools import "
                   r"cache\b|\bzoneinfo\b|\bgraphlib\b|math\.prod\(|shlex\.join\(")


def engine_files():
    out = [os.path.join(REPO, "bin", "hasta")]
    for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, "hasta")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        out += [os.path.join(dirpath, f) for f in filenames if f.endswith(".py")]
    return sorted(out)


class Python37(unittest.TestCase):

    @unittest.skipIf(sys.version_info < (3, 8), "ast feature_version needs 3.8+")
    def test_syntax_is_37(self):
        for path in engine_files():
            with open(path) as fh:
                ast.parse(fh.read(), path, feature_version=(3, 7))

    def test_no_newer_library_calls(self):
        for path in engine_files():
            with open(path) as fh:
                for n, line in enumerate(fh, 1):
                    self.assertIsNone(NEWER.search(line), "%s:%d: %s" % (path, n, line.strip()))

    def test_tomllib_only_behind_a_fallback(self):
        for path in engine_files():
            with open(path) as fh:
                text = fh.read()
            if "import tomllib" in text:
                self.assertIn("except ImportError", text, path)


if __name__ == "__main__":
    unittest.main()
