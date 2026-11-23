"""What a box is, resolved once: `hasta facts`.

Every fact is read from files under a root, so the same code answers on a live box (root "/")
and on a tree given by `--root` (a fixture, a card in a reader). A few facts only exist on a
running system (the running kernel, mounted filesystems, /dev/disk/by-label): those are read
only when the root is "/", and every fact says where it came from (`sources`).

Absorbs setup/detect.py (machine, Pi model, boot directory). That file stays as it is until
the installers move onto the engine: nothing a box runs changes in this phase.

Standard library only; runs on Python 3.7.
"""

import os
import platform
import re

# Pi device-tree model strings → machine. Checked in order; first match wins.
_PI_MODEL_RULES = [
    (re.compile(r"Raspberry Pi (?:Compute Module )?5|Raspberry Pi 500"), "rpi5"),
    (re.compile(r"Raspberry Pi (?:Compute Module )?4|Raspberry Pi 400"), "rpi4"),
    (re.compile(r"Raspberry Pi Zero 2"), "rpi3"),  # BCM2710: boots and runs like a Pi 3
    (re.compile(r"Raspberry Pi (?:Compute Module )?3"), "rpi3"),
    (re.compile(r"Raspberry Pi 2"), "rpi2"),
    (re.compile(r"Raspberry Pi Zero"), "rpi0"),
    (re.compile(r"Raspberry Pi"), "rpi"),  # a Pi, model not mapped
]

# ELF e_machine → Debian architecture (fallback when dpkg says nothing).
_ELF_ARCH = {0x28: "armhf", 0xB7: "arm64", 0x3E: "amd64", 0x03: "i386"}
_UNAME_ARCH = {"armv7l": "armhf", "armv6l": "armhf", "aarch64": "arm64",
               "x86_64": "amd64", "i686": "i386", "i386": "i386"}

# Where today's installers look for the data partition, in their order (rorw/install.sh).
DATA_PROBES = ["/dev/mmcblk0p3", "/dev/mmcblk1p8", "/dev/sda3", "/dev/nvme0n1p3"]
DATA_LABELS = ["data", "DATA"]

_PI_BOOT_MARKERS = ("config.txt", "cmdline.txt", "start.elf", "bootcode.bin")


def _join(root, path):
    return os.path.join(root, path.lstrip("/"))


def resolve(root, path):
    """`path` as seen from inside `root`: symlinks followed, absolute ones kept inside root."""
    parts = [p for p in path.split("/") if p]
    cur = ""
    hops = 0
    while parts:
        name = parts.pop(0)
        if name == ".":
            continue
        if name == "..":
            cur = cur.rsplit("/", 1)[0] if "/" in cur else ""
            continue
        nxt = cur + "/" + name
        full = _join(root, nxt)
        if os.path.islink(full):
            hops += 1
            if hops > 40:
                return None
            target = os.readlink(full)
            parts = [p for p in target.split("/") if p] + parts
            if target.startswith("/"):
                cur = ""
            continue
        cur = nxt
    return _join(root, cur)


def read_text(root, path):
    full = resolve(root, path)
    if full is None or not os.path.isfile(full):
        return None
    try:
        with open(full, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except (IOError, OSError):
        return None


def exists(root, path):
    full = resolve(root, path)
    return full is not None and os.path.exists(full)


def isdir(root, path):
    full = resolve(root, path)
    return full is not None and os.path.isdir(full)


def parse_kv(text):
    """KEY=value lines (os-release, the release receipt, /etc/hasta/stack). Quotes stripped."""
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


class Facts(object):
    """Computes every fact once. `as_dict()` is what `hasta facts --json` prints."""

    def __init__(self, root="/"):
        self.root = root or "/"
        self.live = os.path.realpath(self.root) == "/"
        self.values = {}
        self.sources = {}
        self._compute()

    def _set(self, key, value, source):
        self.values[key] = value
        self.sources[key] = source

    def _compute(self):
        self._model()
        self._architecture()
        self._base()
        self._kernel()
        self._boot()
        self._machine()
        self._data()
        self._graphics()
        self._release()
        self._stack()

    # -- hardware ------------------------------------------------------------------------

    def _model(self):
        for path in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
            text = read_text(self.root, path)
            if text:
                self._set("model", text.strip("\x00").strip(), path)
                return
        vendor = read_text(self.root, "/sys/class/dmi/id/sys_vendor")
        product = read_text(self.root, "/sys/class/dmi/id/product_name")
        if vendor or product:
            name = " ".join(x.strip() for x in (vendor, product) if x and x.strip())
            self._set("model", name, "/sys/class/dmi/id")
            return
        self._set("model", None, None)

    def _architecture(self):
        status = read_text(self.root, "/var/lib/dpkg/status")
        if status:
            for stanza in status.split("\n\n"):
                fields = dict(
                    line.split(":", 1) for line in stanza.splitlines()
                    if ":" in line and not line.startswith(" "))
                if fields.get("Package", "").strip() == "base-files":
                    arch = fields.get("Architecture", "").strip()
                    if arch:
                        self._set("architecture", arch, "/var/lib/dpkg/status")
                        return
        for path in ("/bin/sh", "/usr/bin/env"):
            full = resolve(self.root, path)
            if full and os.path.isfile(full):
                try:
                    with open(full, "rb") as f:
                        head = f.read(20)
                except (IOError, OSError):
                    continue
                if len(head) == 20 and head[:4] == b"\x7fELF":
                    order = "little" if head[5] == 1 else "big"
                    arch = _ELF_ARCH.get(int.from_bytes(head[18:20], order))
                    if arch:
                        self._set("architecture", arch, path)
                        return
        if self.live:
            arch = _UNAME_ARCH.get(platform.machine(), platform.machine())
            self._set("architecture", arch, "uname")
            return
        self._set("architecture", None, None)

    # -- software ------------------------------------------------------------------------

    def _base(self):
        for path in ("/etc/os-release", "/usr/lib/os-release"):
            text = read_text(self.root, path)
            if text:
                break
        else:
            self._set("base", None, None)
            return
        kv = parse_kv(text)
        osid = kv.get("ID", "").strip() or None
        codename = kv.get("VERSION_CODENAME", "").strip()
        if not codename:
            m = re.search(r"\(([a-z]+)\)", kv.get("VERSION", ""))
            codename = m.group(1) if m else ""
        base = {
            "id": osid,
            "version_id": kv.get("VERSION_ID") or None,
            "codename": codename or None,
            "name": "-".join(x for x in (osid, codename) if x) or None,
        }
        self._set("base", base, path)

    def _kernel(self):
        installed = []
        mods = resolve(self.root, "/lib/modules")
        if mods and os.path.isdir(mods):
            installed = sorted(d for d in os.listdir(mods) if os.path.isdir(os.path.join(mods, d)))
        self._set("kernels", installed, "/lib/modules")
        if self.live:
            self._set("kernel", platform.release(), "uname")
        else:
            self._set("kernel", None, None)

    def _boot(self):
        """The boot FAT directory: /boot/firmware, /boot, or the ESP."""
        if self.live:
            mounts = read_text("/", "/proc/mounts") or ""
            vfat = set()
            for line in mounts.splitlines():
                f = line.split()
                if len(f) >= 3 and f[2] in ("vfat", "msdos"):
                    vfat.add(f[1])
            for cand in ("/boot/firmware", "/boot", "/boot/efi", "/efi"):
                if cand in vfat:
                    kind = "esp" if cand in ("/boot/efi", "/efi") else "firmware"
                    self._set("boot", {"path": cand, "kind": kind}, "/proc/mounts")
                    return
        for cand in ("/boot/firmware", "/boot"):
            if any(exists(self.root, cand + "/" + m) for m in _PI_BOOT_MARKERS):
                self._set("boot", {"path": cand, "kind": "firmware"}, cand)
                return
        for cand in ("/boot/efi", "/efi"):
            if isdir(self.root, cand + "/EFI"):
                self._set("boot", {"path": cand, "kind": "esp"}, cand)
                return
        self._set("boot", None, None)

    def _machine(self):
        model = self.values.get("model")
        source = self.sources.get("model")
        if model and source and "device" in source:
            for rx, machine in _PI_MODEL_RULES:
                if rx.search(model):
                    self._set("machine", machine, source)
                    return
        arch = self.values.get("architecture")
        boot = self.values.get("boot") or {}
        if arch in ("amd64", "i386") or (source or "").startswith("/sys/class/dmi"):
            uefi = exists(self.root, "/sys/firmware/efi") or boot.get("kind") == "esp"
            self._set("machine", "x86-uefi" if uefi else "x86-bios", source or "architecture")
            return
        if boot.get("kind") == "firmware":
            # A card in a reader has no device tree: a Pi, model unknown.
            self._set("machine", "rpi", boot.get("path"))
            return
        self._set("machine", None, None)

    def _data(self):
        """The data partition: by label first, then /etc/fstab's /data line, then the probes."""
        if self.live:
            for label in DATA_LABELS:
                if os.path.exists("/dev/disk/by-label/" + label):
                    self._set("data", "LABEL=" + label, "/dev/disk/by-label")
                    return
        fstab = read_text(self.root, "/etc/fstab") or ""
        for line in fstab.splitlines():
            f = line.split()
            if len(f) >= 2 and not f[0].startswith("#") and f[1].rstrip("/") == "/data":
                self._set("data", f[0], "/etc/fstab")
                return
        if self.live:
            for dev in DATA_PROBES:
                if os.path.exists(dev):
                    self._set("data", dev, "probe")
                    return
        self._set("data", None, None)

    def _graphics(self):
        machine = self.values.get("machine") or ""
        if machine.startswith("x86"):
            self._set("graphics", "x86", "machine")
            return
        if not machine.startswith("rpi"):
            self._set("graphics", None, None)
            return
        boot = (self.values.get("boot") or {}).get("path")
        text = read_text(self.root, boot + "/config.txt") if boot else None
        if text is None:
            self._set("graphics", None, None)
            return
        found = "legacy"
        include = True
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                include = _section_applies(line[1:-1].strip(), machine)
                continue
            if not include:
                continue
            m = re.match(r"dtoverlay\s*=\s*vc4-(f?)kms-v3d", line)
            if m:
                found = "fkms" if m.group(1) else "kms"
        self._set("graphics", found, boot + "/config.txt")

    def _release(self):
        text = read_text(self.root, "/etc/rastaos-release")
        self._set("release", parse_kv(text) if text else None,
                  "/etc/rastaos-release" if text else None)

    def _stack(self):
        text = read_text(self.root, "/etc/hasta/stack")
        if text:
            kv = parse_kv(text)
            self._set("stack", {"name": kv.get("STACK") or None,
                                "revision": kv.get("REVISION") or None,
                                "inferred": False}, "/etc/hasta/stack")
            return
        machine = self.values.get("machine")
        base = (self.values.get("base") or {}).get("name")
        rasta = machine == "rpi3" or (machine == "rpi" and base == "raspbian-buster")
        if rasta:
            tag = (self.values.get("release") or {}).get("RASTAOS_TAG", "")
            m = re.match(r"rastaos-(\d+\.\d+)", tag)
            if m:
                self._set("stack", {"name": "rasta", "revision": m.group(1), "inferred": True},
                          "/etc/rastaos-release")
                return
            boot = (self.values.get("boot") or {}).get("path")
            version = read_text(self.root, boot + "/VERSION") if boot else None
            m = re.match(r"\s*(7\.\d+)", version or "")
            if m:
                self._set("stack", {"name": "rasta", "revision": m.group(1), "inferred": True},
                          boot + "/VERSION")
                return
        self._set("stack", None, None)

    def as_dict(self):
        return {"root": self.root, "live": self.live,
                "facts": dict(self.values), "sources": dict(self.sources)}


def _section_applies(name, machine):
    """config.txt conditional sections: [all], [none], [piN]; any other filter is kept."""
    name = name.lower()
    if name == "all":
        return True
    if name == "none":
        return False
    m = re.match(r"pi(\d+|0w|02)?(\+)?$", name)
    if m:
        num = (m.group(1) or "")[:1]
        return machine == "rpi" + num if num else True
    return True


def compute(root="/"):
    return Facts(root)
