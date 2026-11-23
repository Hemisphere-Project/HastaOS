"""The Rasta 7.5 stack steps (#t-010): on the same 7.4-shaped card, the engine's port and
image/rastaos-bake leave identical trees, and a second run changes nothing."""

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

from hasta import stack
from tests.helpers import REPO

BAKE = os.path.join(REPO, "image", "rastaos-bake")
STEPS = "stamps,version,units,rtc,turbo,apfix,dns,wifi,nowde,media,starter"


def write(base, rel, text, mode=None):
    p = os.path.join(base, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    if mode is not None:
        os.chmod(p, mode)


def link(base, rel, target):
    p = os.path.join(base, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    os.symlink(target, p)


def card74(base):
    """A 7.4 card as bench-01 was before its bake: every defect a step removes, once."""
    S = "etc/systemd/system/"
    write(base, "boot/config.txt", "# 7.4\narm_64bit=0\n  dtoverlay=i2c-rtc,ds3231\n"
          "force_turbo=1\n[pi4]\nforce_turbo=1\n#force_turbo=1\n")
    write(base, "boot/VERSION", "7.0 -- bootstraped from raspbian\n")
    write(base, "boot/starter.txt", "# starter\nhostrename@bench-01\nhplayer2@26-anna\n"
          "  hostrename@player-000\n#hplayer2@old\nhplayer2@auto\n")
    write(base, "boot/hplayer2.conf", "[anna]\n")
    write(base, "boot/wifi/wint-hotspot.nmconnection", "psk=x\n")
    write(base, "etc/rastaos-patches.txt", "anna74 2026-09-01: one\nP3 field\n"
          "anna74 2026-09-02: two\nanna74 2026-09-03: three\nrastaos75 2026-11-01: old@abc\n")
    write(base, "etc/hostname", "bench-01\n")
    write(base, "etc/hosts", "127.0.0.1\tlocalhost\n127.0.1.1\tbench-01\n")
    write(base, "etc/cron.hourly/fake-hwclock", "#!/bin/sh\n", 0o755)
    write(base, "etc/NetworkManager/NetworkManager.conf", "[main]\nplugins=keyfile\n")
    write(base, "etc/NetworkManager/system-connections/wint-hotspot.nmconnection", "psk=x\n",
          0o600)
    write(base, "etc/NetworkManager/system-connections/_anna74-removed/old.nmconnection", "x\n")
    write(base, "etc/NetworkManager/system-connections/garden.nmconnection", "ssid=g\n", 0o600)
    link(base, "etc/resolv.conf", "/etc/resolvconf/run/resolv.conf")
    link(base, S + "sysinit.target.wants/fake-hwclock.service",
         "/lib/systemd/system/fake-hwclock.service")
    link(base, S + "multi-user.target.wants/rsync.service", "/lib/systemd/system/rsync.service")
    link(base, S + "multi-user.target.wants/rpi-eeprom-update.service",
         "/lib/systemd/system/rpi-eeprom-update.service")
    link(base, S + "timers.target.wants/apfix.timer", "/opt/Pi-tools/apfix/apfix.timer")
    link(base, S + "nfs-client.target", "/dev/null")
    link(base, S + "man-db.timer", "/dev/null")
    write(base, "opt/nowde-tools/nowde-2.0.3-esp32.bin", "old\n")
    write(base, "opt/nowde-tools/nowde-2.0.5-esp32.bin", "new\n")
    write(base, "opt/nowde-tools/flash.sh", "#!/bin/sh\n", 0o755)
    write(base, "opt/HPlayer2/core/engine/bootconf.py", "def pick_profile():\n    pass\n")
    write(base, "data/media/0_mire.mp4", "mire\n")
    write(base, "data/media/anna-01.mp4", "show\n")
    write(base, "data/media/anna/02.mp4", "show\n")
    write(base, "data/hplayer2-anna.cfg", "{}\n")
    write(base, "data/var/keep", "x\n")


def tree(base):
    """{relative path: (kind, mode, bytes or link target)}; the stamps line's date dropped."""
    out = {}
    for top, dirs, files in os.walk(base):
        for name in dirs + files:
            p = os.path.join(top, name)
            rel = os.path.relpath(p, base)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                out[rel] = ("link", None, os.readlink(p))
            elif stat.S_ISDIR(st.st_mode):
                out[rel] = ("dir", stat.S_IMODE(st.st_mode), None)
            else:
                with open(p, "rb") as f:
                    data = f.read()
                if rel == "etc/rastaos-patches.txt":
                    data = re.sub(rb"^rastaos75 [0-9-]{10}:", b"rastaos75 D:", data, flags=re.M)
                out[rel] = ("file", stat.S_IMODE(st.st_mode), data)
    return out


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
class BakeParity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hasta-stack-")
        self.a = os.path.join(self.tmp, "bake")
        self.b = os.path.join(self.tmp, "hasta")
        for d in (self.a, self.b):
            os.makedirs(d)
            card74(d)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def bake(self, d):
        return subprocess.run(["bash", BAKE, "--root", d, "--only", STEPS],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True)

    def test_the_port_and_the_bake_leave_identical_trees(self):
        p = self.bake(self.a)
        self.assertEqual(p.returncode, 0, p.stdout)
        r = stack.run(REPO, "rasta/7.5", root=self.b)
        self.assertEqual(r.failed, [])
        self.assertEqual(tree(self.a), tree(self.b))
        # every step had something to do on this card
        self.assertEqual([s["outcome"] for s in r.steps], ["changed"] * len(r.steps))
        self.assertEqual(len(r.changes), len(re.findall(r"^\[bake\]   ", p.stdout, re.M)))

    def test_a_second_run_changes_nothing(self):
        stack.run(REPO, "rasta/7.5", root=self.b)
        before = tree(self.b)
        r = stack.run(REPO, "rasta/7.5", root=self.b)
        self.assertEqual(r.changes, [])
        self.assertEqual(tree(self.b), before)
        # and the bake agrees: the port's result is a baked card
        p = self.bake(self.b)
        self.assertIn("done — 0 change(s)", p.stdout)

    def test_only_runs_the_named_steps(self):
        r = stack.run(REPO, "rasta/7.5", root=self.b, only=["dns", "rtc"])
        self.assertEqual([s["step"] for s in r.steps], ["rtc", "dns"])
        with open(os.path.join(self.b, "etc/hostname")) as f:
            self.assertEqual(f.read(), "bench-01\n")

    def test_refusals_match_the_bake(self):
        os.remove(os.path.join(self.a, "data/media/0_mire.mp4"))
        os.remove(os.path.join(self.b, "data/media/0_mire.mp4"))
        os.remove(os.path.join(self.a, "opt/HPlayer2/core/engine/bootconf.py"))
        os.remove(os.path.join(self.b, "opt/HPlayer2/core/engine/bootconf.py"))
        p = self.bake(self.a)
        self.assertEqual(p.returncode, 1)
        r = stack.run(REPO, "rasta/7.5", root=self.b)
        self.assertEqual(r.failed, ["media", "starter"])
        self.assertEqual(tree(self.a), tree(self.b))

    def test_an_unknown_step_or_stack_is_refused(self):
        with self.assertRaises(ValueError):
            stack.run(REPO, "rasta/7.5", root=self.b, only=["seal"])
        with self.assertRaises(ValueError):
            stack.run(REPO, "rasta/9.9", root=self.b)
        with self.assertRaises(ValueError):
            stack.run(REPO, "../etc", root=self.b)


if __name__ == "__main__":
    unittest.main()
