"""The legacy driver: today's `module.ini` modules, read into the manifest model.

A `module.ini` says how `setup/installer.py` installs a module: its apt packages, the bins it
links into /usr/local/bin, the units it links into /etc/systemd/system, the udev rules, the
directories, npm, the units it enables or masks, its starter entry, and for a script module
whether `install.sh` may run again (`rerun`) or which re-runnable script carries its upgrades
(`upgrade`). This module reads every one of those keys, and nothing else is read by the
installer. Where a `module.toml` sits beside the `.ini`, the two are joined: the `.ini` keeps
saying how the module is installed today, the `.toml` says what it leaves on a box.

Discovery is converge's: every top-level directory of a tree that holds a `module.ini`, plus
app trees given by path (HPlayer2, HKiosk and HNdi each carry one at their root).

Read-only: nothing here installs anything. Standard library only; runs on Python 3.7.
"""

import configparser
import os

from hasta import SCHEMA_VERSION
from hasta import manifest

INI_NAME = "module.ini"

# Every key setup/installer.py and setup/converge.py read, per section.
INI_KEYS = {
    "module": ("name", "description", "group", "platforms"),
    "install": ("script", "rerun", "upgrade"),
    "deps": ("apt",),
    "files": ("bins", "services", "timers", "dirs", "npm", "udev_rules", "enable", "mask"),
    "starter": ("comment", "service"),
}

# The installer's paths (setup/utils.py: link_bin, install_service, install_udev_rule).
BIN_DIR = "/usr/local/bin"
UNIT_DIR = "/etc/systemd/system"
UDEV_DIR = "/etc/udev/rules.d"

# platforms= → the machines a module supports (facts' machine names).
PLATFORM_MACHINES = {"pi": "rpi*", "x86": "x86*"}


class Legacy(object):
    """One module as the legacy installer sees it, joined with its module.toml if any."""

    def __init__(self, name, path, ini, kind="tree", toml=None):
        self.name = name                  # directory name: what receipts and converge use
        self.path = path                  # the module directory
        self.kind = kind                  # "tree" (this checkout) or "app" (given by path)
        self.toml = toml                  # manifest.Manifest beside the .ini, or None
        self.unknown = []                 # ini keys no installer reads: reported, not fatal

        def get(section, key, default=""):
            return ini.get(section, key, fallback=default).strip()

        def words(section, key):
            return get(section, key).split()

        def flag(section, key):
            try:
                return ini.getboolean(section, key, fallback=False)
            except ValueError:
                self.unknown.append("%s.%s=%s (not a boolean)" % (section, key,
                                                                    get(section, key)))
                return False

        self.description = get("module", "description") or name
        self.group = get("module", "group")
        self.platforms = [p.strip() for p in get("module", "platforms", "pi,x86").split(",")
                          if p.strip()]
        self.script = flag("install", "script")
        self.rerun = flag("install", "rerun")
        self.upgrade = get("install", "upgrade")
        self.apt = words("deps", "apt")
        self.bins = words("files", "bins")
        self.services = words("files", "services")
        self.timers = words("files", "timers")
        self.dirs = words("files", "dirs")
        self.npm = flag("files", "npm")
        self.udev_rules = words("files", "udev_rules")
        self.enable = words("files", "enable")
        self.mask = words("files", "mask")
        self.starter = None
        if ini.has_option("starter", "comment") and ini.has_option("starter", "service"):
            self.starter = {"comment": get("starter", "comment"),
                            "service": get("starter", "service")}
        for section in ini.sections():
            for key in ini.options(section):
                if key not in INI_KEYS.get(section, ()):
                    self.unknown.append("%s.%s" % (section, key))

    # -- what converge reads ---------------------------------------------------------------

    def declared(self):
        """The box paths a standard install lays down: converge's `declared()`, verbatim."""
        return (["%s/%s" % (BIN_DIR, b) for b in self.bins]
                + ["%s/%s" % (UNIT_DIR, u) for u in self.services + self.timers])

    def on_platform(self, platform):
        return platform in self.platforms

    # -- the manifest model ------------------------------------------------------------------

    @property
    def layer(self):
        if self.toml is not None and self.toml.valid:
            return self.toml.data["module"]["layer"]
        if self.kind == "app":
            return "app"
        return "core" if self.group == "core" else "feature"

    def model(self):
        """The module as a schema-1 manifest: the module.toml's data when one is valid beside
        the .ini, else what the .ini implies. Either way `manifest.validate` accepts it."""
        if self.toml is not None and self.toml.valid:
            return self.toml.data
        data = {"schema": SCHEMA_VERSION,
                "module": {"id": self.name.lower(), "version": "legacy", "layer": self.layer,
                           "summary": self.description}}
        machines = [PLATFORM_MACHINES[p] for p in self.platforms if p in PLATFORM_MACHINES]
        if machines and len(machines) < len(PLATFORM_MACHINES):
            data["supports"] = {"machine": machines}
        res = {}
        if self.apt:
            res["packages"] = {"any": list(self.apt)}
        links = [{"path": "%s/%s" % (BIN_DIR, b), "target": os.path.join(self.path, b)}
                 for b in self.bins]
        links += [{"path": "%s/%s" % (UDEV_DIR, r), "target": os.path.join(self.path, r)}
                  for r in self.udev_rules]
        if links:
            res["links"] = links
        units = []
        for u in self.services + self.timers:
            state = ("masked" if u in self.mask else "enabled" if u in self.enable
                     else "disabled")
            units.append({"name": u, "state": state, "source": u})
        for u in self.mask:
            if u not in self.services + self.timers:
                units.append({"name": u, "state": "masked"})
        for u in self.enable:
            if u not in self.services + self.timers:
                units.append({"name": u, "state": "enabled"})
        if units:
            res["units"] = units
        if res:
            data["resources"] = res
        undeclared = []
        if self.script:
            undeclared.append("install.sh: what it leaves on a box is not declared")
        if self.dirs:
            undeclared.append("directories created: " + " ".join(self.dirs))
        if self.npm:
            undeclared.append("npm install in the module directory")
        if self.starter:
            undeclared.append("starter.txt entry: %s" % self.starter["service"])
        if undeclared:
            data["undeclared"] = undeclared
        return data

    def install(self):
        """How the module is installed today (the .ini's half of the join)."""
        out = {"script": self.script, "rerun": self.rerun, "upgrade": self.upgrade or None,
               "platforms": list(self.platforms)}
        for k in ("apt", "bins", "services", "timers", "dirs", "udev_rules", "enable",
                  "mask"):
            if getattr(self, k):
                out[k] = list(getattr(self, k))
        if self.npm:
            out["npm"] = True
        if self.starter:
            out["starter"] = dict(self.starter)
        return out

    def as_dict(self):
        return {"name": self.name, "path": self.path, "kind": self.kind, "layer": self.layer,
                "toml": self.toml.path if self.toml is not None else None,
                "install": self.install(), "unknown_keys": list(self.unknown)}


def read(path, name=None, kind="tree"):
    """The module in directory `path`, or None when it has no module.ini."""
    ini_path = os.path.join(path, INI_NAME)
    if not os.path.isfile(ini_path):
        return None
    ini = configparser.ConfigParser()
    ini.read(ini_path)
    toml_path = os.path.join(path, manifest.MANIFEST_NAME)
    toml = manifest.load(toml_path) if os.path.isfile(toml_path) else None
    return Legacy(name or os.path.basename(os.path.normpath(path)), path, ini, kind, toml)


def discover(tree, apps=()):
    """name -> Legacy: the tree's top-level module directories (converge's discovery), then
    each app tree given by path. An app named like a tree module replaces it, as its own
    install would."""
    out = {}
    for name in sorted(os.listdir(tree)):
        m = read(os.path.join(tree, name), name)
        if m is not None:
            out[name] = m
    for app in apps:
        m = read(os.path.abspath(app), kind="app")
        if m is None:
            raise ValueError("%s: no %s" % (app, INI_NAME))
        name = m.name
        try:  # the app's own [module] name, when it gives one
            ini = configparser.ConfigParser()
            ini.read(os.path.join(m.path, INI_NAME))
            name = ini.get("module", "name", fallback="").strip() or name
        except configparser.Error:
            pass
        m.name = name
        out[name] = m
    return out
