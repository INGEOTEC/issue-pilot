#!/usr/bin/env python3
"""Refuse /issue-pilot:plan with nothing to plan.

A UserPromptSubmit hook.  The command's own instructions say the same thing,
but an instruction can be argued with and a hook cannot: the prompt never
reaches the model, and the person is told why.  The whole point of writing the
issue first is that the run is based on what was SAID; a plan produced from an
empty request is the model guessing what somebody might have wanted, which is
exactly the failure this workflow exists to prevent.

Reads the hook payload on stdin and exits 2 -- block, show stderr -- when the
prompt is the plan command with no request after it.  Anything else,
including a payload it cannot parse, exits 0: this hook must never get in the
way of any other prompt.
"""
import json
import re
import sys

MESSAGE = """issue-pilot: /issue-pilot:plan needs to be told what to plan.

  The plan is written from what you say, not from a guess at what you might
  want.  Give it the request in a sentence or two:

    /issue-pilot:plan add a --format flag to the export command
    /issue-pilot:plan --update 42     (rewrite an existing issue instead)
"""

# Installed as a plugin the command is /issue-pilot:plan; installed by
# hand it has the same name, so one pattern covers both.
COMMAND = re.compile(r"^/issue-pilot:plan\b(.*)$", re.DOTALL)
# `--update <n>` is a complete request on its own: the issue supplies the text.
UPDATE = re.compile(r"^--update\s+#?\d+\b")


def verdict(prompt):
    """0 to let the prompt through, 2 to refuse it."""
    match = COMMAND.match((prompt or "").strip())
    if not match:
        return 0
    rest = match.group(1).strip()
    if UPDATE.match(rest):
        return 0
    if rest and rest != "--update":
        return 0
    return 2


def main():
    try:
        prompt = json.load(sys.stdin).get("prompt")
    except Exception:
        return 0
    code = verdict(prompt)
    if code:
        sys.stderr.write(MESSAGE)
    return code


if __name__ == "__main__":
    sys.exit(main())
