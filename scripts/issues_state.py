#!/usr/bin/env python3
"""Run state for issue-pilot, kept on disk so it survives `/clear`.

A run spans several Claude Code conversations on purpose -- that is the whole
point of the tool -- so nothing about it can live in the conversation.  The plan,
which issue is next, what phase 1 was told, which commits closed what and why a
run stopped all live in one JSON file per repository, under
`$ISSUE_PILOT_HOME/state` (`~/.claude/issue-pilot/state` by default).

Subcommands
  init <issues...> [--branch B] [--repo R] [--notes TEXT]
        Builds the plan (issues_plan.py) and stores it.
  next  Prints the next pending issue and whether the conversation must be
        cleared before starting it.  Exits 3 when the run is finished.
  attempt <n>                 Records another attempt on an issue.
  done <n> [--commit SHA]     Marks an issue implemented.
  block <n> [--reason TEXT]   Marks an issue blocked; the run stops there.
  unblock [n]  Clears the blocked flag and puts the blocked issue back to
        pending, so `next` hands it out again.  With no argument it reopens
        whatever is currently blocked.
  pr <url> --number N --base B [--closes-automatically]
        Records the pull request that closes the run.
  show  Dumps the whole state as JSON.
  status  The same thing for a human: what is done, what is next, what stopped.
  path  Prints the state file for this repository.
"""
import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pilot_config  # noqa: E402

MARKS = {"done": "+", "pending": ".", "blocked": "!"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0).isoformat()


def state_path(root=None):
    root = root or pilot_config.repo_root()
    slug = root.strip("/").replace("/", "-")
    return pilot_config.state_dir() / f"issues-run-{slug}.json"


def log_dir(state):
    d = pilot_config.home() / "logs" / (state.get("branch") or "run")
    d.mkdir(parents=True, exist_ok=True)
    return d


def driver(state):
    """(pid, running) for a detached run, or None if it was never detached.

    `os.kill(pid, 0)` is the portable way to ask; a recycled pid can make this
    say "running" about somebody else's process, which is a good deal better
    than the alternative of not reporting it at all.
    """
    pid_file = log_dir(state) / "driver.pid"
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return pid, False
    except PermissionError:
        return pid, True
    return pid, True


def load():
    p = state_path()
    if not p.exists():
        sys.exit(f"no run in progress ({p} does not exist); run `init` first")
    return json.loads(p.read_text())


def save(state):
    state["updated_at"] = now()
    state_path().write_text(json.dumps(state, indent=2, ensure_ascii=False))


def cmd_init(args):
    cmd = [sys.executable, str(HERE / "issues_plan.py")]
    if args.repo:
        cmd += ["--repo", args.repo]
    cmd += [str(n) for n in args.issues]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(out.stderr.strip())
    plan = json.loads(out.stdout)
    state = {
        "repo_root": pilot_config.repo_root(),
        "repo": args.repo,
        "branch": args.branch,
        "base_branch": args.base_branch,
        "notes": args.notes or "",
        "plan": plan["plan"],
        "status": {str(n): "pending" for n in plan["issues"]},
        "attempts": {str(n): 0 for n in plan["issues"]},
        "commits": {},
        "blocked": None,
        "created_at": now(),
    }
    save(state)
    print(json.dumps(state, indent=2, ensure_ascii=False))


def cmd_next(args):
    state = load()
    if state.get("blocked"):
        print(json.dumps({"finished": True, "blocked": state["blocked"]}, indent=2))
        sys.exit(3)
    for item in state["plan"]:
        if state["status"][str(item["issue"])] == "pending":
            print(json.dumps({
                "issue": item["issue"],
                "title": item["title"],
                "depends_on": item["depends_on"],
                "clear_before": item["clear_before"],
                "branch": state["branch"],
                "notes": state["notes"],
                "attempts": state.get("attempts", {}).get(str(item["issue"]), 0),
                "remaining": [i["issue"] for i in state["plan"]
                              if state["status"][str(i["issue"])] == "pending"],
            }, indent=2, ensure_ascii=False))
            return
    print(json.dumps({"finished": True, "blocked": None}, indent=2))
    sys.exit(3)


def cmd_attempt(args):
    state = load()
    key = str(args.issue)
    state.setdefault("attempts", {})[key] = state.get("attempts", {}).get(key, 0) + 1
    save(state)
    print(f"issue {args.issue}: attempt {state['attempts'][key]}")


def cmd_done(args):
    state = load()
    state["status"][str(args.issue)] = "done"
    if args.commit:
        state["commits"][str(args.issue)] = args.commit
    # An issue can be blocked and then finished by a later attempt; leaving the
    # flag behind would stop the run on an issue that is no longer stuck.
    if (state.get("blocked") or {}).get("issue") == args.issue:
        state["blocked"] = None
    save(state)
    print(f"issue {args.issue} marked done")


def cmd_block(args):
    state = load()
    state["status"][str(args.issue)] = "blocked"
    state["blocked"] = {"issue": args.issue, "reason": args.reason or "",
                        "at": now()}
    save(state)
    print(f"issue {args.issue} marked blocked; run stops here")


def cmd_unblock(args):
    state = load()
    issues = [args.issue] if args.issue is not None else []
    if not issues and state.get("blocked"):
        issues = [state["blocked"]["issue"]]
    # A run can only ever be blocked on one issue, but reopen anything marked
    # blocked so a hand-edited state file cannot leave a stale entry behind.
    issues += [int(n) for n, st in state["status"].items()
               if st == "blocked" and int(n) not in issues]
    if not issues:
        print("nothing was blocked")
        return
    for n in issues:
        state["status"][str(n)] = "pending"
    state["blocked"] = None
    save(state)
    print("reopened as pending: " + ", ".join(str(n) for n in sorted(issues)))


def cmd_pr(args):
    state = load()
    state["pull_request"] = {
        "url": args.url,
        "number": args.number,
        "base": args.base,
        # GitHub closes the issues a pull request references only when it merges
        # into the repository's default branch.  Anywhere else, closing them is
        # our job, and the run has to say so rather than leave them open for
        # weeks in the belief that it was handled.
        "closes_automatically": bool(args.closes_automatically),
    }
    save(state)
    print(f"pull request {args.url} recorded")


def cmd_status(args):
    """The state file, read out loud.  `show` is for scripts; this is for people."""
    state = load()
    attempts = state.get("attempts", {})
    print(f"repository : {state['repo_root']}")
    print(f"branch     : {state.get('branch')} (off {state.get('base_branch') or '?'})")
    print(f"started    : {state.get('created_at', '?')}")
    running = driver(state)
    if running:
        pid, alive = running
        print(f"driver     : {'running' if alive else 'not running'} (pid {pid}), "
              f"log {log_dir(state) / 'driver.log'}")
    print()
    for item in state["plan"]:
        n = str(item["issue"])
        st = state["status"][n]
        deps = ", ".join(f"#{d}" for d in item["depends_on"]) or "-"
        line = (f" {MARKS.get(st, '?')} #{n:<6} {st:<8} "
                f"fresh={str(item['clear_before']):<5} depends={deps:<12} "
                f"attempts={attempts.get(n, 0)}")
        if state["commits"].get(n):
            line += f" commit={state['commits'][n][:9]}"
        print(line)
        print(f"     {item['title']}")
    print()

    pr = state.get("pull_request")
    if pr:
        print(f"pull req   : {pr['url']} -> {pr['base']}")
        if not pr.get("closes_automatically"):
            print("             merging this will NOT close the issues (the base is not")
            print("             the default branch); run /issue-pilot:issues-close after.")
        print()

    blocked = state.get("blocked")
    if blocked:
        print(f"STOPPED on #{blocked['issue']}: {blocked.get('reason') or 'no reason recorded'}")
        print(f"logs: {log_dir(state)}")
        print("retry it with: issues_run.sh --resume")
    elif all(v == "done" for v in state["status"].values()):
        print("every issue is done -- open the pull request with /issue-pilot:issues-pr")
    else:
        pending = [n for n, v in state["status"].items() if v == "pending"]
        print(f"pending: {', '.join('#' + n for n in pending)}")
        print(f"logs: {log_dir(state)}")

    if state.get("notes"):
        print()
        print("--- phase 1 notes ---")
        print(state["notes"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("issues", nargs="+", type=int)
    p.add_argument("--branch")
    p.add_argument("--base-branch")
    p.add_argument("--repo")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_init)

    sub.add_parser("next").set_defaults(func=cmd_next)

    p = sub.add_parser("attempt"); p.add_argument("issue", type=int)
    p.set_defaults(func=cmd_attempt)

    p = sub.add_parser("done"); p.add_argument("issue", type=int)
    p.add_argument("--commit"); p.set_defaults(func=cmd_done)

    p = sub.add_parser("block"); p.add_argument("issue", type=int)
    p.add_argument("--reason"); p.set_defaults(func=cmd_block)

    p = sub.add_parser("unblock")
    p.add_argument("issue", nargs="?", type=int)
    p.set_defaults(func=cmd_unblock)

    p = sub.add_parser("pr")
    p.add_argument("url")
    p.add_argument("--number", type=int)
    p.add_argument("--base")
    p.add_argument("--closes-automatically", action="store_true")
    p.set_defaults(func=cmd_pr)

    sub.add_parser("show").set_defaults(
        func=lambda a: print(json.dumps(load(), indent=2, ensure_ascii=False)))
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("path").set_defaults(func=lambda a: print(state_path()))

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
