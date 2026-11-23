import json
import os
import subprocess
import sys
import unittest

from tests.helpers import MODULES, REPO, root

HASTA = os.path.join(REPO, "bin", "hasta")
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def run(*args):
    p = subprocess.run([sys.executable, HASTA] + list(args), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, env=ENV)
    return p.returncode, p.stdout, p.stderr


class Cli(unittest.TestCase):

    def test_facts_json_before_or_after_the_command(self):
        for argv in (["--json", "facts", "--root", root("pi3-buster")],
                     ["facts", "--root", root("pi3-buster"), "--json"]):
            code, out, err = run(*argv)
            self.assertEqual(code, 0, err)
            obj = json.loads(out)
            self.assertEqual(obj["facts"]["machine"], "rpi3")
            self.assertEqual(obj["sources"]["machine"], "/proc/device-tree/model")

    def test_facts_text(self):
        code, out, _ = run("facts", "--root", root("x86-esp"))
        self.assertEqual(code, 0)
        self.assertIn("machine:      x86-uefi", out)

    def test_modules_refuses_a_broken_manifest_with_its_reason(self):
        code, out, _ = run("modules", "--tree", MODULES)
        self.assertEqual(code, 1)
        self.assertIn("INVALID  broken/module.toml", out)
        self.assertIn("module.layer: 'plugin' is not one of", out)
        self.assertIn("ok       good/module.toml", out)
        code, out, _ = run("modules", "--tree", MODULES, "--json")
        obj = json.loads(out)
        self.assertEqual([m["valid"] for m in obj["manifests"]], [False, True])

    def test_modules_on_this_checkout(self):
        code, out, err = run("--json", "modules")
        self.assertIn(code, (0, 1), err)
        obj = json.loads(out)
        self.assertEqual(obj["tree"], REPO)
        self.assertGreater(obj["legacy_module_ini"], 0)

    def test_python_m_hasta(self):
        p = subprocess.run([sys.executable, "-m", "hasta", "--version"], cwd=REPO,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, env=ENV)
        self.assertEqual(p.returncode, 0)
        self.assertTrue(p.stdout.startswith("hasta "))

    def test_no_command_is_usage(self):
        code, _, err = run()
        self.assertEqual(code, 2)
        self.assertIn("usage", err)


if __name__ == "__main__":
    unittest.main()
