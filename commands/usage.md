---
description: Show how much of the Claude usage windows is left and what the usage guard would do about it
allowed-tools: Bash
---

Show how much of the usage windows is left, and what the usage guard would do
about it right now.

1. Take a live reading:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/hooks/usage-guard.sh" --check
   ```

   This is the same script the `PreToolUse` hook runs on every tool call, and
   that is the point of this command: `--check` is a dry run of the thing that
   pauses sessions, using the same code to decide, not a second implementation
   of it. Do not compare percentages against thresholds yourself — report the
   `verdict` it gives.

   Tell the reader it costs one 1-token Haiku request, and that the figures are
   a live reading, not a cached one.

2. The output is one JSON object:

   - `windows` — one entry per window the API named (`5h`, `7d`, `overage`,
     whatever comes back; do not assume a fixed list) with `percent`,
     `seconds_until_reset`, `scope` (`short` is waited out, `long` cannot be),
     `verdict` and `wait_seconds`.
   - `thresholds` — the limits the verdicts were reached under.
   - `halt` — the text of a halt already on record, or `null`.

   Report each window by its name, its percentage, how long until it resets as
   `Xh Ym`, and what the guard would do with it:

   - `ok` — calls go through.
   - `wait` — the session would pause until the reset; name the wait
     (`wait_seconds`, as `Xh Ym`).
   - `halt` — the run would be halted, because the window cannot be waited out.
   - `warn` — it would only log a warning (`USAGE_GUARD_LONG_ACTION=warn`).
   - `ignore` — it would do nothing (`USAGE_GUARD_LONG_ACTION=ignore`).

   Mention the thresholds briefly so the reader knows what "over" means.

3. When `halt` is not `null`, say a halt is already on record and quote it. Say
   that sessions of an autonomous run are refused until it is cleared, and print
   the command for the user to run themselves — do not run it:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/hooks/usage-guard.sh" --clear
   ```

   A halt means a window that cannot be waited out is nearly gone, so clearing
   it is a decision for the user, not a side effect of looking.

4. When the output is `{"error": ...}`, say the guard cannot measure right now
   and that it therefore lets every tool call through: a run is unprotected,
   not blocked. When `windows` is empty with no error, the probe returned
   nothing usable (rate limited or an unexpected answer) and the guard likewise
   lets calls through.

This command is read-only. Do not modify anything, do not run the hook (no
argument), do not clear a halt, and do not start, resume or stop a run.
