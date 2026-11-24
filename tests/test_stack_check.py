"""The proof of #t-010: from the same 7.4-shaped box, `hasta apply --stack rasta/7.5` and
`rastaos-bake --root` leave identical trees, `check` on the result is clean, and `hasta seal`
seals it with no waiver.

The box (box74 below) is a 7.4 card as a bake meets it, built from what is known of one:

* /etc/systemd/system is the 7.5 rc's (tests/fixtures/listings/), less the drift its note names
  (EXPECTED_DRIFT: field packs, Anna's 7.4 units, stale links beside masks) and less the app's
  entries (OUTSIDE: HPlayer2 carries its own manifest), and less the four masks the bake's
  `units` step made; a drop-in the listing gives by its size is written from the manifest that
  declares it, and the sizes must agree;
* every module on that box holds its declared state at this tree, with a receipt of this tree's
  fingerprint: the box was converged at this tree, as a bake's `converge` leaves it;
* then card74's defects (tests/test_stack.py), every one a step removes: the wants of the units
  the bake masks, fake-hwclock's hourly save, the RTC and turbo lines, apfix wired, the
  resolvconf link, no scan-MAC setting, the hotspot PSK, the old firmware, the media, the
  starter and the name.

Before the steps, `check` names those defects; after them it names nothing."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from hasta import check, legacy, stack
from tests.helpers import REPO
from tests.test_check import rc_box
from tests.test_cli import run
from tests.test_declared import EXPECTED_DRIFT, OUTSIDE, listing
from tests.test_stack import REFERENCE, STEPS, WRAPPER, card74, tree

UNITS = check.UNIT_DIR.lstrip("/")
BAKE_MASKS = ("fake-hwclock.service", "rpi-eeprom-update.service", "nfs-client.target",
              "rsync.service")
# what `check` reads on box74 before the steps: card74's defects, each with the step that
# removes it. The other defects of card74 (VERSION, stamps, the PSK, the media, ...) are the
# `undeclared` list of the overlay: no form of schema 1 holds them, so check cannot see them.
U = check.UNIT_DIR + "/"
BEFORE = [
    ("/boot/config.txt", "'dtoverlay=i2c-rtc,ds3231' present, declared absent"),  # rtc
    ("/boot/config.txt", "'force_turbo=1' present, declared absent"),             # turbo
    (U + "fake-hwclock.service", "not masked, declared masked"),                  # units
    (U + "rpi-eeprom-update.service", "not masked, declared masked"),             # units
    (U + "rsync.service", "not masked, declared masked"),                         # units
    (U + "apfix.timer", "enabled under timers.target, declared disabled"),        # apfix
    ("/etc/resolv.conf", "a link, declared a copy"),                              # dns
    ("/etc/NetworkManager/conf.d/91-rastaos-scan.conf", "missing"),               # wifi
]
# ... and the wants of the units `units` masks, which no manifest enables (undeclared)
WANTS = "enabled, and no manifest enables it there in this form; declared by rasta-7.5 (masked)"
BEFORE_UNDECLARED = [
    (U + "multi-user.target.wants/rpi-eeprom-update.service", WANTS),
    (U + "multi-user.target.wants/rsync.service", WANTS),
    (U + "sysinit.target.wants/fake-hwclock.service", WANTS),
]


def _write(p, data, mode=None):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.lexists(p):
        os.remove(p)
    with open(p, "wb") as f:
        f.write(data)
    if mode is not None:
        os.chmod(p, mode)


def _link(p, target):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.lexists(p):
        os.remove(p)
    os.symlink(target, p)


def _on_box(decl, rel):
    """Where a module's file sits on a box: under /opt/Pi-tools, as the tree is cloned there."""
    return "/" + os.path.normpath(os.path.join("opt/Pi-tools", os.path.relpath(decl.path, REPO),
                                               rel))


def lay_down(box, decl):
    """One module's declared state at this tree: its units, files, links and fstab lines."""
    r = lambda p: os.path.join(box, p.lstrip("/"))
    for u in decl.units:
        p = r("%s/%s" % (check.UNIT_DIR, u["name"]))
        if u["state"] == "masked":
            _link(p, "/dev/null")
            continue
        if "source" in u:
            if u.get("copy"):
                with open(os.path.join(decl.path, u["source"]), "rb") as f:
                    _write(p, f.read(), 0o644)
            else:
                _link(p, _on_box(decl, u["source"]))
        if u["state"] == "enabled":
            for t in u.get("wanted_by") or []:
                _link(r("%s/%s.wants/%s" % (check.UNIT_DIR, t, u["name"])),
                      "%s/%s" % (check.UNIT_DIR, u["name"]))
    for f in decl.files:
        if f.get("copy"):
            with open(os.path.join(decl.path, f["source"]), "rb") as fh:
                _write(r(f["path"]), fh.read(), int(f.get("mode", "0644"), 8))
        else:
            _link(r(f["path"]), _on_box(decl, f["source"]))
    for l in decl.links:
        t = l["target"]
        _link(r(l["path"]), t if t.startswith("/") else _on_box(decl, t))
    for l in decl.lines:
        if l["file"] != "fstab" or not l.get("present", True):
            continue
        p = r("/etc/fstab")
        have = ""
        if os.path.isfile(p):
            with open(p) as fh:
                have = fh.read()
        if not any(ln.split() == l["line"].split() for ln in have.splitlines()):
            _write(p, (have + l["line"] + "\n").encode())


def box74(base):
    """The 7.4-shaped box of the module docstring, at `base` (its parent is scratch space)."""
    tmp, rc = rc_box()
    try:
        shutil.copytree(rc, base, symlinks=True)
    finally:
        shutil.rmtree(tmp)
    units = os.path.join(base, UNITS)
    for rel in list(EXPECTED_DRIFT) + list(OUTSIDE) + list(BAKE_MASKS):
        p = os.path.join(units, rel)
        if os.path.lexists(p):
            os.remove(p)
    facts_box = check.make(REPO, base)
    decls, overlay, _ = check.declarations(REPO, facts_box.facts)
    # the listing's drop-ins and copies, their bytes from the manifest that declares them
    declared = dict((f["path"], d) for d in list(decls.values()) + [overlay] for f in d.files)
    for rel, target, size in listing():
        path = "%s/%s" % (check.UNIT_DIR, rel)
        if target is None and path in declared and rel not in EXPECTED_DRIFT:
            d = declared[path]
            f = [x for x in d.files if x["path"] == path][0]
            with open(os.path.join(d.path, f["source"]), "rb") as fh:
                data = fh.read()
            if len(data) != size:
                raise AssertionError("%s: %d B in the listing, %d B declared by %s"
                                     % (path, size, len(data), d.name))
            _write(os.path.join(units, rel), data, int(f.get("mode", "0644"), 8))
    for n in facts_box.modules:
        lay_down(base, decls[n])
    mods = legacy.discover(REPO)
    with open(os.path.join(base, "etc", "pi-tools-modules.txt"), "w") as f:
        for n in sorted(set(facts_box.modules) & set(mods)):
            f.write("%s %s 2026-11-24T00:00:00+01:00 -\n" % (n, check.fingerprint(mods[n].path)))
    # card74 lays its defects over the box: what it writes replaces what the box had there
    scratch = os.path.join(os.path.dirname(base), "card74")
    os.makedirs(scratch)
    card74(scratch)
    for rel, (kind, _, _) in tree(scratch).items():
        p = os.path.join(base, rel)
        if kind != "dir" and os.path.lexists(p) and not os.path.isdir(p):
            os.remove(p)
    shutil.rmtree(scratch)
    card74(base)
    return sorted(facts_box.modules)


@unittest.skipUnless(shutil.which("bash") and shutil.which("git") and shutil.which("ssh-keygen"),
                     "needs bash, git and ssh-keygen")
class TheBakeOnA74Box(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="hasta-stack-check-")
        cls.box = os.path.join(cls.tmp, "box")
        cls.modules = box74(cls.box)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def copy(self, name):
        d = os.path.join(self.tmp, name)
        if os.path.exists(d):
            shutil.rmtree(d)
        shutil.copytree(self.box, d, symlinks=True)
        return d

    def bake(self, d, script):
        env = dict(os.environ, BAKE_HOME=os.path.join(REPO, "image"))
        return subprocess.run(["bash", script, "--root", d, "--only", STEPS], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True)

    def test_the_box_is_a_rasta_box_with_its_modules(self):
        for m in ("rorw", "network-tools", "audiohub", "usbfix", "extendfs", "hostrename"):
            self.assertIn(m, self.modules)

    def test_before_the_steps_check_names_the_7_4_defects(self):
        r = check.make(REPO, self.box)
        self.assertEqual([f for f in r.receipts if f.kind != "same"], [])
        self.assertEqual(sorted((f.path, f.what) for f in r.different), sorted(BEFORE))
        self.assertEqual(sorted((f.path, f.what) for f in r.undeclared),
                         sorted(BEFORE_UNDECLARED))
        self.assertFalse(r.clean)

    def test_apply_and_the_bake_leave_identical_trees_and_check_is_clean(self):
        a, ref, wrap = self.copy("apply"), self.copy("reference"), self.copy("wrapper")
        code, out, err = run("apply", "--root", a, "--stack", "rasta/7.5")
        self.assertEqual(code, 0, out + err)
        for d, script in ((ref, REFERENCE), (wrap, WRAPPER)):
            p = self.bake(d, script)
            self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(tree(a), tree(ref))
        self.assertEqual(tree(a), tree(wrap))
        r = check.make(REPO, a)
        self.assertEqual((r.different, r.undeclared), ([], []), [
            (f.path, f.what) for f in r.different + r.undeclared])
        self.assertTrue(r.clean)
        self.assertEqual(r.stack, "rasta-7.5")
        code, out, err = run("check", "--root", a)
        self.assertEqual(code, 0, out + err)

    def test_the_result_seals_with_no_waiver(self):
        b = self.copy("seal")
        self.assertEqual(stack.run(REPO, "rasta/7.5", root=b).failed, [])
        keys = os.path.join(self.tmp, "keys")
        os.makedirs(keys, exist_ok=True)
        key = os.path.join(keys, "keep")
        if not os.path.exists(key):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "keep", "-f",
                            key], check=True)
        fp = subprocess.run(["ssh-keygen", "-lf", key + ".pub"], stdout=subprocess.PIPE,
                            check=True).stdout.decode().split()[1]
        keep = os.path.join(keys, "keys.keep")
        with open(keep, "w") as f:
            f.write("%s keep\n" % fp)
        with open(key + ".pub", "rb") as f:
            _write(os.path.join(b, "root/.ssh/authorized_keys"), f.read())
        _write(os.path.join(b, "usr/bin/growpart"), b"#!/bin/sh\n", 0o755)
        code, out, err = run("--json", "seal", "--root", b, "--keys", keep)
        self.assertEqual(code, 0, out + err)
        r = json.loads(out)
        self.assertTrue(r["sealed"])
        self.assertEqual(r["differences"], [])
        with open(os.path.join(b, "etc/hasta/seal")) as f:
            self.assertIn("CHECK=clean\nWAIVED=0\n", f.read())
        self.assertFalse(os.path.exists(os.path.join(b, "etc/hasta/seal.waived")))


if __name__ == "__main__":
    unittest.main()
