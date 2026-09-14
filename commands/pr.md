---
description: Open the single pull request at the end of an issue-pilot run
allowed-tools: Bash, Read, Glob, Grep
---

Close the issue-pilot run by opening **one single** pull request.

1. Read the state:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" status
   ```

   If any issue is not `done`, or `blocked` is not null, **do not open a pull
   request**: report the state and stop.

   The driver leaves the repository on the base branch when it finishes, so the
   work is probably not checked out. Get onto the run branch (`branch` in the
   state) — the tree has to be clean first; if it is not, stop and say what is
   in the way:

   ```bash
   git status --porcelain
   git checkout <branch>
   ```

2. Run the full test suite once more, network tests included:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" get test_command
   ```

3. `git push -u origin <branch>`.

4. Open it against the run's base branch, which is in the state:

   ```bash
   gh pr create --base <base_branch> --title '<summary of the run>' --body-file <file>
   ```

   The body: a section per issue, in the order they were implemented; the list of
   commits; `Closes #<n>` for every issue in the run; and, at the end:

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

5. **Work out whether merging will actually close the issues.** GitHub honours
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
   same string.

6. Put the repository back on the base branch — the run branch is pushed now,
   and what stays checked out should be the branch everything starts from:

   ```bash
   git checkout <base_branch>
   ```

7. Report the pull request URL. When the base is not the default branch, say
   plainly that merging will not close the issues and that
   `/issue-pilot:close` does it once the pull request is merged. Do not
   close them now: the work is not merged yet.
