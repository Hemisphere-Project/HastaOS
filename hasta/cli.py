"""`hasta` — the command line. Every command takes --json. Exit 0 ok, 1 a finding, 2 usage."""

import argparse
import json
import os
import subprocess
import sys

from hasta import SCHEMA_VERSION, __version__
from hasta import apply as apply_mod
from hasta import check as check_mod
from hasta import facts as facts_mod
from hasta import manifest
from hasta import plan as plan_mod

# The HastaOS checkout this engine runs from: the tree `hasta modules` reads by default.
TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _emit(args, obj, text_lines):
    if args.json:
        json.dump(obj, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        for line in text_lines:
            print(line)


def _show(value):
    if value is None:
        return "-"
    if isinstance(value, list):
        return " ".join(value) if value else "-"
    if isinstance(value, dict):
        return " ".join("%s=%s" % (k, value[k]) for k in sorted(value) if value[k] is not None)
    return str(value)


FACT_ORDER = ("machine", "model", "architecture", "base", "kernel", "kernels", "boot", "data",
              "graphics", "stack", "release")


def cmd_facts(args):
    f = facts_mod.compute(args.root)
    obj = f.as_dict()
    lines = ["root: %s%s" % (f.root, " (live)" if f.live else "")]
    for key in FACT_ORDER:
        src = f.sources.get(key)
        lines.append("%-13s %s%s" % (key + ":", _show(f.values.get(key)),
                                     "    (%s)" % src if src else ""))
    _emit(args, obj, lines)
    return 0


def cmd_modules(args):
    tree = os.path.abspath(args.tree)
    found = manifest.discover(tree)
    legacy = manifest.count_legacy(tree)
    bad = [m for m in found if not m.valid]
    obj = {"tree": tree, "schema": SCHEMA_VERSION, "legacy_module_ini": legacy,
           "manifests": [m.as_dict() for m in found]}
    lines = []
    for m in found:
        rel = os.path.relpath(m.path, tree)
        if m.valid:
            mod = m.data["module"]
            lines.append("ok       %-28s %s %s (%s)" % (rel, mod["id"], mod["version"],
                                                       mod["layer"]))
        else:
            lines.append("INVALID  %s" % rel)
            for e in m.errors:
                lines.append("           %s: %s" % (e.key or "(file)", e.reason))
    lines.append("%d manifest(s), %d invalid, in %s; %d module.ini (legacy)"
                 % (len(found), len(bad), tree, legacy))
    _emit(args, obj, lines)
    return 1 if bad else 0


def _tree_at(ref, tree):
    """The tree's modules as committed at `ref`, unpacked into a temporary directory."""
    import io
    import subprocess
    import tarfile
    import tempfile
    p = subprocess.run(["git", "-C", tree, "archive", "--format=tar", ref],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise ValueError("--to %s: %s" % (ref, p.stderr.decode("utf-8", "replace").strip()))
    tmp = tempfile.mkdtemp(prefix="hasta-plan-")
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as tar:
        # a plan reads only each top-level module's module.ini / module.toml: plain files
        wanted = [m for m in tar.getmembers() if m.isfile() and m.name.count("/") == 1
                  and m.name.split("/")[1] in ("module.ini", "module.toml")]
        if hasattr(tarfile, "data_filter"):  # 3.12+, and the 3.7-3.11 security backports
            tar.extractall(tmp, members=wanted, filter="data")
        else:
            tar.extractall(tmp, members=wanted)
    return tmp


def cmd_plan(args):
    tree = os.path.abspath(args.tree)
    tmp = None
    try:
        if args.to:
            try:
                tmp = tree = _tree_at(args.to, tree)
            except ValueError as exc:
                print("hasta plan: %s" % exc, file=sys.stderr)
                return 2
        select = None
        if args.modules is not None:
            select = [n for n in args.modules.replace(",", " ").split() if n]
        try:
            p = plan_mod.make(tree, args.root, receipts_path=args.receipts, select=select,
                              platform=args.platform, apps=args.app or ())
        except ValueError as exc:
            print("hasta plan: %s" % exc, file=sys.stderr)
            return 2
    finally:
        if tmp:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
    obj = p.as_dict()
    obj["tree"] = args.to or os.path.abspath(args.tree)
    lines = []
    for r in p.rows:
        lines.append("%-10s %-18s %s" % (r.action, r.name, r.why))
    for prob in p.problems:
        lines.append("problem    %s" % prob)
    lines.append("%d module(s) on %s (%s), from %s; %d problem(s)"
                 % (len(p.rows), p.platform or "?", p.facts.root, p.selected_by,
                    len(p.problems)))
    _emit(args, obj, lines)
    return 1 if p.problems else 0


def cmd_apply(args):
    root = args.root
    if args.resume:
        req = apply_mod.pending(root)
        if req is None:
            _emit(args, {"resumed": False, "steps": []}, ["nothing to resume"])
            return 0
        tree, select = req.get("tree") or os.path.abspath(args.tree), req.get("modules")
        receipts, platform = req.get("receipts"), req.get("platform")
        apps, force = req.get("apps") or (), bool(req.get("force"))
    else:
        tree = os.path.abspath(args.tree)
        select = None
        if args.modules is not None:
            select = [n for n in args.modules.replace(",", " ").split() if n]
        receipts, platform, apps, force = args.receipts, args.platform, args.app or (), args.force
    request = {"tree": tree, "modules": select, "receipts": receipts, "platform": platform,
               "apps": [os.path.abspath(a) for a in apps], "force": force}
    try:
        r = apply_mod.apply(tree, root, receipts_path=receipts, select=select, platform=platform,
                            apps=request["apps"], dry_run=args.dry_run, force=force,
                            resumed=args.resume, request=request)
    except ValueError as exc:
        print("hasta apply: %s" % exc, file=sys.stderr)
        return 2
    lines = ["%-10s %-18s %s" % (s["action"], s["module"], s["why"]) for s in r.steps]
    for s in r.steps:
        if s["outcome"] not in ("none",):
            lines.append("%-10s %-18s %s" % (s["outcome"], s["module"], s["action"]))
    lines.append("%s%d module(s), %d ran, %d current, %d failed%s"
                 % ("resumed: " if r.resumed else "", len(r.steps),
                    sum(1 for s in r.steps if s["outcome"] in ("ran", "would-run")),
                    sum(1 for s in r.steps if s["outcome"] == "current"), len(r.failed),
                    " (dry run: nothing changed)" if args.dry_run else ""))
    if r.failed:
        lines.append("apply: failed: %s" % " ".join(r.failed))
    _emit(args, r.as_dict(), lines)
    return 1 if r.failed else 0


def cmd_check(args):
    for flag, d in (("--boot", args.boot), ("--data", args.data)):
        if d and not os.path.isdir(d):
            print("hasta check: %s %s: not a directory" % (flag, d), file=sys.stderr)
            return 2
    tree = os.path.abspath(args.tree)
    opened = None
    if args.image:
        if args.root != "/" or args.boot or args.data:
            print("hasta check: --image reads its own root, boot and data", file=sys.stderr)
            return 2
        if not os.path.isfile(args.image):
            print("hasta check: --image %s: not a file" % args.image, file=sys.stderr)
            return 2
        from hasta import image as image_mod
        try:
            opened = image_mod.Opened(args.image, check_mod.declared_paths(tree, args.app or ()))
        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
            print("hasta check: %s" % exc, file=sys.stderr)
            return 2
        root = opened.root
    else:
        root = facts_mod.mounted(args.root, boot=args.boot, data=args.data)
    try:
        r = check_mod.make(tree, root, receipts_path=args.receipts,
                           apps=args.app or (), platform=args.platform, only=args.module)
    except ValueError as exc:
        print("hasta check: %s" % exc, file=sys.stderr)
        return 2
    finally:
        if opened is not None:
            opened.close()
    lines = []
    if opened is not None:
        for p in opened.parts:
            lines.append("partition   p%-3d %-6s %-10s %s" % (p.number, p.fs or "?",
                                                            p.label or "-", p.role or "-"))
        for n in opened.notices:
            lines.append("notice      %s" % n)
    for f in r.different:
        lines.append("different   %-18s %s: %s" % (f.module, f.path, f.what))
    for f in r.undeclared:
        lines.append("undeclared  %-18s %s: %s" % (f.kind, f.path, f.what))
    off = [f for f in r.receipts if f.kind != "same"]
    for f in r.receipts:
        if f.kind != "same" or f.module is None:
            lines.append("receipt     %-18s %s: %s" % (f.kind, f.module or "release", f.what))
    for prob in r.problems:
        lines.append("problem     %s" % prob)
    obj = r.as_dict()
    if opened is not None:
        obj["root"] = opened.path
        obj["image"] = opened.as_dict()
    lines.append("%s: %d module(s)%s; %d different, %d undeclared, %d receipt(s) off"
                 % (obj["root"], len(r.modules), ", stack %s" % r.stack if r.stack else "",
                    len(r.different), len(r.undeclared), len(off)))
    _emit(args, obj, lines)
    return 0 if r.clean else 1


def parser():
    p = argparse.ArgumentParser(
        prog="hasta", description="Hasta: one engine, one module contract (docs/DESIGN.md).")
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    p.add_argument("--version", action="version", version="hasta " + __version__)
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    def add(name, help_text):
        sp = sub.add_parser(name, help=help_text, description=help_text)
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print JSON instead of text")
        return sp

    sp = add("facts", "what this box is: machine, base, boot and data partitions, stack")
    sp.add_argument("--root", default="/", metavar="DIR",
                    help="read the facts of the tree at DIR instead of this box")
    sp.set_defaults(func=cmd_facts)

    sp = add("modules", "every module.toml in the tree, valid or not, and why not")
    sp.add_argument("--tree", default=TREE, metavar="DIR",
                    help="the module tree to read (default: this checkout)")
    sp.set_defaults(func=cmd_modules)

    sp = add("plan", "what an apply would do: the box's modules, resolved, in converge's classes"
             " (upgrade, script, held, install, linked); changes nothing")
    sp.add_argument("--root", default="/", metavar="DIR",
                    help="plan for the tree at DIR (a card in a reader) instead of this box")
    sp.add_argument("--to", metavar="REF",
                    help="plan towards this checkout as committed at REF (default: as it is)")
    sp.add_argument("--tree", default=TREE, metavar="DIR",
                    help="the module tree to move to (default: this checkout)")
    sp.add_argument("--receipts", metavar="FILE",
                    help="the module receipts (default: ROOT/etc/pi-tools-modules.txt)")
    sp.add_argument("--modules", metavar="LIST",
                    help="plan this module set (comma or space separated), not the receipts'")
    sp.add_argument("--app", action="append", metavar="DIR",
                    help="an app tree carrying its own module.ini (repeatable)")
    sp.add_argument("--platform", choices=("pi", "x86"),
                    help="override the platform the facts give")
    sp.set_defaults(func=cmd_plan)

    sp = add("apply", "run the plan through the legacy driver (converge's code paths), one"
             " receipt per module under /etc/hasta/receipts; a module whose receipt is current"
             " is not run again")
    sp.add_argument("--root", default="/", metavar="DIR",
                    help="only with --dry-run: the legacy driver applies to the running box")
    sp.add_argument("--tree", default=TREE, metavar="DIR",
                    help="the module tree to apply (default: this checkout)")
    sp.add_argument("--receipts", metavar="FILE",
                    help="the module receipts (default: ROOT/etc/pi-tools-modules.txt)")
    sp.add_argument("--modules", metavar="LIST",
                    help="apply this module set (comma or space separated), not the receipts'")
    sp.add_argument("--app", action="append", metavar="DIR",
                    help="an app tree carrying its own module.ini (repeatable)")
    sp.add_argument("--platform", choices=("pi", "x86"),
                    help="override the platform the facts give")
    sp.add_argument("--dry-run", action="store_true", help="say what would run, change nothing")
    sp.add_argument("--force", action="store_true",
                    help="run every module again, even those whose receipt is current")
    sp.add_argument("--resume", action="store_true",
                    help="rerun what an interrupted apply's marker on /data names; nothing"
                    " without one (the boot unit's call)")
    sp.set_defaults(func=cmd_apply)

    sp = add("check", "what the manifests declare against what the box holds, and the unit"
             " state no manifest declares; changes nothing")
    sp.add_argument("--root", default="/", metavar="DIR",
                    help="check the tree at DIR (a card in a reader) instead of this box")
    sp.add_argument("--boot", metavar="DIR",
                    help="the boot partition, where it is not under ROOT (a card's FAT, mounted"
                    " on its own); read at the point ROOT's fstab mounts it")
    sp.add_argument("--data", metavar="DIR",
                    help="the data partition, where it is not ROOT/data")
    sp.add_argument("--image", metavar="FILE",
                    help="check an image file: partitions found by label, mounted read-only as"
                    " root, else read through debugfs (ext) and mtools (FAT)")
    sp.add_argument("--module", metavar="M",
                    help="only this module's declared resources (no undeclared part)")
    sp.add_argument("--tree", default=TREE, metavar="DIR",
                    help="the module tree that declares (default: this checkout)")
    sp.add_argument("--receipts", metavar="FILE",
                    help="the module receipts (default: ROOT/etc/pi-tools-modules.txt)")
    sp.add_argument("--app", action="append", metavar="DIR",
                    help="an app tree carrying its own manifest (repeatable)")
    sp.add_argument("--platform", choices=("pi", "x86"),
                    help="override the platform the facts give")
    sp.set_defaults(func=cmd_check)
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help(sys.stderr)
        return 2
    return args.func(args)
