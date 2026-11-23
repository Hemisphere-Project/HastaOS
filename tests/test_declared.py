"""Declared state of the core modules (#t-005): one module.toml beside each module.ini, and
datesync's at the root of the tree, beside its three loose files.

What is checked: every manifest validates and names its module; every `source` and every
relative link `target` exists in the module's directory; everything the module.ini declares
(bins, services, timers, enable, mask) is declared again by the manifest; and every unit file in
the module's directory is declared or named in `undeclared` — listed, never dropped.
"""

import configparser
import os
import unittest

from hasta import legacy, manifest
from tests.helpers import REPO

CORE = ("rorw", "extendfs", "flightbox", "blackbox", "splash", "starter", "hostrename")
UNIT_SUFFIXES = (".service", ".timer", ".socket", ".mount", ".path")


def manifests():
    """(module id, its directory, the loaded manifest) for the eight core modules."""
    out = [(name, os.path.join(REPO, name)) for name in CORE]
    out.append(("datesync", REPO))
    return [(name, d, manifest.load(os.path.join(d, manifest.MANIFEST_NAME))) for name, d in out]


def ini(name):
    parser = configparser.ConfigParser()
    parser.read(os.path.join(REPO, name, "module.ini"))
    return lambda section, key: parser.get(section, key, fallback="").split()


class CoreManifests(unittest.TestCase):

    def test_each_validates_and_names_its_module(self):
        for name, _, m in manifests():
            with self.subTest(module=name):
                self.assertEqual([str(e) for e in m.errors], [])
                self.assertEqual(m.id, name)
                self.assertEqual(m.data["module"]["layer"], "core")

    def test_every_source_exists_in_the_tree(self):
        for name, d, m in manifests():
            res = m.data.get("resources", {})
            named = [f["source"] for f in res.get("files", [])]
            named += [u["source"] for u in res.get("units", []) if "source" in u]
            named += [l["target"] for l in res.get("links", [])
                      if not l["target"].startswith("/")]
            for src in named:
                with self.subTest(module=name, source=src):
                    self.assertTrue(os.path.isfile(os.path.join(d, src)), src)

    def test_the_manifest_declares_what_the_ini_does(self):
        for name, _, m in manifests():
            if name == "datesync":
                continue  # no module.ini: setup/installer.py lays it down inline
            words = ini(name)
            res = m.data.get("resources", {})
            links = set(l["path"] for l in res.get("links", []))
            units = dict((u["name"], u["state"]) for u in res.get("units", []))
            with self.subTest(module=name):
                for b in words("files", "bins"):
                    self.assertIn("/usr/local/bin/" + b, links)
                for u in words("files", "services") + words("files", "timers"):
                    self.assertIn(u, units)
                for u in words("files", "enable"):
                    self.assertEqual(units.get(u), "enabled", u)
                for u in words("files", "mask"):
                    self.assertEqual(units.get(u), "masked", u)

    def test_no_unit_file_is_dropped(self):
        for name, d, m in manifests():
            res = m.data.get("resources", {})
            declared = set(u["name"] for u in res.get("units", []))
            listed = " ".join(m.data.get("undeclared", []))
            files = [f for f in os.listdir(d) if f.endswith(UNIT_SUFFIXES)]
            if name == "datesync":
                files = [f for f in files if f.startswith("datesync")]
            for f in files:
                with self.subTest(module=name, unit=f):
                    self.assertTrue(f in declared or f in listed, f)

    def test_state_writes_and_seal_paths_stay_absolute(self):
        for name, _, m in manifests():
            for key in ("state", "writes", "seal"):
                for t in m.data.get(key, []):
                    with self.subTest(module=name, key=key, path=t["path"]):
                        self.assertTrue(t["path"].startswith("/"))

    def test_the_legacy_driver_joins_them(self):
        modules = legacy.discover(REPO)
        for name in CORE:
            with self.subTest(module=name):
                self.assertIsNotNone(modules[name].toml)
                self.assertTrue(modules[name].toml.valid)
                self.assertEqual(modules[name].layer, "core")
                self.assertEqual(modules[name].model()["module"]["id"], name)

    def test_hasta_modules_finds_all_eight(self):
        found = dict((m.id, m) for m in manifest.discover(REPO))
        for name, _, _ in manifests():
            with self.subTest(module=name):
                self.assertIn(name, found)
                self.assertTrue(found[name].valid)


if __name__ == "__main__":
    unittest.main()
