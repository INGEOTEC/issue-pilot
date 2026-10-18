---
description: Show the state of the issue-pilot run in this repository
allowed-tools: Bash
---

Report the state of the issue-pilot run in this repository.

Run this one command and show its output; it decides by itself how much to
gather, so run nothing else:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" report
```

If it says no run is in progress, say exactly that and stop — that is not an
error, it just means nothing has been started here.

Then follow the rule for its first line.

**`report     : quick`** — the run is going. Relay the lines as they are, with no
summary, and stop. If the `guard` line says `paused` or `halted`, say plainly
that this is why the run is not moving (a halt stays until the
user clears it and resumes the run; a pause ends by itself).

**`report     : full`** — the run is not going. Write a brief summary of what the
output shows: the running issue-pilot version and where it runs from, what is
done and what is pending (naming any `fix-<k>` items alongside the issues, the
way the table does), the driver, the model and effort level the run's sessions
are using (when the state has them), and whether the usage windows allow a run
now. If the run stopped on a blocked item, say **why**, from the log tails in the
output: the actual error, not just "it failed".

What the next step is:

- Something `blocked`: `issues_run.sh --resume --detach`.
- Every issue `done` but no `pull_request` recorded yet: `/issue-pilot:pr`
  to close the run, or `/issue-pilot:fix <finding>` first if a review of the
  branch turned up something to correct — it goes onto the same branch and
  the same pull request, so there is no need to decide between them; a fix
  can be added and applied as many times as review turns up findings.
- `pull_request` is recorded: point at it and, if it is not merged into the
  default branch, at `/issue-pilot:close` once it is.

Do not implement anything, do not modify the run state, and do not wait for a
running driver to finish — report where it is and stop.
