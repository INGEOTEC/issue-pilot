# Changelog

All notable changes to issue-pilot are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.3] - 2026-10-26

### Changed

- `/issue-pilot:fix <finding>` is refused, before it reaches the model, when
  the repository has no run state or the run's pull request is already
  recorded -- typing it then is nearly always a slip for `/issue-pilot:plan`.
  The refusal shows `/issue-pilot:plan <the same text>` ready to copy and, when
  the pull request is open, says a finding on it goes in by hand. Any doubt (no
  `cwd`, not a git repository, an unreadable state file) lets the prompt
  through, and the hook never creates the state directory. `commands/fix.md`
  step 1 states the same two cases for installs without the hook.

## [0.5.2] - 2026-10-22

### Fixed

- `issues_state.py wait`, `quick`, `report` and `/issue-pilot:status` right
  after `--detach` described the **previous** run of the repository: the child
  driver only rewrites the per-repository state file after syncing the base
  branch and cutting the run branch, so for those seconds the state still named
  the old run, whose driver was long dead. `wait` returned at once with that
  run's full report.
- `--detach` printed `run started` even when the child died before initialising
  the run (an unreachable origin, say). It now exits non-zero with the error and
  the tail of `driver.log`.
- `issues_run.sh --resume --detach` was refused with "nobody to ask phase 1's
  questions" although `--resume` never runs phase 1; `--resume` now implies no
  interview, as `--fix` already did.
- `commands/run.md` says the notes are written with the Write tool in one step,
  not through a shell chain where an earlier command's exit status can skip the
  write silently.

### Added

- `--detach` returns once the state names the run branch and has been rewritten
  since launch (`init`, or `engine` for `--resume`/`--fix`). It gives up waiting
  after `ISSUE_PILOT_DETACH_TIMEOUT` seconds (default 120, `0` to not wait),
  says the driver is still starting and exits 0.
- A driver pointer per repository, `state/issues-driver-<slug>.json` (branch,
  pid, log, start time), written by `--detach` through
  `issues_state.py driver-pointer`. `wait`, `quick` and `report` resolve the
  driver from it; `quick` says `starting <branch>` until the run is initialised,
  and `wait` exits 1 with the log tail if the driver dies first. Without a
  pointer they behave as before.
- `run.md` and `fix.md`: a non-zero `--detach` is reported and the background
  `wait` is not started.

## [0.5.1] - 2026-10-21

### Added

- `issues_state.py wait`: blocks while the detached driver is alive, polling
  `driver.pid` every `--interval` seconds (60 by default), and when it stops
  sends a best-effort desktop notification (`notify-send`, or `osascript` on
  macOS) saying whether the run finished, is blocked or has items pending, then
  prints the same full report as `report`. With no `driver.pid` it says there is
  no detached driver and prints the report at once.
- `/issue-pilot:run` and `/issue-pilot:fix` start that wait as a background
  command right after `--detach`, so the conversation is woken up once, when the
  run stops, and summarises it. It costs no model turns while the run goes,
  unlike polling with `/loop`.
- The `watch` setting (default `true`; `ISSUE_PILOT_WATCH=0` or
  `"watch": false` turns it off) controls that automatic wait.

## [0.5.0] - 2026-10-20

### Changed

- A finished run, a `--resume` and a `--fix` now leave the run branch checked
  out instead of going back to the base branch: the next step is reviewing the
  work, and every review used to start with a manual `git checkout`. The
  driver's last message says which branch is checked out and that
  `/issue-pilot:pr` is the next step. A run that stops early stayed on its branch
  already, and still does.
- Going back to the base branch now happens only when the pull request is
  opened. With `--pr` the driver does it itself, and only once the pull request
  is recorded in the state, not from whatever branch the pr session left; a pr
  session that failed leaves you on the work. `/issue-pilot:pr` still ends on
  the base branch, as before.
- `commands/run.md`, `commands/fix.md`, `commands/pr.md` and the README say so.

## [0.4.1] - 2026-10-17

### Changed

- `/issue-pilot:status` answers at once while a detached run is going. It runs
  the new `issues_state.py quick`, which reads `driver.pid`, the run state and
  the usage guard's halt file and log (no `--check` probe, no session logs, no
  model summary), and says whether the run is moving or why it is not: paused
  or halted by the guard. Once the driver has stopped, or when the run was
  never detached, it prints the full report as before.
- The choice between the two is made in code, not by the model:
  `/issue-pilot:status` is now one command, `issues_state.py report`, which
  prints `report     : quick` or `report     : full`. The full report gathers
  the run table, the usage guard's `--check` (an error line if it fails, never a
  crash) and the tails of `driver.log` and of the latest session log itself.

## [0.4.0] - 2026-10-17

### Added

- `/issue-pilot:usage` shows how much of each usage window is left, when it
  resets and what the usage guard would do about it. It runs
  `hooks/usage-guard.sh --check` and measures nothing itself; it is read-only
  and prints the `--clear` command rather than running it.
- The guard's per-window decision (`ok`, `wait`, `halt`, `warn`, `ignore`, and
  the wait it would sleep) now lives in one function that hook mode acts on and
  `--check` reports, so `--check` is a dry run of the code that stops a session.
  Hook mode behaves as before. `--check` gains `thresholds`, and per window
  `scope`, `verdict` and `wait_seconds`.
- The usage guard reads the macOS Keychain (`Claude Code-credentials`) as a
  fourth source of credentials, after the environment variable, the shell rc
  and `~/.claude/.credentials.json`, so it measures on a Mac logged in with
  `claude login`. Before, it found no credentials there and let every call
  through.
- The guard can read `GET /api/oauth/usage` (what `/usage` shows, no model
  request) as well as the rate-limit headers of a 1-token request, chosen by
  `USAGE_GUARD_SOURCE`: `auto` (default; the endpoint first, the headers when
  the token cannot use it), `headers` or `oauth`.

### Changed

- `usage-guard.sh --check` gains `source` (`oauth_usage`, `headers` or `null`),
  and the header probe is now the fallback for tokens that cannot read
  `/api/oauth/usage`.
- `usage-guard.sh --check` always prints exactly one JSON object: a halt on
  record is now the `halt` key (its text, or `null`) instead of `halted:` lines
  printed after the JSON.

## [0.3.2] - 2026-10-17

### Fixed

- `scripts/issues_run.sh` refused to start on macOS with "no credentials" for
  anyone logged in with `claude login`, because the login lives in the Keychain
  and there is no `~/.claude/.credentials.json`. On macOS it now also looks for
  a `Claude Code-credentials` item in the Keychain (existence only, the secret
  is never read), and the error message names it and suggests `claude login`.

## [0.3.1] - 2026-10-01

### Added

- `scripts/config_land.sh` lands `.issue-pilot.json` on the base branch through
  a pull request: a branch `configure-issue-pilot` from the base at origin's
  tip, with the file as its one commit, pushed and opened against the base
  (`--draft` when `pr_draft` is set). It refuses, naming the way out, when the
  tree has anything else in it, when it is not on the base or the base is not
  at origin's tip, or when a `configure-issue-pilot` branch is left over.
- `config_land.sh --merge` squash-merges that pull request right away, deletes
  the branch and fast-forwards the local base. When GitHub refuses the merge it
  still exits 0, with the pull request open to be merged by hand.
- `/issue-pilot:init` runs `base_sync.sh` before anything is asked or written,
  asks whether to merge the pull request right away, and its report says
  whether `/issue-pilot:plan` can run now or has to wait for the merge.

### Fixed

- `/issue-pilot:init` committed the configuration on the local base branch,
  which `base_sync.sh` then refused ("local main is 1 commit(s) ahead of
  origin/main"), and on a base branch that requires pull requests a direct push
  would not have worked either. The configuration now reaches origin through a
  pull request.

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

[Unreleased]: https://github.com/INGEOTEC/issue-pilot/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/INGEOTEC/issue-pilot/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/INGEOTEC/issue-pilot/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/INGEOTEC/issue-pilot/releases/tag/v0.3.1
[0.3.0]: https://github.com/INGEOTEC/issue-pilot/releases/tag/v0.3.0
[0.2.1]: https://github.com/INGEOTEC/issue-pilot/releases/tag/v0.2.1
[0.2.0]: https://github.com/INGEOTEC/issue-pilot/releases/tag/v0.2.0
[0.1.0]: https://github.com/INGEOTEC/issue-pilot/releases/tag/v0.1.0
