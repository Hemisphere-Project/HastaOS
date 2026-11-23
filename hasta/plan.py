"""Resolve and plan: facts × a module set → the ordered actions, read-only (`hasta plan`).

The selected set comes from today's sources: the module receipts a box keeps in
/etc/pi-tools-modules.txt (setup/module-manifest writes one line per install pass), or a list
given on the command line. Resolve checks `supports`, `requires` / `provides` and `claims`, and
orders the set by layer, then by what each module requires. A conflict between two legacy
modules that declare nothing (two of them laying down the same box path) is reported, not
enforced.

The plan sorts every module into the classes `setup/converge.py` decides today — upgrade,
script, held, install, linked, plus skip (not for this platform) and unrecorded (a script
module the box shows no trace of) — with the same rules, so that on the same inputs the two
decide the same thing (tests/test_plan.py runs both). Nothing is applied: that is #t-009.

Standard library only; runs on Python 3.7.
"""

import fnmatch
import os

from hasta import facts as facts_mod
from hasta import legacy
from hasta.manifest import LAYERS

RECEIPTS = "/etc/pi-tools-modules.txt"
ACTIONS = ("upgrade", "script", "held", "install", "linked", "skip", "unrecorded")


def platform_of(facts):
    """converge's platform word (pi | x86) for a facts object, or None."""
    machine = facts.values.get("machine") or ""
    if machine.startswith("rpi"):
        return "pi"
    if machine.startswith("x86"):
        return "x86"
    return None


def read_receipts(path):
    """name -> {fingerprint, installed_at, source}; {} when the file is absent (a box from
    before the receipts). Read the way converge reads it: first word of each line that is
    neither blank nor a comment."""
    out = {}
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except (IOError, OSError):
        return out
    for ln in lines:
        if not ln.strip() or ln.startswith("#"):
            continue
        parts = ln.split()
        out[parts[0]] = {"fingerprint": parts[1] if len(parts) > 1 else None,
                         "installed_at": parts[2] if len(parts) > 2 else None,
                         "source": parts[3] if len(parts) > 3 else None}
    return out


class Row(object):
    def __init__(self, name, action, why, module=None, receipt=None):
        self.name, self.action, self.why = name, action, why
        self.module, self.receipt = module, receipt

    def as_dict(self):
        return {"module": self.name, "action": self.action, "why": self.why,
                "layer": self.module.layer if self.module else None,
                "receipt": self.receipt}


class Plan(object):
    def __init__(self, facts, platform, receipts_path, receipts, rows, problems, selected_by):
        self.facts, self.platform = facts, platform
        self.receipts_path, self.receipts = receipts_path, receipts
        self.rows, self.problems, self.selected_by = rows, problems, selected_by

    def as_dict(self):
        return {"root": self.facts.root, "platform": self.platform,
                "selected_by": self.selected_by, "receipts": self.receipts_path,
                "actions": [r.as_dict() for r in self.rows],
                "problems": list(self.problems)}


def _present(root, paths):
    return [p for p in paths if os.path.lexists(facts_mod._join(root, p))]


def _supported(model, facts):
    """Every reason the module's `supports` excludes this box (empty = supported)."""
    out = []
    for key, allowed in sorted((model.get("supports") or {}).items()):
        value = facts.values.get(key)
        if isinstance(value, dict):
            value = value.get("id") or value.get("name")
        if value is None:
            continue  # a fact this box does not state cannot exclude it
        if not any(fnmatch.fnmatch(str(value), pat) for pat in allowed):
            out.append("%s %s not in %s" % (key, value, ", ".join(allowed)))
    return out


def resolve(modules, names, facts):
    """(ordered names, problems): `names` ordered by layer, then requires before requirers,
    then name; problems = unsupported, unmet requires, two claims on one capability, two
    modules on one box path. Problems are reported; none is enforced here."""
    problems = []
    names = sorted(n for n in names if n in modules)
    models = dict((n, modules[n].model()) for n in names)

    provided = {}
    for n in names:
        for cap in [models[n]["module"]["id"]] + list(models[n].get("provides") or []):
            provided.setdefault(cap, []).append(n)
    needs = {}
    for n in names:
        needs[n] = set()
        for cap in models[n].get("requires") or []:
            if cap in provided:
                needs[n].update(p for p in provided[cap] if p != n)
            else:
                problems.append("%s requires %s: nothing in the set provides it" % (n, cap))
        for why in _supported(models[n], facts):
            problems.append("%s does not support this box: %s" % (n, why))

    claimed = {}
    for n in names:
        for cap in models[n].get("claims") or []:
            claimed.setdefault("claim " + cap, []).append(n)
        for p in modules[n].declared():
            claimed.setdefault(p, []).append(n)
    for what in sorted(claimed):
        if len(claimed[what]) > 1:
            problems.append("conflict: %s held by %s" % (what, ", ".join(claimed[what])))

    rank = dict((layer, i) for i, layer in enumerate(LAYERS))
    key = lambda n: (rank.get(modules[n].layer, len(LAYERS)), n)
    ordered, done, todo = [], set(), sorted(names, key=key)
    while todo:
        ready = [n for n in todo if needs[n] <= done]
        if not ready:  # a requires cycle: report it, keep the layer order for the rest
            problems.append("requires cycle among: %s" % ", ".join(todo))
            ready = todo
        nxt = ready[0]
        ordered.append(nxt)
        done.add(nxt)
        todo.remove(nxt)
    return ordered, problems


def decide(m, platform, present, on_box=True):
    """converge's decision for one module on this box, as (action, why)."""
    paths = m.declared()
    if not m.on_platform(platform):
        return "skip", "not for %s" % platform
    if not on_box:
        return "install", "not on this box: %s" % (
            "install.sh" if m.script else "the standard install")
    if m.script and m.upgrade:
        return "upgrade", "%s --converging" % m.upgrade
    if m.script and m.rerun:
        return "script", "install.sh again"
    if m.script:
        return "held", "install.sh not rerun-safe, no upgrade script — not run"
    if paths and len(present) == len(paths):
        return "linked", "symlinks already carry the new code"
    return "install", "%d declared file(s) missing" % (len(paths) - len(present))


def make(tree, root="/", receipts_path=None, select=None, platform=None, apps=()):
    """The plan that moves the box at `root` from its receipts to the modules of `tree`.

    `select` (a list of names) replaces the receipts as the module set; otherwise the set is
    converge's: every module named in the receipts, plus every module with one of its declared
    bins or units on the box."""
    facts = facts_mod.compute(root)
    platform = platform or platform_of(facts)
    if receipts_path is None:
        receipts_path = facts_mod._join(root, RECEIPTS)
    receipts = read_receipts(receipts_path)
    modules = legacy.discover(tree, apps)

    problems = []
    rows = {}
    if select is not None:
        selected_by = "list"
        for n in select:
            if n not in modules:
                problems.append("%s: no such module in %s" % (n, tree))
        chosen = [n for n in select if n in modules]
        for n in chosen:
            m = modules[n]
            present = _present(facts.root, m.declared())
            on_box = n in receipts or bool(present)
            rows[n] = decide(m, platform, present, on_box)
    else:
        selected_by = "receipts"
        chosen = []
        for n in sorted(modules):
            m = modules[n]
            present = _present(facts.root, m.declared())
            if n not in receipts and not present:
                if m.script:
                    rows[n] = ("unrecorded", "script module, no receipt — not run")
                    chosen.append(n)
                continue  # not on this box: setup.sh's job
            rows[n] = decide(m, platform, present)
            chosen.append(n)
        for n in sorted(receipts):
            if n not in modules:
                problems.append("%s: in the receipts, not in %s" % (n, tree))

    if platform is None:
        problems.append("unknown platform (machine %s): pass --platform"
                        % facts.values.get("machine"))
    active = [n for n in chosen if rows[n][0] not in ("skip", "unrecorded")]
    ordered, found = resolve(modules, active, facts)
    problems += found
    tail = [n for n in sorted(chosen) if n not in ordered]
    out = [Row(n, rows[n][0], rows[n][1], modules[n], receipts.get(n))
           for n in ordered + tail]
    return Plan(facts, platform, receipts_path, receipts, out, problems, selected_by)
