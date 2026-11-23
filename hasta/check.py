"""`hasta check`: what the manifests declare against what a box holds, read-only.

Two parts today (docs/DESIGN.md "hasta check"):

1. **Declared and different** — per module on the box, each declared unit, file or link that is
   not as declared: a mask missing or extra, an enabled unit with no `*.wants/` link, a disabled
   one with one, a copy where the module links its unit (or the reverse), a file or link absent.
2. **Undeclared** — every entry of `/etc/systemd/system` that no manifest of the tree claims in
   the form it has: a mask, a `*.wants/` or `*.requires/` link, an alias, a drop-in, a plain
   unit file, a link to a unit file.

Unit states are read from the links on disk, so no running systemd is needed and `--root` reads a
card's tree the same way. The box's stack overlay (`stacks/<name>/<revision>/`, from the facts)
declares after the modules and wins. Changes nothing.

Standard library only; runs on Python 3.7.
"""

import filecmp
import os
import stat

from hasta import facts as facts_mod
from hasta import legacy, manifest
from hasta import plan as plan_mod

UNIT_DIR = "/etc/systemd/system"


class Finding(object):
    def __init__(self, part, module, path, what, kind=None):
        self.part, self.module, self.path, self.what, self.kind = part, module, path, what, kind

    def as_dict(self):
        out = {"path": self.path, "what": self.what}
        if self.module:
            out["module"] = self.module
        if self.kind:
            out["kind"] = self.kind
        return out


class Report(object):
    def __init__(self, facts, modules, stack, different, undeclared, problems):
        self.facts, self.modules, self.stack = facts, modules, stack
        self.different, self.undeclared, self.problems = different, undeclared, problems

    @property
    def clean(self):
        return not self.different and not self.undeclared

    def as_dict(self):
        return {"root": self.facts.root, "modules": list(self.modules), "stack": self.stack,
                "different": [f.as_dict() for f in self.different],
                "undeclared": [f.as_dict() for f in self.undeclared],
                "problems": list(self.problems), "clean": self.clean}


class Decl(object):
    """One manifest's resources, with the directory its sources are read from."""

    def __init__(self, name, path, data, layer):
        self.name, self.path, self.layer = name, path, layer
        res = (data or {}).get("resources") or {}
        self.units = list(res.get("units") or [])
        self.files = list(res.get("files") or [])
        self.links = list(res.get("links") or [])


def _stack_dir(tree, facts):
    st = facts.values.get("stack")
    if not isinstance(st, dict) or not st.get("name") or not st.get("revision"):
        return None
    d = os.path.join(tree, "stacks", st["name"], str(st["revision"]))
    return d if os.path.isfile(os.path.join(d, manifest.MANIFEST_NAME)) else None


def declarations(tree, facts, apps=()):
    """(every module's Decl by name, the box's stack Decl or None, problems): the legacy
    driver's modules (module.toml where one is valid, else what the module.ini implies), the
    manifests it does not reach (datesync's, at the root), the app trees, and the stack overlay
    that matches the box's facts. Other stacks' manifests are not this box's."""
    problems = []
    decls = {}
    for name, m in sorted(legacy.discover(tree, apps).items()):
        decls[name] = Decl(name, m.path, m.model(), m.layer)
    stack = None
    stack_dir = _stack_dir(tree, facts)
    for m in manifest.discover(tree):
        if not m.valid:
            problems.append("%s: invalid manifest, not read" % os.path.relpath(m.path, tree))
            continue
        d = os.path.dirname(m.path)
        if m.data["module"]["layer"] == "stack":
            if d == stack_dir:
                stack = Decl(m.id, d, m.data, "stack")
            continue
        if any(x.path == d for x in decls.values()):
            continue
        decls[m.id] = Decl(m.id, d, m.data, m.data["module"]["layer"])
    return decls, stack, problems


def _on_box(root, decl):
    """A module the legacy driver does not reach is on the box when one of its declared
    unit files, files or links is."""
    paths = ["%s/%s" % (UNIT_DIR, u["name"]) for u in decl.units if "source" in u]
    paths += [f["path"] for f in decl.files] + [l["path"] for l in decl.links]
    return any(os.path.lexists(facts_mod._join(root, p)) for p in paths)


def _link(path):
    try:
        return os.readlink(path)
    except OSError:
        return None


def _wanted(root, unit):
    """The targets under whose `*.wants/` or `*.requires/` this unit is linked."""
    base = facts_mod._join(root, UNIT_DIR)
    out = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for d in names:
        for suffix in (".wants", ".requires"):
            if d.endswith(suffix) and os.path.lexists(os.path.join(base, d, unit)):
                out.append(d[:-len(suffix)])
    return out


def check_unit(root, owner, u):
    """Every way the unit on the box differs from its declaration."""
    name, state = u["name"], u["state"]
    p = facts_mod._join(root, "%s/%s" % (UNIT_DIR, name))
    box = "%s/%s" % (UNIT_DIR, name)
    target = _link(p)
    out = []

    def diff(what):
        out.append(Finding("different", owner, box, what))

    if state == "masked":
        if target != "/dev/null":
            diff("not masked, declared masked")
        return out
    if target == "/dev/null":
        diff("masked, declared %s" % state)
        return out
    if "source" in u:
        if not os.path.lexists(p):
            diff("missing")
        elif u.get("copy"):
            if target is not None:
                diff("a link, declared a copy")
        elif target is None:
            diff("a copy, where the module links its unit")
    wanted = _wanted(root, name)
    if state == "enabled":
        for t in u.get("wanted_by") or []:
            if t not in wanted:
                diff("not enabled under %s" % t)
    elif wanted:
        diff("enabled under %s, declared %s" % (", ".join(wanted), state))
    return out


def check_file(root, owner, src_dir, f):
    p = facts_mod._join(root, f["path"])
    target = _link(p)
    if not os.path.lexists(p):
        return [Finding("different", owner, f["path"], "missing")]
    if f.get("copy"):
        if target is not None:
            return [Finding("different", owner, f["path"], "a link, declared a copy")]
        out = []
        src = os.path.join(src_dir, f["source"])
        if os.path.isfile(src) and not filecmp.cmp(src, p, shallow=False):
            out.append(Finding("different", owner, f["path"], "content differs from %s"
                               % f["source"]))
        if "mode" in f:
            have = stat.S_IMODE(os.lstat(p).st_mode)
            if have != int(f["mode"], 8):
                out.append(Finding("different", owner, f["path"], "mode %04o, declared %s"
                                   % (have, f["mode"])))
        return out
    if target is None:
        return [Finding("different", owner, f["path"], "a copy, declared a link")]
    if os.path.basename(target) != os.path.basename(f["source"]):
        return [Finding("different", owner, f["path"], "links to %s, declared %s"
                        % (target, f["source"]))]
    return []


def check_link(root, owner, l):
    p = facts_mod._join(root, l["path"])
    target = _link(p)
    if target is None:
        what = "missing" if not os.path.lexists(p) else "not a link"
        return [Finding("different", owner, l["path"], what)]
    want = l["target"]
    ok = target == want if want.startswith("/") else (
        os.path.basename(target) == os.path.basename(want))
    return [] if ok else [Finding("different", owner, l["path"], "links to %s, declared %s"
                                  % (target, want))]


def claims(decls, stack):
    """units: name -> [(owner, unit table)]; files: box path -> owner. Every manifest of the
    tree claims, on the box or not: undeclared means no manifest at all says it."""
    units, files = {}, {}
    for d in list(decls.values()) + ([stack] if stack else []):
        for u in d.units:
            units.setdefault(u["name"], []).append((d.name, u))
        for f in d.files:
            files[f["path"]] = d.name
        for l in d.links:
            files[l["path"]] = d.name
    return units, files


def owner(rel, target, units, files):
    """The manifest that claims this /etc/systemd/system entry in the form it has, or None.
    `rel` is relative to /etc/systemd/system; `target` the link target, None for a file."""
    if "/" in rel:
        parent, unit = rel.split("/", 1)
        if parent.endswith(".d"):
            return files.get("%s/%s" % (UNIT_DIR, rel))
        for suffix in (".wants", ".requires"):
            if parent.endswith(suffix):
                wanted_by = parent[:-len(suffix)]
                for who, u in units.get(unit, []):
                    if u["state"] == "enabled" and wanted_by in (u.get("wanted_by") or []):
                        return who
        return None
    if target == "/dev/null":
        for who, u in units.get(rel, []):
            if u["state"] == "masked":
                return who
        return None
    if target is None:  # a plain file: a unit the box holds as a copy
        for who, u in units.get(rel, []):
            if u.get("copy") and u["state"] != "masked":
                return who
        return files.get("%s/%s" % (UNIT_DIR, rel))
    for who, u in units.get(rel, []):  # a link to the unit file
        if u["state"] != "masked" and not u.get("copy"):
            return who
    aliased = os.path.basename(target)  # an alias, made by enabling the unit it points to
    if aliased != rel:
        for who, u in units.get(aliased, []):
            if u["state"] == "enabled":
                return who
    return files.get("%s/%s" % (UNIT_DIR, rel))


def kind_of(rel, target):
    if "/" in rel:
        parent = rel.split("/", 1)[0]
        if parent.endswith(".d"):
            return "drop-in"
        return "enabled"
    if target == "/dev/null":
        return "masked"
    if target is None:
        return "file"
    if os.path.basename(target) != rel:
        return "alias"
    return "link"


KIND_WORDS = {
    "drop-in": "a drop-in no manifest declares",
    "enabled": "enabled, and no manifest enables it there",
    "masked": "masked, and no manifest masks it",
    "file": "a unit file copied in, no manifest declares it",
    "alias": "an alias of a unit no manifest enables",
    "link": "a unit linked in, no manifest declares it",
}


def entries(root):
    """(name relative to /etc/systemd/system, link target or None) per entry, one level of
    `*.wants/`, `*.requires/` and `*.d/` directories down. None when the directory is absent."""
    base = facts_mod._join(root, UNIT_DIR)
    if not os.path.isdir(base):
        return None
    out = []
    for name in sorted(os.listdir(base)):
        p = os.path.join(base, name)
        if os.path.isdir(p) and not os.path.islink(p):
            for sub in sorted(os.listdir(p)):
                q = os.path.join(p, sub)
                if os.path.isdir(q) and not os.path.islink(q):
                    continue
                out.append(("%s/%s" % (name, sub), _link(q)))
            continue
        out.append((name, _link(p)))
    return out


def make(tree, root="/", receipts_path=None, select=None, platform=None, apps=(),
         only=None):
    """The check of the box at `root` against the modules of `tree`. Raises ValueError when
    `root` is not a box (no /etc/systemd/system) or `only` names no module."""
    facts = facts_mod.compute(root)
    listed = entries(facts.root)
    if listed is None:
        raise ValueError("%s: no %s, not a box" % (facts.root, UNIT_DIR))
    decls, stack, problems = declarations(tree, facts, apps)
    p = plan_mod.make(tree, root, receipts_path=receipts_path, select=select,
                      platform=platform, apps=apps)
    problems += p.problems
    on_box = [r.name for r in p.rows if r.action not in ("skip", "unrecorded")
              and not r.why.startswith("not on this box")]
    reached = set(legacy.discover(tree, apps))  # the rest: datesync's, at the root
    on_box += [n for n in sorted(decls) if n not in reached and _on_box(facts.root, decls[n])]
    if only is not None:
        if only not in decls:
            raise ValueError("--module %s: no such module in %s" % (only, tree))
        on_box = [only]

    stack_units = dict((u["name"], u) for u in stack.units) if stack else {}
    different = []
    for n in on_box:
        d = decls[n]
        for u in d.units:
            if u["name"] in stack_units:
                continue  # the stack declares after the modules and wins
            different += check_unit(facts.root, n, u)
        for f in d.files:
            different += check_file(facts.root, n, d.path, f)
        for l in d.links:
            different += check_link(facts.root, n, l)
    if stack is not None and only is None:
        for u in stack.units:
            different += check_unit(facts.root, stack.name, u)
        for f in stack.files:
            different += check_file(facts.root, stack.name, stack.path, f)
        for l in stack.links:
            different += check_link(facts.root, stack.name, l)

    undeclared = []
    if only is None:
        units, files = claims(decls, stack)
        seen = set(f.path for f in different)
        for rel, target in listed:
            if owner(rel, target, units, files) is not None:
                continue
            box = "%s/%s" % (UNIT_DIR, rel)
            if box in seen:
                continue  # already reported as declared and different
            k = kind_of(rel, target)
            what = KIND_WORDS[k]
            unit = rel.split("/", 1)[-1] if k == "enabled" else rel
            other = sorted(set("%s (%s%s)" % (who, u["state"], ", copy" if u.get("copy") else "")
                               for who, u in units.get(unit, [])))
            if other:
                what += " in this form; declared by %s" % ", ".join(other)
            undeclared.append(Finding("undeclared", None, box, what, k))
    return Report(facts, on_box, stack.name if stack else None, different, undeclared,
                  problems)
