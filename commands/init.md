---
description: Configure issue-pilot for this repository, creating .issue-pilot.json
allowed-tools: Bash, Read, Write, Glob, Grep, AskUserQuestion
---

Set up issue-pilot for this repository.

Nothing else in issue-pilot runs without `.issue-pilot.json`, and that is
deliberate: an unattended run has nobody to ask what "green" means and no way to
tell you it guessed wrong. Decided once here, in writing, it costs a minute;
discovered to be wrong three issues into a run, it costs the run.

If `$ARGUMENTS` is `--show`, print the current configuration
(`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" show`) and stop.

## 1. See what the repository suggests

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" detect
```

That inspects the project — Makefile targets, `package.json` scripts, Python
packaging, `Cargo.toml`, `go.mod` — and asks GitHub for the default branch. It is
a proposal, not an answer.

## 2. Check the proposal against the project

This is the part that matters, and the reason a human is in the loop. Read
`CLAUDE.md`, the CI workflows under `.github/workflows/`, the `Makefile`, and the
test layout, and work out what the project **actually** runs. A repository whose
tests live in `tests/test_*.py` but are written with `unittest` will be offered
`pytest -q`, and that is wrong; the CI workflow usually tells the truth.

Note also whether a full run needs anything the bare command omits — network
tests that are skipped by default, a marker that has to be enabled, a package
path.

## 3. Confirm with the user

Ask in a single `AskUserQuestion` batch, offering what you worked out in step 2
as the first option and the detected value as an alternative:

- **`test_command`** — what has to be green before an autonomous session may
  commit. The one setting with no sensible fallback. A project with no tests can
  use a command that exits 0, but say so out loud.
- **`base_branch`** — which branch runs start from and pull requests target, when
  the repository's default is not it.
- **`model`** and **`effort`** — what the autonomous sessions run on, regardless
  of what you are running on now. `sonnet`/`high` unless the work is unusually
  hard.

Do not ask about `branch_prefix`, `max_attempts`, `pr_draft` or `issue_labels`
unless the user brings them up: the defaults are fine and each question spends
attention that step 2 needed.

## 4. Write it

Write `.issue-pilot.json` at the repository root, pretty-printed, with the
settings that differ from the defaults plus `test_command` and `base_branch`
always. Then prove it is valid:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" check
```

If that reports a problem, fix it and check again. Do not report success on an
unchecked file.

## 5. Commit it

This is project configuration, not personal preference: it belongs in the
repository so that everybody's runs behave the same way. Offer to commit it —
`Configure issue-pilot for this repository` — and do so if the user agrees and
the working tree is otherwise clean. Never sweep unrelated changes into that
commit.

## 6. Report

Show the final configuration, say what you inferred versus what the user chose,
and point at the next step:

```
/issue-pilot:issue-plan <what you want built>
```
