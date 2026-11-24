"""bench/sync-upstream.sh (#t-012): Pi-tools `2026` merged into `main` — a plain merge, never a
rebase; a conflict stops it and names the files; the gate runs in the merged tree before it
reports done; it never pushes.

Each test builds three small repos in a temporary directory: `up` (Pi-tools, branch 2026),
`origin.git` (HastaOS's remote, seeded from it) and `hasta` (a clone of that remote, `main`
checked out), and runs the real script with `-C hasta`, SYNC_UPSTREAM pointing at `up` and
SYNC_VERIFY standing in for bench/verify (which would run this suite again)."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

from tests.helpers import REPO

SCRIPT = os.path.join(REPO, "bench", "sync-upstream.sh")


def git_version():
    out = subprocess.run(["git", "--version"], stdout=subprocess.PIPE,
                         universal_newlines=True).stdout
    m = re.search(r"(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


@unittest.skipIf(shutil.which("git") is None, "no git")
class SyncUpstream(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hasta-sync-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = dict(os.environ, HOME=self.tmp, XDG_CONFIG_HOME=self.tmp,
                        GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@localhost",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@localhost")
        self.up = self.path("up")
        self.origin = self.path("origin.git")
        self.hasta = self.path("hasta")
        self.git(self.tmp, "-c", "init.defaultBranch=2026", "init", "-q", "up")
        self.commit(self.up, "seed", {"a.txt": "a\n", "shared.txt": "one\ntwo\nthree\n"})
        self.git(self.tmp, "clone", "-q", "--bare", "up", "origin.git")
        self.git(self.origin, "branch", "main", "2026")
        self.git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        self.git(self.tmp, "clone", "-q", "origin.git", "hasta")

    # ── helpers ──────────────────────────────────────────────────────────

    def path(self, *p):
        return os.path.join(self.tmp, *p)

    def git(self, where, *args):
        r = subprocess.run(["git", "-C", where] + list(args), env=self.env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True)
        self.assertEqual(r.returncode, 0, "git %s: %s" % (" ".join(args), r.stdout))
        return r.stdout.strip()

    def commit(self, where, msg, files):
        for name, text in files.items():
            with open(os.path.join(where, name), "w") as f:
                f.write(text)
        self.git(where, "add", "-A")
        self.git(where, "commit", "-q", "-m", msg)
        return self.git(where, "rev-parse", "HEAD")

    def main_moves(self, files):
        sha = self.commit(self.hasta, "hasta work", files)
        self.git(self.hasta, "push", "-q", "origin", "main")
        return sha

    def sync(self, *args, verify="true"):
        env = dict(self.env, SYNC_UPSTREAM=self.up, SYNC_VERIFY=verify)
        r = subprocess.run(["bash", SCRIPT, "-C", self.hasta] + list(args), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True)
        return r.returncode, r.stdout

    def ref(self, where, name):
        r = subprocess.run(["git", "-C", where, "rev-parse", "-q", "--verify", name],
                           env=self.env, stdout=subprocess.PIPE, universal_newlines=True)
        return r.stdout.strip() if r.returncode == 0 else None

    def worktree(self, sha):
        return self.path("hasta", ".git", "hasta-sync", "main-" + sha[:7])

    def land(self, out):
        """Paste what the script printed under « To land », line by line."""
        lines = out.split("To land", 1)[1].splitlines()[1:]
        cmds = [l.strip() for l in lines if l.startswith("  git ")]
        self.assertTrue(cmds, out)
        for c in cmds:
            r = subprocess.run(["bash", "-c", c], env=self.env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True)
            self.assertEqual(r.returncode, 0, "%s: %s" % (c, r.stdout))

    # ── the cases ────────────────────────────────────────────────────────

    def test_up_to_date(self):
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        self.assertIn("main: up to date", out)
        self.assertFalse(os.path.exists(self.path("hasta", ".git", "hasta-sync")))

    def test_clean_merge_is_green_lands_by_paste_then_up_to_date(self):
        up = self.commit(self.up, "a 7.x fix", {"a.txt": "a fixed\n"})
        om = self.main_moves({"b.txt": "hasta only\n"})
        rc, out = self.sync("-r", "hasta#t-099",
                            verify="grep -q fixed a.txt && test -f b.txt && echo GATE-RAN")
        self.assertEqual(rc, 0, out)
        self.assertIn("lacks 1 commit(s)", out)
        # the gate ran, in the merged tree: its own output line, not the script's echo of it
        self.assertIn("GATE-RAN", out.splitlines())
        self.assertIn("merged and green", out)
        sb = "sync/main-" + up[:7]
        tip = self.ref(self.hasta, sb)
        self.assertIsNotNone(tip, out)
        # a plain merge: two parents, origin/main first, upstream second — nothing rebased
        self.assertEqual(self.git(self.hasta, "rev-list", "--parents", "-n", "1", tip).split()[1:],
                         [om, up])
        body = self.git(self.hasta, "log", "-1", "--format=%B", tip)
        self.assertIn("Refs-37: hasta#t-099 [done]", body)
        # it never pushed and never moved main
        self.assertEqual(self.ref(self.origin, "refs/heads/main"), om)
        self.assertEqual(self.ref(self.hasta, "refs/heads/main"), om)
        self.land(out)
        self.assertEqual(self.ref(self.origin, "refs/heads/main"), tip)
        self.assertEqual(self.ref(self.hasta, "refs/heads/main"), tip)
        self.assertFalse(os.path.exists(self.worktree(up)))
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        self.assertIn("main: up to date", out)

    def test_dry_run_reports_and_moves_nothing(self):
        up = self.commit(self.up, "a 7.x fix", {"a.txt": "a fixed\n"})
        rc, out = self.sync("-n")
        self.assertEqual(rc, 0, out)
        self.assertIn("lacks 1 commit(s)", out)
        self.assertIn("a 7.x fix", out)
        if git_version() >= (2, 38):
            self.assertIn("merge: would be clean", out)
        self.assertIsNone(self.ref(self.hasta, "refs/heads/sync/main-" + up[:7]))
        self.assertFalse(os.path.exists(self.worktree(up)))

    def test_conflict_stops_names_files_then_resumes(self):
        up = self.commit(self.up, "upstream edit", {"shared.txt": "one\nTWO upstream\nthree\n"})
        om = self.main_moves({"shared.txt": "one\nTWO hasta\nthree\n"})
        if git_version() >= (2, 38):
            rc, out = self.sync("-n")
            self.assertIn("would CONFLICT", out)
            self.assertIn("    shared.txt", out)
        rc, out = self.sync(verify="echo GATE-RAN")
        self.assertEqual(rc, 1, out)
        self.assertIn("CONFLICT", out)
        self.assertIn("    shared.txt", out)
        self.assertNotIn("GATE-RAN", out.splitlines())
        self.assertNotIn("To land", out)
        wt = self.worktree(up)
        self.assertIsNotNone(self.ref(wt, "MERGE_HEAD"))
        # run again before resolving: still stopped, still named
        rc, out = self.sync()
        self.assertEqual(rc, 1, out)
        self.assertIn("    shared.txt", out)
        # a person resolves and commits there; the next run picks up from that commit
        with open(os.path.join(wt, "shared.txt"), "w") as f:
            f.write("one\nTWO both\nthree\n")
        self.git(wt, "add", "shared.txt")
        self.git(wt, "commit", "-q", "--no-edit")
        resolved = self.git(wt, "rev-parse", "HEAD")
        rc, out = self.sync(verify="grep -q 'TWO both' shared.txt && echo GATE-RAN")
        self.assertEqual(rc, 0, out)
        self.assertIn("picking up the worktree", out)
        self.assertIn("GATE-RAN", out.splitlines())
        self.assertEqual(self.ref(self.hasta, "refs/heads/sync/main-" + up[:7]), resolved)
        self.assertEqual(self.ref(self.origin, "refs/heads/main"), om)

    def test_main_moving_after_the_merge_is_merged_in_on_the_next_run(self):
        up = self.commit(self.up, "a 7.x fix", {"a.txt": "a fixed\n"})
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        om = self.main_moves({"c.txt": "landed meanwhile\n"})
        rc, out = self.sync(verify="test -f c.txt && grep -q fixed a.txt")
        self.assertEqual(rc, 0, out)
        tip = self.ref(self.hasta, "refs/heads/sync/main-" + up[:7])
        for sha in (om, up):
            self.git(self.hasta, "merge-base", "--is-ancestor", sha, tip)
        self.land(out)
        self.assertEqual(self.ref(self.origin, "refs/heads/main"), tip)

    def test_red_gate_stops_and_lands_nothing(self):
        up = self.commit(self.up, "a 7.x fix", {"a.txt": "a fixed\n"})
        rc, out = self.sync(verify="echo GATE-RED; false")
        self.assertEqual(rc, 1, out)
        self.assertIn("GATE-RED", out.splitlines())
        self.assertIn("verify RED", out)
        self.assertNotIn("To land", out)
        self.assertTrue(os.path.isdir(self.worktree(up)))

    def test_unpushed_local_main_stops_it(self):
        self.commit(self.up, "a 7.x fix", {"a.txt": "a fixed\n"})
        self.commit(self.hasta, "not pushed", {"d.txt": "local\n"})
        rc, out = self.sync()
        self.assertEqual(rc, 2, out)
        self.assertIn("local main has 1 commit(s) origin/main lacks", out)

    def test_bad_refs_argument_is_refused(self):
        rc, out = self.sync("-r", "t-099")
        self.assertEqual(rc, 2, out)
        self.assertIn("-r wants", out)


if __name__ == "__main__":
    unittest.main()
