"""`hasta` — the command line. Every command takes --json. Exit 0 ok, 1 a finding, 2 usage."""

import argparse
import json
import os
import sys

from hasta import SCHEMA_VERSION, __version__
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


def cmd_check(args):
    try:
        r = check_mod.make(os.path.abspath(args.tree), args.root, receipts_path=args.receipts,
                           apps=args.app or (), platform=args.platform, only=args.module)
    except ValueError as exc:
        print("hasta check: %s" % exc, file=sys.stderr)
        return 2
    lines = []
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
    lines.append("%s: %d module(s)%s; %d different, %d undeclared, %d receipt(s) off"
                 % (r.facts.root, len(r.modules), ", stack %s" % r.stack if r.stack else "",
                    len(r.different), len(r.undeclared), len(off)))
    _emit(args, r.as_dict(), lines)
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

    sp = add("check", "what the manifests declare against what the box holds, and the unit"
             " state no manifest declares; changes nothing")
    sp.add_argument("--root", default="/", metavar="DIR",
                    help="check the tree at DIR (a card in a reader) instead of this box")
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
