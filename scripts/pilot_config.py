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
network.  It is also not made at all when the project does not use GitHub
(`uses_github()`): there is nobody to ask, and `check` then insists on the
setting instead.

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
    "base_branch": (None, "Branch a run starts from. Default: the repository's default branch on GitHub, when it is on GitHub."),
    "branch_prefix": ("issues", "Prefix of the run branch; the issue numbers are appended (issues-165-166)."),
    "test_command": (None, "Shell command that runs the test suite. Default: let the session infer it from the project."),
    "model": ("sonnet", "Model every autonomous session runs on, independent of your interactive default."),
    "effort": ("high", "Effort level of those sessions."),
    "max_attempts": (3, "Attempts on one issue before the run stops."),
    "pr_draft": (False, "Open the final pull request as a draft."),
    "issue_labels": ([], "Labels applied to issues opened by /issue-pilot:plan."),
    "tracker": ("github", "Where issues live: github, or local (markdown files under issues_dir)."),
    "issues_dir": ("issues", "Directory, relative to the repository root, that holds the issues when tracker is local."),
    "landing": ("pull_request", "How a finished run reaches the base branch: pull_request (GitHub), merge, push or branch."),
    "watch": (True, "After /issue-pilot:run and /issue-pilot:fix detach the driver, wait in the background and report when it stops."),
}

TRACKERS = ("github", "local")
LANDINGS = ("pull_request", "merge", "push", "branch")


def issues_dir_problem(value):
    """Why `value` cannot be `issues_dir`, or None.

    The directory is created, written and `git add`ed by the tracker, so it has
    to stay inside the repository: an absolute path or a `..` would let a typo
    write (and commit) somewhere else.
    """
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty path relative to the repository root"
    path = pathlib.PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or pathlib.PureWindowsPath(value).is_absolute():
        return "must be relative to the repository root, not absolute (%s)" % value
    if ".." in path.parts:
        return "must not contain a `..` component (%s)" % value
    return None


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


def repo_slug(root=None) -> str:
    """The repository's path as one file-name-safe word (`/home/u/foo` -> `home-u-foo`).

    Also names the run state file: `issues_state.repo_slug()` calls this. It
    lives here because `issues_state` imports this module, not the other way.
    """
    root = root or repo_root()
    return root.strip("/").replace("/", "-")


def notes_file(issues, cfg=None) -> pathlib.Path:
    """Where the answers gathered before a run are kept.

    They are written by whoever conducts the interview and read by the driver;
    both sides ask for the path here so they cannot disagree about it.

    The path is per repository (`state/notes/<slug>-<branch>.txt`): the branch
    name alone is the same in every repository that runs the same issue numbers.
    Only prints a path; see `fresh_notes_file()` for the one that clears it.
    """
    return state_dir() / "notes" / ("%s-%s.txt" % (repo_slug(), branch_name(issues, cfg)))


def fresh_notes_file(issues, cfg=None) -> pathlib.Path:
    """`notes_file()`, with nothing at that path, ready for a brand-new file.

    A file left by an earlier run of the same issues would make the Write tool
    refuse to overwrite it (it has not read it) and let a stale answer sheet be
    taken for this run's. Also deletes the old-format notes, which lived
    directly in the state directory under a name that did not include the
    repository: `state/issues-notes-*.txt`, files only, and nothing else there.
    """
    path = notes_file(issues, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    for legacy in state_dir().glob("issues-notes-*.txt"):
        if legacy.is_file() or legacy.is_symlink():
            legacy.unlink(missing_ok=True)
    return path


def running_copy():
    """The root and version of the copy of issue-pilot that is executing.

    Derived from `__file__`, not `CLAUDE_PLUGIN_ROOT`: the driver's
    subprocesses need not inherit that variable, and `__file__` is correct in
    every layout -- plugin cache, development checkout, and an `install.sh`
    lib alike.

    Never raises. A missing, unreadable or malformed manifest, or one with no
    `"version"` key, just means the version is unknown: `status` is what
    people run when something is already wrong, and may not fail because a
    manifest is absent.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    version = None
    try:
        data = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
        if isinstance(data, dict):
            version = data.get("version")
    except (OSError, json.JSONDecodeError):
        pass
    return str(root), version


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


def uses_github(cfg=None) -> bool:
    """Whether `gh` is needed at all: the issues or the landing are GitHub's.

    Derived rather than a third setting because there is nothing else `gh` is
    used for.  With the defaults it is true, so existing projects are unaffected.
    """
    cfg = cfg if cfg is not None else load()
    return cfg["tracker"] == "github" or cfg["landing"] == "pull_request"


def has_origin(root=None) -> bool:
    """Whether this repository has a remote called `origin`.

    Detected, not configured: the repository already knows.  Only `origin` is
    supported, here and everywhere else.
    """
    out = subprocess.run(["git", "remote", "get-url", "origin"],
                         capture_output=True, text=True, cwd=root)
    return out.returncode == 0


def hosting(root=None) -> dict:
    """Where this repository's remote is, judged offline from the URL of `origin`.

    `origin` is whether there is one; `github` whether its URL names github.com.
    No network and no `gh`: it only decides which option /issue-pilot:init
    offers first, and a wrong guess costs one different answer, not a failure.
    """
    out = subprocess.run(["git", "remote", "get-url", "origin"],
                         capture_output=True, text=True, cwd=root)
    origin = out.returncode == 0
    return {"origin": origin, "github": origin and "github.com" in out.stdout}


def _gh_default_branch(repo=None) -> str:
    """The default branch GitHub reports, or "" when gh cannot say.

    "Cannot say" includes gh not being installed: a machine that has never
    heard of GitHub must still get past base_sync.sh on its way to
    /issue-pilot:init, which is where it is told not to use GitHub.
    (Review fix-1: before this, a missing `gh` was a traceback.)
    """
    cmd = ["gh", "repo", "view", "--json", "defaultBranchRef",
           "--jq", ".defaultBranchRef.name"]
    if repo:
        cmd[3:3] = [repo]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def default_branch(repo=None) -> str:
    """The repository's default branch on GitHub.

    Worth keeping apart from `base_branch`: GitHub only closes the issues a pull
    request references when that pull request merges into *this* branch.  A
    project whose runs target `develop` therefore has to close them itself.
    """
    if not uses_github():
        return ""
    return _gh_default_branch(repo)


def resolve_base_branch(cfg=None, repo=None) -> str:
    """Configured base branch, or the repository's default branch on GitHub.

    Falls back to whatever git says HEAD is when `gh` cannot answer -- or is not
    to be asked, because the project does not use GitHub -- so a network outage
    degrades into "start from where you are" instead of failing.
    """
    cfg = cfg if cfg is not None else load()
    if cfg.get("base_branch"):
        return cfg["base_branch"]
    if uses_github(cfg):
        found = _gh_default_branch(repo)
        if found:
            return found
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
    # Before the base branch is resolved: a repository that is not on GitHub is
    # never asked of `gh`, and the proposal is then the branch it is on.
    if not hosting(root)["github"]:
        cfg["tracker"], cfg["landing"] = "local", "merge"
    cfg["base_branch"] = resolve_base_branch(cfg)
    cfg["test_command"] = detect_test_command(root)
    return cfg


def _info_exclude(root):
    """The clone-local ignore file, wherever git keeps it (worktrees included)."""
    out = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"],
                         capture_output=True, text=True, cwd=root)
    if out.returncode != 0:
        sys.exit("not inside a git repository")
    return pathlib.Path(root) / out.stdout.strip()


def _repo_path(root, path):
    """`(name, is_directory)` for a path given relative to the repository root."""
    name = path.strip("/")
    problem = issues_dir_problem(name) if name else "is empty"
    if problem:
        sys.exit("cannot exclude %r: it %s" % (path, problem.replace("must be", "has to be")))
    return name, path.endswith("/") or (pathlib.Path(root) / name).is_dir()


def exclude(paths, root=None):
    """Keep `paths` out of git in this clone only, through `.git/info/exclude`.

    Not `.gitignore`: the point of ignoring is to leave the repository untouched,
    and `.gitignore` is itself a versioned file.  Patterns are anchored
    (`/issues/`), so they cannot catch a same-named path elsewhere.  Returns the
    lines to print; exits 2, changing nothing, when git already tracks something
    there -- an ignore pattern does not apply to a tracked file, so it would
    promise something it cannot deliver.
    """
    root = root or repo_root()
    wanted = []
    for path in paths:
        name, is_dir = _repo_path(root, path)
        tracked = subprocess.run(["git", "ls-files", "--", name], capture_output=True,
                                 text=True, cwd=root).stdout.strip()
        if tracked:
            print("error: git already tracks %s, so ignoring it would do nothing.\n"
                  "  To turn a versioned path into an ignored one:\n"
                  "    git rm -r --cached %s    # then commit\n"
                  "  and run this again." % (name, name), file=sys.stderr)
            sys.exit(2)
        wanted.append("/" + name + ("/" if is_dir else ""))

    target = _info_exclude(root)
    existing = target.read_text().splitlines() if target.exists() else []
    report, added = [], []
    for pattern in wanted:
        if pattern in (line.strip() for line in existing + added):
            report.append("already excluded " + pattern)
        else:
            added.append(pattern)
            report.append("excluded " + pattern)
    if added:
        target.parent.mkdir(parents=True, exist_ok=True)
        text = target.read_text() if target.exists() else ""
        if text and not text.endswith("\n"):
            text += "\n"
        target.write_text(text + "".join(p + "\n" for p in added))
    return report


def tracking(path, root=None) -> str:
    """`ignored` when git ignores `path` here, else `versioned`.

    Asked of git every time rather than remembered: an answer from memory can
    disagree with the repository, git cannot.  A directory is probed through a
    file that would live in it, which works before it exists.
    """
    root = root or repo_root()
    name, is_dir = _repo_path(root, path)
    probe = name + "/.probe" if is_dir else name
    out = subprocess.run(["git", "check-ignore", "-q", "--", probe], cwd=root)
    return "ignored" if out.returncode == 0 else "versioned"


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
    if "tracker" in data and data["tracker"] not in TRACKERS:
        problems.append("%s: tracker is %r; it must be one of %s"
                        % (path, data["tracker"], ", ".join(TRACKERS)))
    if "landing" in data and data["landing"] not in LANDINGS:
        problems.append("%s: landing is %r; it must be one of %s"
                        % (path, data["landing"], ", ".join(LANDINGS)))
    if "issues_dir" in data:
        problem = issues_dir_problem(data["issues_dir"])
        if problem:
            problems.append("%s: issues_dir %s" % (path, problem))
    effective = {key: data.get(key, default) for key, (default, _) in DEFAULTS.items()}
    if (not uses_github(effective)
            and not (isinstance(data.get("base_branch"), str) and data["base_branch"].strip())):
        problems.append("%s: base_branch is not set, and without GitHub there is nothing "
                        "to ask for a default" % path)
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
    sub.add_parser("version", help="print the running version and the path it runs from")

    sub.add_parser("uses-github", help="print yes when gh is needed (tracker or landing is GitHub's), else no")
    sub.add_parser("remote", help="print origin when that remote exists, nothing otherwise")
    sub.add_parser("hosting", help="print as JSON whether origin exists and whether it is on GitHub")
    p = sub.add_parser("exclude", help="keep paths out of git in this clone (.git/info/exclude)")
    p.add_argument("paths", nargs="+")
    p = sub.add_parser("tracking", help="print ignored or versioned for a path")
    p.add_argument("path")
    sub.add_parser("default-branch", help="print the repository's default branch on GitHub")
    sub.add_parser("check", help="fail unless this repository is configured")
    sub.add_parser("detect", help="propose a configuration for this repository")

    p = sub.add_parser("branch-name", help="print the branch a run over these issues uses")
    p.add_argument("issues", nargs="+", type=int)

    p = sub.add_parser("notes-file", help="print where a run over these issues keeps its answers")
    p.add_argument("--fresh", action="store_true",
                   help="first remove any file already at that path, and the old-format "
                        "notes (state/issues-notes-*.txt), so the caller writes a new one")
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
    elif args.cmd == "version":
        root, version = running_copy()
        print(version or "unknown")
        print(root)
    elif args.cmd == "uses-github":
        print("yes" if uses_github() else "no")
    elif args.cmd == "remote":
        if has_origin():
            print("origin")
    elif args.cmd == "hosting":
        print(json.dumps(hosting()))
    elif args.cmd == "exclude":
        print("\n".join(exclude(args.paths)))
    elif args.cmd == "tracking":
        print(tracking(args.path))
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
        print(fresh_notes_file(args.issues) if args.fresh else notes_file(args.issues))


if __name__ == "__main__":
    main()
