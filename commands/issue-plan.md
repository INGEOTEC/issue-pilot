---
description: Turn an idea into a well-formed implementation issue that an autonomous run can execute
argument-hint: <what you want built> | --update <issue number>
allowed-tools: Bash, Read, Write, Glob, Grep, WebFetch
---

Write the implementation issue for: $ARGUMENTS

This is the front half of issue-pilot. `/issue-pilot:issues` will later hand this
issue to a session that has **nobody to ask** — so everything that session will
need has to be decided here, while you still have a person in front of you.

If `$ARGUMENTS` starts with `--update`, load the issue that follows with
`gh issue view <n> --comments` and rewrite it into the shape below instead of
creating a new one, preserving anything already agreed in its comments.

## 0. Refuse to start unconfigured

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" check
```

If that fails, **stop**: tell the user to run `/issue-pilot:init` first and do
nothing else. Everything below depends on knowing what this project's tests are
and which branch runs start from, and guessing either is how an unattended run
goes quietly wrong.

## 1. Read the code where the work will land

Not the working tree. It may be on a feature branch, behind origin, or in the
middle of something else, and a plan written against that is a plan for the
wrong codebase. Check out the tip of the base branch **as it is on origin** in a
worktree of its own:

```bash
BASE_TREE="$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/base_worktree.sh" add)"
```

That fetches first, every time, and prints a path outside the repository. Read
`CLAUDE.md`, the code and the tests **under `$BASE_TREE`**, not under the
current directory. If it fails because origin is unreachable, stop and say so:
do not fall back to whatever is checked out.

## 2. Read before you ask

Explore first, so your questions are about decisions and not about facts you
could have looked up — all of it under `$BASE_TREE`:

- `CLAUDE.md` and the project's conventions (tests, layout, style, base branch).
- The code the change touches: where it would live, what already does something
  similar, what would have to change with it.
- The existing issues that overlap: `gh issue list --search '<keywords>' --state all`.
- `.issue-pilot.json`, for the test command and base branch the issue must
  name.

## 3. Ask everything, once

Put **all** your open questions into a single `AskUserQuestion` batch. What
counts as a question: where two readings lead to materially different work, a
design decision the request does not settle, a trade-off with no obvious default,
or scope that could reasonably be cut. What does not: anything a sensible default
settles — decide it, and record it under "Decisions" so it is on the record.

Ask as well, when they are not already obvious:

- How large is this really? If it is more than one coherent commit, propose
  splitting it into several issues and say which order they go in.
- How is "done" checked — which test, which command, which observable behaviour?
- Anything slow the implementation will have to run (downloads, training,
  sweeps), roughly how long it takes, and how to tell it finished. An autonomous
  session that does not know this is the single most common way a run dies.

## 4. Write the issue

One issue = one coherent piece of work with its own commit and its own tests.
Use exactly these sections; the autonomous session reads them in this order:

```markdown
## Context
Why this is being done, and what is true about the code today. Enough for
somebody who has never seen this conversation.

## Scope
What is in. Then, explicitly, **Out of scope:** what is not — this is what keeps
an unattended session from wandering.

## Implementation plan
Numbered steps, each naming the files or modules it touches. Concrete enough to
follow, not so prescriptive that it forbids a better local solution.

## Acceptance criteria
- [ ] Checkable statements, one per line. Behaviour, not implementation.

## Tests
Which tests to add or change, where they go, and the exact command that must be
green.

## Decisions
The defaults you settled here, with a one-line reason each. This is what stops a
later session from re-litigating them.
```

**Dependencies, deliberately.** issue-pilot decides whether a later issue starts
from a clean conversation by looking for `#N` mentions between the issues of a
run. So mention a sibling issue when the work genuinely continues from it — that
is how they get to share context — and do *not* mention one merely as background
reading, because that will force them into the same conversation and spend
context on it. Say which it is in the text: "builds on #12" versus "see #12 for
history".

## 5. Create it

```bash
gh issue create --title '<imperative, specific, under ~70 chars>' --body-file <file>
```

Add labels from `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" get issue_labels`
if it prints any. Write the body to a temporary file rather than passing it
inline: bodies with backticks and newlines do not survive shell quoting.

If the work was split, create the issues in dependency order and cross-reference
them as described above.

## 6. Report

Print the issue URLs and the exact command to implement them:

```
/issue-pilot:issues <n> [<n> ...]
```

Also say, in one line each, which questions you decided yourself and what the
defaults were — the user should be able to catch a wrong default here, before a
run spends an hour on it.

Finally, drop the worktree:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/base_worktree.sh" remove
```

Do not implement anything and do not touch the working tree.
