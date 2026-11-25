---
description: Close the issues of a run once its work is merged, when nothing closed them automatically
argument-hint: [--force]
allowed-tools: Bash
---

Close the issues of this run, now that its work is merged.

This exists because nothing closes them by itself in two cases. GitHub only
honours `Closes #<n>` when a pull request merges into the repository's
**default** branch, so a run that targets `develop` — or any other integration
branch — leaves its issues open no matter what the body says. And a run landed
with `push` or `branch` (see the `landing` setting) leaves the merge to you, so
nobody knows yet when the work arrives. Closing them is then a separate act,
and this is it.

1. Read the run and what recorded its end:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" show
   ```

   If there is no `pull_request` in the state, stop: the run has not been
   finished with `/issue-pilot:pr` yet. (That key holds the record of every
   kind of landing, not only a pull request; its `kind` says which.)

   If `pull_request.closes_automatically` is true, stop and say why there is
   nothing left to do: GitHub closes them when the pull request merges, or they
   were already closed when the run landed. Closing them by hand would only add
   noise.

2. Check that the work is actually merged.

   - `kind` is `pull_request`, or absent (the state of an older run):

     ```bash
     gh pr view <number> --json state,mergedAt,mergeCommit,baseRefName
     ```

     `state` has to be `MERGED`.
   - `kind` is `merge`, `push` or `branch`: `head` is the commit the run landed,
     and it has to be in the base branch. For `merge` the check normally passes
     at once, because the landing itself put `head` on the base; it is reached
     only when closing the issues failed at landing time. When
     `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" remote` prints
     `origin`, run `git fetch origin <base>` first, then:

     ```bash
     git merge-base --is-ancestor <head> origin/<base>    # or <base>, with no remote
     ```

     Exit 0 means merged. A squash or rebase merge on the server rewrites the
     commits, so it cannot be recognised this way: that is the case `--force`
     is for.

   If it is not merged, **stop and report it**, unless `$ARGUMENTS` contains
   `--force`. Closing issues whose work is not merged is how a project ends up
   believing something shipped that did not. With `--force`, say in the comment
   that the work is not merged yet and why it is being closed anyway.

3. For every issue of the run, in order, skipping any that is already closed
   (`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_tracker.py" view <n> --json`
   says its `state`; a review fix in the plan is not an issue, skip it):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_tracker.py" comment <n> --body-file <file>
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_tracker.py" close <n>
   ```

   The comment says where the work went — `Implemented in <pr url>, merged into
   <base>. Commit <sha>.` for a pull request, `Implemented on branch <branch>,
   merged into <base>. Commit <sha>.` otherwise — and the commit for each issue
   is in the state under `commits`. Comment first, then close: a bare closed
   issue tells a future reader nothing about where the work went. Write the
   comment to a temporary file rather than passing it inline.

   Afterwards, once, whatever the tracker is (it only does something when the
   issues are files versioned in this repository):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_tracker.py" commit --push \
       -m 'Close issue(s) #<a> #<b> (landed by issue-pilot)'
   ```

4. Tidy the working copy, now that the work is merged. The run is over; it
   should look over here too:

   ```bash
   git checkout <base>
   git pull --ff-only origin <base>      # only when there is a remote
   ```

   Then the local run branch. Delete it **only** when the work is actually
   merged (never under `--force`) **and** nothing is lost with it:

   - the run branch was pushed (`pull_request`, `push`): everything on it is on
     origin —

     ```bash
     git fetch origin <branch>
     git rev-list --count origin/<branch>..<branch>     # must print 0
     ```

   - it was not pushed (`merge`, `branch`): it is an ancestor of the base —
     `git merge-base --is-ancestor <branch> <base>`.

   Then `git branch -D <branch>`. `-D`, not `-d`: after a squash or rebase merge
   git cannot tell the branch is merged, which is what the checks above are for.
   If a check fails, or the work is not merged, keep the branch and say why.

5. Report which issues were closed, which were already closed, and any that
   failed — with the exact error. Do not report success for an issue whose
   close did not return 0. Say which branch the repository is on now and whether
   the run branch was deleted.
