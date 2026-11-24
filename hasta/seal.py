"""`hasta seal`: the image's clone-bound state reset, refused unless `check` is clean (#t-010).

Principle 2 of docs/DESIGN.md: a box cannot be captured as an image while `check` shows state no
module declares. So the seal first runs `hasta check` on the mounted image, against the tree this
engine runs from, and refuses while it reports anything: a difference, an undeclared entry, a
receipt off. There is no silent override: `--waive FILE` names each accepted difference, one per
line, by the key the refusal lists (the path `check` names), and the sealed image records the file.
A waiver that matches nothing is refused too, so the record says only what was accepted.

A box whose release is a git checkout (/opt/Pi-tools on Rasta) is refused the same way until its
tree is clean and committed: every path `git status` lists there is one more difference, waivable
by the same file. Live edits on a bench box are a diff to commit, never a sealed image.

What `check` reports on every captured image and the stack's seal itself sets right (its
SEAL_SETS: Rasta's bench card keeps extendfs unwired, the seal wires it) needs no waiver.

Then the box's stack does the reset (Rasta 7.5: stacks/rasta/7.5/steps.py `seal`, the bake's own
step), and /etc/hasta/seal records what was sealed: the stack, the engine's tree, the check, and
the waivers (/etc/hasta/seal.waived, the file as given). Offline only, as the bake's seal was: it
strips the rig's key and resets the box at its next boot, so the running box is refused.

Standard library only; runs on Python 3.7.
"""

import os
import subprocess

from hasta import check as check_mod
from hasta import facts as facts_mod
from hasta import stack as stack_mod

CHECKOUT = "/opt/Pi-tools"
RECORD = "/etc/hasta/seal"
WAIVED = "/etc/hasta/seal.waived"
KEEP_LIST = os.path.join("image", "authorized-keys.keep")


class Difference(object):
    """One thing `check` (or the checkout) reports, by the key a waiver names it with."""

    def __init__(self, part, key, what, who=None):
        self.part, self.key, self.what, self.who = part, key, what, who
        self.waived = None  # the waiver's line, when one names it

    def line(self):
        return "%-11s %s%s: %s" % (self.part, (self.who + " ") if self.who else "", self.key,
                                   self.what)

    def as_dict(self):
        out = {"part": self.part, "key": self.key, "what": self.what,
               "waived": self.waived is not None}
        if self.who:
            out["who"] = self.who
        return out


def read_waivers(path):
    """({key: line}, the file's text) from a waive file: one difference per line, its key as the
    refusal lists it, then ` # reason` if any; blank lines and `#` lines are comments.
    ValueError when it cannot be read."""
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", "surrogateescape")
    except (IOError, OSError) as exc:
        raise ValueError("--waive %s: %s" % (path, exc.strerror or exc))
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key = line.split(" #", 1)[0].strip()
        if key:
            out[key] = line
    return out, text


def checkout_differences(root):
    """The box's release checkout, when it is one: each path `git status` lists. None when the
    release is no git checkout."""
    co = facts_mod._join(root, CHECKOUT)
    if not os.path.exists(os.path.join(co, ".git")):
        return None
    try:
        p = subprocess.run(["git", "-c", "safe.directory=*", "-C", co, "status", "--porcelain",
                            "--untracked-files=normal"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
    except OSError as exc:
        return [Difference("checkout", CHECKOUT, "git status: %s" % exc)]
    if p.returncode != 0:
        why = p.stderr.decode("utf-8", "replace").strip().split("\n")[-1]
        return [Difference("checkout", CHECKOUT, "git status failed: %s" % why)]
    out = []
    for ln in p.stdout.decode("utf-8", "surrogateescape").splitlines():
        if len(ln) > 3:
            rel = ln[3:].split(" -> ")[-1].strip('"')
            out.append(Difference("checkout", "%s/%s" % (CHECKOUT, rel),
                                  "untracked" if ln[:2] == "??" else "not committed (%s)"
                                  % ln[:2].strip()))
    return out


def differences(report):
    out = []
    for f in report.different:
        out.append(Difference("different", f.path, f.what, f.module))
    for f in report.undeclared:
        out.append(Difference("undeclared", f.path, f.what, f.kind))
    for f in report.receipts:
        if f.kind != "same":
            out.append(Difference("receipt", f.path, f.what, f.kind))
    return out


def engine_rev(tree):
    p = subprocess.run(["git", "-C", tree, "describe", "--always", "--dirty", "--abbrev=7"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return p.stdout.decode().strip() if p.returncode == 0 and p.stdout.strip() else "unknown"


class Result(object):
    def __init__(self, root, stack):
        self.root, self.stack = root, stack
        self.differences, self.unused, self.problems = [], [], []
        self.sets = []            # what check reports and the stack's seal sets right itself
        self.checkout = None      # None: the release is no checkout; else the checkout's state
        self.refused, self.sealed, self.failed = None, False, False
        self.changes = []

    def as_dict(self):
        return {"root": self.root, "stack": self.stack, "refused": self.refused,
                "sealed": self.sealed, "failed": self.failed,
                "differences": [d.as_dict() for d in self.differences],
                "seal_sets": [d.as_dict() for d in self.sets],
                "unused_waivers": list(self.unused), "problems": list(self.problems),
                "checkout": self.checkout, "changes": list(self.changes)}


def _record_text(stack, rev, check, n):
    return ("STACK=%s\nENGINE=%s\nCHECK=%s\nWAIVED=%d\n" % (stack, rev, check, n))


def _record(c, tree, stack, waivers, waive_text):
    """/etc/hasta/seal (and seal.waived): written when what they say changed, so a second seal
    changes nothing. SEALED_AT is the day the record last changed."""
    rec, wv = c.r(RECORD), c.r(WAIVED)
    want = _record_text(stack, engine_rev(tree), "waived" if waivers else "clean", len(waivers))
    try:
        with open(rec) as f:
            have = "".join(ln for ln in f if not ln.startswith("SEALED_AT="))
    except (IOError, OSError):
        have = None
    if have != want:
        os.makedirs(os.path.dirname(rec), exist_ok=True)
        with open(rec, "w") as f:
            f.write(want + "SEALED_AT=%s\n" % c.today)
        c.did("seal: %s records %s, check %s" % (RECORD, stack, "clean" if not waivers
                                                  else "with %d waiver(s)" % len(waivers)))
    if waivers:
        try:
            with open(wv, "rb") as f:
                same = f.read() == waive_text.encode("utf-8", "surrogateescape")
        except (IOError, OSError):
            same = False
        if not same:
            with open(wv, "wb") as f:
                f.write(waive_text.encode("utf-8", "surrogateescape"))
            c.did("seal: %s holds the waive file" % WAIVED)
    elif os.path.lexists(wv):
        os.remove(wv)
        c.did("seal: %s removed (nothing waived)" % WAIVED)


def run(tree, root, boot=None, data=None, keys=None, waive=None, receipts_path=None, apps=(),
        platform=None, today=None, out=None):
    """Check the mounted image at `root`, then seal it. Returns a Result: `refused` names why
    nothing changed (None when the seal ran), `failed` when the stack's seal refused.
    ValueError for usage: the running box, a waive file that cannot be read, no stack."""
    if os.path.realpath(root) == "/":
        raise ValueError("seal runs on the captured image (--root, --boot, --data), never on the"
                         " running box: it strips the rig's key and resets the box at its next"
                         " boot")
    for flag, d in (("--root", root), ("--boot", boot), ("--data", data)):
        if d and not os.path.isdir(d):
            raise ValueError("%s %s: not a directory" % (flag, d))
    waivers, waive_text = read_waivers(waive) if waive else ({}, "")
    mroot = facts_mod.mounted(root, boot=boot, data=data)
    report = check_mod.make(tree, mroot, receipts_path=receipts_path, apps=apps,
                            platform=platform)
    st = report.facts.values.get("stack") or {}
    name = "%s/%s" % (st.get("name"), st.get("revision")) if st.get("name") else None
    if not name:
        raise ValueError("%s: the facts name no stack (/etc/hasta/stack, /etc/rastaos-release or"
                         " /boot/VERSION): nothing says how to seal it" % root)
    d = stack_mod.stack_dir(tree, name)
    mod = stack_mod.load(d)
    if not callable(getattr(mod, "seal", None)):
        raise ValueError("stack %s has no seal (%s)"
                         % (name, os.path.join(d, stack_mod.STEPS_FILE)))

    r = Result(root, name)
    r.problems = list(report.problems)
    r.differences = differences(report)
    co = checkout_differences(mroot)
    if co is not None:
        r.checkout = "clean" if not co else "dirty"
        r.differences += co
    sets = getattr(mod, "SEAL_SETS", None) or {}
    r.sets = [x for x in r.differences if x.key in sets and x.what.startswith(sets[x.key])]
    r.differences = [x for x in r.differences if x not in r.sets]
    keys_seen = set()
    for x in r.differences:
        keys_seen.add(x.key)
        if x.key in waivers:
            x.waived = waivers[x.key]
    r.unused = [waivers[k] for k in waivers if k not in keys_seen]
    open_ = [x for x in r.differences if x.waived is None]
    if open_ or r.unused:
        bits = []
        if open_:
            bits.append("%d difference(s) not waived" % len(open_))
        if r.unused:
            bits.append("%d waiver(s) matching no difference" % len(r.unused))
        r.refused = ", ".join(bits)
        return r

    boot = boot or os.path.join(root, "boot")
    data = data or os.path.join(root, "data")
    opts = {"keys": os.path.abspath(keys) if keys else os.path.join(tree, KEEP_LIST)}
    c = stack_mod.Context(root, boot, data, False, stack_mod.recipe_rev(tree), opts,
                          today or mod.today(), out or (lambda m: None))
    try:
        ok = mod.seal(c)
    except (IOError, OSError) as exc:
        c.say("seal: %s" % exc)
        ok = False
    if ok:
        _record(c, tree, name, waivers, waive_text)
        r.sealed = True
    else:
        r.failed = True
    r.changes = list(c.changes)
    return r
