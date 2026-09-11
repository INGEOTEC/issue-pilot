#!/usr/bin/env bash
# Install issue-pilot without the plugin system.
#
# The plugin is the supported way to install this (see the README); use this
# script when you would rather have plain files under ~/.claude, or when you are
# on a Claude Code old enough not to have `/plugin`.
#
# It copies the toolkit to ~/.claude/issue-pilot/lib and writes the commands to
# ~/.claude/commands/issue-pilot/, so they are invoked exactly as they are under
# the plugin: /issue-pilot:issues, /issue-pilot:issue-plan, and so on.  The
# ${CLAUDE_PLUGIN_ROOT} the commands refer to is rewritten to that lib path,
# since outside a plugin nothing would define it.
#
#   ./install.sh              install or update
#   ./install.sh --hook       also register the usage guard in settings.json
#   ./install.sh --uninstall  remove everything this script installed
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
LIB="${ISSUE_PILOT_HOME:-$CLAUDE_DIR/issue-pilot}/lib"
COMMANDS="$CLAUDE_DIR/commands/issue-pilot"
SETTINGS="$CLAUDE_DIR/settings.json"

WITH_HOOK=0; UNINSTALL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --hook) WITH_HOOK=1; shift;;
    --uninstall) UNINSTALL=1; shift;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \?//'; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

if [[ $UNINSTALL -eq 1 ]]; then
  rm -rf "$LIB" "$COMMANDS"
  echo "removed $LIB and $COMMANDS"
  echo "note: the usage-guard hook, if you registered it, is still in $SETTINGS;"
  echo "      remove the issue-pilot entry from its PreToolUse list by hand."
  echo "note: run state in ${ISSUE_PILOT_HOME:-$CLAUDE_DIR/issue-pilot} was left alone."
  exit 0
fi

for tool in git gh claude python3 curl; do
  command -v "$tool" >/dev/null 2>&1 || echo "warning: \`$tool\` is not on PATH; issue-pilot needs it" >&2
done

mkdir -p "$LIB" "$COMMANDS"
rm -rf "$LIB/scripts" "$LIB/hooks"
cp -r "$SRC/scripts" "$SRC/hooks" "$LIB/"
find "$LIB" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
chmod +x "$LIB"/scripts/*.py "$LIB"/scripts/*.sh "$LIB"/hooks/*.sh

# The commands are written for a plugin, where Claude Code defines
# CLAUDE_PLUGIN_ROOT.  Installed as plain files nothing does, so bake the path in.
for f in "$SRC"/commands/*.md; do
  sed "s|\${CLAUDE_PLUGIN_ROOT}|$LIB|g" "$f" > "$COMMANDS/$(basename "$f")"
done

echo "installed:"
echo "  toolkit  -> $LIB"
echo "  commands -> $COMMANDS  (/issue-pilot:issues, /issue-pilot:issue-plan, ...)"

if [[ $WITH_HOOK -eq 1 ]]; then
  python3 - "$SETTINGS" "$LIB/hooks/usage-guard.sh" <<'PY'
import json, pathlib, shutil, sys

settings_path, guard = pathlib.Path(sys.argv[1]), sys.argv[2]
command = f'bash "{guard}"'

settings = {}
if settings_path.exists():
    try:
        settings = json.loads(settings_path.read_text())
    except json.JSONDecodeError:
        sys.exit(f"{settings_path} is not valid JSON; not touching it")
    shutil.copy(settings_path, str(settings_path) + ".issue-pilot.bak")

hooks = settings.setdefault("hooks", {}).setdefault("PreToolUse", [])
# Replace any entry we installed before instead of stacking another one.
hooks[:] = [h for h in hooks
            if not any("usage-guard.sh" in (inner.get("command") or "")
                       for inner in h.get("hooks", []))]
hooks.append({"matcher": "*",
              "hooks": [{"type": "command", "command": command,
                         "timeout": 21600}]})
settings_path.parent.mkdir(parents=True, exist_ok=True)
settings_path.write_text(json.dumps(settings, indent=2) + "\n")
print(f"  hook     -> registered in {settings_path} (backup: {settings_path}.issue-pilot.bak)")
PY
else
  cat <<EOF

The usage guard is not registered. To have it watch your usage windows, either
re-run with --hook, or add this to the "hooks" section of $SETTINGS:

  "PreToolUse": [
    {
      "matcher": "*",
      "hooks": [
        { "type": "command", "command": "bash \\"$LIB/hooks/usage-guard.sh\\"", "timeout": 21600 }
      ]
    }
  ]
EOF
fi

echo
echo "Restart Claude Code, then try:  /issue-pilot:issue-plan <what you want built>"
