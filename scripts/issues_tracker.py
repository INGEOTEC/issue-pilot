#!/usr/bin/env python3
"""Every issue operation issue-pilot needs, for whichever tracker is configured.

issue-pilot used to say `gh issue ...` everywhere, which made it unusable in a
project that cannot be on GitHub (a paper whose only remote is Overleaf's git,
a self-hosted server, a repository with no remote at all).  This script is the
one place that knows the difference.  The commands and prompts call it and never
branch on the tracker themselves: "if the tracker is local do this, otherwise
that" is prose a model has to interpret, while the same sentence here is Python
with tests.

The `tracker` setting (see pilot_config.py) picks the backend:

  github   `gh issue ...`, exactly what issue-pilot always did.
  local    one markdown file per issue under `issues_dir`, described below.

Usage from the shell:

  issues_tracker.py kind
  issues_tracker.py view 7 [--json] [--repo owner/name]
  issues_tracker.py list [--state open|closed|all] [--search TEXT]
  issues_tracker.py create --title T --body-file F [--label L]...
  issues_tracker.py comment 7 --body-file F
  issues_tracker.py close 7
  issues_tracker.py commit -m MESSAGE [--push]

The local format
----------------
`<issues_dir>/<NNNN>-<slug>.md`, NNNN being the issue number zero-padded to four
digits so a directory listing sorts in issue order.  The file is the whole
history of the issue, comments included, so there is nothing else to keep in
step with it:

    ---
    number: 7
    title: Add a --format flag to the export command
    state: open
    created: 2026-11-25
    closed:
    labels: bug, export
    ---

    <the body, markdown>

    <!-- issue-pilot:comment 2026-11-25T18:03:11Z -->

    <the first comment>

The header is plain `key: value` lines and nothing more: the scripts are
standard-library only, and a YAML parser is not.  The comment marker is an HTML
comment so a markdown renderer hides it.  A closed issue stays where it is with
`state: closed`, so paths mentioned in old commits and comments stay valid and
there is no rename to merge.

A file written by hand is accepted (no header: the number is the leading digits
of its name, the title its first `# ` heading, the state open).  The file name is
what identifies an issue -- `number:` in the header is there for the reader --
so `7-x.md`, `007-x.md` and `0007-x.md` are all issue 7.

Rewriting a file (a comment, a close) changes only what it must.  The text is
edited as text rather than parsed and re-serialised, because the body belongs to
a person: unknown header keys, their order and every byte of the body survive.
"""
import argparse
import datetime
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pilot_config  # noqa: E402

MARKER = re.compile(r"^<!-- issue-pilot:comment (\S+) -->$")
LEADING_DIGITS = re.compile(r"^(\d+)")
SLUG_LENGTH = 50


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def _git(*args, check=False):
    out = subprocess.run(("git",) + args, capture_output=True, text=True,
                         cwd=pilot_config.repo_root())
    if check and out.returncode != 0:
        sys.exit("git %s failed: %s" % (" ".join(args), out.stderr.strip()))
    return out


def _settings():
    cfg = pilot_config.load()
    tracker = cfg["tracker"]
    if tracker not in pilot_config.TRACKERS:
        sys.exit("unknown tracker %r; it must be one of %s"
                 % (tracker, ", ".join(pilot_config.TRACKERS)))
    return cfg


def _one_line(text):
    return re.sub(r"[\r\n]+", " ", text).strip()


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


# --------------------------------------------------------------------------
# GitHub backend
# --------------------------------------------------------------------------

def _gh(*args):
    if shutil.which("gh") is None:
        sys.exit("the GitHub CLI (`gh`) is not installed; see https://cli.github.com")
    out = subprocess.run(("gh",) + args, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("gh %s failed: %s" % (" ".join(args[:2]), out.stderr.strip()))
    return out


def _gh_fetch(number, repo):
    cmd = ["issue", "view", str(number), "--json", "number,title,body,comments,state"]
    if repo:
        cmd += ["--repo", repo]
    data = json.loads(_gh(*cmd).stdout)
    return {
        "number": data["number"],
        "title": data.get("title") or "",
        "body": data.get("body") or "",
        # gh says OPEN / CLOSED; every caller wants one spelling.
        "state": (data.get("state") or "open").lower(),
        "comments": [{"body": c.get("body") or ""} for c in data.get("comments") or []],
    }


def _with_body_file(body, run):
    """Run `run(path)` with `body` in a temporary file: gh reads bodies from files."""
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "body.md"
        path.write_text(body, encoding="utf-8")
        return run(str(path))


def _gh_create(title, body, labels):
    def run(path):
        cmd = ["issue", "create", "--title", title, "--body-file", path]
        for label in labels:
            cmd += ["--label", label]
        return _gh(*cmd)
    out = _with_body_file(body, run)
    lines = out.stdout.strip().splitlines()
    location = lines[-1].strip() if lines else ""
    tail = location.rstrip("/").rsplit("/", 1)[-1]
    if not tail.isdigit():
        sys.exit("gh issue create printed no issue URL: %r" % out.stdout)
    return int(tail), location


def _gh_comment(number, body):
    _with_body_file(body, lambda path: _gh("issue", "comment", str(number),
                                           "--body-file", path))


def _gh_close(number):
    out = _gh("issue", "close", str(number))
    # gh exits 0 on an issue that is already closed and says so; closing twice
    # must not look like a failure, but the caller wants to know it was a no-op.
    return "already closed" not in (out.stdout + out.stderr).lower()


def _gh_list(state, search):
    cmd = ["issue", "list", "--state", state, "--limit", "1000",
           "--json", "number,title,state"]
    if search:
        cmd += ["--search", search]
    found = json.loads(_gh(*cmd).stdout or "[]")
    return [{"number": i["number"], "title": i.get("title") or "",
             "state": (i.get("state") or "open").lower()} for i in found]


# --------------------------------------------------------------------------
# Local backend
# --------------------------------------------------------------------------

def slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:SLUG_LENGTH]
    return slug or "issue"


def _issues_dir(cfg):
    problem = pilot_config.issues_dir_problem(cfg["issues_dir"])
    if problem:
        # The environment can set it without going through `check`.
        sys.exit("issues_dir %s" % problem)
    return pathlib.Path(pilot_config.repo_root()) / cfg["issues_dir"]


def _files(directory):
    """(number, path) of every issue file directly in `directory`, by file name."""
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.iterdir()):
        match = LEADING_DIGITS.match(path.name)
        if match and path.is_file() and path.name.lower().endswith(".md"):
            found.append((int(match.group(1)), path))
    return found


def _find(cfg, number):
    directory = _issues_dir(cfg)
    rel = cfg["issues_dir"]
    if not directory.is_dir():
        sys.exit("no issue #%d: the issues directory %s/ does not exist" % (number, rel))
    matches = [path for n, path in _files(directory) if n == number]
    if not matches:
        sys.exit("no issue #%d in %s/" % (number, rel))
    if len(matches) > 1:
        sys.exit("issue #%d is ambiguous in %s/: %s"
                 % (number, rel, ", ".join(p.name for p in matches)))
    return matches[0]


def _read(path):
    try:
        # Bytes, not text mode: universal newlines would rewrite a CRLF body.
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        sys.exit("cannot read %s: %s" % (path, exc))


def _write(path, text):
    path.write_bytes(text.encode("utf-8"))


def _trim(lines):
    """`lines` without the blank lines at both ends."""
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _parse(text):
    """The pieces of an issue file, and where they are, so it can be edited as text.

    Returns a dict: `lines` (the whole file split on newlines), `header` (the
    `(start, end)` indexes of the header lines, or None), `fields` (the header as
    a dict), `body` and `comments`.
    """
    lines = text.split("\n")
    header = None
    fields = {}
    rest_from = 0
    if lines[0].rstrip("\r") == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip("\r") == "---":
                header = (1, i)
                rest_from = i + 1
                break
    if header:
        for line in lines[header[0]:header[1]]:
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key.strip()] = value.strip()

    segments = [[]]
    stamps = []
    for line in lines[rest_from:]:
        match = MARKER.match(line.rstrip("\r"))
        if match:
            stamps.append(match.group(1))
            segments.append([])
        else:
            segments[-1].append(line)
    segments = ["\n".join(_trim(seg)) for seg in segments]
    return {"lines": lines, "header": header, "fields": fields, "rest_from": rest_from,
            "body": segments[0], "comments": segments[1:], "stamps": stamps}


def _title_of(parsed, path):
    if parsed["fields"].get("title"):
        return parsed["fields"]["title"]
    for line in parsed["body"].split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    # Only reachable for a hand-written file with no heading: the slug in its name.
    stem = path.name[:-3]
    return LEADING_DIGITS.sub("", stem).lstrip("-_. ") or stem


def _local_fetch(cfg, number):
    path = _find(cfg, number)
    parsed = _parse(_read(path))
    state = "closed" if parsed["fields"].get("state", "").lower() == "closed" else "open"
    return {"number": number, "title": _title_of(parsed, path), "body": parsed["body"],
            "state": state, "comments": [{"body": c} for c in parsed["comments"]]}


def _local_create(cfg, title, body, labels):
    directory = _issues_dir(cfg)
    number = max([n for n, _ in _files(directory)] or [0]) + 1
    title = _one_line(title)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("%04d-%s.md" % (number, slugify(title)))
    header = ["---", "number: %d" % number, "title: %s" % title, "state: open",
              "created: %s" % _now().strftime("%Y-%m-%d"), "closed:",
              "labels: %s" % ", ".join(labels), "---"]
    text = "\n".join(header) + "\n\n" + body.strip("\n") + "\n"
    try:
        # "x": never overwrite a file somebody else put there.
        with open(path, "xb") as out:
            out.write(text.encode("utf-8"))
    except FileExistsError:
        sys.exit("%s already exists" % path)
    return number, path.relative_to(pilot_config.repo_root()).as_posix()


def _local_comment(cfg, number, body):
    path = _find(cfg, number)
    text = _read(path)
    # Only ever appended to: what was there stays, byte for byte, as a prefix.
    if not text.endswith("\n"):
        text += "\n"
    if not text.endswith("\n\n"):
        text += "\n"
    text += "<!-- issue-pilot:comment %s -->\n\n%s\n" % (
        _now().strftime("%Y-%m-%dT%H:%M:%SZ"), body.strip("\n"))
    _write(path, text)


def _set_header(lines, header, changes):
    """`lines` with the header keys of `changes` replaced, or added at its end."""
    start, end = header
    body = list(lines[start:end])
    for key, value in changes.items():
        for i, line in enumerate(body):
            if ":" in line and line.split(":", 1)[0].strip() == key:
                body[i] = "%s: %s" % (key, value)
                break
        else:
            body.append("%s: %s" % (key, value))
    return lines[:start] + body + lines[end:]


def _local_close(cfg, number):
    path = _find(cfg, number)
    text = _read(path)
    parsed = _parse(text)
    if parsed["fields"].get("state", "").lower() == "closed":
        return False
    today = _now().strftime("%Y-%m-%d")
    if parsed["header"]:
        lines = _set_header(parsed["lines"], parsed["header"],
                            {"state": "closed", "closed": today})
        _write(path, "\n".join(lines))
    else:
        # A hand-written file gets a header now, so its state has somewhere to live.
        header = ["---", "number: %d" % number, "title: %s" % _one_line(_title_of(parsed, path)),
                  "state: closed", "closed: %s" % today, "---"]
        _write(path, "\n".join(header) + "\n\n" + text)
    return True


def _local_list(cfg, state, search):
    found = []
    for number, path in _files(_issues_dir(cfg)):
        parsed = _parse(_read(path))
        closed = parsed["fields"].get("state", "").lower() == "closed"
        issue_state = "closed" if closed else "open"
        title = _title_of(parsed, path)
        if state != "all" and issue_state != state:
            continue
        if search and search.lower() not in (title + "\n" + parsed["body"]).lower():
            continue
        found.append({"number": number, "title": title, "state": issue_state})
    return sorted(found, key=lambda i: i["number"])


# --------------------------------------------------------------------------
# The operations
# --------------------------------------------------------------------------

def fetch(number, repo=None):
    """`{number, title, body, state, comments: [{body}]}` of one issue.

    `repo` goes to `gh`; the local backend has no use for it.
    """
    cfg = _settings()
    if cfg["tracker"] == "local":
        return _local_fetch(cfg, int(number))
    return _gh_fetch(number, repo)


def create(title, body, labels=()):
    """Open an issue; returns `(number, location)` -- a URL, or a path in the repository."""
    cfg = _settings()
    if cfg["tracker"] == "local":
        return _local_create(cfg, title, body, list(labels))
    return _gh_create(title, body, list(labels))


def comment(number, body):
    cfg = _settings()
    if cfg["tracker"] == "local":
        return _local_comment(cfg, int(number), body)
    return _gh_comment(number, body)


def close(number):
    """Close an issue; True if this closed it, False if it already was closed."""
    cfg = _settings()
    if cfg["tracker"] == "local":
        return _local_close(cfg, int(number))
    return _gh_close(number)


def list_issues(state="open", search=None):
    """`[{number, title, state}]`, sorted by number."""
    cfg = _settings()
    if cfg["tracker"] == "local":
        return _local_list(cfg, state, search)
    return sorted(_gh_list(state, search), key=lambda i: i["number"])


def tracked():
    """True when the issues are files that git is expected to version.

    Asked of git every time instead of stored in the configuration: a setting
    could disagree with the repository (someone adds the directory to
    `.gitignore` later), git cannot.  `check-ignore` on a file that would live
    there works before the directory exists.
    """
    cfg = _settings()
    if cfg["tracker"] != "local":
        return False
    probe = "%s/.probe" % cfg["issues_dir"].rstrip("/")
    out = _git("check-ignore", "-q", "--", probe)
    if out.returncode == 0:
        return False
    if out.returncode == 1:
        return True
    sys.exit("git check-ignore failed: %s" % out.stderr.strip())


def commit(message, push=False):
    """Commit the issues directory and nothing else; returns what to tell the user.

    Safe to call in every configuration, so callers never have to ask whether
    it applies.  The path after `--` limits the commit to the issues whatever
    else happens to be staged: an "Add issue" commit must never sweep unrelated
    work in with it.
    """
    cfg = _settings()
    if not tracked():
        return "nothing to commit: issues are not kept in git here", None
    directory = cfg["issues_dir"]
    if not (pathlib.Path(pilot_config.repo_root()) / directory).is_dir():
        return "nothing to commit: no issue changed", None
    _git("add", "--", directory, check=True)
    if _git("diff", "--cached", "--quiet", "--", directory).returncode == 0:
        return "nothing to commit: no issue changed", None
    _git("commit", "-q", "-m", message, "--", directory, check=True)
    sha = _git("rev-parse", "--short", "HEAD", check=True).stdout.strip()
    done = "committed %s: %s" % (sha, message)
    if not push:
        return done, None
    if _git("remote", "get-url", "origin").returncode != 0:
        return done, "no remote named origin: the commit stays local"
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", check=True).stdout.strip()
    if branch == "HEAD":
        sys.exit("%s\nnot pushed: HEAD is detached. The commit is local only and has to be "
                 "pushed before issue-pilot will start from this branch again." % done)
    out = _git("push", "--quiet", "origin", "HEAD:%s" % branch)
    if out.returncode != 0:
        # Exit 1 on purpose: issue-pilot starts from the branch as it is on the
        # remote, so a commit that is only local would silently be left behind.
        sys.exit("%s\npush to origin/%s failed: %s\nThe commit is local only and has to be "
                 "pushed before issue-pilot will start from this branch again."
                 % (done, branch, out.stderr.strip()))
    return done, None


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def render(issue):
    lines = ["#%d %s" % (issue["number"], issue["title"]), "state: %s" % issue["state"],
             "", issue["body"]]
    for k, c in enumerate(issue["comments"], 1):
        lines += ["", "--- comment %d ---" % k, c["body"]]
    return "\n".join(lines)


def _read_body(arg):
    if arg == "-":
        return sys.stdin.read()
    try:
        return pathlib.Path(arg).read_text(encoding="utf-8")
    except OSError as exc:
        sys.exit("cannot read %s: %s" % (arg, exc))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("kind", help="print the configured tracker: github or local")

    p = sub.add_parser("view", help="show one issue")
    p.add_argument("number", type=int)
    p.add_argument("--json", action="store_true")
    p.add_argument("--repo", help="owner/name (github only)")

    p = sub.add_parser("list", help="one line per issue: number, state, title")
    p.add_argument("--state", choices=["open", "closed", "all"], default="open")
    p.add_argument("--search")

    p = sub.add_parser("create", help="open an issue; prints its number and location")
    p.add_argument("--title", required=True)
    p.add_argument("--body-file", required=True, help="file with the body, or - for stdin")
    p.add_argument("--label", action="append", default=[])

    p = sub.add_parser("comment", help="add a comment to an issue")
    p.add_argument("number", type=int)
    p.add_argument("--body-file", required=True, help="file with the comment, or - for stdin")

    p = sub.add_parser("close", help="close an issue")
    p.add_argument("number", type=int)

    p = sub.add_parser("commit", help="commit the local issues, if git versions them")
    p.add_argument("-m", "--message", required=True)
    p.add_argument("--push", action="store_true",
                   help="also push the current branch to origin, when there is one")

    args = ap.parse_args()

    if args.cmd == "kind":
        print(_settings()["tracker"])
    elif args.cmd == "view":
        issue = fetch(args.number, args.repo)
        print(json.dumps(issue, indent=2, ensure_ascii=False) if args.json else render(issue))
    elif args.cmd == "list":
        for i in list_issues(args.state, args.search):
            print("%d\t%s\t%s" % (i["number"], i["state"], i["title"]))
    elif args.cmd == "create":
        number, location = create(args.title, _read_body(args.body_file), args.label)
        print(number)
        print(location)
    elif args.cmd == "comment":
        comment(args.number, _read_body(args.body_file))
        print("commented on #%d" % args.number)
    elif args.cmd == "close":
        print("closed #%d" % args.number if close(args.number)
              else "#%d was already closed" % args.number)
    elif args.cmd == "commit":
        message, warning = commit(args.message, args.push)
        print(message)
        if warning:
            print(warning, file=sys.stderr)


if __name__ == "__main__":
    main()
