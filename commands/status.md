---
description: Show the state of the issue-pilot run in this repository
allowed-tools: Bash
---

Report the state of the issue-pilot run in this repository.

1. Print the run itself:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" status
   ```

   If it says no run is in progress, say exactly that and stop — that is not an
   error, it just means nothing has been started here.

   The `driver` line says whether a detached run is still going. **Running** and
   stalled are not the same thing: check the tail of `driver.log` to see what it
   is doing before telling the user it is stuck.

2. Print how much of the usage windows is left, since that is what decides
   whether a run can continue right now:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/hooks/usage-guard.sh" --check
   ```

3. Read the tail of the most recent log in that directory — `driver.log` for the
   run as a whole, `issue-<n>-attempt-<k>.log` for the issue being worked on. If
   the run stopped on a blocked issue, summarise **why** from that log: the
   actual error, not just "it failed".

4. Summarise for the reader, briefly: what is done, what is pending, whether the
   driver is still running, what the next step is (`/issue-pilot:pr` when
   everything is done, `issues_run.sh --resume --detach` when something is
   blocked), and whether the usage windows allow it now.

Do not implement anything, do not modify the run state, and do not wait for a
running driver to finish — report where it is and stop.
