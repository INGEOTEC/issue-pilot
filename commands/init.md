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

## 1. Put the repository on the base branch

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/base_sync.sh"
```

Do this before anything is asked or written. The configuration is going to be
landed from the base branch at the tip origin has for it, and a dirty tree or a
stale base is much better found now than after the questions. If it refuses,
show the user what it says and stop: it names the way out.

This also holds when the repository already has an `.issue-pilot.json`: init
then proposes changes to it, and the pull request in step 6 updates the file.

## 2. See what the repository suggests

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" detect
```

That inspects the project — Makefile targets, `package.json` scripts, Python
packaging, `Cargo.toml`, `go.mod` — and asks GitHub for the default branch. It is
a proposal, not an answer.

## 3. Check the proposal against the project

This is the part that matters, and the reason a human is in the loop. Read
`CLAUDE.md`, the CI workflows under `.github/workflows/`, the `Makefile`, and the
test layout, and work out what the project **actually** runs. A repository whose
tests live in `tests/test_*.py` but are written with `unittest` will be offered
`pytest -q`, and that is wrong; the CI workflow usually tells the truth.

Note also whether a full run needs anything the bare command omits — network
tests that are skipped by default, a marker that has to be enabled, a package
path.

## 4. Confirm with the user

Ask in a single `AskUserQuestion` batch, offering what you worked out in step 3
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
attention that step 3 needed.

Put one more question in that same batch: **merge the pull request right away?**
Step 6 opens a pull request for the configuration, and merging it is the moment
the configuration becomes binding for everyone's runs, so a person confirms it —
but they should not have to leave the session to do it. Offer "Yes, merge it now"
and "No, I will review it first". If the batch cannot hold it, ask it as one
follow-up question before step 6.

## 5. Write it

If the user chose a `base_branch` other than the one step 1 put the repository
on, move there first, while the tree is still clean — the file has to be written
on the base it configures:

```bash
ISSUE_PILOT_BASE_BRANCH=<chosen> bash "${CLAUDE_PLUGIN_ROOT}/scripts/base_sync.sh"
```

Write `.issue-pilot.json` at the repository root, pretty-printed, with the
settings that differ from the defaults plus `test_command` and `base_branch`
always. Then prove it is valid:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" check
```

If that reports a problem, fix it and check again. Do not report success on an
unchecked file.

## 6. Land it

Everything else in issue-pilot starts from the base branch as it is on origin, so
a configuration that only exists on this machine — committed or not — is exactly
what the next command refuses ("local main is 1 commit(s) ahead of origin/main").
A pull request is the one path that also works when the base branch is protected,
so that is how it gets there, always:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/config_land.sh"            # open the pull request
bash "${CLAUDE_PLUGIN_ROOT}/scripts/config_land.sh" --merge    # ... and merge it now
```

Use `--merge` only if the user said yes to merging. The script puts
`.issue-pilot.json` alone on a branch `configure-issue-pilot`, pushes it, opens
the pull request against the base branch and leaves the repository on the base
branch; its only stdout line is the pull request URL. With `--merge` it also
squash-merges, deletes the branch and brings the local base up to the merge. If
GitHub refuses the merge (approvals required, checks failing) the script still
exits 0, with the pull request open — that is the second case of the report.

If it refuses (exit 2) it names the cause and the way out; show that to the user
and stop. A leftover `configure-issue-pilot` branch is refused rather than
reused: it may be an earlier attempt that someone is still looking at.

## 7. Report

Show the final configuration, say what you inferred versus what the user chose,
and say which of the two cases happened:

- **Merged, and on the base branch.** Everything is in place; point at the next
  step:

  ```
  /issue-pilot:plan <what you want built>
  ```

- **Pull request open.** Give its URL, and say plainly that `/issue-pilot:plan`
  and `/issue-pilot:run` refuse until it is merged and the base branch pulled
  (`base_sync.sh` does the pull). Once it is merged, the next step is the same.
