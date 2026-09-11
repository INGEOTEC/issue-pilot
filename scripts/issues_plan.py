#!/usr/bin/env python3
"""Build the execution plan for an issue-pilot run.

Decides, deterministically and without asking the language model, which issues
of a run can start from a clean conversation (`/clear`) because they do not
depend on any issue implemented earlier in the same run.

Dependency rule: issue B depends on the already-implemented issue A when either
one mentions the other (`#A` / `#B`, or a full GitHub issue URL) anywhere in its
title, body or comments.  Mentions are symmetric on purpose: an issue that says
"supersedes #A" and an issue A whose comment says "see #B" both mean the two
were written as one piece of work and must share a conversation.

Being wrong in the two directions costs very differently.  A missed dependency
sends a session into an issue whose context it cannot see; a spurious one only
carries history that was not needed.  The rule errs towards the cheap mistake.

Usage: issues_plan.py [--repo owner/name] 165 166 170
Writes the plan to stdout as JSON.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys

MENTION = re.compile(r"#(\d+)\b")
ISSUE_URL = re.compile(r"github\.com/[^/\s]+/[^/\s]+/issues/(\d+)")


def gh_issue(repo, number):
    if shutil.which("gh") is None:
        sys.exit("the GitHub CLI (`gh`) is not installed; see https://cli.github.com")
    cmd = ["gh", "issue", "view", str(number), "--json",
           "number,title,body,comments"]
    if repo:
        cmd += ["--repo", repo]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"gh issue view {number} failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def mentions(issue, numbers):
    """Issue numbers of `numbers` referenced by `issue`'s text."""
    text = " ".join([issue.get("title") or "", issue.get("body") or ""] +
                    [c.get("body") or "" for c in issue.get("comments") or []])
    found = {int(n) for n in MENTION.findall(text)}
    found |= {int(n) for n in ISSUE_URL.findall(text)}
    found.discard(issue["number"])
    return found & set(numbers)


def build(order, repo=None, fetch=gh_issue):
    """The plan for `order`, in the order given.  `fetch` exists for the tests."""
    data = {n: fetch(repo, n) for n in order}
    refs = {n: mentions(data[n], order) for n in order}

    plan = []
    for i, n in enumerate(order):
        previous = order[:i]
        # Symmetric: n names an earlier issue, or an earlier issue names n.
        deps = sorted(p for p in previous if p in refs[n] or n in refs[p])
        plan.append({
            "issue": n,
            "title": data[n]["title"],
            "depends_on": deps,
            # The first issue has no history worth keeping, so it starts clean too.
            "clear_before": not deps,
        })
    return {"issues": order, "plan": plan}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", help="owner/name, when not running inside the repository")
    ap.add_argument("issues", nargs="+", type=int)
    args = ap.parse_args()

    seen = [n for i, n in enumerate(args.issues) if n in args.issues[:i]]
    if seen:
        sys.exit("issue listed more than once: " +
                 ", ".join(str(n) for n in sorted(set(seen))))

    print(json.dumps(build(args.issues, args.repo), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
