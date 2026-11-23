"""`hasta check`: what the manifests declare against what a box holds, read-only.

Three parts (docs/DESIGN.md "hasta check"):

1. **Declared and different** — per module on the box, each declared unit, file, link, package
   or line that is not as declared: a mask missing or extra, an enabled unit with no `*.wants/`
   link, a disabled one with one, a copy where the module links its unit (or the reverse), a file
   or link absent, a package not installed, a `cmdline`/`config`/`fstab` line present or absent.
2. **Undeclared** — every entry of `/etc/systemd/system` that no manifest of the tree claims in
   the form it has: a mask, a `*.wants/` or `*.requires/` link, an alias, a drop-in, a plain
   unit file, a link to a unit file.
3. **Receipts** — each module receipt's fingerprint (`/etc/pi-tools-modules.txt`, written by
   `setup/module-manifest`) against the same fingerprint of the tree's module directory, and the
   release receipt's commit (`/etc/rastaos-release`) against the tree's history.

Unit states are read from the links on disk, so no running systemd is needed and `--root` reads a
card's tree the same way. The box's stack overlay (`stacks/<name>/<revision>/`, from the facts)
declares after the modules and wins. Changes nothing.

Standard library only; runs on Python 3.7.
"""

import filecmp
import hashlib
import os
import stat
import subprocess

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
    def __init__(self, facts, modules, stack, different, undeclared, receipts, problems):
        self.facts, self.modules, self.stack = facts, modules, stack
        self.different, self.undeclared, self.problems = different, undeclared, problems
        self.receipts = receipts

    @property
    def clean(self):
        return not (self.different or self.undeclared
                    or [f for f in self.receipts if f.kind != "same"])

    def as_dict(self):
        return {"root": self.facts.root, "modules": list(self.modules), "stack": self.stack,
                "different": [f.as_dict() for f in self.different],
                "undeclared": [f.as_dict() for f in self.undeclared],
                "receipts": [f.as_dict() for f in self.receipts],
                "problems": list(self.problems), "clean": self.clean}


class Decl(object):
    """One manifest's resources, with the directory its sources are read from."""

    def __init__(self, name, path, data, layer):
        self.name, self.path, self.layer = name, path, layer
        res = (data or {}).get("resources") or {}
        self.units = list(res.get("units") or [])
        self.files = list(res.get("files") or [])
        self.links = list(res.get("links") or [])
        self.lines = list(res.get("lines") or [])
        self.packages = dict(res.get("packages") or {})


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


def installed(root):
    """The packages dpkg says are installed under `root`, or None without a dpkg status."""
    text = facts_mod.read_text(root, "/var/lib/dpkg/status")
    if text is None:
        return None
    out, name = set(), None
    for ln in text.splitlines():
        if ln.startswith("Package:"):
            name = ln.split(":", 1)[1].strip()
        elif ln.startswith("Status:") and name and ln.split()[-1] == "installed":
            out.add(name.split(":")[0])
    return out


def check_packages(owner, packages, base, have):
    out = []
    for key in sorted(packages):
        if key not in ("any", base):
            continue
        for pkg in packages[key]:
            if pkg not in have:
                out.append(Finding("different", owner, "package " + pkg, "not installed"))
    return out


def _config_lines(text, section):
    """config.txt's lines under `[section]` (before any header counts as [all])."""
    cur, out = "all", []
    for ln in text.splitlines():
        t = ln.strip()
        if t.startswith("[") and t.endswith("]"):
            cur = t[1:-1].strip()
            continue
        if t and not t.startswith("#") and (section is None or cur == section):
            out.append(t)
    return out


def check_line(root, owner, boot, l):
    """A declared line of cmdline.txt, config.txt or fstab, present or absent as declared.
    cmdline and config lines hold only where the box has the file (a Pi)."""
    want, present = l["line"], l.get("present", True)
    if l["file"] == "fstab":
        path = "/etc/fstab"
        text = facts_mod.read_text(root, path)
        if text is None:
            return [Finding("different", owner, path, "missing, a line is declared")] \
                if present else []
        fields = want.split()
        have = any(ln.split() == fields for ln in text.splitlines())
    else:
        if not boot:
            return []
        path = "%s/%s.txt" % (boot.rstrip("/"), l["file"])
        text = facts_mod.read_text(root, path)
        if text is None:
            return []
        if l["file"] == "cmdline":
            have = want in text.split()
        else:
            have = want.strip() in _config_lines(text, l.get("section"))
    if have == present:
        return []
    where = " [%s]" % l["section"] if l.get("section") else ""
    return [Finding("different", owner, path, "%r%s %s, declared %s" % (
        want, where, "present" if have else "absent", "present" if present else "absent"))]


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


# -- receipts -------------------------------------------------------------------------------

RELEASE = "/etc/rastaos-release"


def _git(tree, *args):
    try:
        p = subprocess.run(["git", "-C", tree] + list(args), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
    except OSError:
        return None
    return p.stdout if p.returncode == 0 else None


def _files(d):
    """The module directory's files as setup/module-manifest lists them: what git sees inside a
    checkout (tracked, and untracked not ignored), else everything but caches and *.pyc."""
    out = _git(d, "ls-files", "-z", "-co", "--exclude-standard", "--", ".")
    if out is not None:
        return [f.decode("utf-8", "surrogateescape") for f in out.split(b"\0") if f]
    found = []
    for dirpath, dirnames, filenames in os.walk(d):
        dirnames[:] = [x for x in dirnames if x not in ("__pycache__", "node_modules", ".venv")]
        for f in filenames:
            p = os.path.join(dirpath, f)
            if not f.endswith(".pyc") and os.path.isfile(p) and not os.path.islink(p):
                found.append(os.path.relpath(p, d))
    return found


def fingerprint(d):
    """setup/module-manifest's fingerprint: 12 hex of sha1 over the sorted `<git-blob-id> <path>`
    lines of the directory's files."""
    lines = []
    for rel in _files(d):
        p = os.path.join(d, rel)
        if not os.path.isfile(p):
            continue  # tracked but deleted on disk
        with open(p, "rb") as fh:
            body = fh.read()
        size = os.lstat(p).st_size  # module-manifest's `stat -c %s` does not follow a link
        blob = hashlib.sha1(b"blob %d\0" % size + body).hexdigest()
        lines.append(("%s %s\n" % (blob, rel)).encode("utf-8", "surrogateescape"))
    return hashlib.sha1(b"".join(sorted(lines))).hexdigest()[:12]


def check_receipts(tree, root, receipts_path, modules, on_box):
    """Per module: its receipt against the tree's fingerprint; and the release receipt's commit
    against the tree's history. Kinds: same, differs, unrecorded (on the box, no receipt),
    not-in-tree (a receipt for a module this tree lacks), release."""
    if receipts_path is None:
        receipts_path = facts_mod._join(root, plan_mod.RECEIPTS)
    receipts = plan_mod.read_receipts(receipts_path)
    out = []
    for n in sorted(set(receipts) | (set(on_box) & set(modules))):
        box = "%s %s" % (plan_mod.RECEIPTS, n)
        if n not in modules:
            out.append(Finding("receipts", n, box, "a receipt, and no such module in the tree",
                               "not-in-tree"))
            continue
        if n not in receipts:
            out.append(Finding("receipts", n, box, "on the box, no receipt", "unrecorded"))
            continue
        have, want = receipts[n]["fingerprint"], fingerprint(modules[n].path)
        if have == want:
            out.append(Finding("receipts", n, box, "the tree's version (%s)" % want, "same"))
        else:
            out.append(Finding("receipts", n, box, "receipt %s, tree %s (from %s)"
                               % (have, want, receipts[n]["source"] or "-"), "differs"))
    text = facts_mod.read_text(root, RELEASE)
    if text:
        rel = facts_mod.parse_kv(text)
        commit = rel.get("PITOOLS_COMMIT", "")
        head = (_git(tree, "rev-parse", "--short", "HEAD") or b"").decode().strip()
        if not head:
            what, kind = "%s at %s; the tree is not a checkout" % (
                rel.get("RASTAOS_TAG", "?"), commit or "?"), "release"
        elif commit and _git(tree, "merge-base", "--is-ancestor", commit, "HEAD") is not None:
            what, kind = "%s at %s, in the tree's history (tree at %s)" % (
                rel.get("RASTAOS_TAG", "?"), commit, head), "same"
        else:
            what, kind = "%s at %s, not in the tree's history (tree at %s)" % (
                rel.get("RASTAOS_TAG", "?"), commit or "?", head), "release"
        out.append(Finding("receipts", None, RELEASE, what, kind))
    return out


def declared_paths(tree, apps=()):
    """Every box path a check of this tree may read beyond /etc: each manifest's files and links
    (any stack's too) and what the legacy driver probes (bins, units). `--image` without root
    extracts these, not the whole partition."""
    out = set()
    for m in legacy.discover(tree, apps).values():
        out.update(m.declared())
    for m in manifest.discover(tree):
        if m.valid:
            d = Decl(m.id, os.path.dirname(m.path), m.data, m.data["module"]["layer"])
            out.update(f["path"] for f in d.files)
            out.update(l["path"] for l in d.links)
    return sorted(p for p in out if p.startswith("/"))


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
    boot = (facts.values.get("boot") or {}).get("path")
    base = (facts.values.get("base") or {}).get("name")
    have = installed(facts.root)
    if have is None:
        problems.append("no /var/lib/dpkg/status: packages not checked")
    different = []
    for n in on_box:
        d = decls[n]
        for l in d.lines:
            different += check_line(facts.root, n, boot, l)
        if have is not None:
            different += check_packages(n, d.packages, base, have)
        for u in d.units:
            if u["name"] in stack_units:
                continue  # the stack declares after the modules and wins
            different += check_unit(facts.root, n, u)
        for f in d.files:
            different += check_file(facts.root, n, d.path, f)
        for l in d.links:
            different += check_link(facts.root, n, l)
    if stack is not None and only is None:
        for l in stack.lines:
            different += check_line(facts.root, stack.name, boot, l)
        if have is not None:
            different += check_packages(stack.name, stack.packages, base, have)
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
    receipts = []
    if only is None:
        receipts = check_receipts(tree, facts.root, receipts_path,
                                  legacy.discover(tree, apps), on_box)
    return Report(facts, on_box, stack.name if stack else None, different, undeclared,
                  receipts, problems)
