#!/usr/bin/env bash
# usage-guard.sh -- keeps a long autonomous run from burning through your usage
# windows.
#
# Registered as a PreToolUse hook, so it is reached on every tool call, but it
# only measures once every CHECK_INTERVAL seconds (default 300 = 5 min).
#
# What it does with a reading depends on how long the window is, and that is
# decided by the reading itself rather than by a hardcoded list of window names:
#
#   * A window that resets within MAX_WAIT (the 5-hour one) is waited out.  Over
#     threshold, the hook sleeps until the reset, so the session pauses instead
#     of hitting the hard limit mid-task.
#   * A window that resets later than that (the weekly one) cannot be waited
#     out -- sleeping for three days is not a pause, it is a hang.  Over its own
#     threshold the guard halts: it writes a halt file the run driver checks
#     between issues, and blocks the tool call with an explanation.
#
# It must never break a session by accident: any failure -- no network, expired
# token, unexpected payload -- exits 0 and lets the tool call through.
#
# Where the reading comes from, two ways, chosen by USAGE_GUARD_SOURCE:
#
#   * /api/oauth/usage -- what the session's own /usage shows, and free: no
#     model request.  It needs the `user:profile` scope, which a `claude login`
#     token carries and a `claude setup-token` one does not.
#   * The `anthropic-ratelimit-unified-*` response headers of an ordinary
#     /v1/messages call.  It works with any token, which is why it exists: a
#     setup-token (the way a remote, ssh-only machine is logged in) gets 403 from
#     the endpoint above.  It costs one real (1-output-token, Haiku) request per
#     check; that is a rounding error against the window it is protecting.
#
# In the default `auto` the first is tried and a 401/403 falls back to the
# second in the same run; `headers` and `oauth` pin one of them.  Both print the
# same '<window> <percent> <seconds-until-reset>' lines, so the decision below
# is shared whatever produced the reading.
#
# Modes:
#   usage-guard.sh            hook mode: measure, then wait or halt
#   usage-guard.sh --check    probe now regardless of the interval, print one JSON
#                             object -- the readings, where they came from, the
#                             verdict hook mode would
#                             reach on each, the thresholds and any halt on
#                             record -- never sleep, never block (used by the run
#                             driver, /issue-pilot:status and /issue-pilot:usage)
#   usage-guard.sh --clear    forget a recorded halt

set -uo pipefail

THRESHOLD="${USAGE_GUARD_THRESHOLD:-70}"          # percent of a short window
LONG_THRESHOLD="${USAGE_GUARD_LONG_THRESHOLD:-90}" # percent of a long one
LONG_ACTION="${USAGE_GUARD_LONG_ACTION:-halt}"    # halt | warn | ignore
CHECK_INTERVAL="${USAGE_GUARD_INTERVAL:-300}"     # seconds between real checks
MAX_WAIT="${USAGE_GUARD_MAX_WAIT:-21600}"         # longest single sleep (6 h)
SOURCE_MODE="${USAGE_GUARD_SOURCE:-auto}"         # auto | headers | oauth
PROBE_MODEL="${USAGE_GUARD_MODEL:-claude-haiku-4-5-20251001}"
API="${USAGE_GUARD_API:-https://api.anthropic.com}"

PILOT_HOME="${ISSUE_PILOT_HOME:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/issue-pilot}"
CREDS="$HOME/.claude/.credentials.json"
STATE="$PILOT_HOME/usage-guard.state"
BACKOFF="$PILOT_HOME/usage-guard.backoff"
HALT="$PILOT_HOME/usage-guard.halt"
LOG="$PILOT_HOME/usage-guard.log"
mkdir -p "$PILOT_HOME" 2>/dev/null || true

MODE=hook
case "${1:-}" in
  --check|check) MODE=check ;;
  --clear|clear) rm -f "$HALT"; echo "halt cleared"; exit 0 ;;
  "") ;;
  *) echo "usage: usage-guard.sh [--check|--clear]" >&2; exit 2 ;;
esac

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOG"; }

now=$(date +%s)

# A halt already on record is final for the sessions of an autonomous run: they
# stop at the very next tool call, before spending another five minutes of a
# window that is nearly gone.  An interactive session is left alone -- a person
# can see the halt in /issue-pilot:status and decide for themselves.  The
# run driver marks its sessions with ISSUE_PILOT_RUN.
if [ "$MODE" = hook ] && [ -n "${ISSUE_PILOT_RUN:-}" ] && [ -r "$HALT" ]; then
  cat >&2 <<MSG
issue-pilot usage guard: this run is halted -- $(cat "$HALT")

Stop working on this issue now. Do not retry, do not try other tools: every
call will be refused until the window resets and the halt is cleared with
$0 --clear
MSG
  exit 2
fi

if [ "$MODE" = hook ]; then
  last=$(cat "$STATE" 2>/dev/null || echo 0)
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  (( now - last < CHECK_INTERVAL )) && exit 0
  echo "$now" >"$STATE"
fi

# ------------------------------------------------------------------ the token
# Four ways to be authenticated, and a given machine may use any of them.  The
# credentials file is absent when you log in via CLAUDE_CODE_OAUTH_TOKEN, and
# that variable is stripped from the environment Claude Code hands its hook
# subprocesses -- so also recover the export straight from ~/.bashrc.  Reading
# only the credentials file left this guard dead: it exited early on every
# single tool call and never throttled anything.  On macOS a `claude login`
# keeps its credentials in the Keychain instead (there is no credentials file),
# so that is the last resort, tried only when nothing above produced a token.
token="${CLAUDE_CODE_OAUTH_TOKEN:-}"

if [ -z "$token" ]; then
  for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
    [ -r "$rc" ] || continue
    token_line=$(grep -hE '^[[:space:]]*export[[:space:]]+CLAUDE_CODE_OAUTH_TOKEN=' "$rc" | tail -1)
    if [ -n "$token_line" ]; then
      eval "$token_line" 2>/dev/null || true
      token="${CLAUDE_CODE_OAUTH_TOKEN:-}"
      [ -n "$token" ] && break
    fi
  done
fi

if [ -z "$token" ] && [ -r "$CREDS" ]; then
  token=$(python3 -c "
import json, sys
try:
    print(json.load(open('$CREDS'))['claudeAiOauth']['accessToken'])
except Exception:
    sys.exit(1)
" 2>/dev/null) || token=""
fi

if [ -z "$token" ] && [ "$(uname -s 2>/dev/null)" = Darwin ] \
    && command -v security >/dev/null 2>&1; then
  # Bounded, because macOS has no `timeout` and an access prompt could hang a
  # hook.  Any failure leaves the token empty: that is the no-credentials path.
  token=$(python3 -c "
import json, subprocess, sys
try:
    out = subprocess.run(
        ['security', 'find-generic-password', '-s', 'Claude Code-credentials', '-w'],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
        check=True).stdout
    print(json.loads(out)['claudeAiOauth']['accessToken'])
except Exception:
    sys.exit(1)
" 2>/dev/null) || token=""
fi

if [ -z "$token" ]; then
  [ "$MODE" = check ] && echo '{"error":"no credentials available to probe usage"}'
  exit 0
fi

# A 429 asks for a retry-after measured in tens of minutes.  Probing straight
# through it (this hook fires from every session) just wastes calls, so park
# every session until the header says we are welcome back.
if [ "$MODE" = hook ] && [ -r "$BACKOFF" ]; then
  until=$(cat "$BACKOFF" 2>/dev/null || echo 0)
  case "$until" in ''|*[!0-9]*) until=0 ;; esac
  (( now < until )) && exit 0
fi

# Which reading produced the answer cannot be a plain variable -- the readers run
# in a subshell -- so they leave its name here: oauth_usage or headers.
SRC_FILE=$(mktemp 2>/dev/null) && trap 'rm -f "$SRC_FILE"' EXIT || SRC_FILE=/dev/null

# ------------------------------------------------------------------- the probe
# Prints one line per window: '<name> <percent> <seconds-until-reset>'.
# On 429 it records the backoff and prints nothing.
probe() {
  local hdr code retry
  hdr=$(mktemp) || return 0
  code=$(curl -s -m 30 -o /dev/null -D "$hdr" -w '%{http_code}' \
    "$API/v1/messages" \
    -H "Authorization: Bearer $token" \
    -H "anthropic-beta: oauth-2025-04-20" \
    -H "anthropic-version: 2023-06-01" \
    -H "content-type: application/json" \
    -d "{\"model\":\"$PROBE_MODEL\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}" \
    2>/dev/null)

  if [ "$code" = "429" ]; then
    retry=$(grep -i '^retry-after:' "$hdr" 2>/dev/null | tail -1 | tr -d '\r' | awk '{print $2}')
    case "${retry:-}" in ''|*[!0-9]*) retry=1800 ;; esac
    echo "$(( now + retry ))" >"$BACKOFF"
    log "rate limited (429); not probing again for ${retry}s"
    rm -f "$hdr"
    return 0
  fi

  if [ "$code" != "200" ]; then
    log "probe answered HTTP $code; letting the call through"
    rm -f "$hdr"
    return 0
  fi

  rm -f "$BACKOFF"
  echo headers >"$SRC_FILE"
  # Every window the API reports, whatever it is called: pair each
  # anthropic-ratelimit-unified-<window>-utilization with its -reset sibling.
  # Utilization arrives as a 0..1 fraction, but a 0..100 percentage is accepted
  # too in case that ever changes -- anything above 1 is already a percentage.
  python3 -c "
import re, sys, time
h = {}
for line in sys.stdin:
    if ':' in line:
        k, _, v = line.partition(':')
        h[k.strip().lower()] = v.strip()
now = int(time.time())
for key, value in sorted(h.items()):
    m = re.fullmatch(r'anthropic-ratelimit-unified-(.+)-utilization', key)
    if not m:
        continue
    window = m.group(1)
    try:
        u = float(value)
    except ValueError:
        continue
    pct = u * 100 if u <= 1 else u
    try:
        reset = int(h.get('anthropic-ratelimit-unified-%s-reset' % window) or 0)
    except ValueError:
        reset = 0
    left = max(reset - now, 0) if reset else 0
    print('%s %d %d' % (window, round(pct), left))
" <"$hdr" 2>/dev/null
  rm -f "$hdr"
}

# The other reading: GET /api/oauth/usage, free, but only for a token with the
# `user:profile` scope.  Prints the very lines probe() prints, so classify()
# and everything after it are shared.  Returns 10 when the token cannot use the
# endpoint (401/403) so that `auto` can fall back to the header probe; prints
# nothing, and returns 0, for every other way of not getting a reading.
probe_oauth() {
  local body hdr code retry
  body=$(mktemp) || return 0
  hdr=$(mktemp) || { rm -f "$body"; return 0; }
  code=$(curl -s -m 30 -o "$body" -D "$hdr" -w '%{http_code}' \
    "$API/api/oauth/usage" \
    -H "Authorization: Bearer $token" \
    -H "anthropic-beta: oauth-2025-04-20" \
    -H "content-type: application/json" \
    2>/dev/null)

  case "$code" in
    200) ;;
    429)
      retry=$(grep -i '^retry-after:' "$hdr" 2>/dev/null | tail -1 | tr -d '\r' | awk '{print $2}')
      case "${retry:-}" in ''|*[!0-9]*) retry=1800 ;; esac
      echo "$(( now + retry ))" >"$BACKOFF"
      log "rate limited (429); not probing again for ${retry}s"
      rm -f "$body" "$hdr"
      return 0
      ;;
    401|403)
      [ "$SOURCE_MODE" = oauth ] && log "usage endpoint answered HTTP $code; letting the call through"
      rm -f "$body" "$hdr"
      return 10
      ;;
    *)
      log "usage endpoint answered HTTP ${code:-none}; letting the call through"
      rm -f "$body" "$hdr"
      return 0
      ;;
  esac

  rm -f "$BACKOFF"
  # limits[] is the primary source: every entry with a numeric percent and a
  # parseable reset is a window, active or not.  Without it, fall back to the
  # top-level objects that carry both a utilization and a resets_at.  Both are
  # already 0..100.
  if python3 -c "
import json, re, sys, time
from datetime import datetime, timezone

def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)

def seconds_left(ts):
    if not isinstance(ts, str):
        return None
    t = ts.strip()
    if t.endswith('Z'):
        t = t[:-1] + '+00:00'
    t = re.sub(r'(\.\d{6})\d+', r'\1', t)   # fromisoformat() stops at 6 digits
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return max(0, int(d.timestamp() - time.time()))

def slug(text):
    return re.sub(r'[^a-z0-9]', '', str(text).lower())

body = json.load(open(sys.argv[1]))
found = {}
def add(name, pct, ts):
    left = seconds_left(ts)
    if not number(pct) or left is None:
        return
    pct = round(pct)
    if name not in found or pct > found[name][0]:
        found[name] = (pct, left)

for entry in body.get('limits') or []:
    if not isinstance(entry, dict):
        continue
    kind = entry.get('kind')
    if kind == 'session':
        name = '5h'
    elif kind == 'weekly_all':
        name = '7d'
    elif kind == 'weekly_scoped':
        model = ((entry.get('scope') or {}).get('model') or {}).get('display_name')
        name = '7d_' + (slug(model) or 'scoped')
    elif isinstance(kind, str) and kind:
        name = kind
    else:
        continue
    add(name, entry.get('percent'), entry.get('resets_at'))

if not found:
    for key, value in body.items():
        if key == 'limits' or not isinstance(value, dict):
            continue
        if key == 'five_hour':
            name = '5h'
        elif key == 'seven_day':
            name = '7d'
        elif key.startswith('seven_day_'):
            name = '7d_' + key[len('seven_day_'):]
        else:
            name = key
        add(name, value.get('utilization'), value.get('resets_at'))

if not found:
    sys.exit(1)
for name, (pct, left) in found.items():
    print('%s %d %d' % (name, pct, left))
" "$body" 2>/dev/null; then
    echo oauth_usage >"$SRC_FILE"
  else
    log "usage endpoint answered 200 but with nothing readable; letting the call through"
  fi
  rm -f "$body" "$hdr"
}

# Whichever reading USAGE_GUARD_SOURCE asks for, same output as probe().
measure() {
  local rc
  : >"$SRC_FILE"
  case "$SOURCE_MODE" in
    headers) probe ;;
    oauth)   probe_oauth ;;
    *)
      probe_oauth; rc=$?
      [ "$rc" -eq 10 ] && probe
      ;;
  esac
  return 0
}

# ---------------------------------------------------------------- the verdict
# The one place a reading becomes a decision, shared by hook mode (which acts on
# it) and --check (which only reports it), so --check is a dry run of the very
# code that stops a session rather than a second opinion about it.
#
# Reads probe's '<window> <percent> <seconds-until-reset>' lines and prints, per
# window:
#   <window> <percent> <seconds-until-reset> <scope> <verdict> <wait-seconds>
# scope   short | long -- long is a window that resets later than MAX_WAIT
# verdict ok | wait | halt | warn | ignore
# It only classifies: it never sleeps, logs, writes a file or exits non-zero.
classify() {
  local window pct left scope verdict wait_for
  while read -r window pct left; do
    [ -n "${window:-}" ] || continue
    wait_for=0
    if (( left > 0 && left > MAX_WAIT )); then
      # Long window: cannot be waited out.
      scope=long
      verdict=ok
      if (( pct >= LONG_THRESHOLD )); then
        case "$LONG_ACTION" in
          ignore) verdict=ignore ;;
          warn)   verdict=warn ;;
          *)      verdict=halt ;;
        esac
      fi
    else
      scope=short
      verdict=ok
      if (( pct >= THRESHOLD )); then
        verdict="wait"
        # A small buffer past the reset, never a busy-spin, and never longer
        # than MAX_WAIT -- the cap is applied last, so configuring a short one
        # actually shortens the sleep instead of being overridden by the floor.
        wait_for=$(( left + 60 ))
        (( wait_for < 60 )) && wait_for=60
        (( wait_for > MAX_WAIT )) && wait_for=$MAX_WAIT
      fi
    fi
    printf '%s %s %s %s %s %s\n' "$window" "$pct" "$left" "$scope" "$verdict" "$wait_for"
  done
}

# --------------------------------------------------------------- check mode
if [ "$MODE" = check ]; then
  readings="$(measure)"
  SOURCE="$(cat "$SRC_FILE" 2>/dev/null)"
  classify <<<"$readings" | SOURCE="$SOURCE" THRESHOLD="$THRESHOLD" LONG_THRESHOLD="$LONG_THRESHOLD" \
    LONG_ACTION="$LONG_ACTION" MAX_WAIT="$MAX_WAIT" HALT="$HALT" python3 -c "
import json, os, sys
windows = []
for line in sys.stdin:
    parts = line.split()
    if len(parts) == 6:
        windows.append({'window': parts[0], 'percent': int(parts[1]),
                        'seconds_until_reset': int(parts[2]),
                        'scope': parts[3], 'verdict': parts[4],
                        'wait_seconds': int(parts[5])})
try:
    with open(os.environ['HALT']) as fh:
        halt = fh.read().strip() or None
except OSError:
    halt = None
print(json.dumps({
    'source': os.environ.get('SOURCE') or None if windows else None,
    'thresholds': {'short': int(os.environ['THRESHOLD']),
                   'long': int(os.environ['LONG_THRESHOLD']),
                   'long_action': os.environ['LONG_ACTION'],
                   'max_wait': int(os.environ['MAX_WAIT'])},
    'windows': windows,
    'halt': halt,
}, indent=2))
"
  exit 0
fi

# ---------------------------------------------------------------- hook mode
for _ in 1 2 3; do
  readings="$(measure)"
  [ -n "$readings" ] || exit 0
  case "$(cat "$SRC_FILE" 2>/dev/null)" in
    oauth_usage) log "reading via oauth usage" ;;
    *)           log "reading via headers" ;;
  esac

  worst_wait=0
  halt_reason=""
  ok=1

  while read -r window pct left scope verdict wait_for; do
    [ -n "${window:-}" ] || continue
    case "$verdict" in
      halt) halt_reason="${window} window at ${pct}% (threshold ${LONG_THRESHOLD}%), resets in $(( left / 3600 ))h" ;;
      warn) log "warning: ${window} window at ${pct}% (threshold ${LONG_THRESHOLD}%)" ;;
      wait)
        ok=0
        (( wait_for > worst_wait )) && worst_wait=$wait_for
        log "throttling: ${window} window at ${pct}% >= ${THRESHOLD}%, sleeping ${wait_for}s"
        ;;
      ok) [ "$scope" = short ] && log "ok: ${window} window at ${pct}% (threshold ${THRESHOLD}%)" ;;
    esac
  done < <(classify <<<"$readings")

  if [ -n "$halt_reason" ]; then
    printf '%s\n' "$halt_reason" >"$HALT"
    log "halting: $halt_reason"
    # Exit 2 is how a PreToolUse hook blocks a call and tells the model why.
    cat >&2 <<MSG
issue-pilot usage guard: $halt_reason

This window cannot be waited out, so the guard stopped here instead of
exhausting it.  Stop the current run, report this, and do not retry.
Clear it with: $0 --clear
MSG
    exit 2
  fi

  (( ok == 1 )) && exit 0

  sleep "$worst_wait"
  echo "$(date +%s)" >"$STATE"
done

log "resuming after wait"
exit 0
