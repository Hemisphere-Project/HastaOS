#!/usr/bin/env bash
# sync-upstream.sh — bring Pi-tools `2026` (the 7.x line) into HastaOS `main`, so every 7.x fix
# reaches the trunk. HastaOS was seeded from that branch and shares its history: the sync is a
# plain merge, never a rebase, never a cherry-pick.
#
#   bench/sync-upstream.sh [-C repo] [-n] [-r slug#t-NNN]
#     -C  the HastaOS clone (default: the one this script sits in)
#     -n  report only: which upstream commits main lacks, and which files a merge would
#         conflict on (git 2.38+); no branch moves, no worktree
#     -r  the hub row this sync is: its trailer `Refs-37: <slug>#t-NNN [done]` goes on the merge
#
# What it does:
#   1. fetches origin `main`, and Pi-tools `2026` into pi-tools/2026; upstream already in
#      origin/main → « up to date », exit 0. A local `main` with commits origin lacks stops it:
#      the merge is cut from origin/main, so landing it would leave them behind;
#   2. merges upstream into origin/main on the branch sync/main-<sha7>, in a worktree under the
#      git dir (<git dir>/hasta-sync/main-<sha7>): the checkout you run it from is not touched.
#      A CONFLICT STOPS it: it names the files and leaves the worktree — resolve there, commit,
#      run it again: it picks up from that commit (and merges origin/main in if main moved);
#   3. runs bench/verify in the merged tree: red stops it, the worktree stays;
#   4. green: prints the merge and the commands that land it.
# It never pushes and never moves `main`: landing is one paste, and a person makes it.
#
# Exit: 0 up to date, or merged and green · 1 conflict, or verify red · 2 could not run.
#
# env: SYNC_UPSTREAM  Pi-tools, a URL or a path (default https://github.com/Hemisphere-Project/Pi-tools.git)
#      SYNC_BRANCH    its branch (default 2026)
#      SYNC_VERIFY    the gate, run in the merged tree (default: bash bench/verify)

set -u
SELF=sync-upstream
HERE=$(cd "$(dirname "$0")" && pwd)
UPSTREAM=${SYNC_UPSTREAM:-https://github.com/Hemisphere-Project/Pi-tools.git}
UBR=${SYNC_BRANCH:-2026}
VERIFY=${SYNC_VERIFY:-bash bench/verify}

say() { printf '%s\n' "$*"; }
die() { printf '%s: %s\n' "$SELF" "$*" >&2; exit 2; }
usage() { sed -n '2,/^$/s/^# \{0,1\}//p' "$0"; }

REPO=$HERE; DRY=0; REFS=""
while getopts "C:nr:h" o; do case $o in
    C) REPO=$OPTARG ;; n) DRY=1 ;; r) REFS=$OPTARG ;; h) usage; exit 0 ;; *) usage >&2; exit 2 ;;
esac; done
shift $((OPTIND - 1))
[ $# -eq 0 ] || die "unexpected argument: $1"
[ -z "$REFS" ] || [[ $REFS =~ ^[a-z0-9][a-z0-9-]*#t-[0-9]+$ ]] || die "-r wants <slug>#t-NNN, not « $REFS »"

# the main worktree, even when this runs from a sync worktree
REPO=$(git -C "$REPO" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')
[ -n "$REPO" ] || die "not inside a git checkout"
G() { git -C "$REPO" "$@"; }

G fetch -q origin main || die "fetch origin main failed"
G fetch -q "$UPSTREAM" "+refs/heads/$UBR:refs/remotes/pi-tools/$UBR" || die "fetch $UPSTREAM $UBR failed"
UP=$(G rev-parse "refs/remotes/pi-tools/$UBR") || exit 2
OM=$(G rev-parse refs/remotes/origin/main) || exit 2
S=${UP:0:7}
SB="sync/main-$S"
WORK=$(cd "$REPO" && cd "$(git rev-parse --git-common-dir)" && pwd)/hasta-sync
WT="$WORK/main-$S"
say "Pi-tools $UBR = $S  $(G log -1 --format='%cs %s' "$UP" | cut -c1-90)"
say "origin/main  = ${OM:0:7}  $(G log -1 --format='%cs %s' "$OM" | cut -c1-90)"

if G merge-base --is-ancestor "$UP" "$OM"; then
    say "main: up to date — origin/main already has $S"
    [ -e "$WT" ] && say "  (that sync's worktree is still here: git -C $REPO worktree remove $WT && git -C $REPO branch -d $SB)"
    exit 0
fi
G merge-base "$UP" "$OM" >/dev/null || die "origin/main and Pi-tools $UBR share no history: not the HastaOS clone, or a rewritten main"

N=$(G rev-list --count "$OM..$UP")
say "main: lacks $N commit(s) of Pi-tools $UBR, oldest first:"
G log --reverse --format='  %h %s' "$OM..$UP" | head -n 20 | cut -c1-100
[ "$N" -gt 20 ] && say "  … and $((N - 20)) more"

AHEAD=""
if G rev-parse -q --verify refs/heads/main >/dev/null && ! G merge-base --is-ancestor refs/heads/main "$OM"; then
    AHEAD="local main has $(G rev-list --count "$OM..refs/heads/main") commit(s) origin/main lacks"
fi

if [ $DRY = 1 ]; then
    [ -n "$AHEAD" ] && say "note: $AHEAD — push them (or drop them) before the real run"
    mt=$(G merge-tree --write-tree --name-only --no-messages "$OM" "$UP" 2>&1); rc=$?
    case $rc in
        0) say "merge: would be clean" ;;
        1) say "merge: would CONFLICT in:"; printf '%s\n' "$mt" | sed '1d; /^$/d; s/^/    /' ;;
        *) say "merge: could not predict ($(printf '%s' "$mt" | tail -n 1 | cut -c1-80))" ;;
    esac
    exit 0
fi
[ -z "$AHEAD" ] || die "$AHEAD — push them (or drop them) first: the merge is cut from origin/main"

W() { git -C "$WT" "$@"; }
conflict_stop() {
    say "CONFLICT — the merge stopped in $WT on:"
    W diff --name-only --diff-filter=U | sed 's/^/    /'
    say "resolve there (git -C $WT status), commit, then run this again: it picks up from that commit."
    say "to give up instead: git -C $WT merge --abort"
    exit 1
}
merge_in() {   # $1 commit, $2 message — a plain merge into the worktree; nothing when already in
    W merge-base --is-ancestor "$1" HEAD && return 0
    W merge -q --no-ff --no-edit -m "$2" "$1" && return 0
    W rev-parse -q --verify MERGE_HEAD >/dev/null && conflict_stop
    die "git merge failed in $WT with no conflict recorded: read the lines above"
}

if [ -e "$WT" ]; then
    [ -e "$WT/.git" ] || die "$WT is in the way and is not a worktree: move it"
    W rev-parse -q --verify MERGE_HEAD >/dev/null && conflict_stop
    [ -z "$(W status --porcelain)" ] || die "uncommitted changes in $WT: commit them (or remove that worktree), then run this again"
    say "picking up the worktree an earlier run left: $WT ($(W rev-parse --short HEAD))"
else
    G worktree prune
    if G rev-parse -q --verify "refs/heads/$SB" >/dev/null; then
        G worktree add -q "$WT" "$SB" || die "cannot check $SB out at $WT"
    else
        G worktree add -q -b "$SB" "$WT" "$OM" || die "cannot make the worktree $WT"
    fi
fi

MSG="Merge Pi-tools $UBR into main — upstream $S

$N commit(s) of the 7.x line, merged by bench/sync-upstream.sh."
[ -n "$REFS" ] && MSG="$MSG

Refs-37: $REFS [done]"
merge_in "$OM" "Merge origin/main into $SB — main moved to ${OM:0:7} during the sync"
merge_in "$UP" "$MSG"

say
say "── $VERIFY  (in $WT)"
if ! (cd "$WT" && bash -c "$VERIFY"); then
    say
    say "verify RED on the merge: nothing to land. Fix it in $WT (commit there), then run this again."
    exit 1
fi

say
say "merged and green: $SB = $(W rev-parse --short HEAD), Pi-tools $UBR $S on top of origin/main ${OM:0:7}"
say "To land (after reading the report above):"
say "  git -C $REPO push origin $SB:main"
if [ "$(G symbolic-ref -q --short HEAD)" = main ]; then
    say "  git -C $REPO merge --ff-only $SB"
    say "  git -C $REPO worktree remove $WT && git -C $REPO branch -d $SB"
else   # main is not checked out here: move it by fetch; -D, as main (not HEAD) holds the branch
    say "  git -C $REPO fetch -q . $SB:main"
    say "  git -C $REPO worktree remove $WT && git -C $REPO branch -D $SB"
fi
exit 0
