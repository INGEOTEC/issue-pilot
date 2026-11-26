#!/usr/bin/env python3
"""Publish the tag and the GitHub release of every version that has none.

Why this exists: a pull request that releases a version bumps "version" in
.claude-plugin/plugin.json and writes a "## [X.Y.Z] - date" section in
CHANGELOG.md, and nothing then created the tag or the release.  It was done by
hand and forgotten, and the releases page fell nine versions behind the code.

The question this answers is "which versions of main have no tag?", not "did
this push change the version?".  The two agree on a merge that bumps the
version, but only the first is right when the workflow is re-run, when two
merges land close together or when a run was skipped: a missing tag is the
actual thing to fix.

It lives under .github/ and not under scripts/ on purpose: it is maintainer
tooling, and scripts/ is what gets installed on users' machines with the plugin.

Standard library only; `git` and `gh` are run as subprocesses.  The tag needs a
committer identity, which this script does not set: the workflow does.

    release.py notes <version>              the changelog section of a version
    release.py versions                     every version and where it appeared
    release.py pending [--all]              the ones that have no tag
    release.py publish [--all] [--dry-run]  tag them and create the releases
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

MANIFEST = ".claude-plugin/plugin.json"
CHANGELOG = "CHANGELOG.md"


def run(*cmd, check=True):
    out = subprocess.run(cmd, capture_output=True, text=True)
    if check and out.returncode != 0:
        sys.exit("%s failed: %s" % (" ".join(cmd), (out.stderr or out.stdout).strip()))
    return out


def git(*args, check=True):
    return run("git", *args, check=check)


def version_key(version):
    """Numbers compare as numbers: 0.10.0 is later than 0.9.0."""
    return tuple(int(n) for n in re.findall(r"\d+", version))


def version_at(commit):
    """The "version" of the manifest at a commit, or None if it has none."""
    out = git("show", "%s:%s" % (commit, MANIFEST), check=False)
    if out.returncode != 0:
        return None
    try:
        version = json.loads(out.stdout).get("version")
    except (ValueError, AttributeError):
        return None
    return version if isinstance(version, str) and version else None


def notes(version):
    """The body of the changelog section of a version, without its heading."""
    try:
        with open(CHANGELOG, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as exc:
        sys.exit("cannot read %s: %s" % (CHANGELOG, exc))
    heading = "## [%s]" % version
    body = None
    for line in lines:
        if body is None:
            if line.startswith(heading):
                body = []
        elif line.startswith("## ["):
            break
        else:
            body.append(line)
    text = "\n".join(body or []).strip("\n")
    if not text.strip():
        sys.exit("%s has no section for %s" % (CHANGELOG, version))
    return text + "\n"


def versions():
    """[(version, sha)], oldest first: each version at the commit it first appeared.

    Only the first-parent line is walked, so a version is dated by the merge
    that brought it to main and not by a commit of the branch that was merged.
    """
    first = {}
    for sha in git("rev-list", "--first-parent", "--reverse", "HEAD").stdout.split():
        version = version_at(sha)
        if version is not None and version not in first:
            first[version] = sha
    return list(first.items())


def has_tag(version):
    return git("rev-parse", "-q", "--verify", "refs/tags/v%s" % version,
               check=False).returncode == 0


def pending(everything):
    found = versions()
    if not everything:
        current = version_at("HEAD")
        found = [(v, s) for v, s in found if v == current]
    return [(v, s) for v, s in found if not has_tag(v)]


def cmd_notes(args):
    sys.stdout.write(notes(args.version))


def cmd_versions(args):
    for version, sha in versions():
        print(version, sha)


def cmd_pending(args):
    for version, sha in pending(args.all):
        print(version, sha)


def cmd_publish(args):
    todo = pending(args.all)
    if not todo:
        print("nothing to release: every version has a tag" if args.all else
              "nothing to release: v%s already exists" % version_at("HEAD"))
        return
    # Every section is read before anything is created, so a missing one cannot
    # leave half of the versions published.
    bodies, missing = {}, []
    for version, _ in todo:
        try:
            bodies[version] = notes(version)
        except SystemExit:
            missing.append(version)
    if missing:
        sys.exit("%s has no section for %s" % (CHANGELOG, ", ".join(missing)))
    if args.dry_run:
        for version, sha in todo:
            print("would release v%s at %s" % (version, sha[:7]))
        return
    # The Latest badge goes to the highest version of the whole branch, so
    # backfilling an old version never takes it from a newer one.
    highest = max((v for v, _ in versions()), key=version_key)
    for version, sha in todo:
        tag = "v%s" % version
        git("tag", "-a", tag, "-m", "issue-pilot %s" % version, sha)
        git("push", "origin", "refs/tags/%s" % tag)
        fd, path = tempfile.mkstemp(suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(bodies[version])
            out = run("gh", "release", "create", tag, "--verify-tag",
                      "--title", "issue-pilot %s" % version, "--notes-file", path,
                      "--latest=%s" % ("true" if version == highest else "false"),
                      check=False)
        finally:
            os.unlink(path)
        if out.returncode != 0:
            sys.exit("gh release create %s failed: %s\nThe tag %s is already pushed, "
                     "so running this again skips it: create the release by hand with "
                     "`gh release create %s --verify-tag ...`."
                     % (tag, (out.stderr or out.stdout).strip(), tag, tag))
        print("released %s at %s" % (tag, sha[:7]))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("notes", help="print the changelog section of a version")
    p.add_argument("version")
    p.set_defaults(func=cmd_notes)
    p = sub.add_parser("versions", help="list every version and its first commit")
    p.set_defaults(func=cmd_versions)
    p = sub.add_parser("pending", help="list the versions that have no tag")
    p.add_argument("--all", action="store_true",
                   help="every missing version, not only the one of HEAD")
    p.set_defaults(func=cmd_pending)
    p = sub.add_parser("publish", help="tag and release the versions that have no tag")
    p.add_argument("--all", action="store_true",
                   help="every missing version, not only the one of HEAD")
    p.add_argument("--dry-run", action="store_true",
                   help="say what would be released and create nothing")
    p.set_defaults(func=cmd_publish)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
