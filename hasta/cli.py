"""`hasta` — the command line. Every command takes --json. Exit 0 ok, 1 a finding, 2 usage."""

import argparse
import json
import os
import sys

from hasta import SCHEMA_VERSION, __version__
from hasta import facts as facts_mod
from hasta import manifest

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
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help(sys.stderr)
        return 2
    return args.func(args)
