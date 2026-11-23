"""Rasta 7.5: the bake's steps that make a 7.4 card a 7.5 one, as the engine runs them (#t-010).

A port of image/rastaos-bake's `stamps` to `starter`, step for step: the same files, the same
bytes, the same refusals, so that `hasta apply --stack rasta/7.5` and the bake leave identical
trees (tests/test_stack.py holds them to it). Each step is what module.toml's `undeclared` list
names: what schema 1 cannot say yet. The declared part (the masks, the lines, the files) is what
`hasta check` holds the result to.

Not here: `converge` and `hplayer2` move the checkouts to a release tag (rastaos-update's and the
app's); `seal` is `hasta seal`'s. Read by hasta/stack.py, which gives each step a context `c`:
c.r(path) the path inside the target root, c.boot / c.data the boot and data partitions, c.live,
c.rev the recipe's sha, c.opts (nowde, mire), c.did(msg) for a change, c.say(msg) for the rest.
A step returns False when it failed.

Standard library only; runs on Python 3.7.
"""

import filecmp
import glob
import os
import re
import shutil
import subprocess
import time

STEPS = ("stamps", "version", "units", "rtc", "turbo", "apfix", "dns", "wifi", "nowde",
         "media", "starter")
# the bake's own step list, as its stamps line names it
BAKE_STEPS = "converge hplayer2 stamps version units rtc turbo apfix dns wifi nowde media starter"
STAMP = "rastaos75"
MASK_UNITS = ("fake-hwclock.service", "rpi-eeprom-update.service", "nfs-client.target",
              "rsync.service")
HOST, PLAYER, HPLAYER = "player-000", "hplayer2@auto", "/opt/HPlayer2"
NOWDE = "2.0.5"
MIRE = "0_mire.mp4"
UNIT_DIR = "/etc/systemd/system"
HERE = os.path.dirname(os.path.abspath(__file__))
SPACE = "[ \t\n\v\f\r]"       # sed's [[:space:]]


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "surrogateescape")
    except (IOError, OSError):
        return None


def _write(path, text, mode=None):
    """Into the file in place, as a shell redirection does: an existing file keeps its mode."""
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)


def _lines(text):
    """The lines awk and sed see: a last line without its newline is still a line."""
    if not text:
        return []
    return text[:-1].split("\n") if text.endswith("\n") else text.split("\n")


def _shell_value(text):
    """`$(cat f)`: every trailing newline stripped."""
    return (text or "").rstrip("\n")


def _systemctl(*args):
    subprocess.run(("systemctl",) + args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wants(c, unit, kinds=(".wants", ".requires")):
    """Every `*.wants/<unit>` (then every `*.requires/<unit>`) link, in the shell's glob order."""
    s = c.r(UNIT_DIR)
    out = []
    for k in kinds:
        out += sorted(p for p in glob.glob(os.path.join(glob.escape(s), "*" + k, unit))
                      if os.path.lexists(p))
    return out


# ---------------------------------------------------------------- the steps

def step_stamps(c):
    f = c.r("/etc/rastaos-patches.txt")
    have = _read(f) if os.path.isfile(f) else None
    lines = _lines(have)
    anna = [i for i, ln in enumerate(lines) if ln.startswith("anna74 ")]
    last = anna[-1] if anna else -1

    def first(ln):
        return (ln.split() or [""])[0]

    keep = [ln for i, ln in enumerate(lines)
            if not (first(ln) == "anna74" and i != last) and first(ln) != STAMP]
    keep.append("%s %s: RastaOS-7.5 from 7.4 — Pi-tools image/rastaos-bake@%s (steps: %s)"
                % (STAMP, c.today, c.rev, BAKE_STEPS))
    want = "".join(ln + "\n" for ln in keep)
    # its recipe sha counts, the date alone never does
    nodate = re.compile(r"^%s [0-9]{4}-[0-9]{2}-[0-9]{2}:" % STAMP, re.M)
    if have is not None and nodate.sub(STAMP + " D:", have) == nodate.sub(STAMP + " D:", want):
        return True
    before = sum(1 for ln in lines if ln.startswith("anna74 "))
    _write(f, want, 0o644)
    c.did("stamps: anna74 lines %d → %d, one %s line"
          % (before, sum(1 for ln in keep if ln.startswith("anna74 ")), STAMP))
    return True


def step_version(c):
    # the WHOLE line is the check, sha included: a re-bake from a later recipe re-stamps it
    f = os.path.join(c.boot, "VERSION")
    want = "7.5 -- RastaOS-7.5, baked from a 7.4 card by Pi-tools image/rastaos-bake@%s" % c.rev
    was = (_lines(_read(f)) or [""])[0]
    if was == want:
        return True
    _write(f, want + "\n")
    if "rastaos-bake@" in was:
        was = "7.5 @" + was.rsplit("@", 1)[1]
    c.did("version: /boot/VERSION '%s' → 7.5 @%s" % ((was or "absent")[:60], c.rev))
    return True


def step_units(c):
    s = c.r(UNIT_DIR)
    os.makedirs(s, exist_ok=True)
    for u in MASK_UNITS:
        for w in _wants(c, u):
            os.remove(w)
            c.did("units: %s unwired from %s" % (u, os.path.basename(os.path.dirname(w))))
        p = os.path.join(s, u)
        if _readlink(p) != "/dev/null":
            if os.path.exists(p) and not os.path.islink(p):
                c.say("WARN: %s is a real file, not masking it" % p)
                continue
            if os.path.lexists(p):
                os.remove(p)
            os.symlink("/dev/null", p)
            c.did("units: %s masked" % u)
    # fake-hwclock's hourly save writes /etc on a read-only root: it can only fail
    cron = c.r("/etc/cron.hourly/fake-hwclock")
    if os.path.exists(cron):
        os.remove(cron)
        c.did("units: cron.hourly/fake-hwclock removed")
    if c.live and c.changed:
        _systemctl("daemon-reload")
    return True


def _comment_out(c, key, note, what):
    f = os.path.join(c.boot, "config.txt")
    text = _read(f) or ""
    pat = re.compile(r"^%s*(%s.*)$" % (SPACE, re.escape(key)))
    lines = text.split("\n")
    if not any(pat.match(ln) for ln in lines):
        return True
    _write(f, "\n".join(pat.sub(lambda m: "#%s    # %s" % (m.group(1), note), ln)
                        for ln in lines))
    c.did(what)
    return True


def step_rtc(c):
    return _comment_out(c, "dtoverlay=i2c-rtc",
                        "per card: uncomment on a board WITH the DS3231 (rastaos 7.5)",
                        "rtc: i2c-rtc overlay commented out in config.txt (per card)")


def step_turbo(c):
    return _comment_out(c, "force_turbo=1",
                        "per card: uncomment to pin the max clock (rastaos 7.5)",
                        "turbo: force_turbo=1 commented out in config.txt (per card)")


def step_apfix(c):
    for unit in ("apfix.timer", "apfix.service"):
        for w in _wants(c, unit, kinds=(".wants",)):
            os.remove(w)
            c.did("apfix: %s unwired from %s (opt-in module, off by default)"
                  % (unit, os.path.basename(os.path.dirname(w))))
    if c.live:
        _systemctl("stop", "apfix.timer")
    return True


def step_dns(c):
    want = "nameserver 1.1.1.1\nnameserver 1.0.0.1"
    f = c.r("/etc/resolv.conf")
    if c.live:
        for p in ("openresolv", "resolvconf"):
            if subprocess.run(["dpkg", "-s", p], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0:
                env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
                if subprocess.run(["dpkg", "--purge", p], stdout=subprocess.DEVNULL,
                                  env=env).returncode == 0:
                    c.did("dns: %s purged" % p)
    if os.path.islink(f) or _shell_value(_read(f)) != want:
        if os.path.lexists(f):
            os.remove(f)
        _write(f, want + "\n", 0o644)
        c.did("dns: static resolv.conf (1.1.1.1, 1.0.0.1)")
    nm = c.r("/etc/NetworkManager")
    confs = [os.path.join(nm, "NetworkManager.conf")] + sorted(
        glob.glob(os.path.join(glob.escape(nm), "conf.d", "*.conf")))
    if not any("dns=none" in _lines(_read(p)) for p in confs):
        os.makedirs(os.path.join(nm, "conf.d"), exist_ok=True)
        _write(os.path.join(nm, "conf.d", "90-rastaos-dns.conf"), "[main]\ndns=none\n")
        c.did("dns: NetworkManager dns=none (conf.d/90-rastaos-dns.conf)")
    return True


def step_wifi(c):
    for p in (os.path.join(c.boot, "wifi", "wint-hotspot.nmconnection"),
              c.r("/etc/NetworkManager/system-connections/wint-hotspot.nmconnection")):
        if os.path.exists(p):
            os.remove(p)
            c.did("wifi: %s removed (fleet hotspot PSK; hotspot off by default)" % c.show(p))
    d = c.r("/etc/NetworkManager/system-connections/_anna74-removed")
    if os.path.isdir(d):
        shutil.rmtree(d) if not os.path.islink(d) else os.remove(d)
        c.did("wifi: NM store _anna74-removed/ removed")
    # a scan with a fresh random MAC re-inits the idle dongle's chip and stalls the player
    src = os.path.join(HERE, "files", "91-rastaos-scan.conf")
    f = c.r("/etc/NetworkManager/conf.d/91-rastaos-scan.conf")
    if not (os.path.isfile(f) and filecmp.cmp(src, f, shallow=False)):
        try:
            os.makedirs(os.path.dirname(f), exist_ok=True)
            if os.path.lexists(f):
                os.remove(f)
            shutil.copyfile(src, f)
            os.chmod(f, 0o644)
        except OSError:
            c.say("wifi: could not write %s" % f)
            return False
        c.did("wifi: NM scans keep the MAC (91-rastaos-scan.conf)")
    return True


def step_nowde(c):
    d = c.r("/opt/nowde-tools")
    if not os.path.isdir(d):
        return True
    ver = c.opts.get("nowde") or NOWDE
    if not glob.glob(os.path.join(glob.escape(d), "nowde-%s-*.bin" % glob.escape(ver))):
        c.say("nowde: no %s image in %s — nothing removed (--nowde <version>)" % (ver, d))
        return False
    for f in sorted(glob.glob(os.path.join(glob.escape(d), "nowde-*.bin"))):
        if os.path.basename(f).startswith("nowde-%s-" % ver):
            continue
        os.remove(f)
        c.did("nowde: %s removed (one firmware: %s)" % (os.path.basename(f), ver))
    return True


def step_media(c):
    m = os.path.join(c.data, "media")
    mire = c.opts.get("mire")
    if not os.path.isdir(m):
        c.say("media: no %s%s" % (m, "" if c.live else
                                  " — pass --data <the card's p3, mounted>"))
        return False
    if mire:
        if not (os.path.isfile(mire) and os.path.getsize(mire) > 0):
            c.say("media: --mire %s is not a non-empty file — nothing removed" % mire)
            return False
    elif not (os.path.isfile(os.path.join(m, MIRE))
              and os.path.getsize(os.path.join(m, MIRE)) > 0):
        c.say("media: no %s on the card and no --mire FILE (a 7.3 card's %s) — nothing removed"
              % (MIRE, MIRE))
        return False
    gone = sorted(n for n in os.listdir(m) if n != MIRE)
    if gone:
        size = 0
        for n in gone:
            p = os.path.join(m, n)
            if os.path.isdir(p) and not os.path.islink(p):
                for top, _, files in os.walk(p):
                    size += sum(os.lstat(os.path.join(top, x)).st_size for x in files)
                shutil.rmtree(p)
            else:
                size += os.lstat(p).st_size
                os.remove(p)
        c.did("media: %d show file(s) removed from %s (%.1fM): %s"
              % (len(gone), m, size / 1048576.0, " ".join(gone)[:160]))
    if mire and not (os.path.isfile(os.path.join(m, MIRE))
                     and filecmp.cmp(mire, os.path.join(m, MIRE), shallow=False)):
        shutil.copyfile(mire, os.path.join(m, MIRE))
        os.chmod(os.path.join(m, MIRE), 0o644)
        c.did("media: %s from %s" % (MIRE, mire))
    for x in sorted(glob.glob(os.path.join(glob.escape(c.data), "hplayer2*.cfg"))):
        if os.path.isfile(x):
            os.remove(x)
            c.did("media: saved player settings %s removed (/boot seeds apply only without one)"
                  % os.path.basename(x))
    return True


def step_starter(c):
    f = os.path.join(c.boot, "starter.txt")
    hp = c.r(HPLAYER)
    if not os.path.isfile(f):
        c.say("starter: no %s" % f)
        return False
    if "def pick_profile" not in (_read(os.path.join(hp, "core", "engine", "bootconf.py")) or ""):
        c.say("starter: %s has no 'auto' profile (hplayer2#t-101) — move it to the release"
              " first; starter.txt left as is" % HPLAYER)
        return False
    # the FIRST active hostrename@ / hplayer2@ line becomes the generic one, later active ones
    # go; comments stay (the bake's awk, `[ \t]` and not [[:space:]]: the card's mawk has none)
    text = _read(f)
    out, h, p = [], 0, 0
    for ln in _lines(text):
        if re.match(r"^[ \t]*hostrename@", ln):
            if not h:
                out.append("hostrename@" + HOST)
            h += 1
        elif re.match(r"^[ \t]*hplayer2@", ln):
            if not p:
                out.append(PLAYER)
            p += 1
        else:
            out.append(ln)
    if not h:
        out.append("hostrename@" + HOST)
    if not p:
        out.append(PLAYER)
    want = "".join(ln + "\n" for ln in out)
    if want != text:
        was = " ".join(ln for ln in _lines(text)
                       if re.match(r"^%s*(hostrename|hplayer2)@" % SPACE, ln))
        _write(f, want)
        c.did("starter: '%s' → 'hostrename@%s %s'" % (was, HOST, PLAYER))
    conf = os.path.join(c.boot, "hplayer2.conf")
    if os.path.exists(conf):
        os.remove(conf)
        c.did("starter: /boot/hplayer2.conf removed (a role file; the base image has none)")
    x = c.r("/etc/hostname")
    if _shell_value(_read(x)) != HOST:
        _write(x, HOST + "\n")
        c.did("starter: /etc/hostname = %s" % HOST)
    x = c.r("/etc/hosts")
    hosts = _read(x) if os.path.isfile(x) else None
    if hosts is not None:
        lines = hosts.split("\n")
        if (any(ln.startswith("127.0.1.1") for ln in lines)
                and "127.0.1.1\t" + HOST not in _lines(hosts)):
            _write(x, "\n".join("127.0.1.1\t" + HOST if ln.startswith("127.0.1.1") else ln
                                for ln in lines))
            c.did("starter: /etc/hosts 127.0.1.1 = %s" % HOST)
    return True


def _readlink(path):
    try:
        return os.readlink(path)
    except OSError:
        return None


def today():
    return time.strftime("%Y-%m-%d")
