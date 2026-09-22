#!/usr/bin/env python3
"""Refuse /issue-pilot:plan or /issue-pilot:fix with nothing said.

A UserPromptSubmit hook.  The command's own instructions say the same thing,
but an instruction can be argued with and a hook cannot: the prompt never
reaches the model, and the person is told why.  The whole point of writing the
issue -- or the review fix -- first is that the run is based on what was SAID;
one produced from an empty request is the model guessing what somebody might
have wanted, which is exactly the failure this workflow exists to prevent.

Reads the hook payload on stdin and exits 2 -- block, show stderr -- when the
prompt is /issue-pilot:plan or /issue-pilot:fix with nothing after it.
Anything else, including a payload it cannot parse, exits 0: this hook must
never get in the way of any other prompt.
"""
import json
import re
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

# Installed as a plugin the commands are /issue-pilot:plan and /issue-pilot:fix;
# installed by hand they have the same names, so one pattern covers both.
COMMAND = re.compile(r"^/issue-pilot:(plan|fix)\b(.*)$", re.DOTALL)
# `--update <n>` is a complete request on its own: the issue supplies the text.
# Only /issue-pilot:plan has an --update form; a fix has no issue to reread.
UPDATE = re.compile(r"^--update\s+#?\d+\b")


def verdict(prompt):
    """(exit code, message to print) -- 0 and None let the prompt through."""
    match = COMMAND.match((prompt or "").strip())
    if not match:
        return 0, None
    command, rest = match.group(1), match.group(2).strip()
    if command == "fix":
        return (0, None) if rest else (2, FIX_MESSAGE)
    if UPDATE.match(rest) or (rest and rest != "--update"):
        return 0, None
    return 2, PLAN_MESSAGE


def main():
    try:
        prompt = json.load(sys.stdin).get("prompt")
    except Exception:
        return 0
    code, message = verdict(prompt)
    if message:
        sys.stderr.write(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
