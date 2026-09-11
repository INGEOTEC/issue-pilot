---
description: Implement ONE issue of an issue-pilot run, in a possibly clean conversation
argument-hint: <issue number>
allowed-tools: Bash, Read, Edit, Write, Glob, Grep
---

Implement **one single** issue of the run in progress: $ARGUMENTS

This conversation may have just been cleared (`/clear`): do not assume you
remember anything about the previous issues. All the context of the run is in
the state on disk, not in the history.

## First of all: how NOT to kill the run

You are running inside `claude -p`, with nobody watching. **Ending your turn ends
the process**: the session dies and every process you started in the background
is killed at that same instant. The two times this driver has failed, it was for
the same reason — a download and a sweep started with `run_in_background`, and
the turn ended "to wait for the notification". The work was written and green,
and it was lost entirely because it had not been committed.

So:

- **Never end your turn in order to wait.** No "I'll wait for the notification",
  "waiting for it to finish" or "I'll pick this up when it completes". That is
  not a pause, it is the end of the run.
- For a long command, in this order: (1) foreground, with the Bash tool's
  `timeout`, up to 600000 ms; (2) if you already started it with
  `run_in_background`, wait on it with `TaskOutput(task_id, block=true,
  timeout=600000)`, calling it again until it finishes; (3) if it takes longer,
  make it resumable (`nohup`, a log and a sentinel file) and keep making tool
  calls while you check the sentinel. Foreground `sleep` is blocked; do not try
  to work around it.
- **Commit as soon as the tests are green**, before any optional polish. A commit
  survives the session dying; a dirty working tree, in practice, does not.
- Nobody will answer a question. Anything the run's notes do not settle, you
  decide with a sensible default and document in the issue comment.

## What to do

1. Read the state of the run (branch, the answers gathered up front,
   dependencies):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" show
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" next
   ```

   The issue you are given is the one `next` reports. If `next` reports a
   different number, **stop** and report it: the state and the argument disagree.

2. Get onto the run branch (`branch` in the state). The driver creates it before
   starting; do not create another one and do not `reset --hard`:

   ```bash
   git checkout <branch>
   ```

3. **Check what is already done before doing anything.** You may be the second or
   third attempt on this issue because an earlier session died mid-task: look at
   `git log --oneline`, `git status` and the artifacts the issue produces, and
   continue from there instead of redoing it. If a long process was left
   half-way, start it again and wait for it as described above.

4. Read the issue (`gh issue view <n> --comments`), `CLAUDE.md` and the code it
   touches. If `depends_on` is not empty, also read the commits of those issues
   (`git log --oneline` on the branch) — that is your only source about them.

5. Implement it following the project's conventions in `CLAUDE.md`, add or update
   tests, and run **all** the tests of the affected package, network tests
   included. The command comes from the project's configuration:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" get test_command
   ```

   If that prints nothing, work the command out from the project itself
   (`CLAUDE.md`, `Makefile`, `pyproject.toml`, CI workflow) and say in the issue
   comment which one you used.

6. **Only with everything green**, make a commit of your own for the issue:
   `<summary> (issue #<n>)`, comment on the issue with
   `gh issue comment <n> --body '...'` (what was implemented, branch, commit,
   tests and their result, defaults you decided), and mark the issue finished:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" done <n> --commit "$(git rev-parse HEAD)"
   ```

   Marking it `done` is the last step and it is mandatory: the driver only moves
   on if the state says so, not if you say so in prose.

7. If you **cannot** finish it (red tests you cannot fix, or a real blocker):
   touch nothing else, comment the blocker on the issue with the exact output,
   and mark it explicitly:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" block <n> --reason '...'
   ```

   Marking it `block` stops the run deliberately and without retries, so use it
   only for a real blocker. Ending your turn without marking anything is not a
   way to give up: the driver treats that as an accidental death and launches
   you again on the same issue.

Do not open a pull request here, and do not start any other issue: the driver
(`issues_run.sh`) decides what comes next and whether the conversation is
cleared.
