---
description: Close the issues of a run whose pull request has been merged into a non-default branch
argument-hint: [--force]
allowed-tools: Bash
---

Close the issues of this run, now that its pull request is merged.

This exists because GitHub only honours `Closes #<n>` when a pull request merges
into the repository's **default** branch. A run that targets `develop` — or any
other integration branch — leaves its issues open no matter what the body says.
Closing them is then a separate act, and this is it.

1. Read the run and the pull request it produced:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" show
   ```

   If there is no `pull_request` in the state, stop: the run has not reached
   `/issue-pilot:issues-pr` yet.

   If `pull_request.closes_automatically` is true, stop and say so — GitHub has
   already closed them, or will when the pull request merges. Closing them by
   hand would only add noise.

2. Check that it is actually merged:

   ```bash
   gh pr view <number> --json state,mergedAt,mergeCommit,baseRefName
   ```

   If `state` is not `MERGED`, **stop and report it**, unless `$ARGUMENTS`
   contains `--force`. Closing issues whose work is not merged is how a project
   ends up believing something shipped that did not. With `--force`, say in the
   comment that the work is not merged yet and why it is being closed anyway.

3. For every issue of the run, in order, skipping any that is already closed
   (`gh issue view <n> --json state`):

   ```bash
   gh issue comment <n> --body 'Implemented in <pr url>, merged into <base>. Commit <sha>.'
   gh issue close <n>
   ```

   The commit for each issue is in the state under `commits`. Comment first, then
   close: a bare closed issue tells a future reader nothing about where the work
   went.

4. Report which issues were closed, which were already closed, and any that
   failed — with the exact error. Do not report success for an issue whose
   `gh issue close` did not return 0.
