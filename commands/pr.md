---
description: Finish an issue-pilot run: open its pull request, or land it the way the project is configured
allowed-tools: Bash, Read, Glob, Grep
---

Close the issue-pilot run by opening **one single** pull request.

1. Read the state:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" status
   ```

   If any issue is not `done`, or `blocked` is not null, **do not open a pull
   request**: report the state and stop.

   The driver leaves the run branch checked out when it finishes, so the work is
   normally there already. Make sure of it: get onto the run branch (`branch` in
   the state) if the repository is anywhere else — the tree has to be clean
   first; if it is not, stop and say what is in the way:

   ```bash
   git status --porcelain
   git checkout <branch>
   ```

2. Find out how this project lands a run:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" get landing
   ```

   **Anything other than `pull_request`:** run
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/run_land.sh"` in the foreground, with
   the Bash tool's `timeout` at 600000 ms — it runs the full test suite itself,
   merges the base in when it moved, lands the branch the way the project is
   configured, closes the issues when that is the right moment, and records
   the result. Report its output as it is: what it printed on success; on exit
   2 or 1, its message, which names the way out. Then stop — the remaining
   steps are only for a pull request.

   For `pull_request`, continue.

3. Run the full test suite once more, network tests included:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" get test_command
   ```

4. Close the local issues, if the project keeps them as files:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/run_land.sh" --close-issues-only
   ```

   It does nothing unless the issues are local markdown files; then it closes
   them in a commit on this branch, so merging the pull request is what closes
   them. If it exits non-zero, report its message and stop.

5. `git push -u origin <branch>`.

6. Open it against the run's base branch, which is in the state:

   ```bash
   gh pr create --base <base_branch> --title '<summary of the run>' --body-file <file>
   ```

   The body: a section per issue, in the order they were implemented; then,
   only when the plan has any `fix-<k>` items, a **Review fixes** section —
   one entry per fix with its first line (the title), the issues it named (or
   "no issue named" when it named none) and its commit; the list of commits
   (issues and fixes alike); `Closes #<n>` for every issue in the run, and
   **none** for a fix — it corrected something already in the run, not a
   separate piece of work with its own issue to close; and, at the end:

   🤖 Generated with [Claude Code](https://claude.com/claude-code)

   If the repository has a pull request template
   (`.github/PULL_REQUEST_TEMPLATE.md`, or `PULL_REQUEST_TEMPLATE.md` at the
   root or under `docs/`), the body follows **its** structure, with the sections
   above fitted into it. Tick only the checklist items that are actually true
   for this run — an unchecked box the reviewer has to ask about is better than a
   ticked one that was not verified — and say in the body which ones you checked
   and how.

   Pass `--draft` if `pr_draft` is true in the project's configuration. Write the
   body to a temporary file rather than passing it inline: bodies with backticks
   and newlines do not survive shell quoting.

   When `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_tracker.py" kind`
   prints `local`, the issues are not GitHub's: GitHub would link a `Closes
   #<n>` line to unrelated GitHub issues or pull requests. List them as
   `Issues: #1 #2 (local tracker, closed on this branch)` instead of the
   `Closes #<n>` lines.

7. **Work out whether merging will actually close the issues.** GitHub honours
   `Closes #<n>` only when the pull request merges into the repository's
   **default** branch. A project whose runs target `develop`, or any other
   integration branch, gets the link in the UI and nothing else — the issues stay
   open until somebody closes them.

   ```bash
   base=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" get base_branch)
   default=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" default-branch)
   ```

   Record the pull request either way, saying which case this is:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" pr <url> \
       --number <n> --base "$base" [--closes-automatically]
   ```

   Pass `--closes-automatically` **only** when `$base` and `$default` are the
   same string. When the tracker is `local`, skip that comparison: the issues
   were closed on the run branch in step 4, so record the pull request with
   `--closes-automatically`.

8. Put the repository back on the base branch — the run branch is pushed now,
   and what stays checked out should be the branch everything starts from:

   ```bash
   git checkout <base_branch>
   ```

9. Report the pull request URL. When the base is not the default branch, say
   plainly that merging will not close the issues and that
   `/issue-pilot:close` does it once the pull request is merged. Do not
   close them now: the work is not merged yet.
