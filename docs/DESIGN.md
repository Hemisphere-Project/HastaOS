# Hasta — design

Hasta is the platform layer of HastaOS: **one engine and one module contract** that converge a
fresh Debian-family OS, upgrade a living box, build an image and report drift, for headless media
appliances (players, kiosks, NDI boxes) on Raspberry Pi 3 and 4 and on x86 minis. It replaces the
RastaOS / NastaOS image lineages: what a box is stops being an image's history and becomes the
modules it declares.

This file is the contract. A box and a code session can read this repo and nothing else, so
everything a change needs to respect is here.

## 1. Principles

1. **One trunk, two doors.** Building a fresh OS and moving a living box forward run the same
   module code. A change exists once it is in a module; anything else on a box is drift, and drift
   is detectable.
2. **Sealing refuses undeclared drift.** A box cannot be captured as an image, nor a fleet recorded
   as "at release X", while `check` shows state that no module declares.
3. **Behaviour is data.** Fleet → role → unit. A fleet's behaviour survives an OS change because it
   never lived in the OS.
4. **A box says what it is in one place.** One identity file on the FAT partition; one receipt
   directory on the root (`/etc/hasta/`).
5. **Hardware and OS knowledge is resolved once.** Modules ask the engine for facts (where the FAT
   partition is, which graphics stack, where the data partition is) instead of probing.
6. **Nothing from the network at run time.** Every outside input is pinned in the release lock and
   carried in the offline bundle. No `hasta` verb opens a connection.
7. **A power cut at any instant is survivable.** Steady state writes nothing to root or boot;
   updates are staged, flipped and resumable.
8. **Field-proven beats new.** rorw, flightbox, extendfs and their lessons stay the implementation;
   the contract around them changes first, their insides later and only where a defect was found.
9. **The compatibility window is written down**, per release.
10. **Tests gate releases**, including power cuts on real hardware.
11. **A qualified stack is pinned as a whole.** Where performance depends on an exact combination
    of OS release, kernel and firmware, graphics driver and player build, that combination is one
    named, versioned thing, and a platform upgrade never changes it by side effect.

## 2. Layers and words

| Layer | What it is | Examples |
|---|---|---|
| **base** | An OS release, pinned by name, date, checksum | `raspbian-buster` (armhf, 2023-05-03), `debian-trixie` |
| **machine** | How this hardware boots, partitions, names its interfaces | `rpi3`, `rpi4`, `x86-uefi` |
| **stack** | The qualified media combination for a machine and a use: base + kernel and firmware + graphics driver + player build, with its measurements | **Rasta** = Pi 3B+, Buster, legacy firmware, mmal, mpv 0.33 on ffmpeg 4.4 (revision 7.5). **Nasta** = x86, Debian 13, distro mpv and VAAPI |
| **core** | What every appliance gets: storage, identity, clock, logging, quiet boot, boot list, updates, the device agent | rorw, extendfs, flightbox, blackbox, datesync, splash, starter |
| **feature** | Optional platform services | network roles, USB automount, audio hub, web UIs, Tailscale, synczinc |
| **app** | A product from its own repo, carrying its own manifest | HPlayer2, HKiosk, HNdi, Régie |
| **fleet** | Machines, modules, options, app versions, roles, media policy | kept in a private fleets repo |
| **role** | A named option overlay inside a fleet | `wired-master`, `wifi-slave` |
| **unit** | One device: name, number, role, overrides | `player-012`, `kmini-003` |

A **target** is a machine on a stack (`rpi3` on Rasta, `x86-uefi` on Nasta). Options resolve in
this order, later wins: module default → base → machine → stack → fleet → role → unit → local
override on the box.

A tight stack (Rasta) is frozen whole and still gets a rebuildable recipe; a loose stack (Nasta) is
a thin pin on a stock base. "Hasta 1.0 on Rasta 7.5" names both things a box needs to say.

## 3. The module contract

Every module — platform, app or fleet overlay — is a directory with a manifest and five verbs.

| Verb | Meaning |
|---|---|
| `check` | Read-only. Is the box converged for this module? Lists every difference. |
| `apply` | Converge. Safe to run at any time, twice in a row, and after any interruption. |
| `remove` | Undo. |
| `seal` | Reset clone-bound state (identity, keys, locks, logs). |
| `status` | One JSON object for the dashboard, blackbox and the bench tools. |

**Migration without a big bang.** The engine first runs today's modules unchanged through a
*legacy driver* (`module.ini` + `install.sh` / `upgrade.sh`); modules are then ported one at a time
and the trunk stays shippable throughout. A `module.toml` beside a `module.ini` says what the module
leaves on a box; the `.ini` keeps saying how it is installed today.

## 4. One engine, four situations

Four separate mechanisms change a box today (`setup.sh`, `setup/converge.py`, `image/rastaos-bake`
and the field `stamp`). They become one resolve → plan → apply → receipt cycle, run in four
situations:

| Situation | Input | Replaces |
|---|---|---|
| **build** | a fresh base, live or as a mounted image | `setup.sh` + `bootstrap.py` |
| **adopt / upgrade** | a living box at an older release | `rastaos-update` + `converge.py` + the bake's fix-ups |
| **stamp** | a burned card or disk plus a unit id | the field stamp and card-prep scripts |
| **check** | a box, a card in a reader, or an image file | the level probes and the field patch ledger |

So **the bake dissolves** (every step it does by hand becomes module state, and a box upgraded in
place gets it too), and **equivalence is testable** (a target built fresh and a box moved forward
compare equal, or every difference is declared). On a bench box the release directory may be a git
checkout, so live work is a diff to commit; `seal` refuses that box until `check` is clean.

## 5. Configuration

- **One identity file on the FAT partition** (`unit.conf`): fleet, revision, unit name and number,
  role, per-unit options, re-applied whenever it changes.
- Today's files (`starter.txt`, `wifi/*.nmconnection`, `hplayer2.conf`, `kiosk.url`…) stay, as
  rendered outputs. A hand edit is a local override: honoured, reported by `check`, never silently
  overwritten.
- **One resolver for the FAT directory**: no module hard-codes `/boot`, `/boot/firmware` or
  `/boot/efi` again — it asks `hasta facts`.
- A small **device agent** in core exposes settings (the module options a unit may change,
  validated against the manifests), status, events and a few actions over one local API. A set is a
  local override written to the identity file. Boot-to-play never waits on it.

## 6. The engine today

`bin/hasta` (or `python3 -m hasta` from the repo root). Every command takes `--json`. Exit 0 ok,
1 a finding (an invalid manifest), 2 usage.

Rules every change to the engine keeps:

- **Python standard library only, and it runs on Python 3.7** (Rasta's Buster userland) as well as
  on current Debian: no walrus, no `match`, no `dict | dict`, no positional-only markers, no
  `tomllib` without the fallback. `tests/test_py37.py` and the 3.7 run in `bench/verify` hold this.
- **No module directory moves or is renamed** while Pi-tools keeps merging in.
- **Nothing a box runs changes** until `hasta apply` exists: the engine reads, it does not write.
- Names: command `hasta`, package `hasta/`, receipts under `/etc/hasta/`.

### `hasta facts [--root DIR]`

What the box is. Every fact is read from files under a root, so the same code answers on a live
box and on a tree (`--root`: a fixture, a card in a reader). Facts that only exist on a running
system (running kernel, mounts, `/dev/disk/by-label`) are read only when the root is `/`. Each fact
carries its `source`.

| Fact | Read from, in order |
|---|---|
| `model` | `/proc/device-tree/model`, `/sys/firmware/devicetree/base/model`, else DMI vendor + product |
| `machine` | the Pi model (`rpi0`…`rpi5`; Zero 2 = `rpi3`); DMI or an x86 userland → `x86-uefi` (`/sys/firmware/efi` or an ESP) / `x86-bios`; a FAT boot dir with Pi firmware and no device tree (a card in a reader) → `rpi` |
| `architecture` | dpkg's `base-files` entry (the userland: a 32-bit Buster on a 64-bit kernel is `armhf`), else the ELF header of `/bin/sh`, else `uname` (live only) |
| `base` | `/etc/os-release` (or `/usr/lib/os-release`): `id`, `version_id`, `codename`, `name` = `<id>-<codename>` |
| `kernel` / `kernels` | `uname` (live only) / the directories of `/lib/modules` |
| `boot` | live: the vfat mount among `/boot/firmware`, `/boot`, `/boot/efi`; else by content: Pi firmware files in `/boot/firmware` then `/boot`, an `EFI/` in `/boot/efi`. `{path, kind: firmware|esp}` |
| `data` | `/dev/disk/by-label/data` (live), then the `/data` line of `/etc/fstab`, then the installers' device probes (live) |
| `graphics` | `x86` on x86; on a Pi, the `vc4-kms-v3d` / `vc4-fkms-v3d` overlay in the boot dir's `config.txt` (`[all]`, `[none]`, `[piN]` sections honoured) → `kms` / `fkms`, else `legacy` |
| `release` | `/etc/rastaos-release`, the 7.x receipt (`KEY=value` lines) |
| `stack` | `/etc/hasta/stack` (`STACK=`, `REVISION=` lines) when present; else inferred on a Pi 3 (or a Buster card in a reader): Rasta at the receipt's `RASTAOS_TAG` revision, else at `/boot/VERSION`'s. `inferred: true` says which |

`setup/detect.py` knew part of this; `hasta.facts` is its successor, and the installers move onto
it when the engine starts applying.

### `hasta modules [--tree DIR]`

Every `module.toml` under the tree (default: this checkout; `tests/` and `hasta/` are skipped),
valid or not, and why not; and how many legacy `module.ini` the tree holds.

### The legacy driver (`hasta/legacy.py`)

Reads a `module.ini` — every key `setup/installer.py` and `setup/converge.py` honour: `[module]`
name, description, group, platforms; `[install]` script, rerun, upgrade; `[deps]` apt; `[files]`
bins, services, timers, dirs, npm, udev_rules, enable, mask; `[starter]` comment, service — into
the manifest model, which `manifest.validate` accepts: bins and udev rules become `links`, units
take their state (`enable` → enabled, `mask` → masked, else disabled: the starter starts them),
`platforms` becomes `supports.machine` (`pi` → `rpi*`, `x86` → `x86*`), and what the `.ini` cannot
say (an `install.sh`, directories, npm, the starter entry) goes to `undeclared`. A key no installer
reads is reported. Where a `module.toml` sits beside the `.ini` the two are joined: the `.toml` is
what the module leaves on a box, the `.ini` stays how it is installed today. Discovery is
converge's — the tree's top-level module directories — plus app trees given by path (`--app`).
Layer: the `.toml`'s, else `core` for the `core` group, `app` for an app tree, `feature` otherwise.

### `hasta plan [--root DIR] [--to REF] [--receipts FILE | --modules LIST] [--app DIR]`

The actions that move the box from its receipts to this tree; changes nothing. The module set is
the box's receipts (`ROOT/etc/pi-tools-modules.txt`, written by `setup/module-manifest`) plus every
module with a declared bin or unit on the box — converge's set — or a list given with `--modules`.
`--to REF` plans towards the tree as committed at REF (`git archive`, nothing checked out).
Resolve checks `supports`, `requires` / `provides` and `claims`, and two modules on one box path
(a conflict between legacy modules that declare nothing): each is a problem, reported, never
enforced, and the exit is 1. Order: layer, then what a module requires, then name.

Each module lands in converge's classes, by converge's rules: `upgrade` (a script module with an
`[install] upgrade` script, run `--converging`), `script` (`rerun = yes`: install.sh again),
`held` (neither: not run), `install` (a standard module with a declared file missing), `linked`
(every declared bin and unit present: the symlinks carry the new code); plus `skip` (not for this
platform) and `unrecorded` (a script module with no receipt and no trace). The platform is the
facts' machine (`rpi*` → pi, `x86*` → x86), or `--platform`. `tests/test_plan.py` runs
`setup/converge.py --plan` and `hasta plan` on the same 7.5 receipts and boxes and requires the
same decision for every module.

## 7. The manifest — `module.toml`, schema 1

Read with `tomllib` where it exists (3.11+) and with the vendored `hasta/_vendor/tomli.py` (tomli
2.0.1, MIT) elsewhere. The validator refuses unknown keys, and every error names the file, the key
and the reason (`rorw/module.toml: resources.units[0].state: 'running' is not one of …`).

```toml
schema = 1                                  # required; the engine reads schema 1
requires = ["storage.data"]                 # capabilities, not module names (dotted, lowercase)
provides = ["storage.ro-root"]
claims = ["display"]                        # exclusive resources, checked when a fleet resolves
undeclared = ["rewrites fstab from a probe"]  # what the schema cannot say yet — listed, never dropped

[module]                                    # required: id, version, layer, summary
id = "rorw"
version = "1.0"
layer = "core"                              # base | machine | stack | core | feature | app | fleet
summary = "Read-only root with a data partition"

[supports]                                  # selectors; a key left out = any
machine = ["rpi3", "rpi4"]                  # also: base, architecture, graphics, stack

[options.readonly]                          # typed options with defaults: what a fleet may set
type = "bool"                               # bool | int | float | string | enum | list
default = true                              # an enum also has choices = [...]

[resources.packages]                        # per base name, or "any"
any = ["rsync"]

[[resources.files]]    # path (absolute), source (in the tree), mode "0644", copy = true|false
[[resources.links]]    # path, target
[[resources.units]]    # name (a systemd unit), state = enabled|disabled|masked|static,
                       # wanted_by = [...], source, copy (the box holds a copy, not a link)
[[resources.lines]]    # file = cmdline|config|fstab, line, present = true|false, section (config)
[[resources.artefacts]]  # path, target, sha256
[[state]]              # path under /data the module owns, summary
[[writes]]             # path, when = boot|steady|update|shutdown|event, summary
[[seal]]               # path, action = delete|truncate|regenerate, summary
[[migrations]]         # from (a version), summary, script
```

Top-level keys (`requires`, `undeclared`…) go before the first `[table]`: TOML puts a key written
after a table header inside that table, and the validator then reports it as unknown there.

## 8. Tests

`tests/` (standard-library `unittest`) runs inside `bench/verify`, the repo's one gate, under
`python3` and again under a 3.7 interpreter when one is at hand; verify prints which it used.
Fixtures are small text trees with no key and no identity in them: `tests/fixtures/roots/` (a Pi 3
on Buster, a Pi 4 with `/boot/firmware`, an x86 box with an ESP, a Rasta card in a reader) and
`tests/fixtures/modules/` (a manifest using every key, and a broken one), and
`tests/fixtures/receipts/rastaos-7.5.txt` (module receipts as `setup/module-manifest` writes them,
fingerprinted at the rastaos-7.5 commit `1c927c4`).
