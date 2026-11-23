"""The legacy driver and `hasta plan` — and the parity with setup/converge.py on 7.5 receipts.

tests/fixtures/receipts/rastaos-7.5.txt is what setup/module-manifest writes, generated at the
rastaos-7.5 commit (1c927c4): each line's fingerprint is `module-manifest fingerprint-at 1c927c4
<module>`. The boxes themselves are built per test in a temporary root.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from hasta import legacy, manifest
from hasta import plan as plan_mod
from tests.helpers import REPO, TESTS, root

HASTA = os.path.join(REPO, "bin", "hasta")
CONVERGE = os.path.join(REPO, "setup", "converge.py")
RECEIPTS_75 = os.path.join(TESTS, "fixtures", "receipts", "rastaos-7.5.txt")
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
ENV.pop("PITOOLS_ROOT", None)
ENV.pop("PITOOLS_MODULE_MANIFEST", None)


def receipted(path=RECEIPTS_75):
    return sorted(plan_mod.read_receipts(path))


def touch(box, path):
    full = os.path.join(box, path.lstrip("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w"):
        pass


def converge_plan(box, platform, receipts):
    env = dict(ENV, PITOOLS_ROOT=box)
    p = subprocess.run([sys.executable, CONVERGE, "--plan", "--platform", platform,
                        "--manifest", receipts], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, env=env)
    assert p.returncode == 0, p.stderr
    out = {}
    for ln in p.stdout.splitlines():
        action, name, why = ln.split(None, 2)
        out[name] = (action, why)
    return out


def hasta_plan(*args):
    p = subprocess.run([sys.executable, HASTA, "--json", "plan"] + list(args),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                       env=ENV)
    obj = json.loads(p.stdout) if p.stdout.strip() else None
    return p.returncode, obj, p.stderr


def as_decisions(obj):
    return dict((r["module"], (r["action"], r["why"])) for r in obj["actions"])


class LegacyDriver(unittest.TestCase):

    def test_every_module_ini_reads_into_a_valid_manifest(self):
        mods = legacy.discover(REPO)
        self.assertGreaterEqual(len(mods), 20)
        for name, m in sorted(mods.items()):
            self.assertEqual(m.unknown, [], "%s: keys no installer reads" % name)
            errors = manifest.validate(m.model(), name + "/module.ini")
            self.assertEqual([str(e) for e in errors], [], name)

    def test_the_ini_says_how_it_installs(self):
        mods = legacy.discover(REPO)
        rorw = mods["rorw"]
        self.assertTrue(rorw.script)
        self.assertEqual(rorw.upgrade, "upgrade.sh")
        self.assertEqual(rorw.platforms, ["pi", "x86"])
        self.assertIn("/usr/local/bin/ro", rorw.declared())
        units = dict((u["name"], u["state"]) for u in rorw.model()["resources"]["units"])
        self.assertEqual(units["data-repair.service"], "enabled")
        self.assertEqual(units["fake-clock.service"], "disabled")
        nt = mods["network-tools"].model()
        units = dict((u["name"], u["state"]) for u in nt["resources"]["units"])
        self.assertEqual(units["hostapd.service"], "masked")
        self.assertEqual(nt["resources"]["packages"], {"any": ["hostapd"]})
        self.assertEqual(mods["usbfix"].model()["supports"], {"machine": ["rpi*"]})
        self.assertNotIn("supports", mods["starter"].model())
        self.assertEqual(mods["starter"].layer, "core")
        self.assertEqual(mods["webconf"].layer, "feature")
        self.assertTrue(mods["webconf"].install()["npm"])

    def test_a_module_toml_beside_the_ini_is_joined(self):
        tree = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tree)
        d = os.path.join(tree, "demo")
        os.makedirs(d)
        with open(os.path.join(d, "module.ini"), "w") as f:
            f.write("[module]\ndescription = demo\nplatforms = pi\n[install]\nscript = yes\n"
                    "rerun = yes\n")
        with open(os.path.join(d, "module.toml"), "w") as f:
            f.write('schema = 1\nclaims = ["audio.output"]\n[module]\nid = "demo"\n'
                    'version = "1.0"\nlayer = "feature"\nsummary = "demo"\n')
        m = legacy.discover(tree)["demo"]
        self.assertEqual(m.model()["claims"], ["audio.output"])     # what it leaves: the toml
        self.assertEqual(m.install()["rerun"], True)                # how it installs: the ini
        self.assertEqual(m.as_dict()["toml"], os.path.join(d, "module.toml"))

    def test_an_app_tree_given_by_path(self):
        app = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, app)
        with open(os.path.join(app, "module.ini"), "w") as f:
            f.write("[module]\nname = hkiosk\ndescription = kiosk\n[install]\nscript = yes\n")
        mods = legacy.discover(REPO, apps=[app])
        self.assertEqual(mods["hkiosk"].kind, "app")
        self.assertEqual(mods["hkiosk"].layer, "app")


class ConvergeParity(unittest.TestCase):
    """On 7.5 receipts, `hasta plan` decides what setup/converge.py decides."""

    def box(self, base=None):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        if base:
            shutil.rmtree(d)
            shutil.copytree(base, d, symlinks=True)
        return d

    def rasta_75(self, base=None):
        """A box as a 7.5 install leaves it, with the cases converge tells apart: a standard
        module fully linked, one with a declared file gone, one with no receipt but a bin on
        the box, and script modules with and without receipts."""
        b = self.box(base)
        mods = legacy.discover(REPO)
        for name in receipted():
            for p in mods[name].declared():
                touch(b, p)
        os.remove(os.path.join(b, "etc/systemd/system/extendfs.service"))   # partly linked
        touch(b, "/usr/local/bin/webconf")                                   # no receipt
        return b

    def check(self, box, platform, receipts, *extra):
        want = converge_plan(box, platform, receipts)
        code, obj, err = hasta_plan("--root", box, "--receipts", receipts, *extra)
        self.assertIsNotNone(obj, err)
        got = as_decisions(obj)
        self.assertEqual(got, want)
        return want, obj

    def test_rasta_75_pi(self):
        want, obj = self.check(self.rasta_75(), "pi", RECEIPTS_75, "--platform", "pi")
        actions = set(a for a, _ in want.values())
        # the receipts exercise every class converge has on a Pi
        self.assertEqual(actions, {"upgrade", "script", "held", "install", "linked",
                                   "unrecorded"})
        self.assertEqual(want["rorw"], ("upgrade", "upgrade.sh --converging"))
        self.assertEqual(want["tailscale"][0], "held")
        self.assertEqual(want["extendfs"], ("install", "1 declared file(s) missing"))
        self.assertEqual(want["webconf"][0], "install")
        self.assertEqual(want["starter"][0], "linked")
        self.assertEqual(obj["selected_by"], "receipts")

    def test_rasta_75_x86_skips_the_pi_modules(self):
        want, _ = self.check(self.rasta_75(), "x86", RECEIPTS_75, "--platform", "x86")
        self.assertEqual(want["usbfix"], ("skip", "not for x86"))

    def test_platform_from_the_facts(self):
        box = self.rasta_75(root("pi3-buster"))
        _, obj = self.check(box, "pi", RECEIPTS_75)
        self.assertEqual(obj["platform"], "pi")

    def test_a_box_without_receipts(self):
        box = self.rasta_75()
        self.check(box, "pi", os.path.join(box, "etc", "pi-tools-modules.txt"),
                   "--platform", "pi")

    def test_receipts_on_the_box_by_default(self):
        box = self.rasta_75()
        os.makedirs(os.path.join(box, "etc"), exist_ok=True)
        shutil.copy(RECEIPTS_75, os.path.join(box, "etc", "pi-tools-modules.txt"))
        want = converge_plan(box, "pi", os.path.join(box, "etc", "pi-tools-modules.txt"))
        code, obj, err = hasta_plan("--root", box, "--platform", "pi")
        self.assertEqual(as_decisions(obj), want)
        self.assertEqual(obj["actions"][0]["receipt"] is None
                         or "fingerprint" in obj["actions"][0]["receipt"], True)


class Plan(unittest.TestCase):

    def test_order_is_layer_first(self):
        box = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, box)
        code, obj, err = hasta_plan("--root", box, "--platform", "pi", "--receipts",
                                    RECEIPTS_75)
        self.assertEqual(code, 0, obj and obj["problems"])
        layers = [r["layer"] for r in obj["actions"] if r["action"] not in ("skip",
                                                                            "unrecorded")]
        self.assertEqual(layers, sorted(layers, key=("core", "feature").index))
        rows = dict((r["module"], r) for r in obj["actions"])
        self.assertEqual(rows["rorw"]["receipt"]["fingerprint"], "2c6ff75ba27b")
        self.assertEqual(rows["rorw"]["receipt"]["source"], "1c927c4")

    def test_a_given_list_replaces_the_receipts(self):
        box = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, box)
        code, obj, err = hasta_plan("--root", box, "--platform", "pi", "--modules",
                                    "starter,usbfix,nosuch")
        self.assertEqual(code, 1)
        self.assertEqual(obj["selected_by"], "list")
        d = as_decisions(obj)
        self.assertEqual(d["starter"], ("install", "not on this box: the standard install"))
        self.assertEqual(d["usbfix"], ("install", "not on this box: install.sh"))
        self.assertIn("nosuch: no such module in %s" % REPO, obj["problems"])

    def test_two_legacy_modules_on_one_path_is_reported_not_enforced(self):
        tree = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tree)
        for name in ("one", "two"):
            os.makedirs(os.path.join(tree, name))
            with open(os.path.join(tree, name, "module.ini"), "w") as f:
                f.write("[module]\ndescription = %s\n[files]\nbins = same\n" % name)
        box = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, box)
        code, obj, err = hasta_plan("--root", box, "--platform", "pi", "--tree", tree,
                                    "--modules", "one two")
        self.assertEqual(code, 1)
        self.assertEqual(obj["problems"], ["conflict: /usr/local/bin/same held by one, two"])
        self.assertEqual(sorted(as_decisions(obj)), ["one", "two"])

    def test_requires_orders_and_unmet_is_reported(self):
        tree = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tree)
        specs = {"aaa": 'requires = ["bbb", "net.thing"]', "bbb": ""}
        for name, extra in specs.items():
            os.makedirs(os.path.join(tree, name))
            with open(os.path.join(tree, name, "module.ini"), "w") as f:
                f.write("[module]\ndescription = %s\n" % name)
            with open(os.path.join(tree, name, "module.toml"), "w") as f:
                f.write('schema = 1\n%s\n[module]\nid = "%s"\nversion = "1"\n'
                        'layer = "feature"\nsummary = "x"\n' % (extra, name))
        box = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, box)
        code, obj, err = hasta_plan("--root", box, "--platform", "pi", "--tree", tree,
                                    "--modules", "aaa,bbb")
        self.assertEqual([r["module"] for r in obj["actions"]], ["bbb", "aaa"])
        self.assertEqual(obj["problems"],
                         ["aaa requires net.thing: nothing in the set provides it"])

    def test_to_a_ref(self):
        if not os.path.isdir(os.path.join(REPO, ".git")) and not os.path.isfile(
                os.path.join(REPO, ".git")):
            self.skipTest("not a git checkout")
        box = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, box)
        a = hasta_plan("--root", box, "--platform", "pi", "--receipts", RECEIPTS_75)[1]
        code, b, err = hasta_plan("--root", box, "--platform", "pi", "--receipts", RECEIPTS_75,
                                  "--to", "HEAD")
        self.assertIsNotNone(b, err)
        self.assertEqual(as_decisions(a), as_decisions(b))
        self.assertEqual(b["tree"], "HEAD")
        code, _, err = hasta_plan("--root", box, "--to", "no-such-ref-anywhere")
        self.assertEqual(code, 2)
        self.assertIn("--to no-such-ref-anywhere", err)


if __name__ == "__main__":
    unittest.main()
