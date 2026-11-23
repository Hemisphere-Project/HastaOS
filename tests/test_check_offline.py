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

from hasta import check, facts, image
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


TOOLS = all(shutil.which(c) for c in ("mke2fs", "debugfs", "sfdisk"))
FAT = all(shutil.which(c) for c in ("mkfs.vfat", "mcopy"))
MiB = 1024 * 1024


def _sh(*cmd, **kw):
    subprocess.run(list(cmd), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)


def build_image(path, root_dir, boot_dir=None, data_dir=None):
    """A card image built without root: an MBR table (sfdisk on a plain file), the FAT by
    mkfs.vfat --offset and mcopy, each ext4 by `mke2fs -d -E offset=`."""
    with open(path, "wb") as fh:
        fh.truncate(64 * MiB)
    _sh("sfdisk", "-q", path, input=b"label: dos\nstart=2048, size=16384, type=c\n"
        b"start=18432, size=65536, type=83\nstart=83968, type=83\n")
    if boot_dir and FAT:
        _sh("mkfs.vfat", "-n", "boot", "--offset", "2048", path, "8192")
        names = [os.path.join(boot_dir, n) for n in sorted(os.listdir(boot_dir))]
        if names:
            _sh(*(["mcopy", "-s", "-i", "%s@@%d" % (path, 2048 * 512)] + names + ["::/"]))
    _sh("mke2fs", "-q", "-t", "ext4", "-L", "rootfs", "-d", root_dir,
        "-E", "offset=%d" % (18432 * 512), path, "32M")
    if data_dir:
        _sh("mke2fs", "-q", "-t", "ext4", "-L", "data", "-d", data_dir,
            "-E", "offset=%d" % (83968 * 512), path, "%dk" % ((131072 - 83968) // 2))


@unittest.skipUnless(TOOLS, "needs mke2fs, debugfs and sfdisk")
class ImageFile(unittest.TestCase):
    """The rc card as an image file: boot FAT, rootfs and data built by the test, no root."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.box = rc_box()
        cls.data = os.path.join(cls.tmp, "data")
        os.makedirs(os.path.join(cls.data, "var"))
        with open(os.path.join(cls.data, "var", "fake-clock.state"), "w") as fh:
            fh.write("2026-11-23\n")
        cls.rootfs = os.path.join(cls.tmp, "rootfs")
        shutil.copytree(cls.box, cls.rootfs, symlinks=True)
        shutil.rmtree(os.path.join(cls.rootfs, "boot"))
        os.mkdir(os.path.join(cls.rootfs, "boot"))
        os.mkdir(os.path.join(cls.rootfs, "data"))
        cls.img = os.path.join(cls.tmp, "card.img")
        build_image(cls.img, cls.rootfs, os.path.join(cls.box, "boot"), cls.data)
        cls.want = check.make(REPO, facts.mounted(cls.box, data=cls.data))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_partitions_found_by_label_and_role(self):
        got = [(p.number, p.fs, p.label, p.role) for p in image.partitions(self.img)]
        want = [(2, "ext", "rootfs", "root"), (3, "ext", "data", "data")]
        if FAT:
            want.insert(0, (1, "vfat", "boot", "boot"))
        self.assertEqual(got, want)

    def test_masked_units_come_out_as_masks(self):
        o = image.Opened(self.img, check.declared_paths(REPO), as_root=False)
        try:
            unit_dir = os.path.join(o.dirs["root"], "etc", "systemd", "system")
            links = [os.path.join(unit_dir, n) for n in os.listdir(unit_dir)]
            masks = [l for l in links if os.path.islink(l) and os.readlink(l) == "/dev/null"]
            self.assertTrue(masks)
        finally:
            o.close()
        self.assertFalse(os.path.exists(o.tmp))

    @unittest.skipUnless(FAT, "needs mkfs.vfat and mtools")
    def test_check_image_reports_what_check_root_reports(self):
        o = image.Opened(self.img, check.declared_paths(REPO), as_root=False)
        try:
            r = check.make(REPO, o.root)
        finally:
            o.close()
        self.assertEqual(_same(r), _same(self.want))
        self.assertEqual(r.stack, "rasta-7.5")

    @unittest.skipUnless(FAT, "needs mkfs.vfat and mtools")
    def test_the_cli_takes_image(self):
        def run(*args):
            p = subprocess.run([sys.executable, HASTA, "--json", "check"] + list(args),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ENV)
            return p.returncode, json.loads(p.stdout.decode() or "null")
        code_r, by_root = run("--root", self.box, "--data", self.data)
        code_i, by_image = run("--image", self.img)
        self.assertEqual(code_i, code_r)
        self.assertEqual(by_image.pop("root"), self.img)
        self.assertEqual(by_image.pop("image")["read"], "extracted")
        by_root.pop("root")
        self.assertEqual(by_image, by_root)
        code, _ = run("--image", self.img, "--root", self.box)
        self.assertEqual(code, 2)

    def test_without_mtools_the_boot_partition_is_a_notice(self):
        which = image.shutil.which
        image.shutil.which = lambda c: None if c == "mcopy" else which(c)
        try:
            o = image.Opened(self.img, (), as_root=False)
            o.close()
        finally:
            image.shutil.which = which
        if FAT:
            self.assertEqual(len(o.notices), 1)
            self.assertIn("mtools is not installed", o.notices[0])
        self.assertNotIn("boot", o.dirs if o.dirs.get("boot") else {})

    def test_a_bare_filesystem_is_its_own_root(self):
        bare = os.path.join(self.tmp, "bare.img")
        with open(bare, "wb") as fh:
            fh.truncate(32 * MiB)
        _sh("mke2fs", "-q", "-t", "ext4", "-d", self.rootfs, bare)
        parts = image.partitions(bare)
        self.assertEqual([(p.number, p.fs, p.role) for p in parts], [(0, "ext", "root")])

    def test_no_ext_partition_is_not_an_image_to_check(self):
        junk = os.path.join(self.tmp, "junk.img")
        with open(junk, "wb") as fh:
            fh.write(b"\0" * 65536)
        with self.assertRaises(ValueError):
            image.Opened(junk, (), as_root=False)


if __name__ == "__main__":
    unittest.main()
