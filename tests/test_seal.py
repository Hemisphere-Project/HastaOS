"""`hasta seal` (#t-010): the bake's seal, refused while `check` is not clean.

On the same baked 7.4-shaped card, `hasta seal` with every difference waived leaves the tree the
bake's own seal leaves (tests/fixtures/rastaos-bake-7.5.reference), plus its record under
/etc/hasta/. Without the waivers it changes nothing; a waiver naming no difference is refused; a
release checkout with uncommitted work is one more difference; the stack's own refusals still
come before any write."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from hasta import check as check_mod
from hasta import seal as seal_mod
from hasta import stack
from tests.helpers import REPO
from tests.test_cli import run
from tests.test_stack import REFERENCE, WRAPPER, card74, link, tree, write

RECORDS = ("etc/hasta", "etc/hasta/seal", "etc/hasta/seal.waived")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@localhost",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@localhost")


def sealable(base, k):
    """A baked card as the capture leaves it: what the seal resets, and what it needs."""
    stack.run(REPO, "rasta/7.5", root=base)
    write(base, "etc/os-release", 'ID=raspbian\nVERSION_ID="10"\nVERSION="10 (buster)"\n')
    link(base, "usr/local/bin/extendfs", "/opt/Pi-tools/extendfs/extendfs")
    link(base, "etc/systemd/system/extendfs.service", "/opt/Pi-tools/extendfs/extendfs.service")
    write(base, "usr/bin/growpart", "#!/bin/sh\n", 0o755)
    write(base, "data/var/drive-id", "SERIAL-BENCH01\n")
    write(base, "data/var/fake-clock", "keep\n")
    write(base, "data/var/tailscale/tailscaled.state", "node\n")
    write(base, "data/var/log/blackbox.log", "bb\n")
    write(base, "data/var/log/journal-export/2026-11-13.log", "je\n")
    write(base, "var/log/journal/abc/system.journal", "j\n")
    write(base, "root/.bash_history", "wpa_passphrase x\n")

    def pub(n):
        with open(os.path.join(k, n + ".pub")) as f:
            return f.read()
    write(base, "root/.ssh/authorized_keys", "# root keys\n" + pub("keepa")
          + 'restrict,command="/usr/bin/false" ' + pub("rig") + pub("keepb"))
    write(base, "home/pi/.ssh/authorized_keys", pub("rig"))


def no_records(t):
    return dict((p, v) for p, v in t.items() if p not in RECORDS)


@unittest.skipUnless(shutil.which("bash") and shutil.which("git") and shutil.which("ssh-keygen"),
                     "needs bash, git and ssh-keygen")
class Seal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.mkdtemp(prefix="hasta-seal-keys-")
        for n in ("keepa", "keepb", "rig"):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", n, "-f",
                            os.path.join(cls.keys, n)], check=True)
        cls.keep = os.path.join(cls.keys, "keys.keep")
        with open(cls.keep, "w") as f:
            for n in ("keepa", "keepb"):
                out = subprocess.run(["ssh-keygen", "-lf", os.path.join(cls.keys, n + ".pub")],
                                     stdout=subprocess.PIPE, check=True).stdout.decode()
                f.write("%s %s\n" % (out.split()[1], n))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.keys)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hasta-seal-")
        self.a = os.path.join(self.tmp, "bake")
        self.b = os.path.join(self.tmp, "hasta")
        for d in (self.a, self.b):
            os.makedirs(d)
            card74(d)
            sealable(d, self.keys)
        self.waive = os.path.join(self.tmp, "waive")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def seal(self, *argv, **kw):
        return run(*(kw.get("pre", ()) + ("seal", "--root", kw.get("root", self.b), "--keys",
                                          self.keep) + argv))

    def refusal(self):
        code, out, err = self.seal(pre=("--json",))
        self.assertEqual(code, 1, out + err)
        return json.loads(out)

    def waive_all(self, but=()):
        keys = sorted(set(d["key"] for d in self.refusal()["differences"]) - set(but))
        with open(self.waive, "w") as f:
            f.write("# the test card: accepted as it stands\n")
            f.write("".join("%s  # fixture\n" % k for k in keys))
        return keys

    def bake(self, d, script=REFERENCE, *argv):
        env = dict(os.environ, BAKE_HOME=os.path.join(REPO, "image"))
        return subprocess.run(["bash", script, "--only", "seal", "--root", d, "--boot",
                               os.path.join(d, "boot"), "--data", os.path.join(d, "data"),
                               "--keys", self.keep] + list(argv), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True)

    def test_refused_while_check_is_not_clean(self):
        before = tree(self.b)
        code, out, _ = self.seal()
        self.assertEqual(code, 1)
        self.assertIn("seal: refused — ", out)
        self.assertRegex(out, r"(?m)^refused    different   rasta-7\.5 /etc/systemd/system/")
        self.assertEqual(tree(self.b), before)
        r = self.refusal()
        self.assertTrue(r["differences"])
        self.assertFalse(any(d["waived"] for d in r["differences"]))
        self.assertEqual((r["sealed"], r["changes"]), (False, []))

    def test_every_difference_waived_it_is_the_bakes_seal(self):
        ref = self.bake(self.a)
        self.assertEqual(ref.returncode, 0, ref.stdout)
        self.waive_all()
        code, out, err = self.seal("--waive", self.waive, pre=("--json",))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(no_records(tree(self.a)), no_records(tree(self.b)))
        # the same change lines as the bake's, then the record's
        r = json.loads(out)
        bake = re.findall(r"^\[bake\]   (.*)$", ref.stdout, re.M)
        self.assertEqual([c for c in r["changes"] if "/etc/hasta/" not in c], bake)
        self.assertTrue(any("key dropped — /home/pi/.ssh/authorized_keys" in c for c in bake))
        with open(os.path.join(self.b, "root/.ssh/authorized_keys")) as f:
            ak = f.read()
        self.assertNotIn(" rig", ak)
        self.assertEqual(ak.count("ssh-ed25519 "), 2)

    def test_the_image_records_the_seal_and_its_waivers(self):
        keys = self.waive_all()
        code, out, err = self.seal("--waive", self.waive)
        self.assertEqual(code, 0, out + err)
        with open(os.path.join(self.b, "etc/hasta/seal")) as f:
            rec = dict(ln.split("=", 1) for ln in f.read().splitlines())
        self.assertEqual(rec["STACK"], "rasta/7.5")
        self.assertEqual(rec["CHECK"], "waived")
        self.assertEqual(rec["WAIVED"], str(len(keys)))
        self.assertTrue(rec["ENGINE"] and rec["SEALED_AT"])
        with open(os.path.join(self.b, "etc/hasta/seal.waived")) as f, open(self.waive) as w:
            self.assertEqual(f.read(), w.read())
        self.assertRegex(out, r"(?m)^waived     different   rasta-7\.5 /etc/systemd/system/")
        self.assertIn("seal       rasta/7.5: ", out)

    def test_a_second_seal_changes_nothing(self):
        self.waive_all()
        self.assertEqual(self.seal("--waive", self.waive)[0], 0)
        before = tree(self.b)
        code, out, _ = self.seal("--waive", self.waive, pre=("--json",))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["changes"], [])
        self.assertEqual(tree(self.b), before)

    def test_a_waiver_that_names_no_difference_is_refused(self):
        self.waive_all()
        with open(self.waive, "a") as f:
            f.write("/etc/systemd/system/nothing-here.service  # stale\n")
        before = tree(self.b)
        code, out, _ = self.seal("--waive", self.waive)
        self.assertEqual(code, 1)
        self.assertIn("unused     /etc/systemd/system/nothing-here.service  # stale", out)
        self.assertIn("1 waiver(s) matching no difference", out)
        self.assertEqual(tree(self.b), before)

    def test_a_release_checkout_with_uncommitted_work_is_refused_until_committed(self):
        co = os.path.join(self.b, "opt", "Pi-tools")
        os.makedirs(co)
        git = lambda *a: subprocess.run(["git", "-C", co] + list(a), env=GIT_ENV, check=True,
                                        stdout=subprocess.DEVNULL)
        git("init", "-q")
        write(co, "f", "7.5\n")
        git("add", "f")
        git("commit", "-qm", "7.5")
        write(co, "f", "a live edit\n")
        write(co, "new.sh", "#!/bin/sh\n")
        self.waive_all(but=("/opt/Pi-tools/f", "/opt/Pi-tools/new.sh"))
        r = json.loads(self.seal("--waive", self.waive, pre=("--json",))[1])
        self.assertEqual(r["checkout"], "dirty")
        self.assertEqual(sorted((d["key"], d["what"]) for d in r["differences"]
                                if not d["waived"]),
                         [("/opt/Pi-tools/f", "not committed (M)"),
                          ("/opt/Pi-tools/new.sh", "untracked")])
        self.assertIsNotNone(r["refused"])
        git("add", "-A")
        git("commit", "-qm", "the live work, committed")
        code, out, err = self.seal("--waive", self.waive, pre=("--json",))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["checkout"], "clean")

    def test_the_stacks_own_refusal_changes_nothing(self):
        self.waive_all()
        os.remove(os.path.join(self.b, "usr/bin/growpart"))
        before = tree(self.b)
        code, out, _ = self.seal("--waive", self.waive)
        self.assertEqual(code, 1)
        self.assertIn("step       seal: no growpart", out)
        self.assertIn("the stack's seal (rasta/7.5) refused; nothing changed", out)
        self.assertEqual(tree(self.b), before)

    def test_a_clean_check_needs_no_waiver(self):
        real = check_mod.make

        def clean(*a, **kw):
            r = real(*a, **kw)
            return check_mod.Report(r.facts, r.modules, r.stack, [], [], [], r.problems)
        lines = []
        with mock.patch.object(check_mod, "make", clean):
            r = seal_mod.run(REPO, self.b, keys=self.keep, out=lines.append)
        self.assertTrue(r.sealed, lines)
        self.assertEqual((r.refused, r.differences), (None, []))
        with open(os.path.join(self.b, "etc/hasta/seal")) as f:
            self.assertIn("CHECK=clean\nWAIVED=0\n", f.read())
        self.assertFalse(os.path.exists(os.path.join(self.b, "etc/hasta/seal.waived")))

    def test_usage(self):
        self.assertEqual(self.seal(root="/")[0], 2)
        code, _, err = self.seal("--waive", os.path.join(self.tmp, "absent"))
        self.assertEqual(code, 2)
        self.assertIn("--waive", err)
        os.remove(os.path.join(self.b, "boot/VERSION"))
        code, _, err = self.seal()
        self.assertEqual(code, 2)
        self.assertIn("name no stack", err)

    def test_the_bake_seals_through_it(self):
        ref = self.bake(self.a)
        self.assertEqual(ref.returncode, 0, ref.stdout)
        before = tree(self.b)
        p = self.bake(self.b, WRAPPER)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("[bake] seal: refused — ", p.stdout)
        self.assertIn("[bake] step seal FAILED", p.stdout)
        self.assertEqual(tree(self.b), before)
        self.waive_all()
        p = self.bake(self.b, WRAPPER, "--waive", self.waive)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(no_records(tree(self.a)), no_records(tree(self.b)))
        changes = re.compile(r"^\[bake\]   (?!seal: /etc/hasta/).*$", re.M)
        self.assertEqual(changes.findall(p.stdout), changes.findall(ref.stdout))
        again = self.bake(self.b, WRAPPER, "--waive", self.waive)
        self.assertIn("seal: already done", again.stdout)
        self.assertIn("done — 0 change(s)", again.stdout)


if __name__ == "__main__":
    unittest.main()
