#!/usr/bin/env bash
# Land .issue-pilot.json on the base branch through a pull request.
#
# Everything issue-pilot does starts from the base branch as it is on origin,
# and base_sync.sh refuses a local base that is ahead of it.  So a configuration
# that exists only on this machine -- committed on the local base, or left as an
# uncommitted file -- is exactly the state the next command rejects.  On a fresh
# clone of a repository whose base branch required pull requests, init committed
# the file on the local main, /issue-pilot:plan stopped on "local main is 1
# commit(s) ahead of origin/main", and the commit had to be moved by hand onto a
# branch, pushed and opened as a pull request.  A direct push would not have
# worked either: the base was protected.  This script does what was done by
# hand, deterministically, as base_sync.sh does for the symmetric operation: a
# branch from the base with the file as its one commit, pushed, and a pull
# request.
#
# It starts only from a clean situation -- on the base branch, at the tip origin
# has for it, with .issue-pilot.json as the only change -- and refuses anything
# else without touching a thing.
#
#   config_land.sh            branch, commit, push and open the pull request
#   config_land.sh --merge    ... and squash-merge it right away, then bring the
#                             local base branch up to the merge
#
# Stdout is the pull request URL and nothing else; the narration is on stderr.
# Exit status: 2 for a refusal, 1 for a failed fetch or push, 0 once the pull
# request is open -- even if --merge was refused by GitHub (a base that needs
# approvals or passing checks): the pull request exists, and has to be merged by
# hand.
set -euo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPTS/pilot_config.py"
FILE=".issue-pilot.json"
BRANCH="configure-issue-pilot"
TITLE="Configure issue-pilot for this repository"

merge=0
for arg in "$@"; do
  case "$arg" in
    --merge) merge=1 ;;
    *) echo "usage: config_land.sh [--merge]" >&2; exit 2 ;;
  esac
done

cd "$(git rev-parse --show-toplevel)"

base="$(python3 "$CONFIG" base-branch)"

git fetch --quiet origin "$base" || {
  echo "error: could not fetch origin/$base; there is nothing to judge \"at origin's tip\" against." >&2
  exit 1
}

current="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$current" != "$base" ]]; then
  cat >&2 <<MSG
error: this is $current, not the base branch ($base); the pull request has to be
cut from the base, at the tip origin has for it.

  Move there first (it carries nothing along, and refuses a dirty tree):
    bash "$SCRIPTS/base_sync.sh"
MSG
  exit 2
fi

ahead="$(git rev-list --count "origin/$base..$base")"
behind="$(git rev-list --count "$base..origin/$base")"
if (( ahead > 0 || behind > 0 )); then
  cat >&2 <<MSG
error: local $base is not at origin/$base ($ahead commit(s) ahead, $behind behind);
the pull request would be cut from a base that is not the one on origin.

  Bring it to origin's tip first (this refuses to discard unpushed commits):
    bash "$SCRIPTS/base_sync.sh"
MSG
  exit 2
fi

changes="$(git status --porcelain --untracked-files=all)"
if [[ -z "$changes" ]]; then
  echo "error: $FILE has no changes against $base; there is nothing to land." >&2
  exit 2
fi
others="$(grep -v -E "^.. \"?$FILE\"?\$" <<<"$changes" || true)"
if [[ -n "$others" ]]; then
  cat >&2 <<MSG
error: the working tree has changes other than $FILE; they would not belong in
the configuration pull request:

$(sed 's/^/    /' <<<"$others")

  Commit them, or set them aside:
    git stash -u          # everything, untracked files included
    git stash pop         # afterwards
MSG
  exit 2
fi

if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  cat >&2 <<MSG
error: a local branch $BRANCH already exists, left by an earlier attempt; it is
not reused. If it holds nothing you need:
    git branch -D $BRANCH
MSG
  exit 2
fi
rc=0
git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1 || rc=$?
if (( rc == 0 )); then
  cat >&2 <<MSG
error: origin already has a branch $BRANCH, left by an earlier attempt (it may
be an open pull request); it is not reused. If it holds nothing you need:
    git push origin --delete $BRANCH
MSG
  exit 2
elif (( rc != 2 )); then
  echo "error: could not list the branches on origin." >&2
  exit 1
fi

git checkout --quiet -b "$BRANCH"
git add -- "$FILE"
git commit --quiet -m "$TITLE" -- "$FILE"

if ! git push --quiet -u origin "$BRANCH" >&2; then
  # Put things back as they were: the file in the working tree, no stray branch.
  git checkout --quiet "$base"
  git checkout "$BRANCH" -- "$FILE"
  git reset --quiet -- "$FILE"
  git branch --quiet -D "$BRANCH"
  echo "error: could not push $BRANCH to origin; nothing was changed ($FILE is back in the working tree)." >&2
  exit 1
fi

draft=()
if [[ "$(python3 "$CONFIG" get pr_draft | tr '[:upper:]' '[:lower:]')" == "true" ]]; then
  draft=(--draft)
fi

body="$(mktemp)"
trap 'rm -f "$body"' EXIT
python3 - "$FILE" >"$body" <<'PY'
import json
import sys

settings = json.load(open(sys.argv[1]))
print("Adds `.issue-pilot.json`, which configures issue-pilot for this repository.\n")
print("Settings:\n")
for key, value in settings.items():
    print("- `%s`: `%s`" % (key, value if isinstance(value, str) else json.dumps(value)))
print("""
Nothing in issue-pilot runs until this file is on the base branch: every command
starts from the base branch as it is on origin. Until this is merged and the base
pulled (`base_sync.sh` does the pull), `/issue-pilot:plan` and `/issue-pilot:run`
refuse.

🤖 Generated with [Claude Code](https://claude.com/claude-code)""")
PY

if ! out="$(gh pr create --base "$base" --title "$TITLE" --body-file "$body" ${draft[@]+"${draft[@]}"})"; then
  git checkout --quiet "$base"
  echo "error: $BRANCH is pushed, but the pull request could not be opened." >&2
  exit 1
fi
url="$(tail -n 1 <<<"$out")"

git checkout --quiet "$base"

if (( merge )); then
  if gh pr merge "$url" --squash --delete-branch >&2; then
    git fetch --quiet --prune origin
    git merge --quiet --ff-only "origin/$base" >/dev/null
    git show-ref --verify --quiet "refs/heads/$BRANCH" && git branch --quiet -D "$BRANCH"
    echo "merged $url; on $base at $(git rev-parse --short HEAD), the tip of origin/$base" >&2
  else
    echo "the pull request is open but GitHub refused to merge it; it has to be merged by hand: $url" >&2
  fi
else
  echo "pull request open: $url -- merge it, then run base_sync.sh to pull the base." >&2
fi

echo "$url"
