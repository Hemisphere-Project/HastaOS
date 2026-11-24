# Patch coverage — the field ledger against `hasta check`

From 10 to 21 September 2026 the Biennale players were patched by hand, and a ledger kept the
score: Runbooks `biennale-deploy/PATCHES.md` (private repo; read at its last change, `5c88990`,
13 November). It lists 23 patches, P1 to P23, each with a shell probe that `probe-level.sh` runs
on a player. Phase 1 ends when `hasta check` replaces that ledger. This page says, patch by patch,
how `check` sees it in this tree — or that it does not, why, and which module should own it.
**What it lists as not covered is the to-do of phase 4.**

## How to read it

`check` compares a box with a tree in three parts (`hasta/check.py`, `docs/DESIGN.md`): each
declared unit, file, link, package and boot or `fstab` line of every module on the box; every
entry of `/etc/systemd/system` no manifest claims; each module receipt against the tree. A patch
is covered when a box without it is reported, in one of these ways:

| grade | how a box without the patch shows up |
|---|---|
| **declared** | a manifest names the resource; the missing unit, file, link or line is reported by name |
| **content** | the installed file is a copy, compared byte for byte with the tree's source: an older script reads `content differs`. This is the read the ledger itself asked for on 13 November: the installed script, not the checkout |
| **receipt** | the patch is code in a module directory the box links into; it shows only as the module's receipt fingerprint differing from the tree's. It says the module is behind, not which patch is missing |
| **undeclared** | the patch replaced something; a box still carrying the old form is reported because no manifest claims it |
| **not covered** | `check` cannot tell a box with the patch from a box without it |

Three conditions hold for every row:

1. **Run it against a tree that carries the patches**, from the bench: `hasta check --tree
   <this checkout> --root <card>` or `--image <file>`. A box checked against its own checkout is
   compared with itself, so an old box reads clean on its old code.
2. **Only the modules on the box are read.** A module counts as on the box when it has a receipt
   or one of its declared files is there. A box that never got a module is not told it lacks it:
   `check` has no module set to hold it to (rows marked †, the patches that added a module).
3. **The Rasta 7.5 overlay applies only to a box the facts call Rasta 7.5.** The Biennale cards
   are 7.3: no overlay applies to them, and what the overlay declares reads there as undeclared.

A box installed before module receipts existed (the 7.3 fleet) reads `unrecorded` for each module:
not clean, but `check` cannot say which receipt-graded patch it lacks.

## The table

| patch | what (the ledger's words, short) | how `check` verifies it | grade |
|---|---|---|---|
| **P1** | HPlayer2 wallclock: chase same-duration files, cue by `NN_` prefix, multicast join retry, no-media re-check | nothing: see [A](#a-hplayer2-the-app-commit) | **not covered** |
| **P2** | HPlayer2 `f32aad3`: loop gap, volume link, end-of-file guard | nothing: see [A](#a-hplayer2-the-app-commit) | **not covered** |
| **P3** | HPlayer2 `c7db36f`: DMX loop-wrap hold, zyre waits for the sync address, clock-sync retry | nothing: see [A](#a-hplayer2-the-app-commit) | **not covered** |
| **P4** | audiohub: USB DAC level pinned at 0 dB, `usb_volume=` and a re-assert loop | `audiohub-fwd` (linked as `/usr/local/bin/audiohub-fwd`) sets the level at every forwarder start and re-asserts it; with no `usb_volume=` in the conf it pins 0 dB, so the `/etc/audiohub.conf` line is no longer needed. A conf that sets another level is config, copied only when missing: not read | receipt (audiohub) |
| **P5** | extendfs: growpart NOCHANGE no longer fails under `set -e` | the fix is in `extendfs/extendfs`, linked as `/usr/local/bin/extendfs`; `extendfs.service` enabled under `multi-user.target` is declared | receipt (extendfs) |
| **P6** | rtl8xxxu blacklist removed: WN823N dongles get a driver | nothing: see [B](#b-a-file-that-must-be-absent) | **not covered** |
| **P7** | hdmi-rehandshake v1: re-negotiate the HDMI link at boot | `hdmi-rehandshake.service` enabled under `multi-user.target` | declared (hdmi-rehandshake) |
| **P7b** | hdmi-rehandshake v2: wait up to 20 s for the display, late pass at +75/+150 s | `hdmi-rehandshake-late.timer` enabled under `timers.target`; the wait and the late pass are the script's | declared + receipt |
| **P8** | stray `wlan0-sync-AP` NetworkManager profile removed on non-masters | nothing: see [C](#c-network-profiles-and-the-boxs-role) | **not covered** |
| **P9** | hotspot on hostapd, PMF off | network-tools declares the `hostapd` package, the stock `hostapd.service` masked and the `hostapd@.service` template; PMF off is `setnet`'s renderer (no `ieee80211w` unless a profile asks `pmf=2`). `/etc/hostapd/wint.conf` and the `hostapd@wint` instance are rendered by `setnet` at boot: run-time state, not read | declared + receipt (network-tools) |
| **P10** † | apfix: restart `hostapd@wint` every 20 min while nobody is associated (opt-in) | apfix declares `apfix.timer` enabled and its script as a copy; the Rasta 7.5 overlay declares the timer disabled (opt-in since 7.5) and wins. `check` holds a box to the default: on 7.5 a box opted in reads as drift. See [D](#d-a-per-box-choice) | declared + content; the opt-in **not covered** |
| **P11** | logcap: hourly truncate of tmpfs log files above 20 MB | superseded: blackbox's per-minute `/tmp` guard truncates every file above 20 MB once the tmpfs is 90 % full; `blackbox.timer` enabled is declared. A leftover `/etc/cron.hourly/logcap` is not seen (`check` reads no cron directory) and is harmless | declared + receipt (blackbox) |
| **P12** | `usbvol.timer`: the DAC level re-set every 60 s | superseded by P4's re-assert. A box still carrying it shows `usbvol.service`, `usbvol.timer`, its `timers.target.wants` link and the `audiohub@usb.service.d/usbvol.conf` drop-in as undeclared (the 20 November rc does; `tests/test_declared.py` lists them as expected drift) | undeclared |
| **P13** † | blackbox v2: persistent journal on `/data`, one state line per minute | the state line: `blackbox.timer` enabled. The persistent journal was withdrawn by P19; a v2 leftover, `journal-persist.service` and its `sysinit.target.wants` link, is reported as undeclared | declared + undeclared |
| **P13b** | blackbox tip `de4ba42`: urb/kmiss fields, `TMPDIR` on `/data`, fsync | blackbox's script, linked as `/usr/local/bin/blackbox` | receipt (blackbox) |
| **P14** † | usbfix: re-enumerate a device on a urb storm; rsyslog stops writing kernel lines to tmpfs; since 22/09 `STORM_MIN` ≤ 20, `COOLDOWN` ≤ 90 | `usbfix.timer` enabled is declared; `/usr/local/sbin/usbfix` is a copy, so the thresholds in the script are compared. Not read: `/etc/default/usbfix`, which can override them, and `/etc/rsyslog.d/00-kern-journal-only.conf`, written inline. See [E](#e-settings-and-config-written-inline) | declared + content; partly **not covered** |
| **P15** † | linkwatch: sync-link watchdog | `linkwatch.timer` enabled is declared; `/usr/local/sbin/linkwatch` is a copy | declared + content |
| **P16** | HPlayer2 `60c5743`: slave rebuilds its node, re-joins the clock group, Nowde retry quieter | nothing: see [A](#a-hplayer2-the-app-commit) | **not covered** |
| **P17** | audiohub-fwd: the USB forwarder watches the DAC stream, kills a frozen `alsaloop` | `audiohub-fwd` linked, as in P4. A forwarder still running the old code until its next restart (the ledger's « staged ») is a process: `check` reads files | receipt (audiohub) |
| **P18** | `man-db.timer` masked: with the fake clock it runs after every boot | `man-db.timer` masked, declared by the Rasta 7.5 overlay (condition 3: on a 7.3 card the mask reads as undeclared, and its absence as nothing) | declared, on 7.5 |
| **P19** | blackbox v3: journald in RAM (32 MB), `journal-export` to `/data` every 10 min | `/etc/systemd/journald.conf.d/blackbox.conf` is a copy of `journald-ram.conf` (`Storage=volatile`); `journal-export.timer` enabled is declared | declared + content |
| **P19b** | blackbox v3b: the state line lives on `/run`, reaches `/data` only through the export | blackbox's script | receipt (blackbox) |
| **P20** | HPlayer2 `f87feec` (with `24288eb`, `bb65bd9`): fresh-socket re-join, one delimiter thread, boot fallback, handler race | nothing: see [A](#a-hplayer2-the-app-commit) | **not covered** |
| **P21** | linkwatch v2: master-side deauth opt-in, `MASTER_DEAUTH=1` | the opt-in default is in `/usr/local/sbin/linkwatch`, a copy. `/etc/default/linkwatch`, where a box would set it, is not read: see [E](#e-settings-and-config-written-inline) | content |
| **P22** | = P18 fleet-wide | as P18 | declared, on 7.5 |
| **P23** | `config_hdmi_boost=7` in `config.txt` (HDMI drive strength): rare horizontal image shifts at 1080p50 | nothing: see [F](#f-a-configtxt-line-no-manifest-names) | **not covered** |

**Count.** 15 of the 23 are covered, P10 and P14 only in part. Not covered: the five HPlayer2
patches (P1, P2, P3, P16, P20), P6, P8 and P23.

## Why not, and who should own it

### A. HPlayer2: the app commit

**P1, P2, P3, P16, P20.** These patches are HPlayer2 commits, and nothing `check` reads names an
HPlayer2 commit. `--app DIR` reads an app through a `module.ini` at its root, and HPlayer2's
`master` carries none (read on 24 November 2026), so `--app /opt/HPlayer2` is refused. The release
receipt, `/etc/rastaos-release`, names the Pi-tools commit only. The Rasta 7.5 overlay lists the
bake's `hplayer2` step, which moves `/opt/HPlayer2` to the release tag, among what schema 1
cannot declare.

**Owner: HPlayer2.** An app manifest at its root, a receipt of the commit its install leaves, and
the Rasta overlay naming the app revision a Rasta release pins. `check` then reports an HPlayer2
behind the pinned revision, which covers all five rows at once.

### B. A file that must be absent

**P6.** The fix is a deletion: network-tools' hook (`setup/installer.py`) removes
`/etc/modprobe.d/blacklist-rtl8xxxu.conf` wherever it runs. Schema 1 has no form for « this file
must not exist », and `check` reads nothing under `/etc/modprobe.d`. network-tools lists the step in
its `undeclared`.

**Owner: network-tools**, once the schema has an absent-file form (the engine's). The same form
would let the overlay declare what it now lists as undeclared (the `fake-hwclock` cron, the fleet
hotspot profile) and blackbox declare v2's `journal-persist.service` gone.

### C. Network profiles and the box's role

**P8.** Since 7.5, `setnet` renders `<boot>/wifi` into NetworkManager's store at each run and
moves everything else to `/etc/NetworkManager/setnet-pruned` (its code is network-tools', so
covered by its receipt). A stray profile can therefore only come back from `<boot>/wifi`. Whether
a box should hold a `wlan0-sync-AP` profile depends on its role, master or not, which is fleet
data `check` does not have. The store itself is run-time state `check` does not read.

**Owner: network-tools**, with a check of its own: the store is the render of `<boot>/wifi`.
The profile list per role comes from the fleet layer, once a fleet declares roles.

### D. A per-box choice

**P10.** apfix is opt-in: setup installs it only with `apfix = yes` in `pitools.txt` (the
installer's default is `no`), the 7.5 bake unwires its timer on the image, and a card that keeps
its hotspot runs `apfix/install.sh --now`. `check` holds every 7.5 box to the overlay's
`disabled`, so the masters and solo players that opted in read as drift. (A later field note found
that apfix does not cure the stale hotspot; whether to keep it is not this page's call.)

**Owner: the Rasta overlay, with G.** Once a role says whether apfix is in its module set, the
overlay's `disabled` applies only to a box where it is not, and apfix's own `enabled` holds where it is.

### E. Settings and config written inline

**P14, and P21's opt-in.** usbfix reads `/etc/default/usbfix` after the thresholds in its
script, so a box can set `STORM_MIN` and `COOLDOWN` back to 100 and 300 there and still read clean.
Its installer writes `/etc/rsyslog.d/00-kern-journal-only.conf` and comments out `imklog`
inline, so neither is declared. On Rasta 7.5 rsyslog is masked by the overlay, which makes the
rsyslog half moot there. linkwatch reads `/etc/default/linkwatch` the same way; its installer
writes none, so the opt-in `MASTER_DEAUTH=1` is a hand edit nobody sees.

**Owner: usbfix** (the rsyslog drop-in as a file with a source in the tree; the thresholds as
`options`, so `/etc/default/usbfix` is written from them and compared) and **linkwatch** (the same
for `MASTER_DEAUTH`).

### F. A `config.txt` line no manifest names

**P23.** The 7.x image lineage carries `config_hdmi_boost=7` in the `[pi3+]` section of
`config.txt`; no recipe writes it, and no manifest declares it. `check` already reads `config.txt`
lines by section.

**Owner: the Rasta overlay** (`stacks/rasta/7.5/module.toml`): a `config` line, `section =
"pi3+"`, present. The ledger made it the default for new cards (« golden default from now on »); a
display that needs another value is a per-unit setting, like the HDMI mode below.

### G. The module set (rows †)

P10, P13, P14 and P15 each added a module. `check` reads only the modules a box has, so a box
that never got linkwatch reads clean on linkwatch. `plan` and `apply` take `--modules LIST`;
`check` does not, and no role names its module set yet.

**Owner: the fleet layer** (role → module set). Until then, a `--modules` on `check` matching
`plan`'s would let the bench hold a card to a named set.

## Outside P1–P23

The ledger also counts four things « not code, but part of a player's level »: cue naming across a
synced group, the HDMI mode per display, the group SSID per site, and dongle health. They are fleet
data with no carrier in this tree; they belong to the fleet layer, not to a module.

## Phase 4, from this page

1. HPlayer2's app manifest and commit receipt, and the overlay pinning its revision (A: five rows).
2. An absent-file form in the schema, then network-tools declares the rtl8xxxu blacklist absent (B).
3. network-tools: `check` reads the NetworkManager store against `<boot>/wifi`; roles from fleets (C).
4. apfix's opt-in: the overlay's `disabled` yields to a role that selects apfix (D, with G).
5. usbfix and linkwatch: their `/etc/default` settings and the rsyslog drop-in declared (E).
6. The Rasta overlay declares `config_hdmi_boost=7` under `[pi3+]` (F).
7. A module set to hold a box to: `check --modules` now, the fleet's roles later (G).
