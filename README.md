# issue-pilot

Turn a list of GitHub issues into one reviewed pull request, unattended.

`issue-pilot` is a [Claude Code](https://claude.com/claude-code) plugin for a
specific way of working: you write down what you want as a GitHub issue, and
then a Claude Code run implements it — reading the issue, writing the code and
the tests, committing, commenting on the issue, and moving on to the next one.

Doing that across several issues runs into three problems that no amount of
prompting fixes. issue-pilot is the three answers.

**A long run has to forget.** Issue #170 does not need the conversation that
implemented #165, and carrying it costs context that the actual work needs. But
issue #166 *does*, because it was written as a continuation of #165. issue-pilot
decides this before the run starts, deterministically, from what the issues say
about each other — a `#N` mention in either direction means they share a
conversation, anything else starts clean. It is not a judgement the model makes
mid-run, so it is the same every time and you can read it off the plan.

**A long run has to survive its own sessions dying.** Under `claude -p`, ending
a turn ends the process, and every background job with it. A session that ends
its turn to "wait for the download to finish" takes finished, green,
uncommitted work down with it. issue-pilot keeps the plan, the answers you gave up front,
the per-issue status and the commits in a state file on disk, retries a session
that died mid-task by *continuing* it rather than restarting it, and tells apart
a session that crashed from one that declared a real blocker — only the second
stops the run.

**A long run has to fit in your usage window.** A bundled hook measures your
5-hour and weekly windows every few minutes. Over the 5-hour threshold it
sleeps until the window resets, so the run pauses instead of hitting a hard
limit mid-issue. The weekly window cannot be waited out, so over *its* threshold
it halts the run cleanly and tells you when to come back.

---

## Install

```
/plugin marketplace add INGEOTEC/issue-pilot
/plugin install issue-pilot@ingeotec
```

The repository is its own marketplace, named `ingeotec`; the part after the `@`
is that marketplace, not the GitHub organisation, though here they happen to
read the same. Plain `/plugin install issue-pilot` also works as long as no
other marketplace you have added offers a plugin of that name.

Restart Claude Code, then configure the repository you want to use it on:

```
/issue-pilot:init
```

Prefer plain files under `~/.claude`, or on an older Claude Code without
`/plugin`? Clone the repository and run the installer instead — it puts the
commands in `~/.claude/commands/issue-pilot/`, so they are invoked by exactly
the same names:

```bash
git clone https://github.com/INGEOTEC/issue-pilot
cd issue-pilot
./install.sh --hook      # --hook also registers the usage guard
```

**Requirements:** `git`, `python3` (3.9+, standard library only), the
[GitHub CLI](https://cli.github.com) authenticated (`gh auth login`), `curl`,
and a `claude` that can start subprocesses of its own — run `claude setup-token`
once, or export `CLAUDE_CODE_OAUTH_TOKEN`. Being logged in inside a session is
not enough: that token is not passed to subprocesses, and the driver launches
one per issue.

## The loop

```
  /issue-pilot:init        ->  .issue-pilot.json, once per repository
              |
  /issue-pilot:plan  ->  a well-formed issue on GitHub
              |
  /issue-pilot:run 165 166 170
              |
      the asking: your session reads every issue and asks you everything it
              needs, once, and writes the answers to disk.  Then it starts the
              run in the background and hands you back the prompt.
              |
      the doing: one issue at a time, unattended.  Fresh conversation when the
              plan says the issue is independent; resumed when it is not.
              Implement -> test -> commit -> comment -> mark done.
              |
     the close: /issue-pilot:pr  ->  one pull request, Closes #165 #166 #170
              |
              |  (only if the base branch is not the repository's default one)
              v
  /issue-pilot:close  ->  the issues closed by hand, after the merge
```

Start once per repository, by saying what this project's tests are:

```
/issue-pilot:init
```

That is not optional — nothing else runs without `.issue-pilot.json`. It
inspects the project, proposes a configuration, and asks you to confirm it. The
proposal is often nearly right and occasionally wrong in a way only a person
notices: a repository whose tests live in `tests/test_*.py` but are written with
`unittest` gets offered `pytest -q`.

Then write the issue:

```
/issue-pilot:plan add a --format flag to the export command
```

The request is not optional, and that is enforced by a hook rather than asked
for: `/issue-pilot:plan` with nothing after it is refused before it
reaches the model. The plan is written from what you say; a plan produced from
an empty request would be a guess at what you might have wanted, which is the
one thing this workflow exists to rule out.

That session puts the repository on the base branch, at the tip origin has for
it, and reads your code there. It asks everything it cannot settle with a
sensible default — in one batch, not one question at a time — and opens an
issue with the sections the autonomous side knows how to read: context, scope
(including what is *out* of scope), implementation plan, acceptance criteria,
tests, the defaults it decided, and the commit the plan was read against. It
prints the command to implement it.

Then run it:

```
/issue-pilot:run 165 166 170
```

That session puts the repository on the base branch again, at origin's tip,
and reads each issue against it — including what has landed on the base branch
since the issue was planned, when anything has. It asks you everything the run
will need — in one batch — and then puts the run in the background and gives
you the prompt back. It takes hours; you are not meant to sit in front of it.

Check on it at any time, from any session:

```
/issue-pilot:status
```

```
repository : /home/you/project
branch     : issues-165-166-170 (off develop at 3c9d1e2f0)

 + #165    done     fresh=True  depends=-        attempts=1 commit=a1b2c3d4e
     Extract the reader into its own module
 + #166    done     fresh=False depends=#165     attempts=2 commit=9f8e7d6c5
     Use the new reader in the CLI
 . #170    pending  fresh=True  depends=-        attempts=0
     Add a --format flag to the export command

pending: #170
```

## How the run behaves

**All the asking happens before the run starts.** Everything after it has nobody
to answer, so `/issue-pilot:run` reads every issue in the list, reads
`CLAUDE.md` and the code, and asks all of its questions at once, in the session
you are sitting in. The answers go to a file and are passed to every autonomous
session as `--notes` — they are the only thing that survives the clearing between
issues.

**The run itself is detached.** It is started with `--detach`, in a session of
its own, so it outlives both the tool call that started it and the conversation
it was started from. You get the branch and a log path back immediately, and
follow it with `/issue-pilot:status`. Started from a terminal instead, the
driver can conduct the interview itself; that is the only path that needs a TTY.

**Conversations are addressed by id.** Dependent issues and retries go back to
the run's own conversation, recorded in the state — never to "the most recent
conversation in this directory", which during a run is whichever one you opened
last to look at the status. Opening Claude Code in the repository while a run is
going is safe.

**The branch starts from origin, always.** One branch for the whole run, named
from the issue list (`issues-165-166-170`), cut by the driver right before the
first issue from the base branch **as it is on origin**: the base branch is
checked out, fetched and fast-forwarded first, every time. Whatever you had
checked out does not come into it. A local base branch with commits origin does
not have stops the run rather than being reset — those are somebody's unpushed
work — and the driver lists them. `--from-head` is the deliberate exception.
Never one branch per issue, and never a pull request per issue.

**And you end up back on it.** When the run finishes, the driver checks the base
branch out again: the work is on the run branch, pushed by the pull request
step, and a repository left on `issues-165-166-170` looks a week later like
somebody is still working there. `/issue-pilot:pr` does the same after
opening the pull request, and `/issue-pilot:close` pulls the merge and
deletes the local run branch once everything on it is on origin. A run that
stops early — a blocker, a session that kept dying — stays on its branch, where
the unfinished work is.

**Planning happens on the same code, and the issue remembers which.**
`/issue-pilot:plan` puts the repository on the base branch at origin's
tip before it reads anything — not on the feature branch you happened to be on
— and ends the issue with the commit the plan was read against. Days later,
`/issue-pilot:run` reads that commit back and lists what has landed on the
base branch since, and which files it touched, so a plan that names a file
that has moved or a behaviour another change already altered is caught in the
one session that still has somebody to ask.

**The model.** Every autonomous session runs on the model and effort level in
`.issue-pilot.json` (`sonnet` and `high` by default), never on whatever the
session that started the run happens to be using.

**Retries.** A session that ends without marking its issue done has almost never
hit a blocker — it died mid-task. The driver resumes that same conversation, so
it picks up its own context and whatever is already on disk, up to
`max_attempts` times. It gives up early if two consecutive attempts leave the
working tree byte-identical: more sessions will not help.

**Blocking is explicit.** A session that finds a real blocker calls
`issues_state.py block`. *That* stops the run, without retries, with the commits
of the issues that did finish left intact and no pull request opened. Ending a
turn quietly is not a way to give up.

**Resuming.** `issues_run.sh --resume` reopens the blocked issue and hands it out
again — one retry per invocation, so a persistently failing issue still stops the
run instead of looping. It does not ask the questions again: the answers
are already in the state.

**Logs.** The driver's own output goes to
`~/.claude/issue-pilot/logs/<branch>/driver.log`, and every attempt is kept
whole, with the exit status of `claude` at the end, in
`issue-<n>-attempt-<k>.log` next to it. Those are the first things to read when
a run ends badly.

## The usage guard

Registered as a `PreToolUse` hook, so it is reached on every tool call, but it
only measures once every five minutes. It reads the `anthropic-ratelimit-unified-*`
response headers of one 1-token Haiku request — every window the API reports,
whatever it is called, rather than a hardcoded list.

What it does with a reading depends on how long the window is, decided from the
reading itself:

| Window | Over threshold |
|---|---|
| Resets within `USAGE_GUARD_MAX_WAIT` (the 5-hour one) | Sleeps until it resets. The session pauses; the run continues afterwards. |
| Resets later than that (the weekly one) | Halts: blocks the call, writes a halt file, and the driver stops between issues. |

Once halted, the sessions of an autonomous run are refused at their very next
tool call rather than five minutes later; an interactive session is left alone,
so you can still open Claude Code and look. Clear a halt with
`usage-guard.sh --clear`.

It must never break a session by accident, so any failure — no network, expired
token, an unexpected payload — lets the tool call through.

See it yourself at any time:

```bash
~/.claude/issue-pilot/lib/hooks/usage-guard.sh --check   # or the plugin's copy
```

| Variable | Default | Meaning |
|---|---|---|
| `USAGE_GUARD_THRESHOLD` | `70` | Percent of a short window at which to start waiting. |
| `USAGE_GUARD_LONG_THRESHOLD` | `90` | Percent of a long (weekly) window at which to halt. |
| `USAGE_GUARD_LONG_ACTION` | `halt` | `halt`, `warn` or `ignore`. |
| `USAGE_GUARD_INTERVAL` | `300` | Seconds between real measurements. |
| `USAGE_GUARD_MAX_WAIT` | `21600` | Longest single sleep, and the line between a short and a long window. |
| `USAGE_GUARD_MODEL` | `claude-haiku-4-5-20251001` | Model used for the probe. |

## Configuration

**Required**, per repository, in `.issue-pilot.json` at its root — write it with
`/issue-pilot:init` rather than by hand. Nothing starts without it, and that is
deliberate: an unattended run has nobody to ask what "green" means and no way to
tell you it guessed wrong. `test_command` is the one setting with no sensible
fallback, so it is the one the check insists on; a project with no tests can set
it to a command that exits 0.

Every setting can be overridden for a single run by an environment variable
(`ISSUE_PILOT_TEST_COMMAND`, `ISSUE_PILOT_MODEL`, ...). See
`.issue-pilot.example.json`.

| Setting | Default | Meaning |
|---|---|---|
| `base_branch` | the repository's default branch on GitHub | Branch a run starts from and its pull request targets. |
| `branch_prefix` | `issues` | The issue numbers are appended: `issues-165-166`. |
| `test_command` | inferred from the project | The suite that has to be green before a commit. |
| `model` | `sonnet` | Model every autonomous session runs on, whatever your interactive default is. |
| `effort` | `high` | Effort level of those sessions. |
| `max_attempts` | `3` | Attempts on one issue before the run stops. |
| `pr_draft` | `false` | Open the final pull request as a draft. |
| `issue_labels` | `[]` | Labels applied to issues opened by `/issue-pilot:plan`. |

`ISSUE_PILOT_HOME` (default `~/.claude/issue-pilot`) is where state and logs live.

## Commands

| Command | What it does |
|---|---|
| `/issue-pilot:init` | Configures issue-pilot for this repository. Run it once, before anything else. |
| `/issue-pilot:plan <idea>` | Interviews you and opens a well-formed implementation issue. `--update <n>` rewrites an existing one. |
| `/issue-pilot:run <n>...` | Asks you everything the run needs, then starts it in the background. |
| `/issue-pilot:status` | The run in plain words, plus how much usage window is left. |
| `/issue-pilot:pr` | Opens the single pull request, if every issue is done. |
| `/issue-pilot:close` | Closes the issues by hand once the pull request is merged, when GitHub will not. |
| `/issue-pilot:one <n>` | One issue. Called by the driver; you rarely run it yourself. |

The driver underneath, for the times you want it directly:

```bash
scripts/issues_run.sh 165 166 170                 # interview here, then run
scripts/issues_run.sh --from-head 165             # cut the branch from HEAD, not origin
scripts/issues_run.sh --notes-file notes.txt 165  # answers gathered elsewhere
scripts/issues_run.sh --no-interview 165          # no questions at all
scripts/issues_run.sh --detach --no-interview 165 # run in the background
scripts/issues_run.sh --plan-only 165 166 170     # show the plan, touch nothing
scripts/issues_run.sh --pr 165 166                # open the pull request too
scripts/issues_run.sh --resume                    # retry the blocked issue
```

The driver fast-forwards the local base branch to `origin/<base>` and cuts the
run branch from it unless told `--from-head`; a base branch with unpushed
commits stops it rather than being reset. Run from your own terminal
with no `--notes`, it conducts the interview itself in an interactive `claude`
session. `--detach` cannot: it has nobody to
ask, so it requires the answers up front or none at all.

## Closing the issues

GitHub honours `Closes #165` only when the pull request merges into the
repository's **default** branch. A project whose runs target `develop`, or any
other integration branch, gets the link in the pull request UI and nothing else:
the issues stay open, and it is easy not to notice for weeks.

issue-pilot compares the run's base branch against the repository's default
branch when it opens the pull request, records which case it is, and says so
in `/issue-pilot:status`:

```
pull req   : https://github.com/you/project/pull/42 -> develop
             merging this will NOT close the issues (the base is not
             the default branch); run /issue-pilot:close after.
```

After the merge:

```
/issue-pilot:close
```

which checks the pull request really is merged, comments on each issue with the
pull request and the commit that implemented it, and closes it. It refuses to
close anything for an unmerged pull request unless you pass `--force`.

## Writing issues it can implement

The autonomous session has your issue and nothing else. The difference between a
run that works and one that stalls is almost always in the issue, so
`/issue-pilot:plan` exists to write them — but if you write your own:

- Say what is **out** of scope. That is what stops an unattended session from
  wandering into a refactor.
- Give acceptance criteria that can be checked, and name the test command.
- Say how long anything slow takes and how to tell it finished. A session that
  does not know this is the most common way a run dies.
- Mention a sibling issue with `#N` **only** when the work genuinely continues
  from it: that mention is what makes them share a conversation. For background
  reading, say so in words without the number.

## Developing

```bash
python3 -m unittest discover -s tests -t tests
```

To try a working copy as a plugin, add the checkout itself as a marketplace and
install from it:

```
/plugin marketplace add /path/to/issue-pilot
/plugin install issue-pilot@ingeotec
```

Claude Code copies the plugin into its cache at install time, so edits to the
checkout are not live — and `/plugin update` only acts when the version in
`plugin.json` has changed. While iterating on the same version, reinstall:

```
/plugin uninstall issue-pilot@ingeotec
/plugin install issue-pilot@ingeotec
```

and restart Claude Code.

No dependencies beyond the standard library: the tests run the real scripts
against a throwaway git repository, a fake `gh` and a fake `curl`, so what is
tested is the command lines that actually ship.

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).
