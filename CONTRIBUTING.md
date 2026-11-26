# Contributing to issue-pilot

Bug reports, ideas and pull requests are all welcome. A few things that will
make yours easy to merge.

## Ground rules

- **Standard library only.** The whole point of this toolkit is that it works on
  any machine that already runs Claude Code: `git`, `gh`, `python3`, `curl`, and
  nothing else. A pull request that adds a dependency needs a very good reason.
- **The guard must never break a session.** Any failure inside
  `hooks/usage-guard.sh` — no network, no token, an unexpected payload — has to
  exit 0 and let the tool call through. A guard that blocks a session because it
  could not measure something is worse than no guard.
- **The dependency rule is deterministic.** Whether an issue starts from a clean
  conversation is decided by `scripts/issues_plan.py`, from the text of the
  issues, before the run starts. Do not move that decision into a prompt.
- **English everywhere**: code, comments, docstrings, command files, commit
  messages.

## Running the tests

```bash
python3 -m unittest discover -s tests -t tests
```

They run the real scripts inside a throwaway git repository, against a fake `gh`
and a fake `curl` placed on `PATH` (see `tests/support.py`). Nothing is mocked at
the Python level, because the thing worth testing is the command lines that
actually ship.

Add a test for any change to:

- the dependency rule (`tests/test_issues_plan.py`),
- the run state or the driver's control flow (`tests/test_issues_state.py`),
- configuration precedence (`tests/test_pilot_config.py`),
- what the guard does with a reading (`tests/test_usage_guard.py`).

## Checking the plugin

```bash
claude plugin validate . --strict
```

To exercise a change as an installed plugin, add your checkout as a marketplace
(`/plugin marketplace add /path/to/issue-pilot`) and install
`issue-pilot@ingeotec` from it. The install is a copy, not a link, and
`/plugin update` only acts on a version bump — so while iterating on the same
version, `/plugin uninstall issue-pilot@ingeotec` and `/plugin install
issue-pilot@ingeotec` again, then restart Claude Code.

## Changing a command file

The files in `commands/` are prompts, and they are the only instructions an
unattended session gets. Two habits pay off there:

- Say *why*, not just what. Several of the rules in `scripts/prompts/one.md`
  exist because a run died; the reason is what keeps a later editor from
  removing them. That file is a prompt too, but not a command: the driver reads
  it and fills in `{{ITEM}}` and `{{ROOT}}` itself, so it uses those instead of
  `$ARGUMENTS` and `${CLAUDE_PLUGIN_ROOT}`.
- Refer to scripts as `${CLAUDE_PLUGIN_ROOT}/scripts/...`. `install.sh` rewrites
  that to an absolute path for people who install without the plugin system, and
  a hardcoded path breaks one of the two installations.

## Releasing

1. In the pull request, add a `## [X.Y.Z] - YYYY-MM-DD` section to
   `CHANGELOG.md` and bump `version` in `.claude-plugin/plugin.json`. CI checks
   that the two agree: a version without a changelog section fails the
   `manifests` job.
2. Merge. The `Release` workflow (`.github/workflows/release.yml`) tags `vX.Y.Z`
   on the merge commit and publishes the GitHub release, titled
   `issue-pilot X.Y.Z`, with that changelog section as its notes. Nothing is done
   by hand.
3. If a version ever ends up without a release (the workflow failed, or it
   predates the workflow), go to Actions → Release → Run workflow and tick
   "backfill": every version of `main` that has no tag is tagged on the commit
   where it first appeared.

The tool behind the workflow is `.github/scripts/release.py`. `notes`,
`versions`, `pending` and `publish --dry-run` only read, and are safe to run in a
checkout; `publish` without `--dry-run` creates tags and releases for real.
