"""`hasta check` off the box (#t-008): a card in a reader whose partitions the desktop mounted
apart. `--root` with `--boot` and `--data` reports what `check` reports on the same tree joined.
Read-only: every tree is a temporary directory.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from hasta import check, facts
from tests.helpers import REPO, root
from tests.test_check import ENV, HASTA, rc_box


def _same(report):
    out = report.as_dict()
    out.pop("root")
    return out


class SplitCard(unittest.TestCase):
    """The rc card with its boot FAT moved out of the root, as a reader mounts it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.box = rc_box()
        cls.joined = check.make(REPO, cls.box)
        cls.split = os.path.join(cls.tmp, "split")
        shutil.copytree(cls.box, cls.split, symlinks=True)
        cls.bootfs = os.path.join(cls.tmp, "bootfs")
        shutil.move(os.path.join(cls.split, "boot"), cls.bootfs)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_boot_elsewhere_reports_what_the_joined_tree_reports(self):
        r = check.make(REPO, facts.mounted(self.split, boot=self.bootfs))
        self.assertEqual(_same(r), _same(self.joined))
        self.assertEqual(r.facts.values["boot"], self.joined.facts.values["boot"])

    def test_without_boot_the_card_has_no_boot_partition(self):
        r = check.make(REPO, self.split)
        self.assertIsNone(r.facts.values["boot"])

    def test_the_cli_takes_boot(self):
        def run(*args):
            p = subprocess.run([sys.executable, HASTA, "--json", "check"] + list(args),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ENV)
            return p.returncode, p.stdout.decode()
        code_j, out_j = run("--root", self.box)
        code_s, out_s = run("--root", self.split, "--boot", self.bootfs)
        self.assertEqual(code_s, code_j)
        j, s = json.loads(out_j), json.loads(out_s)
        j.pop("root"), s.pop("root")
        self.assertEqual(s, j)
        code, _ = run("--root", self.split, "--boot", os.path.join(self.tmp, "nope"))
        self.assertEqual(code, 2)


class Mounts(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hasta-mounts-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_boot_point_follows_the_fstab(self):
        self.assertEqual(facts.boot_point(root("pi4-bookworm")), "/boot/firmware")
        self.assertEqual(facts.boot_point(root("pi3-buster")), "/boot")
        self.assertEqual(facts.boot_point(root("x86-esp")), "/boot/efi")
        self.assertEqual(facts.boot_point(root("rasta-card")), "/boot")

    def test_data_is_read_from_its_own_directory(self):
        data = os.path.join(self.tmp, "data")
        os.makedirs(os.path.join(data, "var"))
        with open(os.path.join(data, "var", "fake-clock.state"), "w") as fh:
            fh.write("2026-11-23\n")
        r = facts.mounted(root("rasta-card"), data=data)
        self.assertEqual(facts.read_text(r, "/data/var/fake-clock.state"), "2026-11-23\n")
        self.assertIsNone(facts.read_text(root("rasta-card"), "/data/var/fake-clock.state"))
        self.assertEqual(facts.read_text(r, "/etc/os-release"),
                         facts.read_text(root("rasta-card"), "/etc/os-release"))

    def test_no_directory_given_leaves_the_root_as_it_is(self):
        self.assertIs(facts.mounted("/x"), "/x")

    def test_the_longest_mount_point_wins(self):
        r = facts.Root("/r", [("/boot", "/b"), ("/boot/firmware", "/f")])
        self.assertEqual(facts._join(r, "/boot/firmware/config.txt"), "/f/config.txt")
        self.assertEqual(facts._join(r, "/boot/VERSION"), "/b/VERSION")
        self.assertEqual(facts._join(r, "/bootx"), "/r/bootx")


if __name__ == "__main__":
    unittest.main()
