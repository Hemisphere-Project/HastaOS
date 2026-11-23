"""Declared state: one module.toml beside each module.ini — the core modules (#t-005), datesync's
at the root of the tree beside its three loose files, the feature modules (#t-006) — and the Rasta
7.5 overlay in stacks/rasta/7.5 (#t-006).

What is checked: every manifest validates and names its module; every `source` and every
relative link `target` exists in the module's directory; everything the module.ini declares
(bins, services, timers, enable, mask) is declared again by the manifest; and every unit file in
the module's directory is declared or named in `undeclared` — listed, never dropped.
"""

import configparser
import os
import re
import unittest

try:
    import tomllib as _toml  # Python 3.11+
except ImportError:
    from hasta._vendor import tomli as _toml

from hasta import legacy, manifest
from tests.helpers import REPO, TESTS

CORE = ("rorw", "extendfs", "flightbox", "blackbox", "splash", "starter", "hostrename")
FEATURES = ("network-tools", "audiohub", "usbautomount", "usbfix", "linkwatch", "apfix",
            "hdmi-rehandshake", "webconf", "synczinc", "tailscale", "filebrother", "xrun",
            "rtpmidi", "bluetooth-pi")
LAYER = dict([(n, "core") for n in CORE + ("datesync",)] + [(n, "feature") for n in FEATURES])
UNIT_SUFFIXES = (".service", ".timer", ".socket", ".mount", ".path")


def manifests():
    """(module id, its directory, the loaded manifest) for the core and feature modules."""
    out = [(name, os.path.join(REPO, name)) for name in CORE]
    out.append(("datesync", REPO))
    out += [(name, os.path.join(REPO, name)) for name in FEATURES]
    return [(name, d, manifest.load(os.path.join(d, manifest.MANIFEST_NAME))) for name, d in out]


def ini(name):
    parser = configparser.ConfigParser()
    parser.read(os.path.join(REPO, name, "module.ini"))
    return lambda section, key: parser.get(section, key, fallback="").split()


class ModuleManifests(unittest.TestCase):

    def test_each_validates_and_names_its_module(self):
        for name, _, m in manifests():
            with self.subTest(module=name):
                self.assertEqual([str(e) for e in m.errors], [])
                self.assertEqual(m.id, name)
                self.assertEqual(m.data["module"]["layer"], LAYER[name])

    def test_every_source_exists_in_the_tree(self):
        for name, d, m in manifests():
            res = m.data.get("resources", {})
            named = [f["source"] for f in res.get("files", [])]
            named += [u["source"] for u in res.get("units", []) if "source" in u]
            named += [l["target"] for l in res.get("links", [])
                      if not l["target"].startswith("/")]
            for src in named:
                with self.subTest(module=name, source=src):
                    self.assertTrue(os.path.isfile(os.path.join(d, src)), src)

    def test_the_manifest_declares_what_the_ini_does(self):
        for name, _, m in manifests():
            if name == "datesync":
                continue  # no module.ini: setup/installer.py lays it down inline
            words = ini(name)
            res = m.data.get("resources", {})
            links = set(l["path"] for l in res.get("links", []))
            units = dict((u["name"], u["state"]) for u in res.get("units", []))
            with self.subTest(module=name):
                for b in words("files", "bins"):
                    self.assertIn("/usr/local/bin/" + b, links)
                for u in words("files", "services") + words("files", "timers"):
                    self.assertIn(u, units)
                for u in words("files", "enable"):
                    self.assertEqual(units.get(u), "enabled", u)
                for u in words("files", "mask"):
                    self.assertEqual(units.get(u), "masked", u)

    def test_no_unit_file_is_dropped(self):
        for name, d, m in manifests():
            res = m.data.get("resources", {})
            declared = set(u["name"] for u in res.get("units", []))
            listed = " ".join(m.data.get("undeclared", []))
            files = [f for f in os.listdir(d) if f.endswith(UNIT_SUFFIXES)]
            if name == "datesync":
                files = [f for f in files if f.startswith("datesync")]
            for f in files:
                with self.subTest(module=name, unit=f):
                    self.assertTrue(f in declared or f in listed, f)

    def test_state_writes_and_seal_paths_stay_absolute(self):
        for name, _, m in manifests():
            for key in ("state", "writes", "seal"):
                for t in m.data.get(key, []):
                    with self.subTest(module=name, key=key, path=t["path"]):
                        self.assertTrue(t["path"].startswith("/"))

    def test_the_legacy_driver_joins_them(self):
        modules = legacy.discover(REPO)
        for name in CORE + FEATURES:
            with self.subTest(module=name):
                self.assertIsNotNone(modules[name].toml)
                self.assertTrue(modules[name].toml.valid)
                self.assertEqual(modules[name].layer, LAYER[name])
                self.assertEqual(modules[name].model()["module"]["id"], name)

    def test_hasta_modules_finds_them_all(self):
        found = dict((m.id, m) for m in manifest.discover(REPO))
        for name, _, _ in manifests():
            with self.subTest(module=name):
                self.assertIn(name, found)
                self.assertTrue(found[name].valid)



# -- the Rasta 7.5 overlay (#t-006) ------------------------------------------------------------

RASTA = os.path.join(REPO, "stacks", "rasta", "7.5")
BAKE = os.path.join(REPO, "image", "rastaos-bake")
LISTING = os.path.join(TESTS, "fixtures", "listings", "rasta-7.5-rc-systemd-system.txt")
UNITS_DIR = "/etc/systemd/system/"

# Entries of the 7.5 rc listing that no manifest owns, each with its reason: the defects the
# listing's note names (hub: hasta notes/2026-11-23-rasta-7.5-rc-unit-state.md). `hasta check`
# (#t-007) must report every one of them, and none of the wanted masks.
STALE_BESIDE_MASK = "wanted beside its mask: the mask wins, the link is stale"
EXPECTED_DRIFT = {
    "apfix.service": "a field pack's copy, where apfix links its unit",
    "apfix.timer": "a field pack's copy, where apfix links its unit",
    "usbfix.service": "a field pack's copy, where usbfix links its unit",
    "usbfix.timer": "a field pack's copy, where usbfix links its unit",
    "usbvol.service": "usbvol, a September field patch (P12): no module declares it",
    "usbvol.timer": "usbvol, a September field patch (P12): no module declares it",
    "timers.target.wants/usbvol.timer": "usbvol, a September field patch (P12)",
    "audiohub@usb.service.d/usbvol.conf": "usbvol's drop-in on audiohub: no module declares it",
    "dnsmasq.service": "masked, though network-tools serves the hotspot's DHCP with it",
    "hostapd@wint.service": "masked: no access point from a sync master",
    "wint-rejoin.service": "Anna's 7.4 bake put it there; no module installs it",
    "multi-user.target.wants/wint-rejoin.service": "Anna's 7.4 bake; no module installs it",
    "hdmi-rehandshake.service.d/anna-nowait.conf": "a drop-in no module declares (Anna, 7.4)",
    "multi-user.target.wants/dphys-swapfile.service": STALE_BESIDE_MASK,
    "multi-user.target.wants/gldriver-test.service": STALE_BESIDE_MASK,
    "multi-user.target.wants/rsyslog.service": STALE_BESIDE_MASK,
    "syslog.service": STALE_BESIDE_MASK + " (rsyslog's alias)",
    "timers.target.wants/logrotate.timer": STALE_BESIDE_MASK,
    "timers.target.wants/man-db.timer": STALE_BESIDE_MASK,
}
# Owned outside this tree: an app carries its own manifest, in its own repo.
OUTSIDE = {
    "hplayer2@.service": "HPlayer2's unit, linked from /opt/HPlayer2",
    "hplayer2@.service.d/tmpdir.conf": "HPlayer2's TMPDIR=/run drop-in, a field patch no recipe "
                                       "holds yet: the app's manifest should declare it",
}


def listing():
    """(name relative to /etc/systemd/system, link target or None, size or None) per entry."""
    out = []
    with open(LISTING) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^(\S+) -> (\S+)$", line)
            if m:
                out.append((m.group(1), m.group(2), None))
                continue
            m = re.match(r"^(\S+) \(file, (\d+) B\)$", line)
            if not m:
                raise ValueError("%s: cannot read %r" % (LISTING, line))
            out.append((m.group(1), None, int(m.group(2))))
    return out


def all_manifests():
    found = [(name, d, m) for name, d, m in manifests()]
    found.append(("rasta-7.5", RASTA, manifest.load(os.path.join(RASTA, manifest.MANIFEST_NAME))))
    return found


def declared():
    """units: name -> [(owner, unit table)]; files: box path -> (owner, its directory, table)."""
    units, files = {}, {}
    for name, d, m in all_manifests():
        res = m.data.get("resources", {})
        for u in res.get("units", []):
            units.setdefault(u["name"], []).append((name, u))
        for f in res.get("files", []):
            files[f["path"]] = (name, d, f)
    return units, files


def owner(entry, units, files):
    """The manifest that declares this listing entry in the form it has, or None."""
    rel, target, size = entry
    if "/" in rel:
        parent, unit = rel.split("/", 1)
        if parent.endswith(".d"):
            hit = files.get(UNITS_DIR + rel)
            return hit[0] if hit else None
        for suffix in (".wants", ".requires"):
            if parent.endswith(suffix):
                wanted_by = parent[:-len(suffix)]
                for who, u in units.get(unit, []):
                    if u["state"] == "enabled" and wanted_by in u.get("wanted_by", []):
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
        return None
    for who, u in units.get(rel, []):  # a link to the unit file
        if u["state"] != "masked" and not u.get("copy"):
            return who
    aliased = os.path.basename(target)  # an alias, made by enabling the unit it points to
    if aliased != rel:
        for who, u in units.get(aliased, []):
            if u["state"] == "enabled":
                return who
    return None


def bake_word(var):
    with open(BAKE) as fh:
        text = fh.read()
    m = re.search(r"^%s=\"([^\"]*)\"" % var, text, re.M)
    return m.group(1).split() if m else None


class RastaOverlay(unittest.TestCase):

    def setUp(self):
        self.m = manifest.load(os.path.join(RASTA, manifest.MANIFEST_NAME))

    def test_it_validates_as_a_stack(self):
        self.assertEqual([str(e) for e in self.m.errors], [])
        self.assertEqual(self.m.id, "rasta-7.5")
        self.assertEqual(self.m.data["module"]["layer"], "stack")

    def test_every_source_exists(self):
        for f in self.m.data["resources"].get("files", []):
            with self.subTest(source=f["source"]):
                self.assertTrue(os.path.isfile(os.path.join(RASTA, f["source"])))

    def test_the_stack_description_names_its_parts(self):
        with open(os.path.join(RASTA, "stack.toml"), "rb") as fh:
            data = _toml.loads(fh.read().decode("utf-8"))
        self.assertEqual((data["stack"]["name"], data["stack"]["revision"]), ("rasta", "7.5"))
        for part in ("base", "kernel", "graphics", "player"):
            self.assertIn(part, data)
        self.assertEqual(data["base"]["name"], "raspbian-buster")

    def test_every_mask_of_the_bake_is_declared(self):
        masks = bake_word("MASK_UNITS")
        self.assertTrue(masks)
        units = dict((u["name"], u["state"]) for u in self.m.data["resources"]["units"])
        for u in masks:
            with self.subTest(unit=u):
                self.assertEqual(units.get(u), "masked")

    def test_the_files_say_what_the_bake_writes(self):
        with open(BAKE) as fh:
            bake = fh.read()
        scan = bake.split("<<'NM'\n", 1)[1].split("\nNM\n", 1)[0] + "\n"
        with open(os.path.join(RASTA, "files", "91-rastaos-scan.conf")) as fh:
            self.assertEqual(fh.read(), scan)
        want = re.search(r"want=\$'([^']*)'", bake).group(1).replace("\\n", "\n") + "\n"
        with open(os.path.join(RASTA, "files", "resolv.conf")) as fh:
            self.assertEqual(fh.read(), want)

    def test_a_unit_declared_twice_is_a_stack_overriding_a_module(self):
        units, _ = declared()
        for name, decls in sorted(units.items()):
            states = set(u["state"] for _, u in decls)
            if len(states) > 1:
                with self.subTest(unit=name):
                    self.assertIn("rasta-7.5", [who for who, _ in decls])


class RastaListing(unittest.TestCase):
    """Every entry of the 7.5 rc's /etc/systemd/system is owned by a manifest, or is a defect
    the listing's note names (EXPECTED_DRIFT), or belongs to an app (OUTSIDE)."""

    def test_every_entry_has_an_owner_or_is_named_drift(self):
        units, files = declared()
        for entry in listing():
            rel = entry[0]
            who = owner(entry, units, files)
            with self.subTest(entry=rel):
                if rel in EXPECTED_DRIFT or rel in OUTSIDE:
                    self.assertIsNone(who, "%s is declared by %s: take it off the list" % (rel, who))
                else:
                    self.assertIsNotNone(who, "%s: no manifest declares it" % rel)

    def test_the_lists_name_only_entries_of_the_listing(self):
        names = set(e[0] for e in listing())
        for rel in sorted(set(EXPECTED_DRIFT) | set(OUTSIDE)):
            with self.subTest(entry=rel):
                self.assertIn(rel, names)

    def test_a_declared_copy_has_the_size_the_image_shows(self):
        _, files = declared()
        for rel, target, size in listing():
            hit = files.get(UNITS_DIR + rel)
            if size is None or hit is None:
                continue
            with self.subTest(entry=rel):
                self.assertEqual(os.path.getsize(os.path.join(hit[1], hit[2]["source"])), size)

    def test_every_mask_in_the_image_is_wanted_or_named(self):
        units, files = declared()
        masks = [e for e in listing() if e[1] == "/dev/null"]
        self.assertTrue(masks)
        for e in masks:
            with self.subTest(mask=e[0]):
                self.assertTrue(owner(e, units, files) or e[0] in EXPECTED_DRIFT)


if __name__ == "__main__":
    unittest.main()
