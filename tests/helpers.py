"""Shared paths for the engine tests (run by bench/verify: python3 -m unittest discover -s tests -t .)."""
import os

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS)
ROOTS = os.path.join(TESTS, "fixtures", "roots")
MODULES = os.path.join(TESTS, "fixtures", "modules")


def root(name):
    return os.path.join(ROOTS, name)
