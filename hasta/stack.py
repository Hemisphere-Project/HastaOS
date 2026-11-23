"""A stack overlay's steps, run on a box or a mounted card (`hasta apply --stack`, #t-010).

A stack directory (`stacks/<name>/<revision>/`) declares in module.toml what the stack leaves on a
box, and `hasta check` holds a box to it. What schema 1 cannot declare yet, the overlay's
`undeclared` list, is done by the stack's own steps.py: an ordered STEPS tuple and one
`step_<name>(c)` per step, ported from the recipe that did it by hand (Rasta 7.5:
image/rastaos-bake). This module loads them and runs them in order, the way the recipe did:
every step runs even after one failed, each says « already done » when it changed nothing, and a
second run changes nothing.

Unlike the legacy driver, the steps work on a mounted card (--root, --boot, --data): they touch
files, and touch a running system (systemctl, dpkg) only on the live root.

Standard library only; runs on Python 3.7.
"""

import importlib.util
import os
import subprocess

from hasta import facts as facts_mod

STEPS_FILE = "steps.py"


def stack_dir(tree, name):
    """`rasta/7.5` → tree/stacks/rasta/7.5, when it holds steps; else ValueError."""
    parts = [p for p in (name or "").split("/") if p]
    if len(parts) != 2 or any(p in (".", "..") for p in parts):
        raise ValueError("--stack %s: name/revision, e.g. rasta/7.5" % name)
    d = os.path.join(tree, "stacks", parts[0], parts[1])
    if not os.path.isfile(os.path.join(d, STEPS_FILE)):
        raise ValueError("--stack %s: no %s in %s" % (name, STEPS_FILE, d))
    return d


def load(d):
    spec = importlib.util.spec_from_file_location(
        "hasta_stack_" + "_".join(d.split(os.sep)[-2:]).replace(".", "_"),
        os.path.join(d, STEPS_FILE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def recipe_rev(tree):
    """The sha the stamps name: this tree's, as the bake named its own."""
    p = subprocess.run(["git", "-C", tree, "rev-parse", "--short", "HEAD"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return p.stdout.decode().strip() if p.returncode == 0 and p.stdout.strip() else "unknown"


class Context(object):
    """What a step sees. Paths: r() inside the root; boot and data where they are mounted."""

    def __init__(self, root, boot, data, live, rev, opts, today, out):
        self.root, self.boot, self.data, self.live = root, boot, data, live
        self.rev, self.opts, self.today, self._out = rev, dict(opts or {}), today, out
        self.changes = []

    @property
    def changed(self):
        return len(self.changes)

    def r(self, path):
        return facts_mod._join(self.root, path)

    def show(self, host_path):
        """A host path as the box names it, for the messages."""
        for base, point in ((self.boot, "/boot"), (self.data, "/data"), (self.root, "")):
            rel = os.path.relpath(host_path, base)
            if not rel.startswith(".."):
                return (point + "/" + rel) if rel != "." else (point or "/")
        return host_path

    def did(self, msg):
        self.changes.append(msg)
        self._out("  " + msg)

    def say(self, msg):
        self._out(msg)


class Result(object):
    def __init__(self, stack, steps, failed, changes):
        self.stack, self.steps, self.failed, self.changes = stack, steps, failed, changes

    def as_dict(self):
        return {"stack": self.stack, "steps": [dict(s) for s in self.steps],
                "failed": list(self.failed), "changes": list(self.changes)}


def run(tree, name, root="/", boot=None, data=None, only=None, opts=None, today=None,
        out=None):
    """Run the stack's steps (all of them, or `only`) on `root`. Returns a Result; each step
    dict holds step, outcome (changed | done | failed), changes (the count)."""
    d = stack_dir(tree, name)
    mod = load(d)
    known = tuple(mod.STEPS)
    if only:
        bad = [s for s in only if s not in known]
        if bad:
            raise ValueError("unknown step: %s (the stack's: %s)" % (", ".join(bad),
                                                                     " ".join(known)))
    live = os.path.realpath(root) == "/"
    boot = boot or (("/boot" if live else os.path.join(root, "boot")))
    data = data or (("/data" if live else os.path.join(root, "data")))
    lines = []
    out = out or lines.append
    c = Context(root, boot, data, live, recipe_rev(tree), opts, today or mod.today(), out)
    steps, failed = [], []
    for s in known:
        if only and s not in only:
            continue
        before = c.changed
        try:
            ok = getattr(mod, "step_" + s)(c)
        except (IOError, OSError) as exc:
            c.say("%s: %s" % (s, exc))
            ok = False
        if not ok:
            c.say("step %s FAILED" % s)
            failed.append(s)
            outcome = "failed"
        elif c.changed == before:
            c.say("%s: already done" % s)
            outcome = "done"
        else:
            outcome = "changed"
        steps.append({"step": s, "outcome": outcome, "changes": c.changed - before})
    return Result(name, steps, failed, c.changes)
