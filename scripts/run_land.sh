#!/usr/bin/env bash
# Land a finished run the way the project is configured, without a GitHub pull
# request: `landing` = merge, push or branch (see pilot_config.py).
#
# A run ends with its work on one branch.  A pull request is one way to get it
# onto the base branch; a project with no GitHub -- a paper whose only remote is
# Overleaf's git, a self-hosted server, a repository with no remote at all --
# needs another, and "push to the base branch" is not something to leave to a
# session's judgement: it is the same every time, so it is this script, with
# tests.
#
#   merge   bring the base into the run branch if it moved, run the tests, then
#           make the base equal to the run branch: by pushing the run branch AS
#           the base (HEAD:refs/heads/<base>) when there is a remote, by a local
#           fast-forward when there is none.  The run branch itself is never
#           pushed.  Pushing HEAD as the base, rather than merging locally and
#           then pushing the base, means a refused push leaves the local base
#           untouched: nothing to undo, and base_sync.sh never finds a base that
#           is ahead of origin.  The base is merged INTO the run branch and
#           nothing is rebased or force-pushed, so the remote's base only ever
#           moves forward -- the one thing a single-branch remote accepts.
#   push    run the tests and push the run branch to origin; merging it is done
#           by you, on your server.
#   branch  run the tests and leave the branch for you to merge by hand.
#
# The tests run after the base was merged in, because that merge is code nobody
# has tested.  Local issues (tracker `local`) are closed on the run branch
# before it lands, so "merged" and "closed" are the same event; GitHub issues
# are closed here only for `merge`, where the work is known to be on the base --
# for `push` and `branch` that is not known yet, and /issue-pilot:close checks
# it later.
#
#   run_land.sh                     land the run and record it
#   run_land.sh --close-issues-only close the local issues and stop (the
#                                   pull-request flow calls this before pushing,
#                                   so merging the pull request closes them)
#
# Narration goes to stderr; the one line on stdout is the description that was
# recorded.  Exit 0: landed.  Exit 2: refused, nothing landed.  Exit 1: a fetch
# or a push failed; running it again retries.  Closing is idempotent -- issues
# already closed are skipped -- so a second attempt after a failed push does not
# repeat the comments.
set -euo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPTS/pilot_config.py"
STATE="$SCRIPTS/issues_state.py"
TRACKER="$SCRIPTS/issues_tracker.py"

CLOSE_ONLY=0
case "${1:-}" in
  "") ;;
  --close-issues-only) CLOSE_ONLY=1;;
  *) echo "usage: run_land.sh [--close-issues-only]" >&2; exit 2;;
esac

refuse() { echo "error: $*" >&2; exit 2; }

# ----------------------------------------------------------------- A. refusals
state_json="$(python3 "$STATE" show 2>/dev/null)" || refuse "there is no run to land in this repository."

# One small reader for the state, instead of parsing JSON in shell.
field() { python3 -c 'import json,sys; s=json.load(sys.stdin); print(eval(sys.argv[1]))' "$1" <<<"$state_json"; }

branch="$(field 's["branch"]')"
base="$(field 's.get("base_branch") or ""')"
[[ -n "$base" ]] || base="$(python3 "$CONFIG" base-branch)"
landing="$(python3 "$CONFIG" get landing)"
tracker="$(python3 "$CONFIG" get tracker)"
remote="$(python3 "$CONFIG" remote)"

[[ "$(field 's["blocked"] is None')" == True ]] || refuse "the run is blocked; resume it first (issues_run.sh --resume)."
not_done="$(field '" ".join(str(k) for k, v in s["status"].items() if v != "done")')"
[[ -z "$not_done" ]] || refuse "not every issue is done yet (pending or unfinished: $not_done); nothing was landed."
if [[ "$(field 'bool(s.get("pull_request"))')" == True ]]; then
  refuse "this run was already landed: $(field 's["pull_request"]["url"]')"
fi

if [[ $CLOSE_ONLY -eq 0 ]]; then
  [[ "$landing" != pull_request ]] || refuse "this project lands through a GitHub pull request; /issue-pilot:pr opens it."
  if [[ "$landing" == push && -z "$remote" ]]; then
    refuse "landing \"push\" needs a remote named origin; use \"merge\" or \"branch\"."
  fi
fi

dirty="$(git status --porcelain)"
if [[ -n "$dirty" ]]; then
  cat >&2 <<MSG
error: the working tree is not clean; landing would carry this along:

$(sed 's/^/    /' <<<"$dirty")

  Commit it, or set it aside:
    git stash -u          # everything, untracked files included
    git stash pop         # afterwards
MSG
  exit 2
fi

[[ "$(git rev-parse --abbrev-ref HEAD)" == "$branch" ]] || git checkout --quiet "$branch"

# The numeric items of the plan, in plan order; a review fix has no issue.
issues="$(field '" ".join(str(i["issue"]) for i in s["plan"] if isinstance(i["issue"], int))')"

# ------------------------------------------------------------- B. bring the base in
if [[ $CLOSE_ONLY -eq 0 && "$landing" == merge ]]; then
  if [[ -n "$remote" ]]; then
    git fetch --quiet origin "$base" || {
      echo "error: could not fetch origin/$base; nothing was landed." >&2
      exit 1
    }
    ref="origin/$base"
    if git show-ref --verify --quiet "refs/heads/$base"; then
      ahead="$(git rev-list --count "origin/$base..$base")"
      (( ahead == 0 )) || refuse "local $base has $ahead commit(s) origin/$base does not; landing would bypass them. Push them or move them onto a branch of their own first."
    fi
  else
    ref="$base"
  fi
  if ! git merge-base --is-ancestor "$ref" HEAD; then
    echo "$ref moved since the run started; merging it into $branch" >&2
    if ! git merge --no-edit --quiet "$ref" >&2; then
      git merge --abort >/dev/null 2>&1 || true
      refuse "$ref cannot be merged into $branch without conflicts. Merge it by hand (git checkout $branch && git merge $ref), resolve, commit, and run this again."
    fi
  fi
fi

# ------------------------------------------------------------------- C. tests
if [[ $CLOSE_ONLY -eq 0 ]]; then
  test_command="$(python3 "$CONFIG" get test_command)"
  [[ -n "$test_command" ]] || refuse "test_command is not set; nothing was landed."
  echo "running the tests: $test_command" >&2
  if ! bash -c "$test_command" >&2; then
    echo "error: the tests fail on $branch; nothing was landed." >&2
    [[ "$landing" != merge ]] || echo "       (a merge of $ref made above stays on $branch)" >&2
    exit 2
  fi
fi

# ------------------------------------------------------------ D. close local issues
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

issue_state() {
  python3 "$TRACKER" view "$1" --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])'
}

# Comment, then close, for every item still open.  Closed ones are skipped,
# which is what makes a second attempt after a failed push not repeat itself.
close_issues() {
  local text_template="$1" n commit current failed=0
  for n in $issues; do
    # One issue failing is reported and does not stop the others; the caller
    # decides what a failure means.
    if ! current="$(issue_state "$n")"; then
      echo "warning: could not read #$n" >&2; failed=1; continue
    fi
    if [[ "$current" == open ]]; then
      commit="$(field "s['commits'].get('$n') or 'unknown'")"
      "${text_template}" "$n" "$commit" > "$tmp/comment.md"
      python3 "$TRACKER" comment "$n" --body-file "$tmp/comment.md" >&2 \
        && python3 "$TRACKER" close "$n" >&2 || { echo "warning: could not close #$n" >&2; failed=1; }
    else
      echo "#$n is already closed" >&2
    fi
  done
  return $failed
}

text_local() { echo "Implemented on branch $branch, commit $2. Landing: $landing into $base."; }
text_github() { echo "Implemented in commit $2, merged into $base."; }

if [[ "$tracker" == local ]]; then
  # Here a failure is a reason to stop: the files are ours, and a half-closed
  # set would land as it is.
  close_issues text_local || { echo "error: could not close every local issue; nothing was landed." >&2; exit 2; }
  numbers="$(for n in $issues; do printf '#%s ' "$n"; done)"
  python3 "$TRACKER" commit -m "Close issue(s) ${numbers% } (landed by issue-pilot)" >&2
fi
[[ $CLOSE_ONLY -eq 0 ]] || exit 0

# ------------------------------------------------------------------------ E. land
tip="$(git rev-parse HEAD)"
short="$(git rev-parse --short HEAD)"
case "$landing" in
  merge)
    if [[ -n "$remote" ]]; then
      git push --quiet origin "HEAD:refs/heads/$base" 2>"$tmp/push.err" || {
        echo "error: origin refused the push of $branch as $base:" >&2
        sed 's/^/    /' "$tmp/push.err" >&2
        echo "       Nothing was landed and the local $base was not touched; running this again retries." >&2
        exit 1
      }
      git checkout --quiet "$base"
      git fetch --quiet origin "$base"
      git merge --quiet --ff-only "origin/$base" >/dev/null
      description="merged into $base at $short and pushed to origin"
    else
      git checkout --quiet "$base"
      git merge --quiet --ff-only "$branch" >/dev/null
      description="merged into $base at $short"
    fi
    ;;
  push)
    git push --quiet -u origin "$branch" >&2 2>"$tmp/push.err" || {
      echo "error: could not push $branch to origin:" >&2
      sed 's/^/    /' "$tmp/push.err" >&2
      echo "       Nothing was landed; running this again retries." >&2
      exit 1
    }
    git checkout --quiet "$base"
    description="branch $branch pushed to origin; merge it into $base on your server"
    ;;
  branch)
    git checkout --quiet "$base"
    description="branch $branch is ready; merge it into $base by hand"
    ;;
  *) refuse "unknown landing \"$landing\".";;
esac

# ------------------------------------------------- F. close GitHub issues (merge)
# The work is on the base now.  A failure here is reported and does not undo the
# landing: it is done.  The record then says the issues are still open (no
# --closes-automatically below), which is what lets /issue-pilot:close finish
# them -- it refuses to act on a record that claims nothing is left to close.
# (Review fix-1: the record used to claim it regardless, so the warning named a
# way out that refused to act.)
github_closed=1
if [[ "$tracker" == github && "$landing" == merge ]]; then
  close_issues text_github || {
    github_closed=0
    echo "warning: some issues could not be closed; /issue-pilot:close can do it." >&2
  }
fi

# ------------------------------------------------------------------- G. record
closes=()
if [[ "$tracker" == local || ( "$landing" == merge && $github_closed -eq 1 ) ]]; then
  closes=(--closes-automatically)
fi
python3 "$STATE" pr "$description" --kind "$landing" --base "$base" --head "$tip" ${closes[@]+"${closes[@]}"} >/dev/null
echo "$description"
