---
description: Configure issue-pilot for this repository (GitHub or not), creating .issue-pilot.json
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
then proposes changes to it, and step 6 lands the update the way the project
lands things.

## 2. See what the repository suggests

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" detect
```

That inspects the project — Makefile targets, `package.json` scripts, Python
packaging, `Cargo.toml`, `go.mod` — and asks GitHub for the default branch when
the repository is on GitHub. It is a proposal, not an answer. Then:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" hosting
```

prints `{"origin": ..., "github": ...}`, judged from the URL of `origin`, offline.
`github: true` is the flow issue-pilot always had: issues on GitHub, runs that
end in a pull request. Anything else — a remote that is not GitHub (Overleaf's
git, a self-hosted server) or no remote at all — is a project with a git of its
own: `detect` then proposes `tracker: local` and `landing: merge`, and the
questions of step 4 are about that.

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

`AskUserQuestion` takes at most four questions per call, and there are more than
that, so this is **two calls, in this order**, and nothing else is asked.

**First call — where things live.** Offer the detected value first, marked as
recommended.

1. **Issues** (`tracker`): GitHub issues, or markdown files in a directory of
   this project. Recommended: what `detect` proposed.
2. **Finishing a run** (`landing`). On GitHub, `pull_request` is recommended.
   Otherwise recommend `merge`, described in plain words: the run branch is
   brought up to date with the base, tested, and becomes the base — pushed to
   the remote as the base branch when there is one. Say that this is what a
   single-branch remote such as Overleaf's git needs. Offer `push` (the branch
   goes to your server and you merge it there) and `branch` (nothing leaves this
   machine) as the others. Do not offer `pull_request` when `hosting.github` is
   false, nor `push` when `hosting.origin` is false.
3. **`.issue-pilot.json`**: versioned in the project's git, or kept out of it
   (`.git/info/exclude`: only this clone knows). Recommended: versioned on
   GitHub, kept out otherwise. Say what each costs: versioned is shared and
   travels with the repository, but it is a commit on the base branch that
   everybody sees; kept out leaves no trace, but has to be recreated in every
   clone.
4. **The issues directory** — only when the answer to 1 is local files; if 1 is
   answered "GitHub", skip this question. Versioned or kept out, with the same
   two costs, plus this one: versioned issues are committed to the base branch
   by `/issue-pilot:plan` every time one is written. Recommended: the same
   choice as question 3 is recommended to be on this hosting. The directory is
   `issues` unless the user names another one (the "Other" answer).

   Because question 4 depends on question 1 and all four go in one call: always
   include it when `detect` proposed `local`; when `detect` proposed `github`
   leave it out, and ask it in a follow-up call only if the user chose local
   files anyway.

**Second call — how runs behave.** The existing questions, offering what you
worked out in step 3 as the first option and the detected value as an
alternative:

- **`test_command`** — what has to be green before an autonomous session may
  commit. The one setting with no sensible fallback. A project with no tests can
  use a command that exits 0, but say so out loud.
- **`base_branch`** — which branch runs start from, and pull requests or the
  landing target, when the repository's default is not it. Without GitHub there
  is no default to fall back on, so this one is always written down.
- **`model`** and **`effort`** — what the autonomous sessions run on, regardless
  of what you are running on now (one question for both). `sonnet`/`high` unless
  the work is unusually hard.
- Only when the configuration is to be versioned **and** the landing is
  `pull_request`: **merge the configuration pull request right away?** Step 6
  opens a pull request for the configuration, and merging it is the moment the
  configuration becomes binding for everyone's runs, so a person confirms it —
  but they should not have to leave the session to do it. Offer "Yes, merge it
  now" and "No, I will review it first". If the call cannot hold it, ask it as
  one follow-up question before step 6.

Do not ask about `branch_prefix`, `max_attempts`, `pr_draft` or `issue_labels`
unless the user brings them up: the defaults are fine and each question spends
attention that step 3 needed.

## 5. Write it

If the user chose a `base_branch` other than the one step 1 put the repository
on, move there first, while the tree is still clean — the file has to be written
on the base it configures:

```bash
ISSUE_PILOT_BASE_BRANCH=<chosen> bash "${CLAUDE_PLUGIN_ROOT}/scripts/base_sync.sh"
```

When the configuration is to be kept out of git, first run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" exclude .issue-pilot.json
```

**before** writing the file. If it refuses because the file is already
versioned, show its message and stop: it says how a versioned file becomes an
ignored one.

Write `.issue-pilot.json` at the repository root, pretty-printed, with
`test_command`, `base_branch`, `model` and `effort` always, `tracker` and
`landing` too, `issues_dir` when the tracker is local, plus any other setting that
differs from its default. `model` and `effort` are written even
when they are the defaults: the file should say what the runs use, not leave it
to the plugin's defaults, which can change between versions. Then prove it is
valid:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" check
```

When the tracker is local, make the directory with `mkdir -p <issues_dir>`, and
when the issues are to be kept out of git run
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" exclude <issues_dir>/`.

If that reports a problem, fix it and check again. Do not report success on an
unchecked file.

## 6. Land it

Everything else in issue-pilot starts from the base branch as it is on origin
(or, with no remote, the local base), so a configuration that only exists on this
machine — committed or not — is exactly what the next command refuses ("local
main is 1 commit(s) ahead of origin/main"). Exactly one of:

- **The configuration is kept out of git:** there is nothing to land. Say so.
- **Versioned, and the landing is `pull_request`:** a pull request is the one
  path that also works when the base branch is protected, so that is how it
  gets there:

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
- **Versioned, and any other landing:** there is no review step to route a
  configuration through, so it is committed straight to the base branch, and
  pushed when there is a remote:

  ```bash
  bash "${CLAUDE_PLUGIN_ROOT}/scripts/config_land.sh" --direct
  ```

  Its only stdout line is `committed <sha> on <base>` (and pushed to origin). If
  the remote refuses the push it undoes the commit, leaves the file as an
  uncommitted change and exits 1; tell the user, and offer to keep the
  configuration out of git instead (`pilot_config.py exclude .issue-pilot.json`).

If the script refuses (exit 2) it names the cause and the way out; show that to
the user and stop. A leftover `configure-issue-pilot` branch is refused rather
than reused: it may be an earlier attempt that someone is still looking at.

## 7. Report

Show the final configuration, and say what you inferred versus what the user
chose. Then, asking `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pilot_config.py" tracking <path>`
for each — do not repeat the answer from memory — whether `.issue-pilot.json`
and, with a local tracker, the issues directory are `versioned` or `ignored`.
Say which landing was chosen and what `/issue-pilot:pr` will therefore do
(open a pull request; merge into the base and push it; push the branch; leave
the branch), and then which case happened:

- **Landed, and on the base branch** (merged, committed directly, or nothing to
  land because the file is kept out of git). Everything is in place; point at
  the next step:

  ```
  /issue-pilot:plan <what you want built>
  ```

- **Pull request open** (the GitHub flow). Give its URL, and say plainly that
  `/issue-pilot:plan` and `/issue-pilot:run` refuse until it is merged and the
  base branch pulled (`base_sync.sh` does the pull). Once it is merged, the next
  step is the same.
