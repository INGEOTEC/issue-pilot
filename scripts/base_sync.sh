#!/usr/bin/env bash
# Put the repository on the base branch, at the tip origin has for it.
#
# Everything issue-pilot does starts from the base branch as it is on origin:
# an issue is planned against it, the questions before a run are asked against
# it, and the run branch is cut from it.  The code is read from the working
# tree, so the working tree has to be there -- not on a feature branch, not on
# a base branch that fell behind while something else was being worked on.
#
# This checks the base branch out (creating it from origin if there is no local
# one), fetches, and fast-forwards it.  Fast-forward only: a local base branch
# with commits origin does not have is somebody's unpushed work, and nothing
# here discards it -- the script stops and says what to do instead.  Likewise
# it will not switch branches over a dirty tree.
#
#   base_sync.sh      sync, and print the commit the base branch is now at
set -euo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPTS/pilot_config.py"

base="$(python3 "$CONFIG" base-branch)"

dirty="$(git status --porcelain)"
if [[ -n "$dirty" ]]; then
  cat >&2 <<MSG
error: the working tree is not clean; moving to $base would carry this along:

$(sed 's/^/    /' <<<"$dirty")

  Commit it, or set it aside:
    git stash -u          # everything, untracked files included
    git stash pop         # afterwards
MSG
  exit 2
fi

git fetch --quiet origin "$base" || {
  echo "error: could not fetch origin/$base; issue-pilot reads the code from origin, not from whatever is checked out." >&2
  exit 1
}

if git show-ref --verify --quiet "refs/heads/$base"; then
  ahead="$(git rev-list --count "origin/$base..$base")"
  if (( ahead > 0 )); then
    cat >&2 <<MSG
error: local $base is $ahead commit(s) ahead of origin/$base:

$(git log --oneline "origin/$base..$base" | sed 's/^/    /')

  issue-pilot works from what is on origin, and will not discard these.
  Push them, or move them onto a branch of their own first:
    git branch <name> $base && git checkout $base && git reset --hard origin/$base
MSG
    exit 2
  fi
  [[ "$(git rev-parse --abbrev-ref HEAD)" == "$base" ]] || git checkout --quiet "$base"
  git merge --quiet --ff-only "origin/$base" >/dev/null
else
  git checkout --quiet --track -b "$base" "origin/$base"
fi

echo "on $base at $(git rev-parse --short HEAD), the tip of origin/$base" >&2
git rev-parse HEAD
