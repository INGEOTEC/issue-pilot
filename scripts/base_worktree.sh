#!/usr/bin/env bash
# A read-only checkout of the tip of the base branch as it is on origin.
#
# Both the planning of an issue and the interview before a run read the code
# the work will land on.  The working tree is the wrong place to read it: it may
# be on a feature branch, behind origin, or in the middle of something else, and
# a plan written against that code is a plan for the wrong codebase.  This puts
# origin/<base> in a worktree of its own, outside the repository, and hands back
# the path.
#
#   base_worktree.sh add       fetch, (re)point the worktree at origin/<base>, print its path
#   base_worktree.sh path      print where it is, without touching anything
#   base_worktree.sh remove    drop it
set -euo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPTS/pilot_config.py"
PILOT_HOME="${ISSUE_PILOT_HOME:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/issue-pilot}"

root="$(git rev-parse --show-toplevel)"
slug="$(printf '%s' "$root" | sed 's#^/##; s#/#-#g')"
worktree="$PILOT_HOME/worktrees/$slug"

case "${1:-}" in
  add)
    base="$(python3 "$CONFIG" base-branch)"
    git fetch --quiet origin "$base" || {
      echo "error: could not fetch origin/$base; the plan has to be read from origin, not from whatever is checked out." >&2
      exit 1
    }
    mkdir -p "$(dirname "$worktree")"
    if [[ -d "$worktree" ]] && git -C "$worktree" rev-parse --git-dir >/dev/null 2>&1; then
      # Already there: move it to the current tip rather than leaving a stale copy.
      git -C "$worktree" checkout --quiet --detach "origin/$base"
    else
      rm -rf "$worktree"
      git worktree prune
      # No --quiet: older gits do not have it, and this has to run on whatever
      # git the machine already has.
      git worktree add --detach "$worktree" "origin/$base" >/dev/null
    fi
    echo "$worktree"
    ;;
  path)
    echo "$worktree"
    ;;
  remove)
    if [[ -d "$worktree" ]]; then
      git worktree remove --force "$worktree" 2>/dev/null || rm -rf "$worktree"
    fi
    git worktree prune
    echo "removed $worktree"
    ;;
  *)
    echo "usage: base_worktree.sh {add|path|remove}" >&2
    exit 2
    ;;
esac
