"""`hasta apply` through the legacy driver: receipts, a second apply that changes nothing, an
interrupted apply that resumes, and the fingerprint setup/module-manifest computes.

The box is a temporary root and the runner a stand-in that lays down what the installer would:
the legacy driver itself applies to the running box only, which is a chroot's or a container's
business, never a test's.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from hasta import apply as apply_mod
from tests.helpers import REPO

HASTA = os.path.join(REPO, "bin", "hasta")
MANIFEST = os.path.join(REPO, "setup", "module-manifest")
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


class Killed(Exception):
    pass


class FakeRunner(object):
    """Does to the box what install_module would for a standard module (its bins appear);
    records every call; dies on the module named `kill`."""

    def __init__(self, box, kill=None):
        self.box, self.kill, self.ran, self.finished = box, kill, [], 0

    def run(self, row):
        if row.name == self.kill:
            raise Killed(row.name)
        self.ran.append((row.name, row.action))
        for p in row.module.declared():
            full = os.path.join(self.box, p.lstrip("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            open(full, "w").close()
        return True

    def finish(self):
        self.finished += 1


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Apply(unittest.TestCase):

    def setUp(self):
        self.tree = tempfile.mkdtemp()
        self.box = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tree)
        self.addCleanup(shutil.rmtree, self.box)
        mods = {
            "alpha": "[module]\ndescription = a\n[files]\nbins = alpha-tool\n",
            "beta": "[module]\ndescription = b\n[install]\nscript = yes\nupgrade = upgrade.sh\n",
            "gamma": "[module]\ndescription = g\n[install]\nscript = yes\n",
            "delta": "[module]\ndescription = d\n[files]\nbins = delta-tool\n",
        }
        for name, ini in mods.items():
            write(os.path.join(self.tree, name, "module.ini"), ini)
            write(os.path.join(self.tree, name, "upgrade.sh"), "#!/bin/sh\n")
        open(os.path.join(self.tree, "alpha", "alpha-tool"), "w").close()
        open(os.path.join(self.tree, "delta", "delta-tool"), "w").close()
        # on the box: delta fully linked; alpha, beta, gamma in the receipts only
        write(os.path.join(self.box, "usr/local/bin/delta-tool"), "")
        self.receipts = os.path.join(self.box, "etc", "pi-tools-modules.txt")
        write(self.receipts, "alpha x 0 -\nbeta x 0 -\ngamma x 0 -\n")

    def apply(self, runner, **kw):
        return apply_mod.apply(self.tree, self.box, platform="pi", runner=runner,
                               now="2026-11-24T00:00:00+0100", **kw)

    def receipt(self, name):
        return apply_mod.read_receipt(os.path.join(self.box, "etc/hasta/receipts", name))

    def snapshot(self):
        out = {}
        for top, _, files in os.walk(self.box):
            for name in files:
                p = os.path.join(top, name)
                with open(p, "rb") as f:
                    out[os.path.relpath(p, self.box)] = (f.read(), os.stat(p).st_mtime_ns)
        return out

    def test_first_apply_runs_converges_classes_and_writes_receipts(self):
        run = FakeRunner(self.box)
        r = self.apply(run)
        self.assertEqual(r.failed, [])
        self.assertEqual(sorted(run.ran), [("alpha", "install"), ("beta", "upgrade")])
        outcomes = dict((s["module"], s["outcome"]) for s in r.steps)
        self.assertEqual(outcomes, {"alpha": "ran", "beta": "ran", "gamma": "none",
                                    "delta": "recorded"})
        for name in ("alpha", "beta", "delta"):
            rec = self.receipt(name)
            self.assertEqual(rec["MODULE"], name)
            self.assertEqual(rec["FINGERPRINT"],
                             apply_mod.fingerprint(os.path.join(self.tree, name)))
            self.assertEqual(rec["VERSION"], "legacy")
            self.assertEqual(rec["APPLIED_AT"], "2026-11-24T00:00:00+0100")
        self.assertEqual(self.receipt("gamma"), {})     # held: not run, no receipt
        self.assertIsNone(apply_mod.pending(self.box))  # marker gone once all applied
        self.assertEqual(run.finished, 1)

    def test_twice_in_a_row_changes_nothing_the_second_time(self):
        self.apply(FakeRunner(self.box))
        before = self.snapshot()
        run = FakeRunner(self.box)
        r = self.apply(run)
        self.assertEqual(run.ran, [])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(s["module"] for s in r.steps if s["outcome"] == "current"),
                         ["alpha", "beta", "delta"])

    def test_a_changed_module_runs_again_alone(self):
        self.apply(FakeRunner(self.box))
        write(os.path.join(self.tree, "beta", "upgrade.sh"), "#!/bin/sh\necho new\n")
        run = FakeRunner(self.box)
        self.apply(run)
        self.assertEqual(run.ran, [("beta", "upgrade")])

    def test_force_runs_everything_again(self):
        self.apply(FakeRunner(self.box))
        run = FakeRunner(self.box)
        r = self.apply(run, force=True)
        # alpha's bin is on the box now: converge's rule makes it `linked`, re-recorded only
        self.assertEqual(run.ran, [("beta", "upgrade")])
        self.assertEqual(dict((s["module"], s["outcome"]) for s in r.steps),
                         {"alpha": "recorded", "beta": "ran", "gamma": "none",
                          "delta": "recorded"})

    def test_an_apply_killed_half_way_completes_on_resume(self):
        request = {"tree": self.tree, "platform": "pi"}
        with self.assertRaises(Killed):
            self.apply(FakeRunner(self.box, kill="beta"), request=request)
        self.assertEqual(apply_mod.pending(self.box)["tree"], self.tree)   # marker stays
        self.assertEqual(self.receipt("beta"), {})
        self.assertNotEqual(self.receipt("alpha"), {})                     # finished first
        run = FakeRunner(self.box)
        r = self.apply(run, resumed=True)
        self.assertEqual(run.ran, [("beta", "upgrade")])                  # only what was left
        self.assertTrue(r.resumed)
        self.assertIsNone(apply_mod.pending(self.box))

    def test_a_failed_module_keeps_the_marker_and_no_receipt(self):
        class Fails(FakeRunner):
            def run(self, row):
                return row.name != "beta" and FakeRunner.run(self, row)
        r = self.apply(Fails(self.box))
        self.assertEqual(r.failed, ["beta"])
        self.assertEqual(self.receipt("beta"), {})
        self.assertIsNotNone(apply_mod.pending(self.box))

    def test_dry_run_writes_nothing(self):
        r = self.apply(FakeRunner(self.box), dry_run=True)
        self.assertFalse(os.path.exists(os.path.join(self.box, "etc/hasta")))
        self.assertIsNone(apply_mod.pending(self.box))
        self.assertEqual(dict((s["module"], s["outcome"]) for s in r.steps),
                         {"alpha": "would-run", "beta": "would-run", "gamma": "none",
                          "delta": "would-record"})

    def test_the_legacy_driver_refuses_another_root(self):
        with self.assertRaises(ValueError):
            apply_mod.apply(self.tree, self.box, platform="pi")

    def cli(self, *args):
        p = subprocess.run([sys.executable, HASTA, "--json", "apply"] + list(args),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ENV)
        out = p.stdout.decode()
        return p.returncode, (json.loads(out) if out.strip() else None), p.stderr.decode()

    def test_cli(self):
        code, obj, err = self.cli("--root", self.box, "--tree", self.tree, "--platform", "pi",
                                  "--receipts", self.receipts)
        self.assertEqual(code, 2, err)
        self.assertIn("running box only", err)
        code, obj, err = self.cli("--root", self.box, "--tree", self.tree, "--platform", "pi",
                                  "--receipts", self.receipts, "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(s["module"] for s in obj["steps"]
                                if s["outcome"] == "would-run"), ["alpha", "beta"])
        code, obj, err = self.cli("--root", self.box, "--resume")
        self.assertEqual((code, obj["resumed"]), (0, False), err)


class Fingerprint(unittest.TestCase):
    """The receipts' fingerprint is setup/module-manifest's, byte for byte."""

    def shell(self, d):
        p = subprocess.run(["bash", MANIFEST, "fingerprint", d], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.decode().strip()

    def test_a_module_in_the_checkout(self):
        d = os.path.join(REPO, "rorw")
        self.assertEqual(apply_mod.fingerprint(d), self.shell(d))

    def test_loose_files_outside_a_checkout(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        write(os.path.join(d, "a.sh"), "echo a\n")
        write(os.path.join(d, "sub", "b.txt"), "b\n")
        write(os.path.join(d, "__pycache__", "x.pyc"), "junk")
        self.assertEqual(apply_mod.fingerprint(d), self.shell(d))


class ConvergeCalls(unittest.TestCase):
    """setup/converge.py applies through `hasta apply`, with converge's own semantics."""

    def run_converge(self, failed):
        from unittest import mock
        from setup import converge, detect
        calls = []

        class R(object):
            pass

        def fake(tree, root, **kw):
            calls.append((tree, root, kw))
            r = R()
            r.failed = failed
            return r
        box = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, box)
        manifest = os.path.join(box, "modules.txt")
        open(manifest, "w").close()
        info = {"is_pi": False, "is_x86": True, "arch": "x86_64"}
        argv = ["converge.py", "--platform", "x86", "--manifest", manifest]
        with mock.patch.object(apply_mod, "apply", fake), \
                mock.patch.object(detect, "detect", lambda: info), \
                mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()):
            code = converge.main()   # its plan lines: converge's, unchanged
        return code, calls, manifest

    def test_every_row_runs_and_a_resume_finishes_only_the_rest(self):
        code, calls, manifest = self.run_converge([])
        self.assertEqual(code, 0)
        (tree, root, kw), = calls
        self.assertEqual((tree, root), (REPO, "/"))
        self.assertTrue(kw["force"])
        self.assertFalse(kw["request"]["force"])
        self.assertEqual(kw["receipts_path"], manifest)
        self.assertEqual(kw["platform"], "x86")

    def test_a_failed_module_is_exit_1(self):
        self.assertEqual(self.run_converge(["rorw"])[0], 1)

    def test_plan_still_runs_from_a_lone_copy(self):
        # bench/test-rastaos-update copies converge.py alone into a fake checkout
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        os.makedirs(os.path.join(d, "setup"))
        shutil.copy(os.path.join(REPO, "setup", "converge.py"), os.path.join(d, "setup"))
        p = subprocess.run([sys.executable, os.path.join(d, "setup", "converge.py"), "--plan",
                            "--platform", "pi"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=dict(ENV, PITOOLS_ROOT=d))
        self.assertEqual(p.returncode, 0, p.stderr)
