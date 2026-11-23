"""`hasta check` (#t-007) on the 7.5 rc's /etc/systemd/system, rebuilt as a tree from the listing
(tests/fixtures/listings/): every defect the listing's note names is reported, and none of the
wanted masks. Read-only: the tree is a temporary directory, nothing else is touched.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from hasta import check, legacy
from tests.helpers import REPO, root
from tests.test_declared import EXPECTED_DRIFT, OUTSIDE, LISTING, listing

HASTA = os.path.join(REPO, "bin", "hasta")
RECEIPTS = os.path.join(REPO, "tests", "fixtures", "receipts", "rastaos-7.5.txt")
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def rc_box():
    """A Rasta 7.5 card in a reader: the rasta-card root, the 7.5 receipts, and the rc's
    /etc/systemd/system as the listing gives it (links as links, files at their size)."""
    tmp = tempfile.mkdtemp(prefix="hasta-check-")
    box = os.path.join(tmp, "root")
    shutil.copytree(root("rasta-card"), box, symlinks=True)
    shutil.copy(RECEIPTS, os.path.join(box, "etc", "pi-tools-modules.txt"))
    shutil.copy(os.path.join(root("pi3-buster"), "etc", "rastaos-release"),
                os.path.join(box, "etc", "rastaos-release"))
    base = os.path.join(box, "etc", "systemd", "system")
    for rel, target, size in listing():
        p = os.path.join(base, rel)
        if not os.path.isdir(os.path.dirname(p)):
            os.makedirs(os.path.dirname(p))
        if target is not None:
            os.symlink(target, p)
        else:
            with open(p, "w") as fh:
                fh.write("#" * size)
    return tmp, box


class CheckRc(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.box = rc_box()
        cls.report = check.make(REPO, cls.box)
        cls.paths = set(f.path for f in cls.report.different + cls.report.undeclared)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_it_reads_the_box_as_rasta_7_5(self):
        self.assertEqual(self.report.stack, "rasta-7.5")
        for m in ("rorw", "network-tools", "audiohub", "usbfix"):
            self.assertIn(m, self.report.modules)

    def test_every_defect_the_note_names_is_reported(self):
        for rel in sorted(EXPECTED_DRIFT):
            with self.subTest(entry=rel):
                self.assertIn(check.UNIT_DIR + "/" + rel, self.paths)

    def test_no_wanted_mask_is_reported(self):
        for rel, target, _ in listing():
            if target != "/dev/null" or rel in EXPECTED_DRIFT:
                continue
            with self.subTest(mask=rel):
                self.assertNotIn(check.UNIT_DIR + "/" + rel, self.paths)

    def test_undeclared_is_the_named_drift_and_the_app_s_entries_only(self):
        named = set(check.UNIT_DIR + "/" + rel for rel in list(EXPECTED_DRIFT) + list(OUTSIDE))
        for f in self.report.undeclared:
            with self.subTest(entry=f.path):
                self.assertIn(f.path, named)

    def test_a_copy_where_the_module_links_names_the_module(self):
        # usbfix has a receipt: its module is on the box, the copy is its difference
        diff = dict((f.path, f) for f in self.report.different)
        f = diff[check.UNIT_DIR + "/usbfix.timer"]
        self.assertEqual((f.module, f.what), ("usbfix", "a copy, where the module links its unit"))
        # apfix has none (a field pack put it there): undeclared, and who declares the unit
        und = dict((f.path, f) for f in self.report.undeclared)
        f = und[check.UNIT_DIR + "/apfix.service"]
        self.assertEqual(f.kind, "file")
        self.assertIn("declared by apfix", f.what)

    def test_the_masks_against_a_module_name_their_module(self):
        diff = dict((f.path, f) for f in self.report.different)
        f = diff[check.UNIT_DIR + "/dnsmasq.service"]
        self.assertEqual((f.module, f.what), ("network-tools", "masked, declared enabled"))

    def test_an_app_s_drop_in_is_undeclared_without_its_tree(self):
        kinds = dict((f.path, f.kind) for f in self.report.undeclared)
        self.assertEqual(kinds.get(check.UNIT_DIR + "/hplayer2@.service.d/tmpdir.conf"),
                         "drop-in")

    def test_cli_text_json_and_exit_1(self):
        p = subprocess.run([sys.executable, HASTA, "check", "--root", self.box, "--json"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, env=ENV)
        self.assertEqual(p.returncode, 1, p.stderr)
        obj = json.loads(p.stdout)
        self.assertFalse(obj["clean"])
        self.assertIn(check.UNIT_DIR + "/wint-rejoin.service",
                      [f["path"] for f in obj["undeclared"]])
        p = subprocess.run([sys.executable, HASTA, "check", "--root", self.box],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, env=ENV)
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("undeclared", p.stdout)
        self.assertIn("usbvol.timer", p.stdout)
        self.assertIn("receipt     differs", p.stdout)

    def test_module_narrows_to_one_module(self):
        r = check.make(REPO, self.box, only="usbfix")
        self.assertEqual(r.modules, ["usbfix"])
        self.assertEqual(r.undeclared, [])
        self.assertTrue(all(f.module == "usbfix" for f in r.different))


class CheckReceipts(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.box = rc_box()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_fingerprint_is_module_manifest_s(self):
        for name, m in sorted(legacy.discover(REPO).items()):
            p = subprocess.run(["bash", os.path.join(REPO, "setup", "module-manifest"),
                                "fingerprint", m.path], stdout=subprocess.PIPE,
                               universal_newlines=True)
            with self.subTest(module=name):
                self.assertEqual(check.fingerprint(m.path), p.stdout.strip())

    def test_the_rc_receipts_are_older_than_this_tree(self):
        r = check.make(REPO, self.box)
        kinds = dict((f.module, f) for f in r.receipts)
        self.assertEqual(kinds["rorw"].kind, "differs")
        self.assertIn("receipt 2c6ff75ba27b", kinds["rorw"].what)
        self.assertEqual(kinds["webconf"].kind, "unrecorded")
        release = kinds[None]
        self.assertEqual(release.kind, "same")
        self.assertIn("rastaos-7.5 at 1c927c4, in the tree's history", release.what)

    def test_a_receipt_of_the_tree_s_version_is_the_same(self):
        path = os.path.join(self.tmp, "receipts.txt")
        with open(path, "w") as fh:
            fh.write("usbfix %s 2026-11-23T00:00:00+01:00 -\n"
                     % check.fingerprint(os.path.join(REPO, "usbfix")))
            fh.write("gone 000000000000 2026-11-23T00:00:00+01:00 -\n")
        r = check.make(REPO, self.box, receipts_path=path)
        kinds = dict((f.module, f.kind) for f in r.receipts)
        self.assertEqual(kinds["usbfix"], "same")
        self.assertEqual(kinds["gone"], "not-in-tree")


class CheckLinesAndPackages(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hasta-check-")
        self.box = os.path.join(self.tmp, "root")
        shutil.copytree(root("pi3-buster"), self.box, symlinks=True)
        os.makedirs(os.path.join(self.box, "etc", "systemd", "system"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config_findings(self):
        r = check.make(REPO, self.box)
        return [f for f in r.different if f.path == "/boot/config.txt"]

    def test_a_config_line_the_stack_declares_absent(self):
        self.assertEqual(self.config_findings(), [])
        with open(os.path.join(self.box, "boot", "config.txt"), "a") as fh:
            fh.write("force_turbo=1\n")
        found = self.config_findings()
        self.assertEqual([(f.module, f.what) for f in found],
                         [("rasta-7.5", "'force_turbo=1' present, declared absent")])

    def test_config_sections_and_fstab_fields(self):
        boot = "/boot"
        line = lambda **kw: check.check_line(self.box, "m", boot, kw)
        self.assertEqual(line(file="config", line="disable_splash=1", section="all"), [])
        self.assertEqual(len(line(file="config", line="dtoverlay=vc4-fkms-v3d", section="all")), 1)
        self.assertEqual(line(file="config", line="dtoverlay=vc4-fkms-v3d", section="pi4"), [])
        self.assertEqual(line(file="fstab", line="proc /proc proc defaults 0 0"), [])
        self.assertEqual(len(line(file="fstab", line="tmpfs /tmp tmpfs defaults 0 0")), 1)
        self.assertEqual(line(file="cmdline", line="fastboot"), [])  # no cmdline.txt: not a check

    def test_packages_per_base_or_any(self):
        have = check.installed(self.box)
        self.assertTrue(have)
        pkg = sorted(have)[0]
        ok = check.check_packages("m", {"any": [pkg], "debian-trixie": ["nope"]},
                                  "raspbian-buster", have)
        self.assertEqual(ok, [])
        bad = check.check_packages("m", {"raspbian-buster": ["not-there"]}, "raspbian-buster",
                                   have)
        self.assertEqual([(f.path, f.what) for f in bad], [("package not-there", "not installed")])


class CheckEdges(unittest.TestCase):

    def test_not_a_box_is_exit_2(self):
        p = subprocess.run([sys.executable, HASTA, "check", "--root", root("x86-esp")],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, env=ENV)
        self.assertEqual(p.returncode, 2)
        self.assertIn("not a box", p.stderr)

    def test_the_listing_fixture_is_the_one_the_note_gives(self):
        self.assertTrue(os.path.isfile(LISTING))


if __name__ == "__main__":
    unittest.main()
