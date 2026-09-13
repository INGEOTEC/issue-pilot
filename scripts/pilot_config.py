#!/usr/bin/env python3
"""Per-repository configuration for issue-pilot.

Everything issue-pilot needs to know about a project -- which branch a run
starts from, how its tests are invoked, which model implements the issues --
lives in `.issue-pilot.json` at the root of the repository being worked on.

That file is required, and nothing starts without it.  An unattended run has
nobody to ask and no way to report that it guessed wrong: a session that infers
the test command from a Makefile it half understands can spend an hour being
confidently green about nothing.  Deciding it once, in writing, up front, is
cheap; discovering it was wrong three issues later is not.  `detect()` proposes
the answers and `/issue-pilot:init` confirms them with you.

Precedence, highest first:

  1. the environment (`ISSUE_PILOT_TEST_COMMAND`, `ISSUE_PILOT_MODEL`, ...),
     so a single run can override the project without editing it;
  2. `.issue-pilot.json` in the repository root;
  3. the built-in defaults below.

`base_branch` has one extra fallback the others do not: when nothing sets it,
`resolve_base_branch()` asks GitHub for the repository's default branch.  That
call is deliberately not made here -- reading configuration must not need a
network.

Usage from the shell, which is how the driver and the commands read it:

    python3 pilot_config.py get test_command
    python3 pilot_config.py show
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

CONFIG_FILENAME = ".issue-pilot.json"

# Keys, their defaults, and one line of documentation each -- `show --describe`
# and the README are both generated from this table, so it is the only place a
# new setting has to be added.
DEFAULTS = {
    "base_branch": (None, "Branch a run starts from. Default: the repository's default branch on GitHub."),
    "branch_prefix": ("issues", "Prefix of the run branch; the issue numbers are appended (issues-165-166)."),
    "test_command": (None, "Shell command that runs the test suite. Default: let the session infer it from the project."),
    "model": ("sonnet", "Model every autonomous session runs on, independent of your interactive default."),
    "effort": ("high", "Effort level of those sessions."),
    "max_attempts": (3, "Attempts on one issue before the run stops."),
    "pr_draft": (False, "Open the final pull request as a draft."),
    "issue_labels": ([], "Labels applied to issues opened by /issue-pilot:plan."),
}

# Where run state and logs live.  Not part of the per-repo config: it is a
# property of the machine, so only the environment moves it.
def home() -> pathlib.Path:
    explicit = os.environ.get("ISSUE_PILOT_HOME")
    if explicit:
        return pathlib.Path(explicit)
    claude_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    base = pathlib.Path(claude_dir) if claude_dir else pathlib.Path.home() / ".claude"
    return base / "issue-pilot"


def state_dir() -> pathlib.Path:
    d = home() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def branch_name(issues, cfg=None) -> str:
    """The single branch a run lives on, derived from the issues it covers."""
    cfg = cfg if cfg is not None else load()
    return "%s-%s" % (cfg["branch_prefix"], "-".join(str(n) for n in issues))


def notes_file(issues, cfg=None) -> pathlib.Path:
    """Where the answers gathered before a run are kept.

    They are written by whoever conducts the interview and read by the driver;
    both sides ask for the path here so they cannot disagree about it.
    """
    return state_dir() / ("issues-notes-%s.txt" % branch_name(issues, cfg))


def repo_root(start=None) -> str:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, cwd=start)
    if out.returncode != 0:
        sys.exit("not inside a git repository")
    return out.stdout.strip()


def _coerce(value, default):
    """Environment variables arrive as text; the config file is already typed."""
    if not isinstance(value, str) or default is None:
        return value
    if isinstance(default, bool):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        try:
            return int(value)
        except ValueError:
            return default
    if isinstance(default, list):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


def load(root=None) -> dict:
    """The effective configuration for the repository at `root`."""
    root = root or repo_root()
    cfg = {key: default for key, (default, _) in DEFAULTS.items()}

    path = pathlib.Path(root) / CONFIG_FILENAME
    if path.exists():
        try:
            from_file = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            sys.exit(f"{path}: not valid JSON ({exc})")
        if not isinstance(from_file, dict):
            sys.exit(f"{path}: expected a JSON object")
        unknown = sorted(set(from_file) - set(DEFAULTS))
        if unknown:
            # Loud, but not fatal: a typo that silently does nothing is the
            # worst outcome, and refusing to run over one is worse than saying so.
            print(f"warning: {path}: ignoring unknown key(s): {', '.join(unknown)}",
                  file=sys.stderr)
        cfg.update({k: v for k, v in from_file.items() if k in DEFAULTS})

    for key, (default, _) in DEFAULTS.items():
        env = os.environ.get("ISSUE_PILOT_" + key.upper())
        if env is not None and env != "":
            cfg[key] = _coerce(env, default)
    return cfg


def default_branch(repo=None) -> str:
    """The repository's default branch on GitHub.

    Worth keeping apart from `base_branch`: GitHub only closes the issues a pull
    request references when that pull request merges into *this* branch.  A
    project whose runs target `develop` therefore has to close them itself.
    """
    cmd = ["gh", "repo", "view", "--json", "defaultBranchRef",
           "--jq", ".defaultBranchRef.name"]
    if repo:
        cmd[3:3] = [repo]
    out = subprocess.run(cmd, capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def resolve_base_branch(cfg=None, repo=None) -> str:
    """Configured base branch, or the repository's default branch on GitHub.

    Falls back to whatever git says HEAD is when `gh` cannot answer, so a
    network outage degrades into "start from where you are" instead of failing.
    """
    cfg = cfg if cfg is not None else load()
    if cfg.get("base_branch"):
        return cfg["base_branch"]
    cmd = ["gh", "repo", "view", "--json", "defaultBranchRef",
           "--jq", ".defaultBranchRef.name"]
    if repo:
        cmd[3:3] = [repo]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode == 0 and out.stdout.strip():
        return out.stdout.strip()
    head = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True)
    return head.stdout.strip() or "main"


# Ordered most specific first: a Makefile target is a choice the project made
# explicitly, and beats guessing from the presence of a manifest.
def detect_test_command(root):
    """The project's own way of running its tests, or None if it is not obvious."""
    root = pathlib.Path(root)

    makefile = root / "Makefile"
    if makefile.exists() and re.search(r"^tests?\s*:", makefile.read_text(errors="replace"),
                                       re.MULTILINE):
        return "make test"

    package = root / "package.json"
    if package.exists():
        try:
            scripts = (json.loads(package.read_text()) or {}).get("scripts") or {}
        except json.JSONDecodeError:
            scripts = {}
        if scripts.get("test"):
            return "npm test"

    if ((root / "pyproject.toml").exists() or (root / "setup.py").exists()
            or any(root.glob("tests/test_*.py")) or any(root.glob("test_*.py"))):
        return "pytest -q"

    if (root / "Cargo.toml").exists():
        return "cargo test"
    if (root / "go.mod").exists():
        return "go test ./..."
    return None


def detect(root=None) -> dict:
    """A proposed configuration for this repository, for a human to confirm."""
    root = root or repo_root()
    cfg = {key: default for key, (default, _) in DEFAULTS.items()}
    cfg["base_branch"] = resolve_base_branch(cfg)
    cfg["test_command"] = detect_test_command(root)
    return cfg


def validate(root=None):
    """Everything wrong with this repository's configuration, as messages."""
    root = pathlib.Path(root or repo_root())
    path = root / CONFIG_FILENAME

    if not path.exists():
        return ["%s is missing from %s" % (CONFIG_FILENAME, root)]
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return ["%s: not valid JSON (%s)" % (path, exc)]
    if not isinstance(data, dict):
        return ["%s: expected a JSON object" % path]

    problems = []
    unknown = sorted(set(data) - set(DEFAULTS))
    if unknown:
        # Tolerated at runtime, refused here: a typo caught at setup time costs
        # nothing, and the same typo found mid-run costs the run.
        problems.append("%s: unknown setting(s) %s; known settings are %s"
                        % (path, ", ".join(unknown), ", ".join(sorted(DEFAULTS))))
    if not str(data.get("test_command") or "").strip():
        problems.append(
            "%s: test_command is not set. An unattended session has to know what "
            "green means before it may commit. A project with no tests can set it "
            "to a command that exits 0." % path)
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("get", help="print one setting, empty if unset")
    p.add_argument("key")

    p = sub.add_parser("show", help="print the effective configuration as JSON")
    p.add_argument("--describe", action="store_true",
                   help="document every setting instead of printing values")

    sub.add_parser("base-branch", help="resolve the base branch, asking GitHub if needed")
    sub.add_parser("path", help="print where run state is kept")

    sub.add_parser("default-branch", help="print the repository's default branch on GitHub")
    sub.add_parser("check", help="fail unless this repository is configured")
    sub.add_parser("detect", help="propose a configuration for this repository")

    p = sub.add_parser("branch-name", help="print the branch a run over these issues uses")
    p.add_argument("issues", nargs="+", type=int)

    p = sub.add_parser("notes-file", help="print where a run over these issues keeps its answers")
    p.add_argument("issues", nargs="+", type=int)

    args = ap.parse_args()

    if args.cmd == "get":
        if args.key not in DEFAULTS:
            sys.exit(f"unknown setting {args.key!r}; known: {', '.join(sorted(DEFAULTS))}")
        value = load()[args.key]
        if value is None or value == []:
            return
        print(",".join(str(v) for v in value) if isinstance(value, list) else value)
    elif args.cmd == "show":
        if args.describe:
            for key, (default, doc) in DEFAULTS.items():
                print(f"{key}\n    {doc}\n    default: {json.dumps(default)}\n")
        else:
            print(json.dumps(load(), indent=2))
    elif args.cmd == "base-branch":
        print(resolve_base_branch())
    elif args.cmd == "path":
        print(state_dir())
    elif args.cmd == "default-branch":
        print(default_branch())
    elif args.cmd == "check":
        problems = validate()
        if problems:
            print("issue-pilot is not configured for this repository.\n", file=sys.stderr)
            for problem in problems:
                print("  " + problem, file=sys.stderr)
            print("\nRun /issue-pilot:init to set it up.", file=sys.stderr)
            sys.exit(1)
        print("ok: %s" % (pathlib.Path(repo_root()) / CONFIG_FILENAME))
    elif args.cmd == "detect":
        print(json.dumps(detect(), indent=2))
    elif args.cmd == "branch-name":
        print(branch_name(args.issues))
    elif args.cmd == "notes-file":
        print(notes_file(args.issues))


if __name__ == "__main__":
    main()
