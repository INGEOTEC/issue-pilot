#!/usr/bin/env python3
"""Refuse /issue-pilot:plan or /issue-pilot:fix with nothing said, and
/issue-pilot:fix when there is no finished run to fix.

A UserPromptSubmit hook.  The command's own instructions say the same thing,
but an instruction can be argued with and a hook cannot: the prompt never
reaches the model, and the person is told why.  The whole point of writing the
issue -- or the review fix -- first is that the run is based on what was SAID;
one produced from an empty request is the model guessing what somebody might
have wanted, which is exactly the failure this workflow exists to prevent.

A fix is recorded on a run that finished without a pull request; typed with
nothing of the sort behind it -- no run state for the repository, or one whose
pull request is already open -- it is nearly always /issue-pilot:plan that was
meant, so the refusal hands that command back with the same text.

Reads the hook payload on stdin and exits 2 -- block, show stderr -- when the
prompt is /issue-pilot:plan or /issue-pilot:fix with nothing after it, or a
/issue-pilot:fix in a repository with no run waiting for one.  Anything else,
including a payload it cannot parse and any doubt about the repository or its
state, exits 0: this hook must never get in the way of any other prompt.
"""
import json
import os
import pathlib
import re
import subprocess
import sys

PLAN_MESSAGE = """issue-pilot: /issue-pilot:plan needs to be told what to plan.

  The plan is written from what you say, not from a guess at what you might
  want.  Give it the request in a sentence or two:

    /issue-pilot:plan add a --format flag to the export command
    /issue-pilot:plan --update 42     (rewrite an existing issue instead)
"""

FIX_MESSAGE = """issue-pilot: /issue-pilot:fix needs to be told what the review found.

  A review fix is recorded from what you say, not from a guess at what might
  be wrong.  Give it the finding in a sentence or two:

    /issue-pilot:fix the export flag is ignored when --quiet is set
"""

FIX_NO_RUN_MESSAGE = """issue-pilot: /issue-pilot:fix has no finished run to fix here.

  A review fix corrects the branch of a run that has finished and has no pull
  request yet.  This repository has no such run.  If you meant to plan new
  work, that is /issue-pilot:plan:

    /issue-pilot:plan {text}
"""

FIX_PR_LINE = """
  If this was a finding on the open pull request, it goes into that pull
  request by hand (a commit and a comment), not through this command.
"""

# Installed as a plugin the commands are /issue-pilot:plan and /issue-pilot:fix;
# installed by hand they have the same names, so one pattern covers both.
COMMAND = re.compile(r"^/issue-pilot:(plan|fix)\b(.*)$", re.DOTALL)
# `--update <n>` is a complete request on its own: the issue supplies the text.
# Only /issue-pilot:plan has an --update form; a fix has no issue to reread.
UPDATE = re.compile(r"^--update\s+#?\d+\b")


def state_file(cwd):
    """The run state file of the repository at `cwd`, or None when unsure.

    Resolved like pilot_config.home() and issues_state.repo_slug(), but without
    pilot_config.state_dir(): that creates the directory, and a hook must not
    leave anything behind.
    """
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        out = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    root = out.stdout.strip()
    if out.returncode != 0 or not root:
        return None
    explicit = os.environ.get("ISSUE_PILOT_HOME")
    if explicit:
        home = pathlib.Path(explicit)
    else:
        claude_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        base = pathlib.Path(claude_dir) if claude_dir else pathlib.Path.home() / ".claude"
        home = base / "issue-pilot"
    slug = root.strip("/").replace("/", "-")
    return home / "state" / f"issues-run-{slug}.json"


def fix_without_run(cwd, text):
    """The refusal for a fix with no run waiting for it, or None to let it pass."""
    try:
        path = state_file(cwd)
        if path is None:
            return None
        if not path.exists():
            return FIX_NO_RUN_MESSAGE.format(text=text)
        state = json.loads(path.read_text())
        if isinstance(state, dict) and state.get("pull_request"):
            return FIX_NO_RUN_MESSAGE.format(text=text) + FIX_PR_LINE
    except Exception:
        pass
    return None


def verdict(prompt, cwd=None):
    """(exit code, message to print) -- 0 and None let the prompt through."""
    match = COMMAND.match((prompt or "").strip())
    if not match:
        return 0, None
    command, rest = match.group(1), match.group(2).strip()
    if command == "fix":
        if not rest:
            return 2, FIX_MESSAGE
        message = fix_without_run(cwd, rest)
        return (2, message) if message else (0, None)
    if UPDATE.match(rest) or (rest and rest != "--update"):
        return 0, None
    return 2, PLAN_MESSAGE


def main():
    try:
        payload = json.load(sys.stdin)
        prompt, cwd = payload.get("prompt"), payload.get("cwd")
    except Exception:
        return 0
    code, message = verdict(prompt, cwd)
    if message:
        sys.stderr.write(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
