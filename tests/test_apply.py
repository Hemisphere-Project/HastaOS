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

    def test_to_ref_reads_the_tree_as_committed_and_applies_only_dry(self):
        def git(*a):
            subprocess.run(["git", "-C", self.tree, "-c", "user.name=t", "-c", "user.email=t@t"]
                           + list(a), check=True, stdout=subprocess.DEVNULL)
        git("init", "-q")
        git("add", "-A")
        git("commit", "-qm", "one")
        self.assertEqual(self.apply(FakeRunner(self.box)).failed, [])
        write(os.path.join(self.tree, "beta", "upgrade.sh"), "#!/bin/sh\necho two\n")
        git("commit", "-qam", "two")
        os.remove(os.path.join(self.tree, "beta", "upgrade.sh"))   # the working tree is not read
        args = ("--root", self.box, "--tree", self.tree, "--platform", "pi",
                "--receipts", self.receipts, "--dry-run")
        before = self.snapshot()
        for ref, runs in (("HEAD~1", []), ("HEAD", ["beta"])):
            code, obj, err = self.cli(*(args + ("--to", ref)))
            self.assertEqual(code, 0, err)
            self.assertEqual([s["module"] for s in obj["steps"] if s["outcome"] == "would-run"],
                             runs, ref)
        code, _, err = self.cli("--root", self.box, "--tree", self.tree, "--to", "HEAD")
        self.assertEqual(code, 2)
        self.assertIn("only with --dry-run", err)
        code, _, err = self.cli(*(args + ("--to", "no-such-ref")))
        self.assertEqual(code, 2, err)
        self.assertEqual(self.snapshot(), before)


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


# -- the boot unit that resumes (#t-009 ③) ---------------------------------------------------

ENGINE = os.path.join(REPO, apply_mod.ENGINE_DIR)
UNIT = os.path.join(ENGINE, apply_mod.RESUME_UNIT)


def unit_lines(key):
    with open(UNIT) as f:
        return [ln.split("=", 1)[1] for ln in f.read().splitlines() if ln.startswith(key + "=")]


def lay_down(tree, box):
    """What link_bin, install_service and `systemctl enable` leave, under a temporary root."""
    d = os.path.join(os.path.abspath(tree), apply_mod.ENGINE_DIR)
    units = os.path.join(box, apply_mod.UNIT_DIR.lstrip("/"))
    os.makedirs(os.path.join(box, "usr/local/bin"), exist_ok=True)
    os.makedirs(os.path.join(units, "multi-user.target.wants"), exist_ok=True)
    os.symlink(os.path.join(d, "hasta"), os.path.join(box, apply_mod.ENGINE_LINK.lstrip("/")))
    os.symlink(os.path.join(d, apply_mod.RESUME_UNIT), os.path.join(units, apply_mod.RESUME_UNIT))
    os.symlink("%s/%s" % (apply_mod.UNIT_DIR, apply_mod.RESUME_UNIT),
               os.path.join(units, "multi-user.target.wants", apply_mod.RESUME_UNIT))


class ResumeUnit(unittest.TestCase):

    def test_it_starts_on_either_marker_only(self):
        self.assertEqual(sorted(unit_lines("ConditionPathExists")),
                         sorted("|" + m for m in (apply_mod.MARKER, apply_mod.MARKER_NO_DATA)))
        self.assertEqual(unit_lines("ExecStart"), [apply_mod.ENGINE_LINK + " apply --resume"])
        self.assertEqual(unit_lines("Type"), ["oneshot"])

    def test_its_manifest_declares_what_apply_lays_down(self):
        from hasta import manifest
        m = manifest.load(os.path.join(ENGINE, manifest.MANIFEST_NAME))
        self.assertEqual([str(e) for e in m.errors], [])
        self.assertEqual((m.id, m.data["module"]["layer"]), ("hasta", "core"))
        res = m.data["resources"]
        self.assertEqual([(l["path"], l["target"]) for l in res["links"]],
                         [(apply_mod.ENGINE_LINK, "hasta")])
        self.assertEqual([(u["name"], u["state"], u["wanted_by"], u["source"])
                          for u in res["units"]],
                         [(apply_mod.RESUME_UNIT, "enabled", ["multi-user.target"],
                           apply_mod.RESUME_UNIT)])
        self.assertEqual(unit_lines("WantedBy"), ["multi-user.target"])
        self.assertIn("hasta", dict((x.id, x) for x in manifest.discover(REPO)))

    def test_a_bare_box_gets_all_three_then_nothing(self):
        box = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, box)
        self.assertEqual([v for v, _ in apply_mod.engine_missing(REPO, box)],
                         ["link_bin", "install_service", "enable_service"])
        self.assertEqual(apply_mod.engine_missing(REPO, box)[1][1], UNIT)
        lay_down(REPO, box)
        self.assertEqual(apply_mod.engine_missing(REPO, box), [])

    def test_a_unit_linked_into_another_tree_is_relinked_alone(self):
        box, other = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, box)
        self.addCleanup(shutil.rmtree, other)
        lay_down(REPO, box)
        os.makedirs(os.path.join(other, "bin"))
        open(os.path.join(other, "bin", apply_mod.RESUME_UNIT), "w").close()
        self.assertEqual([v for v, _ in apply_mod.engine_missing(other, box)],
                         ["link_bin", "install_service"])

    def test_a_tree_without_the_unit_lays_nothing(self):
        tree, box = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tree)
        self.addCleanup(shutil.rmtree, box)
        self.assertEqual(apply_mod.engine_missing(tree, box), [])

    def test_prepare_runs_before_the_marker_and_never_on_a_dry_run(self):
        t = Apply("test_cli")
        t.setUp()
        self.addCleanup(t.doCleanups)
        seen = []

        class Runner(FakeRunner):
            def prepare(self):
                seen.append(os.path.exists(apply_mod.marker_path(t.box)))

        t.apply(Runner(t.box), dry_run=True)
        self.assertEqual(seen, [])
        t.apply(Runner(t.box))
        self.assertEqual(seen, [False])

    def test_check_claims_the_unit_once_it_is_there(self):
        from hasta import check
        from tests.test_check import rc_box
        tmp, box = rc_box()
        self.addCleanup(shutil.rmtree, tmp)
        lay_down(REPO, box)
        report = check.make(REPO, box)
        self.assertIn("hasta", report.modules)
        named = [f for f in report.different + report.undeclared if "hasta" in f.path]
        self.assertEqual([f.as_dict() for f in named], [])


class RootBracket(unittest.TestCase):
    """rorw's `rw` / `ro` around a standalone apply, taken as setup/rastaos-update takes it."""

    def setUp(self):
        from unittest import mock
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = os.path.join(self.tmp, "bin")
        self.log = os.path.join(self.tmp, "log")
        self.mounts = os.path.join(self.tmp, "mounts")
        for name in ("rw", "ro"):
            self.tool(name, 0)
        env = {"PATH": self.bin + os.pathsep + os.environ.get("PATH", "")}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("HASTA_NO_RORW", None)

    def tool(self, name, code):
        write(os.path.join(self.bin, name),
              "#!/bin/sh\necho %s >> '%s'\nexit %d\n" % (name, self.log, code))
        os.chmod(os.path.join(self.bin, name), 0o755)

    def root(self, opts):
        write(self.mounts, "proc /proc proc rw 0 0\n/dev/root / ext4 %s 0 0\n" % opts)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return f.read().split()

    def test_a_read_only_root_is_taken_and_given_back(self):
        self.root("ro,noatime")
        b = apply_mod.RootRW(self.mounts)
        self.assertTrue(b.take())
        self.assertEqual(self.calls(), ["rw"])
        self.assertTrue(b.give())
        self.assertTrue(b.give())   # once only
        self.assertEqual(self.calls(), ["rw", "ro"])

    def test_a_writable_root_is_the_callers(self):
        self.root("rw,noatime")
        b = apply_mod.RootRW(self.mounts)
        self.assertTrue(b.take())
        self.assertTrue(b.give())
        self.assertEqual(self.calls(), [])

    def test_the_last_line_for_the_root_is_the_one_in_force(self):
        write(self.mounts, "/dev/root / ext4 ro 0 0\n/dev/root / ext4 rw,relatime 0 0\n")
        self.assertFalse(apply_mod.root_readonly(self.mounts))
        self.root("ro")
        self.assertTrue(apply_mod.root_readonly(self.mounts))
        self.assertFalse(apply_mod.root_readonly(os.path.join(self.tmp, "none")))

    def test_failures_are_said(self):
        self.root("ro")
        self.tool("ro", 1)
        b = apply_mod.RootRW(self.mounts)
        self.assertTrue(b.take())
        self.assertFalse(b.give())
        self.tool("rw", 1)
        b = apply_mod.RootRW(self.mounts)
        self.assertFalse(b.take())
        self.assertTrue(b.give())   # nothing taken, nothing to give back

    def test_no_rorw_or_switched_off_is_no_bracket(self):
        self.root("ro")
        os.environ["HASTA_NO_RORW"] = "1"
        self.assertTrue(apply_mod.RootRW(self.mounts).take())
        del os.environ["HASTA_NO_RORW"]
        os.remove(os.path.join(self.bin, "ro"))
        os.environ["PATH"] = self.bin
        self.assertTrue(apply_mod.RootRW(self.mounts).take())
        self.assertEqual(self.calls(), [])

    def cli(self, take, give):
        """`hasta apply` on the running box, the engine and the bracket stood in for."""
        from unittest import mock
        from hasta import cli
        calls = []

        class Plan(object):
            def as_dict(self):
                return {}

        class Bracket(object):
            def take(self):
                calls.append("rw")
                return take

            def give(self):
                calls.append("ro")
                return give

        def fake(*a, **kw):
            calls.append("apply")
            return apply_mod.Result(Plan(), [], [], False)
        err = io.StringIO()
        with mock.patch.object(apply_mod, "apply", fake), \
                mock.patch.object(apply_mod, "RootRW", Bracket), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cli.main(["apply", "--modules", "alpha"])
        return code, calls, err.getvalue()

    def test_the_verb_brackets_the_running_box(self):
        self.assertEqual(self.cli(True, True)[:2], (0, ["rw", "apply", "ro"]))
        code, calls, err = self.cli(True, False)
        self.assertEqual((code, calls), (1, ["rw", "apply", "ro"]))
        self.assertIn("stayed read-write", err)
        code, calls, err = self.cli(False, True)
        self.assertEqual((code, calls), (1, ["rw"]))
        self.assertIn("nothing ran", err)
