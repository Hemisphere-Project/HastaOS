"""An image file opened for reading: `hasta check --image FILE`.

The partitions are found in the file's MBR or GPT table (a file holding one bare filesystem is
its own single partition) and given a role by filesystem and label: the FAT is the boot
partition, the ext partition labelled `data`/`DATA` the data partition, and the root is the ext
partition labelled `rootfs` (else the first ext one that is not the data partition).

Run as root, each partition is loop-mounted read-only (ext without replaying its journal).
Otherwise the ext partitions are read through `debugfs` into a temporary directory — the root's
/etc whole, the dpkg status, and each path a manifest of the tree declares — and the FAT
through `mtools` when it is installed; without mtools the boot partition is not read, and a
notice says so. Either way the result is a root with its boot and data partitions mapped at
the points the box mounts them (facts.mounted), and `close()` undoes it all. Nothing is ever
written to the image.

Standard library only; runs on Python 3.7.
"""

import os
import shutil
import struct
import subprocess
import tempfile

from hasta import facts as facts_mod

SECTOR = 512
ROOT_LABELS = ("rootfs", "root", "linux-root")
_GPT_EMPTY = b"\0" * 16
# What the facts and the check read on the root beyond the declared paths.
ROOT_PATHS = ("/etc", "/var/lib/dpkg/status", "/usr/lib/os-release")


class Partition(object):
    def __init__(self, number, start, size):
        self.number, self.start, self.size = number, start, size
        self.fs, self.label, self.role = None, None, None

    def as_dict(self):
        return {"number": self.number, "start": self.start, "size": self.size, "fs": self.fs,
                "label": self.label, "role": self.role}


def _read(fh, offset, n):
    fh.seek(offset)
    return fh.read(n)


def table(fh, file_size):
    """The partitions of an image: GPT, else MBR, else the whole file as one."""
    mbr = _read(fh, 0, SECTOR)
    if len(mbr) == SECTOR and mbr[510:512] == b"\x55\xaa":
        entries = [mbr[446 + 16 * i:462 + 16 * i] for i in range(4)]
        if any(e[4] == 0xEE for e in entries):
            return _gpt(fh)
        out = []
        for i, e in enumerate(entries):
            ptype = e[4]
            lba, count = struct.unpack("<II", e[8:16])
            if ptype and count and ptype not in (0x05, 0x0F, 0x85):  # extended: not read
                out.append(Partition(i + 1, lba * SECTOR, count * SECTOR))
        if out:
            return out
    return [Partition(0, 0, file_size)]


def _gpt(fh):
    head = _read(fh, SECTOR, 92)
    if head[:8] != b"EFI PART":
        return []
    lba, count, esize = struct.unpack("<QII", head[72:88])
    out = []
    for i in range(count):
        e = _read(fh, lba * SECTOR + i * esize, esize)
        if len(e) < 48 or e[:16] == _GPT_EMPTY:
            continue
        first, last = struct.unpack("<QQ", e[32:48])
        out.append(Partition(i + 1, first * SECTOR, (last - first + 1) * SECTOR))
    return out


def _text(raw):
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace").strip() or None


def identify(fh, part):
    """The partition's filesystem (`ext`, `vfat`, or None) and label."""
    sb = _read(fh, part.start + 1024, 136)
    if len(sb) == 136 and sb[0x38:0x3A] == b"\x53\xef":
        part.fs, part.label = "ext", _text(sb[0x78:0x88])
        return
    bs = _read(fh, part.start, SECTOR)
    if len(bs) == SECTOR and bs[510:512] == b"\x55\xaa":
        if bs[0x52:0x57] == b"FAT32":
            part.fs, part.label = "vfat", _text(bs[0x47:0x52])
        elif bs[0x36:0x39] == b"FAT":
            part.fs, part.label = "vfat", _text(bs[0x2B:0x36])
        if part.label == "NO NAME":
            part.label = None


def roles(parts):
    """Set each partition's role: boot, root, data (or leave None)."""
    ext = [p for p in parts if p.fs == "ext"]
    for p in ext:
        if p.label in facts_mod.DATA_LABELS:
            p.role = "data"
            break
    root = [p for p in ext if p.label in ROOT_LABELS and p.role is None]
    root = root or [p for p in ext if p.role is None]
    if root:
        root[0].role = "root"
    fat = [p for p in parts if p.fs == "vfat"]
    if fat:
        fat[0].role = "boot"


def partitions(path):
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        parts = table(fh, size)
        for p in parts:
            identify(fh, p)
    roles(parts)
    return parts


class Opened(object):
    """The image's root, with its boot and data partitions mapped; `close()` releases it."""

    def __init__(self, path, declared=(), as_root=None):
        self.path = os.path.abspath(path)
        self.parts = partitions(self.path)
        self.notices = []
        self.mounts = []
        self.tmp = tempfile.mkdtemp(prefix="hasta-image-")
        self.as_root = (os.geteuid() == 0) if as_root is None else as_root
        by_role = dict((p.role, p) for p in self.parts if p.role)
        if "root" not in by_role:
            self.close()
            raise ValueError("%s: no ext partition to read as the root" % path)
        try:
            self.dirs = {}
            for role in ("root", "boot", "data"):
                if role in by_role:
                    self.dirs[role] = self._open(by_role[role], role, declared)
        except Exception:
            self.close()
            raise
        self.root = facts_mod.mounted(self.dirs["root"], boot=self.dirs.get("boot"),
                                      data=self.dirs.get("data"))

    def _open(self, part, role, declared):
        d = os.path.join(self.tmp, role)
        os.mkdir(d)
        if self.as_root:
            opts = "ro,loop,offset=%d,sizelimit=%d" % (part.start, part.size)
            if part.fs == "ext":
                opts += ",noload"  # never replay a journal into the image
            subprocess.run(["mount", "-o", opts, self.path, d], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.mounts.append(d)
            return d
        if part.fs == "vfat":
            if shutil.which("mcopy") is None:
                self.notices.append("boot partition (p%d) not read: mtools is not installed"
                                    % part.number)
                os.rmdir(d)
                return None
            subprocess.run(["mcopy", "-s", "-n", "-p", "-i", "%s@@%d" % (self.path, part.start),
                            "::/", d], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return d
        if shutil.which("debugfs") is None:
            raise ValueError("debugfs is not installed: cannot read %s without root" % self.path)
        self._debugfs(part, d, ROOT_PATHS + tuple(declared) if role == "root" else declared,
                      role)
        return d

    def _debugfs(self, part, d, paths, role):
        """rdump each wanted path of the partition into `d`, at its path. Paths absent from the
        partition are skipped (debugfs only complains)."""
        cmds = []
        prefix = facts_mod.DATA_POINT if role == "data" else ""
        wanted = sorted(set(p.rstrip("/") or "/" for p in paths))
        # a path under another wanted path comes with it: rdump refuses a directory whose
        # destination already exists, so none is made for it
        wanted = [p for p in wanted
                  if not any(p.startswith(q.rstrip("/") + "/") for q in wanted if q != p)]
        for p in wanted:
            if prefix:
                if not (p == prefix or p.startswith(prefix + "/")):
                    continue
                p = p[len(prefix):] or "/"
            elif p.startswith(facts_mod.DATA_POINT + "/"):
                continue
            parent = os.path.dirname(p.rstrip("/")) or "/"
            dest = os.path.join(d, parent.lstrip("/"))
            if not os.path.isdir(dest):
                os.makedirs(dest)
            cmds.append('rdump "%s" "%s"' % (p, dest))
        if not cmds:
            return
        script = os.path.join(self.tmp, "%s.debugfs" % role)
        with open(script, "w") as fh:
            fh.write("\n".join(cmds) + "\n")
        subprocess.run(["debugfs", "-f", script, "%s?offset=%d" % (self.path, part.start)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def as_dict(self):
        return {"image": self.path, "partitions": [p.as_dict() for p in self.parts],
                "read": "mounted" if self.as_root else "extracted", "notices": list(self.notices)}

    def close(self):
        for d in reversed(self.mounts):
            subprocess.run(["umount", d], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.mounts = []
        shutil.rmtree(self.tmp, ignore_errors=True)
