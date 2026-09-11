---
description: Implement a list of GitHub issues autonomously and open a single pull request
argument-hint: <issue numbers, e.g. 165 166 170>
allowed-tools: Bash, Read, Write, Glob, Grep, AskUserQuestion
---

Autonomously implement these issues: $ARGUMENTS

The work is done by the driver, `${CLAUDE_PLUGIN_ROOT}/scripts/issues_run.sh`,
in sessions of its own. You do two things here and nothing else: gather the
answers those sessions will need, and start the driver.

That split is the whole point. Once the driver starts, nobody can be asked
anything — so this is the last moment at which a question is possible, and you
are the one who can ask it.

## 0. Refuse to start unconfigured

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" check
```

If that fails, **stop**: tell the user to run `/issue-pilot:init` first and do
nothing else. Everything below depends on knowing what this project's tests are
and which branch runs start from, and guessing either is how an unattended run
goes quietly wrong.

## 1. Get onto the code the run will start from

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/base_sync.sh"
```

That checks the base branch out, fetches, and fast-forwards it to the tip
origin has for it. The run will start from exactly that commit, so that is
what the issues have to be read against — not the working tree as you found
it, which is usually on a feature branch or a base branch that fell behind.

It refuses three things, and each one is a **stop and report**, in its own
words, with nothing stashed or discarded by you:

- A dirty tree, modified **or untracked**. A run commits as it goes, so anything
  already in the tree would end up inside its commits, attributed to an issue
  it has nothing to do with. List the files; suggest `git stash -u` (and
  `git stash pop` afterwards) for work that is merely in progress.
- A local base branch with commits origin does not have. Those are somebody's
  unpushed work; the run does not include them and will not delete them.
- An origin that cannot be reached.

## 2. Read every issue, against the code the run will start from

All of them, start to finish, in the order given:

```bash
gh issue view <n> --comments
```

Then find out how much the code has moved since each issue was written:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issue_base.py" drift $ARGUMENTS
```

`/issue-pilot:issue-plan` ends every issue it writes with the commit of the
base branch the plan was read against. This prints, per issue, whether that
commit is still the tip or which commits have landed since, and which files
they touched. For an issue that is behind, read those commits next to the
issue — `git log -p <planned>..HEAD -- <files>` is usually enough — looking for
what the plan assumes that is no longer true: a file that moved, a function
that was renamed or removed, a behaviour another change already added or
altered, a step that is now already done. Every such point is either a
question for step 3 or a decision recorded in the notes; none of it may be
left for a session with nobody to ask. An issue with no recorded commit is read
against the tip as it is now, and you say so in the notes.

Then read `CLAUDE.md` and enough of the code each issue touches, in the
working tree — which step 1 put at the tip of origin — to know whether it is
implementable as written. Not just the first issue: a contradiction between
the third and the fifth is exactly the kind of thing that has to surface now.

## 3. Ask everything, once

Put **all** your questions into a single `AskUserQuestion` batch. What counts as
a question: where two readings of an issue lead to materially different work,
where a design decision is not settled, where two issues contradict each other,
where an issue depends on one that is not in the list, where the code has moved
under an issue since it was planned and the plan no longer fits. What does not:
anything a sensible default settles — decide it, and record the default.

Ask as well how long the slowest thing that has to run takes (downloads, sweeps,
training) and how to tell whether it finished. An autonomous session that does
not know this is the most common way a run dies.

## 4. Write the answers down

They are the only thing that survives the clearing between issues, so they go to
disk, written for a session that never saw this conversation:

```bash
NOTES="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" notes-file $ARGUMENTS)"
```

Write to that path: the answers, the defaults you decided, which issue each
point applies to, what has changed in the code since each issue was planned
and how the plan is to be read in the light of it, how long the slow steps
take, and — explicitly — what must **not** be done. Plain text.

## 5. Start the run

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/issues_run.sh" --detach --notes-file "$NOTES" $ARGUMENTS
```

`--detach` puts the run in a session of its own and returns immediately. It has
to: a run takes hours, far longer than any tool call, and a run tied to this
conversation would die with it. The command prints the branch and the driver's
log path.

The driver syncs the base branch with origin once more, exactly as step 1 did,
and cuts the run branch from it right before the first
`/issue-pilot:issues-one` — never from whatever happens to be checked out. The
base branch comes from `.issue-pilot.json` or from the repository's default
branch on GitHub; do not guess it yourself, and do not pass `--from-head`
unless the user explicitly asks to start from the local checkout.

## 6. Report

Tell the user, briefly: the branch, the driver's log path, the questions you
decided yourself and with what defaults, and that the run is now in the
background. Point them at `/issue-pilot:issues-status` to follow it.

Do not tail the log waiting for the run to finish, and do not start implementing
anything yourself.

## What the driver does, so you can read its log

- **Prerequisites and credentials.** It checks `git`, `gh`, `claude` and
  `python3`, that `gh` is authenticated, and resolves a token for the `claude`
  subprocesses it launches: `CLAUDE_CODE_OAUTH_TOKEN`, else that same export in
  your shell rc, else `~/.claude/.credentials.json`. With none of those it exits
  78 before starting rather than failing half-way through.

- **The branch.** Derived from the issue list (`issues-165-166-170`) and cut by
  the driver, right before the first issue, from the base branch after it has
  been checked out, fetched and fast-forwarded to origin's tip — every time. A
  local base branch with commits origin does not have stops the run rather than
  being reset. The commit the branch was cut from is kept in the state. One
  branch for the whole run, not one per issue. When the run finishes the driver checks
  the base branch back out: the work is on the run branch, and a repository
  left on `issues-165-166-170` looks like somebody is still working there. A
  run that stopped early stays on its branch, where the unfinished work is.

- **One issue at a time.** Each issue that does **not** depend on those already
  implemented starts with a clean conversation. Which issue is independent is
  computed by `scripts/issues_plan.py` (an issue depends on an earlier one in
  the list when either mentions the other — `#N` or its URL — in title, body or
  comments), and applied by the driver: a fresh `claude` process for the
  independent ones, resuming the run's own conversation by id for the dependent
  ones — never "the most recent conversation in this directory", which during a
  run is whichever one you opened last. For each issue it
  invokes `/issue-pilot:issues-one <n>`, which implements, tests, commits and
  comments on the issue. It only moves on when the issue is `done`.

- **The model.** Every autonomous session runs on the model and effort level in
  `.issue-pilot.json` (`sonnet`/`high` by default), never on whatever this
  conversation happens to be using.

- **Retries.** A session that ends without marking the issue has almost never hit
  a real blocker: it died mid-task, typically by ending its turn to wait for a
  background process (under `claude -p` that ends the process and kills that
  work). The driver resumes that same conversation with a note to pick up what
  is already there, up to 3 attempts (`--max-attempts N`), giving up earlier if
  two consecutive attempts change absolutely nothing.

- **Explicit blocking.** If a session calls `issues_state.py block`, that *is* an
  answer: the driver stops there without retrying, leaves the commits of the
  issues that did finish intact, and ends **without opening a pull request**.

- **Usage guard.** Between issues the driver checks whether the usage guard has
  halted the run because a window that cannot be waited out (the weekly one) is
  nearly exhausted. If so it stops cleanly and says when to resume.

- **Logs.** The driver's own output goes to
  `$ISSUE_PILOT_HOME/logs/<branch>/driver.log`, and every attempt is saved whole
  to `issue-<n>-attempt-<k>.log` next to it, with `claude`'s exit status at the
  end. Those are the first things to read when a run ends badly.

To see the plan without starting anything — which issues will share a
conversation, and why:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/issues_run.sh" --plan-only $ARGUMENTS
```

Inspect or resume, at any time:

```bash
/issue-pilot:issues-status                                    # the run, in plain words
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" next  # what is next
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issues_state.py" unblock
"${CLAUDE_PLUGIN_ROOT}/scripts/issues_run.sh" --resume --detach  # retry the blocked issue
```

`--resume` does not repeat the questions: the notes are already in the state, and
it returns to the run branch before continuing.

## Phase 3 — pull request

Only if **every** issue in the list is implemented and green
(`/issue-pilot:issues-status` with no `blocked` and everything `done`). Run it
with `/issue-pilot:issues-pr`, or pass `--pr` to the driver to have it done at
the end of the run. Either way the repository is left on the base branch
afterwards, with the work on the pushed run branch.

## Rules

- One pull request at the end; never one per issue.
- Do not implement the issues yourself and do not bypass the driver: the
  clearing between issues is decided by the plan, and outside the driver it
  never happens.
- Never commit data or generated artifacts.
- No force-pushing and no rewriting history that has already been pushed.
- Report results as they are: if something was skipped or failed, say so with
  the output.
