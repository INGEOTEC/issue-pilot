# Changelog

All notable changes to issue-pilot are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-09-22

### Added

- **Review fixes.** Between a finished run and `/issue-pilot:pr` there is
  usually a review of the branch; a finding from it is now `/issue-pilot:fix
  <what you found>` instead of a rerun over the same issue list. It records
  the finding as a plan item of its own (`fix-1`, `fix-2`, ...) directly in
  the run state — `scripts/issues_state.py` gained `fix-add`, and `done`,
  `block`, `attempt` and `unblock` now accept a fix id as well as an issue
  number — and `scripts/issues_run.sh --fix` applies it on the run branch,
  retried and blocked exactly like an issue, with no GitHub issue required. It
  goes into the pull request in its own **Review fixes** section, with no
  `Closes` for it.
- `hooks/require_request.py` now also refuses a bare `/issue-pilot:fix`, in
  the same shape as the existing `/issue-pilot:plan` refusal.

### Changed

- `issues_run.sh` refuses to start a run over the same issue list when its
  state already has every item `done`, instead of silently forgetting which
  issues were already implemented: that path is now `/issue-pilot:fix` for a
  review finding, or deleting the branch and state file on purpose to start
  over.
- `commands/one.md` now handles a `fix-<k>` item: its spec is the fix's
  description rather than a GitHub issue, its commit message names the issues
  it corrects (`<summary> (review fix-<k>: #4 #3)`, or `(review fix-<k>)` for
  none), and it comments only on the issues it names.

## [0.2.1] - 2026-09-17

### Added

- `/issue-pilot:status` now prints, as its first line, the version of
  issue-pilot producing the report and the path it is running from. A missing
  model line used to have two indistinguishable causes -- a run started before
  that field existed, or a reader running an older copy of the tool -- and this
  removes the second one: the version line always names which copy is talking.
  `scripts/pilot_config.py` gained a `version` subcommand for it, and
  `install.sh` now ships `.claude-plugin/plugin.json` into the lib it installs,
  so the plain-files install reports its version too instead of "unknown".

## [0.2.0] - 2026-09-14

### Added

- `/issue-pilot:status` now reports the model and effort level the run's
  sessions are being launched on. The driver records them in the run state
  when it starts handing out issues, and again on a `--resume`, so what is
  reported is what the sessions are really using rather than what
  `.issue-pilot.json` happens to say now; `scripts/issues_state.py` gained an
  `engine` subcommand for it. A state file written before this release has no
  such field and simply prints no model line.
- The README now covers updating and uninstalling the plugin on both install
  paths, including what an uninstall leaves behind — the hook entries in
  `~/.claude/settings.json`, the state and logs under `ISSUE_PILOT_HOME`, and
  each repository's `.issue-pilot.json` — and `/issue-pilot:status` now
  appears in the loop diagram.

### Changed

- **Breaking:** the commands drop the `issue-`/`issues-` prefix that
  repeated the plugin namespace: `/issue-pilot:plan`, `/issue-pilot:run`,
  `/issue-pilot:status`, `/issue-pilot:pr`, `/issue-pilot:close`,
  `/issue-pilot:one` (was `issue-plan`, `issues`, `issues-status`,
  `issues-pr`, `issues-close`, `issues-one`). `/issue-pilot:init` is
  unchanged. There are no aliases; a run started before the upgrade keeps
  its state file and finishes on the new names.

## [0.1.0] - 2026-09-11

First public release.

### Added

- `/issue-pilot:issues` — asks, in your own session, everything an unattended
  run will need — including what has landed on the base branch since each issue
  was planned — then starts that run in the background: a list of GitHub issues
  implemented one at a time with a single pull request at the end, clearing the
  conversation between issues that do not depend on each other.
- `/issue-pilot:issues-one` and `/issue-pilot:issues-pr` — the per-issue and
  pull-request steps the driver invokes.
- `/issue-pilot:init` — inspects the project, proposes a configuration and
  confirms it with you. Nothing else runs until `.issue-pilot.json` exists.
- `/issue-pilot:issue-plan` — interviews you and opens a well-formed
  implementation issue that an unattended run can execute, ending with the
  commit of the base branch the plan was read against. A hook refuses it when
  given nothing to plan: the plan is written from what was said, never
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
  committing to it; and a run branch that is always cut, right before the first
  issue, from the base branch fast-forwarded to its tip on origin (`--from-head`
  to opt out), and checked back out of when the run finishes, so the repository
  is left on the base branch and not on the run's.
- `scripts/base_sync.sh` — puts the repository on the base branch at the tip
  origin has for it, refusing a dirty tree and never discarding unpushed
  commits; every planning command and the driver start there.
- `scripts/issue_base.py` — the stamp that records in an issue which commit it
  was planned against, and the report of what has landed on the base branch
  since, per issue.
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

[Unreleased]: https://github.com/INGEOTEC/issue-pilot/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/INGEOTEC/issue-pilot/releases/tag/v0.3.0
[0.2.1]: https://github.com/INGEOTEC/issue-pilot/releases/tag/v0.2.1
[0.2.0]: https://github.com/INGEOTEC/issue-pilot/releases/tag/v0.2.0
[0.1.0]: https://github.com/INGEOTEC/issue-pilot/releases/tag/v0.1.0
