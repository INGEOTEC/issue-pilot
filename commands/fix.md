---
description: Record a review finding on a finished run and have the driver correct it on the run branch
argument-hint: <what the review found>
allowed-tools: Bash, Read, Write, Glob, Grep, AskUserQuestion
---

Record a review fix for: $ARGUMENTS

This is the step between a finished run and `/issue-pilot:pr`: you reviewed the
branch, found something wrong or worth improving, and want it corrected on that
same branch, without pinning it on one of the original issues (it may not fit
any of them, or it may touch several). A fix is not a GitHub issue — it is
recorded in the run state as a plan item of its own (`fix-<k>`) and applied by
the same driver that implemented the issues.

## 0. Refuse to start unconfigured

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" check
```

If that fails, **stop**: tell the user to run `/issue-pilot:init` first.

## 1. Read the run and refuse the wrong states

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" status
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" show
```

- No run in progress: **stop**, say so in plain words.
- Any issue still `pending`, or `blocked` is not null: **stop**, say what is in
  the way and point at `issues_run.sh --resume` to get the run itself to a
  finished state first. A fix session is not the place to also chase down a
  stuck issue.
- `pull_request` is already recorded: **stop**. The branch is already under
  review out there; a finding at this point goes into the pull request by hand
  (a commit and a comment), not through this command.

## 2. Read the work, without checking anything out

The run branch is not checked out here — the driver does that itself when it
applies the fix. Everything needed is reachable through git refs, from wherever
the repository currently is:

```bash
git log --oneline <base_commit>..<branch>
git diff --stat <base_commit>..<branch>
git show <branch>:<path/to/file>          # the code the finding touches
```

`<base_commit>` and `<branch>` are in the state. Read the texts of the run's
issues too (`gh issue view <n> --comments`, for each `issue` in the plan that is
a number, not a `fix-<k>`) — the finding is usually about work one of them
described, and their text is context for what "correct" means here.

## 3. Ask what is not obvious, once

One `AskUserQuestion` batch, only for what the diff and the issues do not
already settle:

- Which issue(s), if any, this fix corrects — when it is not obvious which
  piece of work it belongs to. Naming none is a legitimate answer: it just means
  the fix's session starts from a clean conversation instead of resuming one.
- How "fixed" is checked, beyond the test suite — a specific behaviour to
  observe, a command to run.
- Anything the fix must explicitly leave alone.

Anything a sensible default settles is not a question: decide it and record the
default in the spec below.

## 4. Write the spec and record it

Write a temporary file with these sections:

```markdown
## What is wrong


## Where
Files, and the commits (from step 2) that introduced it.

## Fix


## How to check
The test command from the project's configuration, and anything else that
must be observable.

## Out of scope

```

Then:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" fix-add \
    --description-file <path> [--issues <n> ...]
```

Omit `--issues` when the fix names no issue. This prints the fix's id
(`fix-1`, `fix-2`, ...) and appends it, `pending`, to the plan — refused (exit
2, with a reason) if a pull request is already recorded, an issue is not
`done`, the run is blocked, a named issue is not part of it, or the
description turned out empty; any of those means step 1 or step 3 above missed
something, so re-read the message and go back rather than retrying blindly.

## 5. Start the driver

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/issues_run.sh" --fix --detach
```

`--fix` continues the run recorded in the state: no questions, no
re-initialisation, no unblocking of anything else — it applies the one fix that
is now `pending` and returns to the base branch when done. `--detach` puts it
in a session of its own, exactly as `/issue-pilot:run` does, and returns at
once with the log path.

## 6. Report

Tell the user: the fix's id, which issues it names (or that it names none and
will start from a clean conversation), the defaults you decided in step 3, and
the driver's log path. Point them at `/issue-pilot:status` to follow it and say
that `/issue-pilot:pr` is the step after every fix is applied.

Do not implement the fix yourself, do not check the run branch out, and do not
touch anything else in the repository.
