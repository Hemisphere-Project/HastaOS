"""Apply a plan through the legacy driver (`hasta apply`).

The plan is `hasta plan`'s (hasta/plan.py); each row runs by the code path setup/converge.py
takes for it today: `upgrade` runs the module's own `[install] upgrade` script with
`--converging`, `script` and `install` run setup/installer.py's install_module, `linked` runs
nothing (the symlinks already carry the new code), `held`, `skip` and `unrecorded` run nothing.

Receipts: one file per module under /etc/hasta/receipts/, KEY=value lines (MODULE, FINGERPRINT,
VERSION, OPTIONS, ACTION, APPLIED_AT). FINGERPRINT is setup/module-manifest's content hash of
the module directory. A module whose receipt already names its current fingerprint and version
is not run again, and its receipt is not rewritten: a second apply changes nothing. The legacy
receipts (/etc/pi-tools-modules.txt, /etc/rastaos-release) keep their writers.

Resume: while an apply runs, a marker on the data partition (/data/.hasta-apply; on a box with
no /data, /var/lib/hasta/apply-pending) names what it was asked to do. It is removed when every module ends applied. `hasta apply --resume` reruns
what a marker names, and does nothing without one; since every finished module left its
receipt, the rerun runs only what the interrupted one had not finished.

The legacy driver applies to the running box only: --root other than / is refused unless a
runner is given (tests). Standard library only; runs on Python 3.7.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

from hasta import facts as facts_mod
from hasta import plan as plan_mod

RECEIPT_DIR = "/etc/hasta/receipts"
MARKER = "/data/.hasta-apply"
MARKER_NO_DATA = "/var/lib/hasta/apply-pending"
RUNS = ("upgrade", "script", "install")          # the actions that run something
RECEIPTED = RUNS + ("linked",)                    # the actions that leave a receipt
SKIP_DIRS = ("__pycache__", "node_modules", ".venv")
ENGINE_DIR = "bin"                                # the engine's entry point and its unit
ENGINE_LINK = "/usr/local/bin/hasta"
RESUME_UNIT = "hasta-resume.service"
UNIT_DIR = "/etc/systemd/system"


# -- fingerprint: setup/module-manifest's, in Python ----------------------------------------

def _blob_id(path):
    with open(path, "rb") as f:
        data = f.read()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _files(module_dir):
    """The module's files the way module-manifest lists them: git's view inside a checkout
    (tracked + untracked-not-ignored), else every file but caches."""
    p = subprocess.run(["git", "-C", module_dir, "ls-files", "-z", "-co", "--exclude-standard",
                        "--", "."], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if p.returncode == 0:
        return [f for f in p.stdout.decode("utf-8", "surrogateescape").split("\0") if f]
    out = []
    for top, dirs, files in os.walk(module_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not name.endswith(".pyc"):
                out.append(os.path.relpath(os.path.join(top, name), module_dir))
    return out


def fingerprint(module_dir):
    """12 hex of sha1 over the sorted `<blob-id> <path>` lines: module-manifest's identity."""
    lines = []
    for rel in _files(module_dir):
        full = os.path.join(module_dir, rel)
        if os.path.isfile(full):
            lines.append(("%s %s\n" % (_blob_id(full), rel)).encode("utf-8", "surrogateescape"))
    return hashlib.sha1(b"".join(sorted(lines))).hexdigest()[:12]


# -- receipts --------------------------------------------------------------------------------

def read_receipt(path):
    out = {}
    try:
        with open(path) as f:
            for ln in f.read().splitlines():
                if "=" in ln and not ln.startswith("#"):
                    k, v = ln.split("=", 1)
                    out[k.strip()] = v.strip()
    except (IOError, OSError):
        pass
    return out


def _write_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, path)


def receipt_for(m, action):
    model = m.model()
    options = dict((k, v.get("default")) for k, v in sorted((model.get("options") or {}).items())
                   if isinstance(v, dict))
    return {"MODULE": m.name, "FINGERPRINT": fingerprint(m.path),
            "VERSION": model["module"].get("version", "legacy"),
            "OPTIONS": json.dumps(options, sort_keys=True, separators=(",", ":")),
            "ACTION": action}


def current(old, new):
    """A receipt that already says what this apply would write: nothing to run."""
    return all(old.get(k) == new[k] for k in ("FINGERPRINT", "VERSION", "OPTIONS"))


# -- the engine's own pieces -----------------------------------------------------------------

def engine_missing(tree, root="/"):
    """What bin/module.toml declares that the box does not hold yet, as the installer verbs
    that lay it down: [("link_bin", src) | ("install_service", src) | ("enable_service", name)].
    Empty on a box that already holds it: nothing to do, nothing written."""
    d = os.path.join(os.path.abspath(tree), ENGINE_DIR)
    out = []
    if not os.path.isfile(os.path.join(d, RESUME_UNIT)):
        return out  # a tree from before the unit: nothing of it to lay down
    src = os.path.join(d, "hasta")
    if _readlink(facts_mod._join(root, ENGINE_LINK)) != src:
        out.append(("link_bin", src))
    unit = os.path.join(d, RESUME_UNIT)
    if _readlink(facts_mod._join(root, "%s/%s" % (UNIT_DIR, RESUME_UNIT))) != unit:
        out.append(("install_service", unit))
    wants = "%s/multi-user.target.wants/%s" % (UNIT_DIR, RESUME_UNIT)
    if not os.path.lexists(facts_mod._join(root, wants)):
        out.append(("enable_service", RESUME_UNIT))
    return out


def _readlink(path):
    try:
        return os.readlink(path)
    except OSError:
        return None


# -- the read-only root ----------------------------------------------------------------------

def root_readonly(mounts="/proc/self/mounts"):
    """True when / is mounted read-only (the last / line of the table is the one in force)."""
    ro = False
    try:
        with open(mounts) as f:
            for ln in f:
                parts = ln.split()
                if len(parts) >= 4 and parts[1] == "/":
                    ro = "ro" in parts[3].split(",")
    except (IOError, OSError):
        return False
    return ro


class RootRW(object):
    """rorw's bracket around a standalone apply, taken the way setup/rastaos-update takes it: on
    a read-only root, `rw` for the run and `ro` after (rorw counts holders). A writable root is
    left alone — a caller holds it (rastaos-update, a shell after `rw`) or the box is not rorw —
    and `ro` is never run for a `rw` this apply did not take: on a writable root with rorw's
    count at 0 it would remount read-only under the caller. HASTA_NO_RORW=1 skips the bracket."""

    def __init__(self, mounts="/proc/self/mounts"):
        self.mounts, self.took = mounts, False

    def take(self):
        """False when the root is read-only and `rw` could not remount it."""
        if (os.environ.get("HASTA_NO_RORW") or not shutil.which("rw")
                or not shutil.which("ro") or not root_readonly(self.mounts)):
            return True
        if subprocess.run(["rw"], stdout=subprocess.DEVNULL).returncode != 0:
            return False
        self.took = True
        return True

    def give(self):
        """False when `ro` could not remount the root (something still holds it)."""
        if not self.took:
            return True
        self.took = False
        return subprocess.run(["ro"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0


# -- runners ---------------------------------------------------------------------------------

class LegacyRunner(object):
    """Runs a row the way setup/converge.py does, on the running box."""

    def __init__(self, tree):
        self.tree = tree
        self._installer = None
        self._reload = None       # setup.utils, when prepare() relinked the unit

    def _path(self):
        if self.tree not in sys.path:
            sys.path.insert(0, self.tree)

    def _setup(self):
        if self._installer is None:
            self._path()
            os.environ["DEBIAN_FRONTEND"] = "noninteractive"
            from setup import config, detect, installer, utils
            platinfo = detect.detect()
            self._installer = (installer, platinfo, config.load(detect.find_config(platinfo)),
                               utils)
        return self._installer

    def run(self, row):
        m = row.module
        if row.action == "upgrade":
            cmd = ["bash", os.path.join(m.path, m.upgrade), "--converging"]
            return subprocess.run(cmd, cwd=m.path).returncode == 0
        installer, platinfo, cfg, _ = self._setup()
        return bool(installer.install_module(m.name, platinfo, cfg))

    def prepare(self):
        """Before the first row: `hasta` on the PATH and the resume unit enabled, by the
        installer's own verbs, each only where the box differs (a second apply writes nothing)."""
        todo = engine_missing(self.tree)
        if not todo:
            return
        self._path()
        from setup import utils
        for verb, arg in todo:
            getattr(utils, verb)(arg)
        if any(verb == "install_service" for verb, _ in todo):
            self._reload = utils

    def finish(self):
        if self._installer is not None:
            self._installer[3].daemon_reload()
        elif self._reload is not None:
            self._reload.daemon_reload()


# -- apply -----------------------------------------------------------------------------------

class Result(object):
    def __init__(self, plan, steps, failed, resumed):
        self.plan, self.steps, self.failed, self.resumed = plan, steps, failed, resumed

    def as_dict(self):
        out = self.plan.as_dict()
        out.update({"steps": [dict(s) for s in self.steps], "failed": list(self.failed),
                    "resumed": self.resumed})
        return out


def marker_path(root):
    """/data/.hasta-apply; a box with no /data directory (an x86 box with no data partition)
    keeps it at /var/lib/hasta/apply-pending rather than make /data on its root."""
    if os.path.isdir(facts_mod._join(root, os.path.dirname(MARKER))):
        return facts_mod._join(root, MARKER)
    return facts_mod._join(root, MARKER_NO_DATA)


def apply(tree, root="/", receipts_path=None, select=None, platform=None, apps=(),
          dry_run=False, force=False, runner=None, now=None, resumed=False, request=None):
    """Run the plan. Returns a Result; `steps` holds one dict per row: module, action, why,
    outcome (ran | failed | current | recorded | none | would-run | would-record)."""
    if runner is None and os.path.realpath(root) != "/" and not dry_run:
        raise ValueError("the legacy driver applies to the running box only (--root %s):"
                         " run it inside the chroot" % root)
    p = plan_mod.make(tree, root, receipts_path=receipts_path, select=select,
                      platform=platform, apps=apps)
    if p.platform is None:
        raise ValueError("unknown platform (machine %s): pass --platform"
                         % p.facts.values.get("machine"))
    stamp = now or time.strftime("%Y-%m-%dT%H:%M:%S%z")
    rdir = facts_mod._join(root, RECEIPT_DIR)
    marker = marker_path(root)
    runner = runner or LegacyRunner(tree)

    if not dry_run:
        prepare = getattr(runner, "prepare", None)
        if prepare:
            prepare()
        req = dict(request or {}, started_at=stamp)
        _write_atomic(marker, json.dumps(req, sort_keys=True) + "\n")

    steps, failed = [], []
    for row in p.rows:
        step = {"module": row.name, "action": row.action, "why": row.why}
        steps.append(step)
        if row.action not in RECEIPTED:
            step["outcome"] = "none"
            continue
        path = os.path.join(rdir, row.name)
        new = receipt_for(row.module, row.action)
        if current(read_receipt(path), new) and not force:
            step["outcome"] = "current"
            continue
        if dry_run:
            step["outcome"] = "would-run" if row.action in RUNS else "would-record"
            continue
        if row.action in RUNS:
            if not runner.run(row):
                step["outcome"] = "failed"
                failed.append(row.name)
                continue
            step["outcome"] = "ran"
        else:
            step["outcome"] = "recorded"
        new["APPLIED_AT"] = stamp
        _write_atomic(path, "".join("%s=%s\n" % (k, new[k]) for k in
                                    ("MODULE", "FINGERPRINT", "VERSION", "OPTIONS", "ACTION",
                                     "APPLIED_AT")))
    if not dry_run:
        finish = getattr(runner, "finish", None)
        if finish:
            finish()
        if not failed:
            os.remove(marker)
    return Result(p, steps, failed, resumed)


def pending(root="/"):
    """The request an interrupted apply left on the data partition, or None."""
    try:
        with open(marker_path(root)) as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return None
