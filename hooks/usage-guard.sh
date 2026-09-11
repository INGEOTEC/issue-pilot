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
# Where the reading comes from: the `anthropic-ratelimit-unified-*` response
# headers of an ordinary /v1/messages call.  NOT /api/oauth/usage, which needs
# the `user:profile` scope that a `claude setup-token` token does not carry --
# against it this guard could only ever get 403/429 and silently do nothing.
# The probe therefore costs one real (1-output-token, Haiku) request per check;
# that is a rounding error against the window it is protecting.
#
# Modes:
#   usage-guard.sh            hook mode: measure, then wait or halt
#   usage-guard.sh --check    probe now regardless of the interval, print JSON,
#                             never sleep, never block (used by the run driver
#                             and by /issue-pilot:issues-status)
#   usage-guard.sh --clear    forget a recorded halt

set -uo pipefail

THRESHOLD="${USAGE_GUARD_THRESHOLD:-70}"          # percent of a short window
LONG_THRESHOLD="${USAGE_GUARD_LONG_THRESHOLD:-90}" # percent of a long one
LONG_ACTION="${USAGE_GUARD_LONG_ACTION:-halt}"    # halt | warn | ignore
CHECK_INTERVAL="${USAGE_GUARD_INTERVAL:-300}"     # seconds between real checks
MAX_WAIT="${USAGE_GUARD_MAX_WAIT:-21600}"         # longest single sleep (6 h)
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

if [ "$MODE" = hook ]; then
  last=$(cat "$STATE" 2>/dev/null || echo 0)
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  (( now - last < CHECK_INTERVAL )) && exit 0
  echo "$now" >"$STATE"
fi

# ------------------------------------------------------------------ the token
# Three ways to be authenticated, and a given machine may use any of them.  The
# credentials file is absent when you log in via CLAUDE_CODE_OAUTH_TOKEN, and
# that variable is stripped from the environment Claude Code hands its hook
# subprocesses -- so also recover the export straight from ~/.bashrc.  Reading
# only the credentials file left this guard dead: it exited early on every
# single tool call and never throttled anything.
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

# --------------------------------------------------------------- check mode
if [ "$MODE" = check ]; then
  probe | python3 -c "
import json, sys
windows = []
for line in sys.stdin:
    parts = line.split()
    if len(parts) == 3:
        windows.append({'window': parts[0], 'percent': int(parts[1]),
                        'seconds_until_reset': int(parts[2])})
print(json.dumps({'windows': windows}, indent=2))
"
  [ -r "$HALT" ] && { echo "halted:"; cat "$HALT"; }
  exit 0
fi

# ---------------------------------------------------------------- hook mode
for _ in 1 2 3; do
  readings="$(probe)"
  [ -n "$readings" ] || exit 0

  worst_wait=0
  halt_reason=""
  ok=1

  while read -r window pct left; do
    [ -n "${window:-}" ] || continue
    if (( left > 0 && left > MAX_WAIT )); then
      # Long window: cannot be waited out.
      if (( pct >= LONG_THRESHOLD )); then
        case "$LONG_ACTION" in
          ignore) : ;;
          warn) log "warning: ${window} window at ${pct}% (threshold ${LONG_THRESHOLD}%)" ;;
          *)    halt_reason="${window} window at ${pct}% (threshold ${LONG_THRESHOLD}%), resets in $(( left / 3600 ))h" ;;
        esac
      fi
    elif (( pct >= THRESHOLD )); then
      ok=0
      # A small buffer past the reset, never a busy-spin, and never longer
      # than MAX_WAIT -- the cap is applied last, so configuring a short one
      # actually shortens the sleep instead of being overridden by the floor.
      wait_for=$(( left + 60 ))
      (( wait_for < 60 )) && wait_for=60
      (( wait_for > MAX_WAIT )) && wait_for=$MAX_WAIT
      (( wait_for > worst_wait )) && worst_wait=$wait_for
      log "throttling: ${window} window at ${pct}% >= ${THRESHOLD}%, sleeping ${wait_for}s"
    else
      log "ok: ${window} window at ${pct}% (threshold ${THRESHOLD}%)"
    fi
  done <<<"$readings"

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
