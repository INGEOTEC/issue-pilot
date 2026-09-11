#!/usr/bin/env python3
"""Which commit an issue was planned against, and what has landed since.

An implementation issue is written against the code as it was that day: the
plan names files, functions and behaviour that were true of the base branch on
origin at that moment.  By the time the issue is implemented -- days later,
after other pull requests have merged -- some of that may no longer hold, and a
session with nobody to ask will not notice.  So the planning side records the
commit in the issue itself, and the run side reads it back and works out what
changed in between, while there is still a person to ask about it.

  issue_base.py stamp             the line /issue-pilot:issue-plan ends an issue with
  issue_base.py drift 165 166     per issue: the commit it was planned against,
                                  and what has landed on the base branch since

The stamp lives in the issue rather than in a file here because it has to be
visible to whoever implements the issue, from whichever machine, and outlive
this directory.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import issues_plan  # noqa: E402
import pilot_config  # noqa: E402

STAMP = re.compile(r"Planned against `(?P<branch>[^`\n]+)` at `(?P<commit>[0-9a-f]{7,40})`")


def stamp(branch, commit):
    return f"_Planned against `{branch}` at `{commit}`._"


def parse(body):
    """(branch, commit) recorded in an issue body, or None."""
    found = STAMP.search(body or "")
    return (found.group("branch"), found.group("commit")) if found else None


def git(*args):
    out = subprocess.run(["git", *args], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def drift_of(recorded, tip, branch):
    """How far the code has moved since `recorded`, towards `tip`."""
    if recorded is None:
        return {"state": "unrecorded"}
    planned_branch, commit = recorded
    full = git("rev-parse", "--verify", "--quiet", commit + "^{commit}")
    if full is None:
        return {"state": "unknown", "commit": commit, "planned_branch": planned_branch}
    if full == tip:
        return {"state": "current", "commit": full, "planned_branch": planned_branch}
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", full, tip]).returncode == 0
    if not ancestor:
        return {"state": "diverged", "commit": full, "planned_branch": planned_branch}
    commits = (git("log", "--oneline", f"{full}..{tip}") or "").splitlines()
    files = (git("diff", "--name-only", full, tip) or "").splitlines()
    return {"state": "behind", "commit": full, "planned_branch": planned_branch,
            "commits": commits, "files": files,
            "stat": git("diff", "--stat", full, tip) or ""}


def cmd_stamp(args):
    branch = pilot_config.resolve_base_branch()
    tip = git("rev-parse", f"origin/{branch}")
    head = git("rev-parse", "HEAD")
    if tip is None:
        sys.exit(f"origin/{branch} is not known here; run base_sync.sh first")
    if head != tip:
        sys.exit(f"HEAD is not at the tip of origin/{branch}; the stamp says the plan was "
                 f"read there, so it has to be true. Run base_sync.sh first.")
    print(stamp(branch, tip))


def cmd_drift(args):
    branch = pilot_config.resolve_base_branch()
    tip = git("rev-parse", f"origin/{branch}")
    if tip is None:
        sys.exit(f"origin/{branch} is not known here; run base_sync.sh first")

    report = []
    for n in args.issues:
        issue = issues_plan.gh_issue(args.repo, n)
        entry = {"issue": n, "title": issue.get("title", "")}
        entry.update(drift_of(parse(issue.get("body")), tip, branch))
        report.append(entry)

    if args.json:
        print(json.dumps({"base_branch": branch, "tip": tip, "issues": report},
                         indent=2, ensure_ascii=False))
        return

    moved = 0
    print(f"{branch} on origin is at {tip[:9]}")
    for e in report:
        n = e["issue"]
        if e["state"] == "current":
            print(f"#{n}  planned against {branch} at {e['commit'][:9]} -- the current tip")
        elif e["state"] == "behind":
            moved += 1
            print(f"#{n}  planned against {branch} at {e['commit'][:9]} -- "
                  f"{len(e['commits'])} commit(s) landed since:")
            for line in e["commits"]:
                print(f"       {line}")
            print(f"     files changed since then ({len(e['files'])}):")
            for line in e["stat"].splitlines():
                print(f"       {line.strip()}")
        elif e["state"] == "unknown":
            moved += 1
            print(f"#{n}  planned against {e['planned_branch']} at {e['commit'][:9]}, a commit "
                  f"this clone does not have; read the issue against {branch} as it is now")
        elif e["state"] == "diverged":
            moved += 1
            print(f"#{n}  planned against {e['planned_branch']} at {e['commit'][:9]}, which is "
                  f"not in the history of {branch}; read the issue against {branch} as it is now")
        else:
            print(f"#{n}  no planning commit recorded in the issue; read it against "
                  f"{branch} as it is now")
    print()
    if moved:
        print(f"{moved} issue(s) were planned against code that has since changed: "
              "what changed belongs in the questions, or in the notes.")
    else:
        print(f"every issue with a recorded commit was planned against the current {branch}.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("stamp", help="print the line that records the planning commit")
    p.set_defaults(func=cmd_stamp)

    p = sub.add_parser("drift", help="what landed on the base branch since each issue was planned")
    p.add_argument("issues", nargs="+", type=int)
    p.add_argument("--repo", help="owner/name, when not running inside the repository")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_drift)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
