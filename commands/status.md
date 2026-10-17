---
description: Show the state of the issue-pilot run in this repository
allowed-tools: Bash
---

Report the state of the issue-pilot run in this repository.

1. Ask the quick question first:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" quick
   ```

   If it says no run is in progress, say exactly that and stop — that is not an
   error, it just means nothing has been started here.

   If its `driver` line says **running**, relay the lines it printed (pid,
   progress, guard state) and **stop**: do not run the steps below, do not run
   `--check`, do not read any log and do not summarise. If `guard` says `paused`
   or `halted`, say plainly that this is why the run is not moving (a halt needs
   `usage-guard.sh --clear` and `issues_run.sh --resume --detach`; a pause ends
   by itself).

   If the driver is **not running**, or is **not detached**, go on with the full
   report below.

2. Print the run itself:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" status
   ```

   The `driver` line says whether a detached run is still going.

3. Print how much of the usage windows is left, since that is what decides
   whether a run can continue right now:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/hooks/usage-guard.sh" --check
   ```

4. Read the tail of the most recent log in that directory — `driver.log` for the
   run as a whole, `issue-<n>-attempt-<k>.log` for the issue being worked on. If
   the run stopped on a blocked issue, summarise **why** from that log: the
   actual error, not just "it failed".

5. Summarise for the reader, briefly: the running issue-pilot version and where
   it runs from, what is done, what is pending, whether the driver is still
   running, the model and effort level the run's sessions are using (when the
   state has them), and whether the usage windows allow it now. Name any
   `fix-<k>` items in the plan alongside the issues, the same way `status`
   prints them.

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
