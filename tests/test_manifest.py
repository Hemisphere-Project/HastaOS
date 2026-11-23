import os
import sys
import unittest

from hasta import manifest
from hasta._vendor import tomli
from tests.helpers import MODULES

GOOD = os.path.join(MODULES, "good", "module.toml")
BROKEN = os.path.join(MODULES, "broken", "module.toml")

MINIMAL = """schema = 1
[module]
id = "m"
version = "1"
layer = "core"
summary = "s"
"""


def reasons(text):
    return {(e.key, e.reason) for e in manifest.loads(text, "x/module.toml").errors}


def keys(text):
    return {e.key for e in manifest.loads(text, "x/module.toml").errors}


class Loader(unittest.TestCase):

    def test_good_manifest_is_valid(self):
        m = manifest.load(GOOD)
        self.assertEqual([str(e) for e in m.errors], [])
        self.assertEqual(m.id, "example")
        self.assertEqual(m.data["resources"]["units"][1]["state"], "masked")

    def test_minimal_manifest_is_valid(self):
        self.assertEqual(manifest.loads(MINIMAL).errors, [])

    def test_broken_manifest_names_file_key_and_reason(self):
        m = manifest.load(BROKEN)
        self.assertFalse(m.valid)
        got = {e.key: e.reason for e in m.errors}
        self.assertEqual(set(got), {"module.layer", "resources.units[0].state",
                                    "resources.files[0].sorce", "resources.files[0].source"})
        self.assertIn("'plugin' is not one of", got["module.layer"])
        self.assertIn("unknown key", got["resources.files[0].sorce"])
        self.assertTrue(all(e.path == BROKEN for e in m.errors))
        self.assertIn(BROKEN + ": module.layer: ", str(m.errors[0]))

    def test_not_toml(self):
        m = manifest.loads("schema = = 1\n", "x/module.toml")
        self.assertIsNone(m.data)
        self.assertEqual(len(m.errors), 1)
        self.assertEqual(m.errors[0].key, "")
        self.assertIn("not valid TOML", m.errors[0].reason)

    def test_vendored_reader_agrees_with_tomllib(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("no tomllib on Python %d.%d" % sys.version_info[:2])
        for path in (GOOD, BROKEN):
            with open(path, "rb") as fh:
                text = fh.read().decode("utf-8")
            self.assertEqual(tomli.loads(text), tomllib.loads(text), path)


class Validator(unittest.TestCase):

    def test_required(self):
        self.assertEqual(keys("schema = 1\n"), {"module"})
        self.assertEqual(keys('schema = 1\n[module]\nid = "m"\n'),
                         {"module.version", "module.layer", "module.summary"})
        self.assertEqual(keys(MINIMAL.replace("schema = 1\n", "")), {"schema"})

    def test_schema_version(self):
        self.assertEqual(keys(MINIMAL.replace("schema = 1", "schema = 2")), {"schema"})

    def test_unknown_top_key(self):
        self.assertEqual(keys(MINIMAL + "\n[resorces]\n"), {"resorces"})

    def test_bad_id_and_capabilities(self):
        self.assertEqual(keys(MINIMAL.replace('id = "m"', 'id = "Rorw!"')), {"module.id"})
        self.assertEqual(keys('requires = ["Storage Data"]\n' + MINIMAL), {"requires[0]"})

    def test_options(self):
        bad = MINIMAL + '[options.x]\ntype = "enum"\nchoices = ["a", "b"]\ndefault = "c"\n'
        self.assertEqual(keys(bad), {"options.x.default"})
        bad = MINIMAL + '[options.x]\ntype = "int"\ndefault = true\n'
        self.assertEqual(keys(bad), {"options.x.default"})
        bad = MINIMAL + '[options.x]\ntype = "enum"\ndefault = "a"\n'
        self.assertEqual(keys(bad), {"options.x.choices"})
        bad = MINIMAL + '[options.x]\ntype = "colour"\ndefault = "a"\n'
        self.assertEqual(keys(bad), {"options.x.type"})

    def test_units_and_lines(self):
        bad = MINIMAL + '[[resources.units]]\nname = "rorw"\nstate = "enabled"\n'
        self.assertEqual(keys(bad), {"resources.units[0].name"})
        bad = MINIMAL + '[[resources.lines]]\nfile = "cmdline"\nsection = "all"\nline = "x"\n'
        self.assertEqual(keys(bad), {"resources.lines[0].section"})
        bad = MINIMAL + '[[resources.lines]]\nfile = "rc.local"\nline = "x"\n'
        self.assertEqual(keys(bad), {"resources.lines[0].file"})

    def test_paths(self):
        bad = MINIMAL + '[[state]]\npath = "/var/lib/x"\n'
        self.assertEqual(keys(bad), {"state[0].path"})
        bad = MINIMAL + '[[resources.files]]\npath = "etc/x"\nsource = "m/x"\nmode = "644x"\n'
        self.assertEqual(keys(bad), {"resources.files[0].path", "resources.files[0].mode"})
        bad = MINIMAL + '[[writes]]\npath = "/data/x"\nwhen = "sometimes"\n'
        self.assertEqual(keys(bad), {"writes[0].when"})

    def test_wrong_shapes_do_not_crash(self):
        for extra in ('options = 5\n', 'resources = "x"\n', 'state = ["/data"]\n',
                      'undeclared = "one"\n', 'supports = ["rpi3"]\n'):
            self.assertTrue(manifest.loads(extra + MINIMAL).errors, extra)


class Discovery(unittest.TestCase):

    def test_discover_lists_valid_and_invalid(self):
        found = manifest.discover(MODULES)
        self.assertEqual([os.path.relpath(m.path, MODULES) for m in found],
                         [os.path.join("broken", "module.toml"), os.path.join("good", "module.toml")])
        self.assertEqual([m.valid for m in found], [False, True])


if __name__ == "__main__":
    unittest.main()
