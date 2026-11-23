"""The module manifest, schema version 1: `module.toml`, its loader and its validator.

The schema is docs/DESIGN.md "The manifest". The validator refuses unknown keys (a typo is an
error, not a silently ignored line) and every error names the file, the key and the reason.
`module.ini` is not read here: it stays the legacy driver's input.

Standard library only; runs on Python 3.7 (tomllib where it exists, the vendored tomli elsewhere).
"""

import os
import re

try:
    import tomllib as _toml  # Python 3.11+
except ImportError:  # Buster's 3.7, and anything before 3.11
    from hasta._vendor import tomli as _toml

from hasta import SCHEMA_VERSION

MANIFEST_NAME = "module.toml"
LAYERS = ("base", "machine", "stack", "core", "feature", "app", "fleet")
UNIT_STATES = ("enabled", "disabled", "masked", "static")
UNIT_SUFFIXES = (".service", ".timer", ".socket", ".mount", ".automount", ".path",
                 ".target", ".swap", ".slice")
LINE_FILES = ("cmdline", "config", "fstab")
OPTION_TYPES = ("bool", "int", "float", "string", "enum", "list")
WRITE_WHEN = ("boot", "steady", "update", "shutdown", "event")
SEAL_ACTIONS = ("delete", "truncate", "regenerate")
SUPPORT_KEYS = ("machine", "base", "architecture", "graphics", "stack")

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_CAP = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)*$")
_MODE = re.compile(r"^0?[0-7]{3,4}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class Error(object):
    def __init__(self, path, key, reason):
        self.path, self.key, self.reason = path, key, reason

    def __str__(self):
        return "%s: %s: %s" % (self.path, self.key or "(file)", self.reason)

    def as_dict(self):
        return {"file": self.path, "key": self.key, "reason": self.reason}


class Manifest(object):
    """A loaded module.toml. `errors` empty = valid; `data` is None when it did not parse."""

    def __init__(self, path, data, errors):
        self.path, self.data, self.errors = path, data, errors

    @property
    def valid(self):
        return not self.errors

    @property
    def id(self):
        return ((self.data or {}).get("module") or {}).get("id")

    def as_dict(self):
        module = (self.data or {}).get("module") or {}
        return {"file": self.path, "id": module.get("id"), "version": module.get("version"),
                "layer": module.get("layer"), "valid": self.valid,
                "errors": [e.as_dict() for e in self.errors]}


def loads(text, path="<string>"):
    try:
        data = _toml.loads(text)
    except _toml.TOMLDecodeError as exc:
        return Manifest(path, None, [Error(path, "", "not valid TOML: %s" % exc)])
    return Manifest(path, data, validate(data, path))


def load(path):
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
    except UnicodeDecodeError:
        return Manifest(path, None, [Error(path, "", "not UTF-8")])
    except (IOError, OSError) as exc:
        return Manifest(path, None, [Error(path, "", "cannot read: %s" % exc)])
    return loads(text, path)


def discover(tree, skip=(".git", "tests", "hasta", "__pycache__", "node_modules")):
    """Every module.toml under `tree`, loaded (valid or not), sorted by path."""
    found = []
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        if MANIFEST_NAME in filenames:
            found.append(load(os.path.join(dirpath, MANIFEST_NAME)))
    return found


def count_legacy(tree, skip=(".git", "tests", "hasta", "__pycache__", "node_modules")):
    n = 0
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames[:] = [d for d in dirnames if d not in skip]
        n += "module.ini" in filenames
    return n


# -- the validator -------------------------------------------------------------------------

class _V(object):
    def __init__(self, path):
        self.path = path
        self.errors = []

    def err(self, key, reason):
        self.errors.append(Error(self.path, key, reason))

    def table(self, key, value, allowed, required=()):
        if not isinstance(value, dict):
            self.err(key, "must be a table")
            return False
        for k in sorted(value):
            if k not in allowed:
                self.err(_k(key, k), "unknown key (allowed: %s)" % ", ".join(allowed))
        for k in required:
            if k not in value:
                self.err(_k(key, k), "required")
        return True

    def string(self, key, value, pattern=None, what=None):
        if not isinstance(value, str) or not value:
            self.err(key, "must be a non-empty string")
            return False
        if pattern is not None and not pattern.match(value):
            self.err(key, "%r is not %s" % (value, what or "valid"))
            return False
        return True

    def one_of(self, key, value, choices):
        if value not in choices:
            self.err(key, "%r is not one of %s" % (value, ", ".join(choices)))

    def path_abs(self, key, value):
        if self.string(key, value) and not value.startswith("/"):
            self.err(key, "%r must be an absolute path" % value)

    def boolean(self, key, value):
        if not isinstance(value, bool):
            self.err(key, "must be true or false")

    def strings(self, key, value, pattern=None, what=None):
        if not isinstance(value, list):
            self.err(key, "must be a list of strings")
            return
        for i, item in enumerate(value):
            self.string("%s[%d]" % (key, i), item, pattern, what)

    def array_of_tables(self, key, value):
        if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
            self.err(key, "must be an array of tables ([[%s]])" % key)
            return []
        return list(enumerate(value))


def _keys(value):
    return tuple(value) if isinstance(value, dict) else ()


def _k(parent, child):
    return "%s.%s" % (parent, child) if parent else child


TOP_KEYS = ("schema", "module", "supports", "requires", "provides", "claims", "options",
            "resources", "state", "writes", "seal", "migrations", "undeclared")


def validate(data, path="<string>"):
    """Every reason `data` (a parsed module.toml) is not a valid schema-1 manifest."""
    v = _V(path)
    if not v.table("", data, TOP_KEYS, required=("schema", "module")):
        return v.errors
    if "schema" in data and data["schema"] != SCHEMA_VERSION:
        v.err("schema", "%r is not a schema this engine reads (it reads %d)"
              % (data["schema"], SCHEMA_VERSION))

    module = data.get("module")
    if module is not None and v.table("module", module, ("id", "version", "layer", "summary"),
                                      required=("id", "version", "layer", "summary")):
        if "id" in module:
            v.string("module.id", module["id"], _ID, "a module id (lowercase, digits, . _ -)")
        if "version" in module:
            v.string("module.version", module["version"])
        if "layer" in module:
            v.one_of("module.layer", module["layer"], LAYERS)
        if "summary" in module:
            v.string("module.summary", module["summary"])

    if "supports" in data and v.table("supports", data["supports"], SUPPORT_KEYS):
        for k in SUPPORT_KEYS:
            if k in data["supports"]:
                v.strings("supports." + k, data["supports"][k])

    for k in ("requires", "provides", "claims"):
        if k in data:
            v.strings(k, data[k], _CAP, "a capability name (dotted, lowercase)")

    if "options" in data and v.table("options", data["options"], _keys(data["options"])):
        for name in sorted(data["options"]):
            _option(v, "options." + name, name, data["options"][name])

    if "resources" in data:
        _resources(v, data["resources"])

    if "state" in data:
        for i, t in v.array_of_tables("state", data["state"]):
            key = "state[%d]" % i
            if v.table(key, t, ("path", "summary"), required=("path",)) and "path" in t:
                p = t["path"]
                if v.string(key + ".path", p) and not (p == "/data" or p.startswith("/data/")):
                    v.err(key + ".path", "%r: a module's state lives under /data" % p)
                if "summary" in t:
                    v.string(key + ".summary", t["summary"])

    if "writes" in data:
        for i, t in v.array_of_tables("writes", data["writes"]):
            key = "writes[%d]" % i
            if v.table(key, t, ("path", "when", "summary"), required=("path", "when")):
                if "path" in t:
                    v.path_abs(key + ".path", t["path"])
                if "when" in t:
                    v.one_of(key + ".when", t["when"], WRITE_WHEN)

    if "seal" in data:
        for i, t in v.array_of_tables("seal", data["seal"]):
            key = "seal[%d]" % i
            if v.table(key, t, ("path", "action", "summary"), required=("path", "action")):
                if "path" in t:
                    v.path_abs(key + ".path", t["path"])
                if "action" in t:
                    v.one_of(key + ".action", t["action"], SEAL_ACTIONS)

    if "migrations" in data:
        for i, t in v.array_of_tables("migrations", data["migrations"]):
            key = "migrations[%d]" % i
            if v.table(key, t, ("from", "summary", "script"), required=("from", "summary")):
                for f in ("from", "summary", "script"):
                    if f in t:
                        v.string(key + "." + f, t[f])

    if "undeclared" in data:
        v.strings("undeclared", data["undeclared"])
    return v.errors


def _option(v, key, name, opt):
    if not _ID.match(name):
        v.err(key, "%r is not an option name (lowercase, digits, . _ -)" % name)
    if not v.table(key, opt, ("type", "default", "choices", "summary"),
                   required=("type", "default")):
        return
    kind = opt.get("type")
    if "type" in opt:
        v.one_of(key + ".type", kind, OPTION_TYPES)
    if "summary" in opt:
        v.string(key + ".summary", opt["summary"])
    if kind == "enum":
        if "choices" not in opt:
            v.err(key + ".choices", "required for type enum")
        else:
            v.strings(key + ".choices", opt["choices"])
    elif "choices" in opt:
        v.err(key + ".choices", "only an enum has choices")
    if "default" not in opt or kind not in OPTION_TYPES:
        return
    if kind == "enum" and not isinstance(opt.get("choices"), list):
        return  # already reported: an enum without its choices
    d = opt["default"]
    ok = {
        "bool": isinstance(d, bool),
        "int": isinstance(d, int) and not isinstance(d, bool),
        "float": isinstance(d, (int, float)) and not isinstance(d, bool),
        "string": isinstance(d, str),
        "enum": isinstance(d, str) and isinstance(opt.get("choices"), list)
        and d in opt["choices"],
        "list": isinstance(d, list),
    }[kind]
    if not ok:
        v.err(key + ".default", "%r does not fit type %s%s" % (
            d, kind, " (not in choices)" if kind == "enum" else ""))


def _resources(v, res):
    if not v.table("resources", res, ("packages", "files", "links", "units", "lines",
                                      "artefacts")):
        return
    if "packages" in res and v.table("resources.packages", res["packages"],
                                     _keys(res["packages"])):
        # key = a base name ("raspbian-buster", "debian-trixie") or "any"
        for base in sorted(res["packages"]):
            v.strings("resources.packages." + base, res["packages"][base])

    for i, t in v.array_of_tables("resources.files", res.get("files", [])):
        key = "resources.files[%d]" % i
        if v.table(key, t, ("path", "source", "mode", "copy"), required=("path", "source")):
            if "path" in t:
                v.path_abs(key + ".path", t["path"])
            if "source" in t:
                v.string(key + ".source", t["source"])
            if "mode" in t:
                v.string(key + ".mode", t["mode"], _MODE, "an octal mode like \"0644\"")
            if "copy" in t:
                v.boolean(key + ".copy", t["copy"])

    for i, t in v.array_of_tables("resources.links", res.get("links", [])):
        key = "resources.links[%d]" % i
        if v.table(key, t, ("path", "target"), required=("path", "target")):
            if "path" in t:
                v.path_abs(key + ".path", t["path"])
            if "target" in t:
                v.string(key + ".target", t["target"])

    for i, t in v.array_of_tables("resources.units", res.get("units", [])):
        key = "resources.units[%d]" % i
        if v.table(key, t, ("name", "state", "wanted_by", "source", "copy"),
                   required=("name", "state")):
            name = t.get("name")
            if "name" in t and v.string(key + ".name", name) and not name.endswith(UNIT_SUFFIXES):
                v.err(key + ".name", "%r is not a systemd unit name" % name)
            if "state" in t:
                v.one_of(key + ".state", t["state"], UNIT_STATES)
            if "wanted_by" in t:
                v.strings(key + ".wanted_by", t["wanted_by"])
            if "source" in t:
                v.string(key + ".source", t["source"])
            if "copy" in t:
                v.boolean(key + ".copy", t["copy"])

    for i, t in v.array_of_tables("resources.lines", res.get("lines", [])):
        key = "resources.lines[%d]" % i
        if v.table(key, t, ("file", "line", "present", "section"), required=("file", "line")):
            if "file" in t:
                v.one_of(key + ".file", t["file"], LINE_FILES)
            if "line" in t:
                v.string(key + ".line", t["line"])
            if "present" in t:
                v.boolean(key + ".present", t["present"])
            if "section" in t:
                if t.get("file") != "config":
                    v.err(key + ".section", "only a config.txt line has a section")
                v.string(key + ".section", t["section"])

    for i, t in v.array_of_tables("resources.artefacts", res.get("artefacts", [])):
        key = "resources.artefacts[%d]" % i
        if v.table(key, t, ("path", "target", "sha256", "summary"),
                   required=("path", "sha256")):
            if "path" in t:
                v.path_abs(key + ".path", t["path"])
            if "target" in t:
                v.string(key + ".target", t["target"])
            if "sha256" in t:
                v.string(key + ".sha256", t["sha256"], _SHA256, "a sha256 (64 hex, lowercase)")
