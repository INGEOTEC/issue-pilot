# Changelog

All notable changes to issue-pilot are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-11

First public release.

### Added

- `/issue-pilot:issues` — asks, in your own session, everything an unattended
  run will need, then starts that run in the background: a list of GitHub issues
  implemented one at a time with a single pull request at the end, clearing the
  conversation between issues that do not depend on each other.
- `/issue-pilot:issues-one` and `/issue-pilot:issues-pr` — the per-issue and
  pull-request steps the driver invokes.
- `/issue-pilot:init` — inspects the project, proposes a configuration and
  confirms it with you. Nothing else runs until `.issue-pilot.json` exists.
- `/issue-pilot:issue-plan` — interviews you and opens a well-formed
  implementation issue that an unattended run can execute. A hook refuses it
  when given nothing to plan: the plan is written from what was said, never
  inferred.
- `/issue-pilot:issues-close` — closes the run's issues once its pull request is
  merged, for the projects where GitHub will not: `Closes #<n>` is honoured only
  on a merge into the repository's default branch.
- `/issue-pilot:issues-status` — the state of the run in plain words, together
  with how much usage window is left.
- `scripts/issues_plan.py` — the deterministic dependency rule that decides
  which issues start from a clean conversation.
- `scripts/issues_state.py` — run state on disk, so a run survives the clearing
  between issues: plan, the answers gathered up front, per-issue status,
  attempts and commits.
- `scripts/issues_run.sh` — the driver: one session per issue, addressed by id
  so other conversations opened in the repository during a run are never picked
  up by mistake; retries that resume a dead session rather than restarting it;
  an explicit distinction between a crash and a declared blocker; `--detach` so
  a run outlives whatever started it; `--plan-only` to read the plan before
  committing to it; and a run branch that is always cut from the tip of the base
  branch on origin, fetched first, without touching any local branch
  (`--from-head` to opt out).
- `scripts/base_worktree.sh` — a worktree of `origin/<base>` that the planning
  commands read code from, so a plan is written against the code the work will
  land on and not against whatever is checked out.
- `hooks/usage-guard.sh` — measures every usage window the API reports; waits
  out a window that resets soon, halts the run on one that cannot be waited out
  (the weekly one), and once halted refuses an autonomous session's very next
  tool call while leaving interactive sessions alone.
- `.issue-pilot.json` — required per-repository configuration (base branch, test
  command, model, effort, attempts), overridable per run from the environment and
  validated before a run may start.
- `install.sh` — installation without the plugin system, under the same command
  names.
- Tests covering the dependency rule, the run state, configuration precedence
  and the guard's decisions, using only the standard library.

[Unreleased]: https://github.com/INGEOTEC/issue-pilot/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/INGEOTEC/issue-pilot/releases/tag/v0.1.0
