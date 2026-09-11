#!/usr/bin/env bash
# Driver for issue-pilot: implements a list of issues one at a time, clearing
# the conversation whenever the next issue does not depend on the ones already
# implemented.
#
# The run is autonomous.  The single exception is phase 1, up front: one
# interactive session reads every issue and asks you, in one batch, everything
# it needs to know.  Its answers become the run's --notes, the only thing that
# survives the /clear between issues.  After phase 1 nobody has to watch.
#
# The clearing is not a decision the model makes: issues_plan.py computes the
# dependencies, and this script either starts a brand-new `claude` session
# (equivalent to /clear) or resumes the previous one.  Sessions are addressed
# by id, never as "the most recent conversation in this directory": a run takes
# hours, and the person who started it will open other sessions in the same
# repository meanwhile -- to look at the run's status, if nothing else.
#
# The branch is derived from the issue list (`issues-165-166`) and created by
# this script, before anything else, off the tip of the base branch AS IT IS ON
# ORIGIN -- fetched first, every time.  Whatever is checked out locally, and
# whatever the local base branch is at, does not come into it: the run has to
# start from what everybody else has, and the local base branch is nobody's to
# reset.  --from-head is the deliberate exception.
#
# A run outlives any single command, so it can also be started detached: with
# --detach the driver relaunches itself in its own session, prints where its log
# is, and returns at once.  That is how /issue-pilot:issues starts it, since a
# run takes hours and no tool call lives that long.  A detached run has nobody to
# interview, so it needs the answers up front (--notes-file / --notes) or none at
# all (--no-interview).
#
#   issues_run.sh 165 166 170           # branch cut from origin/<base>, always fetched first
#   issues_run.sh --from-head 165 166   # cut it from HEAD instead (offline, or on purpose)
#   issues_run.sh --notes "answers agreed elsewhere" 165 166  # skips phase 1
#   issues_run.sh --notes-file notes.txt 165 166              # the same, from a file
#   issues_run.sh --no-interview 165 166                      # no phase 1 at all
#   issues_run.sh --detach --notes-file notes.txt 165 166     # run in the background
#   issues_run.sh --pr 165 166          # also open the pull request at the end
#   issues_run.sh --plan-only 165 166   # show which issues would share a conversation
#   issues_run.sh --resume              # continue, retrying a blocked issue once
set -euo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPTS/.." && pwd)"
STATE="$SCRIPTS/issues_state.py"
CONFIG="$SCRIPTS/pilot_config.py"
GUARD="$ROOT/hooks/usage-guard.sh"
PILOT_HOME="${ISSUE_PILOT_HOME:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/issue-pilot}"

cfg() { python3 "$CONFIG" get "$1" 2>/dev/null || true; }

SELF="$SCRIPTS/$(basename "${BASH_SOURCE[0]}")"
ORIG_ARGS=("$@")

NOTES=""; NOTES_FILE=""; REPO=""; RESUME=0; INTERVIEW=1; FROM_HEAD=0; OPEN_PR=0; DETACH=0; PLAN_ONLY=0
MAX_ATTEMPTS="$(cfg max_attempts)"; MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"

# Every `claude` this driver launches runs on the same model and effort level,
# whatever your interactive default happens to be: a run started from an Opus
# session must not silently become an Opus run.  Change it per project in
# .issue-pilot.json, or per run with ISSUE_PILOT_MODEL / ISSUE_PILOT_EFFORT.
MODEL="$(cfg model)";   MODEL="${MODEL:-sonnet}"
EFFORT="$(cfg effort)"; EFFORT="${EFFORT:-high}"

CLAUDE_ARGS=(--model "$MODEL" --effort "$EFFORT")
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --notes)  NOTES="$2";  INTERVIEW=0; shift 2;;   # answers supplied already
    --notes-file) NOTES_FILE="$2"; INTERVIEW=0; shift 2;;
    --detach) DETACH=1;    shift;;      # run in the background, return at once
    --repo)   REPO="$2";   shift 2;;
    --resume) RESUME=1;    shift;;      # continue a run, retrying if blocked
    --no-interview) INTERVIEW=0; shift;;
    --from-head) FROM_HEAD=1; shift;;   # cut the run branch from HEAD, not origin
    --sync)   shift;;                    # accepted for old habits: it is now the default
    --pr)     OPEN_PR=1;   shift;;
    --plan-only) PLAN_ONLY=1; shift;;  # print the plan, touch nothing
    --max-attempts) MAX_ATTEMPTS="$2"; shift 2;;
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0;;
    -*) echo "unknown option: $1" >&2; exit 2;;
    *) ARGS+=("$1"); shift;;
  esac
done

# --------------------------------------------------------------- prerequisites
for tool in git gh claude python3; do
  command -v "$tool" >/dev/null 2>&1 || { echo "error: \`$tool\` is not installed or not on PATH." >&2; exit 78; }
done
gh auth status >/dev/null 2>&1 || { echo "error: \`gh\` is not authenticated; run \`gh auth login\`." >&2; exit 78; }

# The project's configuration is not optional.  A run has nobody to ask what
# green means, so it is decided once, in writing, before anything starts.
python3 "$CONFIG" check >/dev/null || exit 78

# --------------------------------------------------------------- authentication
# Every issue runs in a `claude` subprocess, which needs credentials of its own.
# Two ways those go missing even though you are logged in: CLAUDE_CODE_OAUTH_TOKEN
# lives in ~/.bashrc, which a non-interactive shell never reads; and Claude Code
# strips that variable from the environment it gives its own Bash tool, so a
# driver launched from inside a session inherits nothing.  Recover just that one
# export instead of sourcing the whole rc file.
if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
  for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
    [[ -r "$rc" ]] || continue
    token_line="$(grep -hE '^[[:space:]]*export[[:space:]]+CLAUDE_CODE_OAUTH_TOKEN=' "$rc" | tail -1 || true)"
    [[ -n "$token_line" ]] && { eval "$token_line"; break; }
  done
fi

if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" && -z "${ANTHROPIC_API_KEY:-}" \
      && ! -f "$HOME/.claude/.credentials.json" ]]; then
  cat >&2 <<'MSG'
error: no credentials for the `claude` subprocesses this driver launches.

  Found no CLAUDE_CODE_OAUTH_TOKEN, no ANTHROPIC_API_KEY and no
  ~/.claude/.credentials.json.  Being logged in inside a Claude Code session is
  not enough: that token is not exported to subprocesses.

  Fix it with one of:
    claude setup-token
    export CLAUDE_CODE_OAUTH_TOKEN=...      (or put it in ~/.bashrc)
MSG
  exit 78
fi

# Every `claude` this driver launches, and every hook inside it, can see this.
# The usage guard uses it to tell an autonomous session from an interactive one
# when it has to decide how hard to stop.
export ISSUE_PILOT_RUN=1

# ------------------------------------------------- the rule that keeps a run alive
# Both observed failures of this driver were the same one: the model started a
# long job with run_in_background and then ENDED ITS TURN to wait for the
# completion notification.  Under `claude -p` a finished turn is a finished
# process, so the session exited, the harness killed the background job
# ("N background shell command tasks didn't finish before the previous session
# ended"), and hours of finished-but-uncommitted work were left on the floor.
# Interactively that pattern is right; here it is fatal, and no command file
# says so.  Say it in the system prompt, where every autonomous session sees it
# regardless of which command it is running.
AUTONOMY_RULES=$(cat <<'RULES'
You are in an autonomous, non-interactive run (`claude -p`). Nobody is watching,
nobody will answer a question, and nobody will restart this if it dies.

HARD RULE: if you end your turn, the process ends. The session dies and EVERY
process you started in the background is killed at that instant. So NEVER end
your turn in order to "wait" for something. In particular, do not finish a turn
saying "I'll wait for the notification", "waiting for it to finish" or "I'll
pick this up when it completes": that is not a pause, it is the end of the run.

To wait for a long command, in order of preference:
  1. Run it in the foreground with the Bash tool's `timeout` (up to 600000 ms).
  2. If you already started it with run_in_background, wait on it with
     TaskOutput(task_id, block=true, timeout=600000), calling it again as many
     times as it takes until the command finishes.
  3. If it runs longer than that, make it resumable (write to a log and a
     sentinel file with `nohup`) and keep making tool calls while you check the
     sentinel; do not stop making tool calls.
Foreground `sleep` is blocked by the harness: do not try to work around it, use
the options above.

Commit as soon as the tests for the issue are green, BEFORE any optional
polish, so that an unexpected death does not take finished work with it.
RULES
)

# The run commits as it goes, so anything already in the tree -- modified or
# untracked -- would end up inside its commits, attributed to an issue it has
# nothing to do with.  Untracked files are refused too, on purpose: the
# guarantee that a run's commits contain only the run's work is worth more than
# the `git stash -u` it costs.
require_clean_tree() {
  local dirty
  dirty="$(git status --porcelain)"
  [[ -z "$dirty" ]] && return 0
  cat >&2 <<MSG
error: the working tree is not clean; the run would sweep this into its commits:

$(sed 's/^/    /' <<<"$dirty")

  Commit it, or set it aside for the duration of the run:
    git stash -u          # everything, untracked files included
    git stash pop         # afterwards
MSG
  exit 2
}

# ----------------------------------------------------- which branch this run is
# Resolved before anything is touched, because --detach needs to say where the
# log will be without having done any work yet.
if [[ $RESUME -eq 1 ]]; then
  BRANCH="$(python3 "$STATE" show | python3 -c 'import json,sys; print(json.load(sys.stdin)["branch"])')"
else
  [[ ${#ARGS[@]} -gt 0 ]] || { echo "usage: issues_run.sh [--from-head] [--detach] [--no-interview] [--pr] <issue>..." >&2; exit 2; }
  BRANCH="$(python3 "$CONFIG" branch-name "${ARGS[@]}")"
fi

# --------------------------------------------------------------- plan only
# The one decision this tool takes away from the model, shown before anything
# is created, so it can be read -- and disagreed with -- for free.
if [[ $PLAN_ONLY -eq 1 ]]; then
  [[ $RESUME -eq 0 ]] || { echo "error: --plan-only makes no sense with --resume; use issues_state.py status." >&2; exit 2; }
  plan=("$SCRIPTS/issues_plan.py" "${ARGS[@]}")
  [[ -n "$REPO" ]] && plan+=(--repo "$REPO")
  python3 "${plan[@]}" | python3 -c '
import json, sys
plan = json.load(sys.stdin)["plan"]
print("branch: '"$BRANCH"'")
for i in plan:
    deps = ", ".join("#%s" % x for x in i["depends_on"]) or "-"
    how = "fresh conversation" if i["clear_before"] else "continues the previous one"
    print("  #%-6s %-28s depends on %s" % (i["issue"], how, deps))
    print("          %s" % i["title"])
'
  exit 0
fi

LOGDIR="$PILOT_HOME/logs/$BRANCH"
mkdir -p "$LOGDIR"

# --------------------------------------------------------------- detach
# A run takes hours, which is longer than any tool call or terminal session
# should have to stay open for.  Detaching puts it in a session of its own, so
# closing the one that started it does not take the run with it.
if [[ $DETACH -eq 1 ]]; then
  if [[ $INTERVIEW -eq 1 ]]; then
    cat >&2 <<'MSG'
error: --detach has nobody to ask phase 1's questions.

  Gather the answers first and pass them in with --notes-file or --notes, or
  accept a run with no answers at all with --no-interview.
MSG
    exit 2
  fi
  # Cheap checks belong to the parent: a mistake this obvious should be visible
  # straight away, not buried in a log the caller has not read yet.
  [[ $RESUME -eq 1 ]] || require_clean_tree
  if [[ -n "$NOTES_FILE" && ! -s "$NOTES_FILE" ]]; then
    echo "error: --notes-file $NOTES_FILE is missing or empty." >&2
    exit 2
  fi

  child=()
  for a in "${ORIG_ARGS[@]}"; do [[ "$a" == "--detach" ]] || child+=("$a"); done
  driver_log="$LOGDIR/driver.log"

  # setsid is what survives the caller's process group being cleaned up; where
  # there is none (macOS), nohup and a disowned job are the best available.
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup bash "$SELF" "${child[@]}" >"$driver_log" 2>&1 </dev/null &
  else
    nohup bash "$SELF" "${child[@]}" >"$driver_log" 2>&1 </dev/null &
  fi
  pid=$!
  disown 2>/dev/null || true
  echo "$pid" >"$LOGDIR/driver.pid"

  cat <<MSG
run started in the background (pid $pid)
  branch : $BRANCH
  log    : $driver_log
Follow it with /issue-pilot:issues-status, or: tail -f "$driver_log"
MSG
  exit 0
fi

# --------------------------------------------------------------- resume
if [[ $RESUME -eq 1 ]]; then
  # A blocked issue would otherwise make `next` report the run finished, so
  # --resume silently did nothing at all.  Reopen it and hand it out again:
  # one retry per --resume invocation, so a persistently failing issue still
  # stops the run instead of looping.
  python3 "$STATE" unblock
  # Resuming from somewhere else must not implement the rest of the run on the
  # wrong branch.
  [[ "$(git rev-parse --abbrev-ref HEAD)" == "$BRANCH" ]] || git checkout "$BRANCH"
fi

# --------------------------------------------------------------- phase 0: branch
if [[ $RESUME -eq 0 ]]; then
  require_clean_tree

  BASE="$(python3 "$CONFIG" base-branch)"

  if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    # A rerun over the same issues picks the existing branch up as it is;
    # starting it over is a decision for a person, made with `git branch -D`.
    echo "branch $BRANCH already exists; checking it out"
    git checkout "$BRANCH"
  elif [[ $FROM_HEAD -eq 1 ]]; then
    echo "creating branch $BRANCH off HEAD ($(git rev-parse --abbrev-ref HEAD)) as asked"
    git checkout -b "$BRANCH"
  else
    echo "fetching origin/$BASE"
    git fetch --quiet origin "$BASE" || {
      echo "error: could not fetch origin/$BASE. The run starts from origin, not from what is checked out;" >&2
      echo "       fix the network or the remote, or pass --from-head to start from HEAD on purpose." >&2
      exit 1
    }
    # The local base branch is left exactly as it was.  If it is ahead of
    # origin, say so: those commits are somebody's unpushed work and this run
    # will not include them, which is either what they want or a surprise.
    if git show-ref --verify --quiet "refs/heads/$BASE"; then
      ahead="$(git rev-list --count "origin/$BASE..$BASE")"
      if (( ahead > 0 )); then
        cat >&2 <<MSG
note: local $BASE is $ahead commit(s) ahead of origin/$BASE; the run starts from origin and will NOT include:

$(git log --oneline "origin/$BASE..$BASE" | sed 's/^/    /')

      Push them first if the run should build on them.
MSG
      fi
    fi
    echo "creating branch $BRANCH off origin/$BASE ($(git rev-parse --short "origin/$BASE"))"
    git checkout --quiet --no-track -b "$BRANCH" "origin/$BASE"
  fi
fi

# --------------------------------------------------------------- phase 1
if [[ $RESUME -eq 0 ]]; then
  if [[ $INTERVIEW -eq 1 ]]; then
    [[ -t 0 ]] || { echo "error: phase 1 needs a terminal to ask its questions; run this from your own shell, or pass --notes/--no-interview." >&2; exit 2; }

    notes_file="$(python3 "$CONFIG" notes-file "${ARGS[@]}")"
    mkdir -p "$(dirname "$notes_file")"
    rm -f "$notes_file"

    echo "=== phase 1: reading every issue and asking what it needs (interactive) ==="
    # Interactive on purpose, and the only interactive step of the run: the
    # prompt goes as a positional argument rather than -p's payload, which is
    # what lets this session talk to you.  It writes its conclusions to
    # $notes_file and implements nothing.
    claude "${CLAUDE_ARGS[@]}" --permission-mode acceptEdits "$(cat <<EOF
Phase 1 of an autonomous issue-pilot run over these issues: ${ARGS[*]}

This is the ONLY chance to ask me anything in the whole run: after this session
nobody will be watching.

1. Read EVERY issue in the list, start to finish, with
   \`gh issue view <n> --comments\`, in the order given. All of them, not just
   the first one.
2. Read CLAUDE.md and the code each issue touches, enough to know whether the
   issue is implementable as written.
3. Collect into a single list every open question about what is to be
   implemented: where two readings of an issue lead to materially different
   work, where there is a design decision the issue does not settle, where two
   issues contradict each other, or where an issue depends on one that is not
   in the list.
4. Ask me ALL of them AT ONCE, in a single batch, with AskUserQuestion. Anything
   a sensible default settles is not a question: decide it, record it, and the
   default goes on the record.
5. Also record how long the slowest thing that has to run takes (downloads,
   sweeps, training) and how to tell whether it has finished, so the autonomous
   sessions do not have to discover it.
6. When you are done, write to $notes_file the answers and the agreed defaults,
   in plain text, written for a session that never saw this conversation: they
   are the only thing that survives the /clear between issues. Say which issue
   each point applies to, and be explicit about what must NOT be done.

You are already on the run branch ($BRANCH); do not create another.
Do NOT implement anything, do not touch the repository, do not commit.
Your only deliverable is $notes_file.
EOF
)" || true

    if [[ ! -s "$notes_file" ]]; then
      echo "error: phase 1 ended without writing $notes_file; refusing to start a blind autonomous run." >&2
      echo "       run it again, or pass --notes '...' / --no-interview to go ahead anyway." >&2
      exit 1
    fi
    NOTES="$(cat "$notes_file")"
    echo "=== phase 1 done: $(wc -l < "$notes_file") lines of notes recorded in $notes_file ==="
  fi

  if [[ -n "$NOTES_FILE" ]]; then
    [[ -s "$NOTES_FILE" ]] || { echo "error: --notes-file $NOTES_FILE is missing or empty." >&2; exit 2; }
    NOTES="$(cat "$NOTES_FILE")"
  fi

  init=("$STATE" init "${ARGS[@]}" --branch "$BRANCH" --base-branch "${BASE:-}")
  [[ -n "$NOTES" ]] && init+=(--notes "$NOTES")
  [[ -n "$REPO"  ]] && init+=(--repo "$REPO")
  python3 "${init[@]}" >/dev/null
  echo "run initialised on branch $BRANCH"
fi

python3 "$STATE" show | python3 -c '
import json, sys
for i in json.load(sys.stdin)["plan"]:
    deps = ", ".join(str(x) for x in i["depends_on"]) or "-"
    print("  #%s clear_before=%s depends_on=%s" % (i["issue"], i["clear_before"], deps))
'

status_of() {  # status_of <issue>
  python3 "$STATE" show \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['status'].get('$1','?'))"
}

# What the working tree looks like, so an attempt that changed literally nothing
# can be told apart from one that made progress but ran out of session.
fingerprint() { echo "$(git rev-parse HEAD) $(git status --porcelain | md5sum)"; }

# The conversation a dependent issue or a retry goes back to.  Kept in the state
# and addressed by id: `--continue` would mean "the most recent conversation in
# this directory", and during a run that is whichever one the user opened last.
current_session() {
  python3 "$STATE" show | python3 -c 'import json,sys; print(json.load(sys.stdin).get("session") or "")'
}
new_session() {
  local sid
  sid="$(python3 -c 'import uuid; print(uuid.uuid4())')"
  python3 "$STATE" session "$sid" >/dev/null
  echo "$sid"
}

# A usage window that cannot be waited out is the one thing that should stop a
# run between issues rather than in the middle of one.
check_usage() {
  [[ -r "$PILOT_HOME/usage-guard.halt" ]] || return 0
  echo "stopping: the usage guard halted this run -- $(cat "$PILOT_HOME/usage-guard.halt")" >&2
  echo "resume it when the window resets with: $0 --resume" >&2
  echo "(clear the halt with: $GUARD --clear)" >&2
  exit 1
}

# --------------------------------------------------------------- autonomous run
while next_json=$(python3 "$STATE" next); do
  check_usage
  issue=$(echo "$next_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["issue"])')
  clear=$(echo "$next_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["clear_before"])')

  attempt=1
  stale=0
  while :; do
    log="$LOGDIR/issue-$issue-attempt-$attempt.log"
    echo "=== issue #$issue, attempt $attempt/$MAX_ATTEMPTS (fresh conversation: $clear) -> $log ==="
    python3 "$STATE" attempt "$issue" >/dev/null
    before="$(fingerprint)"

    if [[ $attempt -eq 1 ]]; then
      prompt="/issue-pilot:issues-one $issue"
    else
      # A session that exits with the issue still pending has almost always
      # ended its turn while work was in flight, not hit a real blocker: the
      # code is usually written, green and uncommitted.  Resuming the same
      # conversation picks it up with all of that context intact.
      prompt=$(cat <<EOF
The previous session on issue #$issue ended WITHOUT marking it done and without
declaring it blocked, so it died mid-task. The usual cause is ending the turn to
wait for a background process: under \`claude -p\` that ends the session and
kills that process.

Pick issue #$issue up where it was left. Before anything else find out what is
already done (\`git status\`, \`git log --oneline\`, the artifacts the issue
produces) instead of redoing it. If a long process died half-way, start it again
and WAIT for it without ending your turn (foreground Bash with a timeout, or
TaskOutput with block=true).

Finish the work: tests green, commit, comment on the issue, and
\`python3 "$STATE" done $issue --commit "\$(git rev-parse HEAD)"\`.
If there really is a blocker you cannot resolve, mark it with
\`python3 "$STATE" block $issue --reason '...'\`
instead of just ending your turn.
EOF
)
    fi

    # A fresh conversation gets a new id; anything else goes back to the one
    # the run is in.  A run recorded before sessions were tracked, or resumed
    # into an empty state, simply starts one.
    if [[ $attempt -eq 1 && "$clear" == "True" ]]; then
      session_args=(--session-id "$(new_session)")
    else
      sid="$(current_session)"
      if [[ -n "$sid" ]]; then
        session_args=(--resume "$sid")
      else
        session_args=(--session-id "$(new_session)")
      fi
    fi

    # No `|| true`: keep the exit code, and keep a log, so a dead run can be
    # read afterwards instead of guessed at.
    rc=0
    claude -p "$prompt" "${CLAUDE_ARGS[@]}" --permission-mode acceptEdits "${session_args[@]}" \
      --append-system-prompt "$AUTONOMY_RULES" 2>&1 | tee "$log" || rc=$?
    echo "--- claude exited with status $rc (session ${session_args[1]})" | tee -a "$log"

    status="$(status_of "$issue")"
    [[ "$status" == "done" ]] && break

    # The model deliberately declared a blocker: that is a real answer, not a
    # crash, and retrying it just burns the window.  Stop and say so.
    if [[ "$status" == "blocked" ]]; then
      reason=$(python3 "$STATE" show | python3 -c 'import json,sys; b=json.load(sys.stdin).get("blocked") or {}; print(b.get("reason",""))')
      echo "issue #$issue was blocked by the session: $reason" >&2
      echo "logs in $LOGDIR" >&2
      exit 1
    fi

    check_usage

    # Still pending: the session ended early.  Retry unless it is achieving
    # nothing -- two attempts in a row that leave the tree byte-identical mean
    # the sessions are dying before they can work, and more of them will not
    # help.
    after="$(fingerprint)"
    if [[ "$before" == "$after" ]]; then
      stale=$((stale + 1))
    else
      stale=0
    fi

    if [[ $stale -ge 2 ]]; then
      python3 "$STATE" block "$issue" \
        --reason "two consecutive sessions ended without changing anything (exit $rc); see $LOGDIR"
      echo "issue #$issue made no progress in two attempts; stopping. Logs in $LOGDIR" >&2
      exit 1
    fi

    attempt=$((attempt + 1))
    if [[ $attempt -gt $MAX_ATTEMPTS ]]; then
      python3 "$STATE" block "$issue" \
        --reason "$MAX_ATTEMPTS sessions ended without marking the issue done; see $LOGDIR"
      echo "issue #$issue did not finish in $MAX_ATTEMPTS attempts; stopping. Logs in $LOGDIR" >&2
      exit 1
    fi
  done
  echo "=== issue #$issue done ==="
done

if [[ $OPEN_PR -eq 1 ]]; then
  echo "=== opening the pull request ==="
  # The pull request needs the state and the git log, not anybody's context.
  claude -p "/issue-pilot:issues-pr" "${CLAUDE_ARGS[@]}" --permission-mode acceptEdits \
    --session-id "$(new_session)" \
    --append-system-prompt "$AUTONOMY_RULES" 2>&1 | tee "$LOGDIR/pull-request.log"
else
  echo "all issues implemented; open the pull request with: /issue-pilot:issues-pr"
fi
