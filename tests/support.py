"""Shared scaffolding for the tests: a throwaway git repository and fake tools.

issue-pilot's scripts only do anything inside a git repository, next to a GitHub
CLI, and -- for the driver -- next to `claude` itself.  None of that is mocked at
the Python level on purpose: the scripts shell out, and stubbing `subprocess`
would test the stub instead of the command lines that actually ship.  Instead,
`tests/stubs/` holds small real programs that are put on `PATH` ahead of the
real ones.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
STUBS = HERE / "stubs"


class PilotTestCase(unittest.TestCase):
    """A temporary repository, a private ISSUE_PILOT_HOME, and fake tools."""

    issues = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

        self.repo = root / "repo"
        self.repo.mkdir()
        self._git("init", "-q")
        self._git("config", "user.email", "pilot@example.com")
        self._git("config", "user.name", "issue-pilot tests")
        (self.repo / "README.md").write_text("test repo\n")
        self._git("add", "README.md")
        self._git("commit", "-qm", "initial")
        # Pin the branch name, so the fake `gh` can agree about the base branch
        # however old the git on this machine is.
        self._git("branch", "-m", "main")

        self.bindir = root / "bin"
        self.bindir.mkdir()
        self.install_stub("gh")
        self.install_stub("claude")

        self.issues_file = root / "issues.json"
        self.write_issues(self.issues)

        self.home = root / "pilot-home"
        self.claude_log = root / "claude-invocations.jsonl"
        self.env = dict(os.environ)
        self.env.update({
            "PATH": f"{self.bindir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_ISSUES": str(self.issues_file),
            "FAKE_GH_DEFAULT_BRANCH": "main",
            "FAKE_CLAUDE_LOG": str(self.claude_log),
            "FAKE_CLAUDE_COUNTER": str(root / "claude-counter"),
            "FAKE_CLAUDE_STATE": str(SCRIPTS / "issues_state.py"),
            "CLAUDE_CODE_OAUTH_TOKEN": "test-token",
            "ISSUE_PILOT_HOME": str(self.home),
        })
        # A stray ISSUE_PILOT_* in the developer's own shell would quietly
        # override what the tests are asserting about precedence.
        for key in list(self.env):
            if key.startswith("ISSUE_PILOT_") and key != "ISSUE_PILOT_HOME":
                del self.env[key]

    def install_stub(self, name):
        target = self.bindir / name
        shutil.copy(STUBS / name, target)
        target.chmod(0o755)
        return target

    def write_issues(self, issues):
        self.issues_file.write_text(json.dumps(
            {str(n): payload for n, payload in issues.items()}))

    def write_config(self, config):
        """Write the project's configuration, and commit it as a project would.

        Leaving it uncommitted would make the tree dirty, which the driver
        rightly refuses to start on.
        """
        (self.repo / ".issue-pilot.json").write_text(json.dumps(config))
        self._git("add", ".issue-pilot.json")
        self._git("commit", "-qm", "configure issue-pilot")

    def _git(self, *args):
        subprocess.run(("git",) + args, cwd=self.repo, check=True,
                       capture_output=True)

    def git(self, *args):
        return subprocess.run(("git",) + args, cwd=self.repo, text=True,
                              capture_output=True).stdout.strip()

    def run_script(self, script, *args, check=True, env=None):
        """Run one of the scripts inside the temporary repository."""
        out = subprocess.run(
            [sys.executable, str(SCRIPTS / script), *args],
            cwd=self.repo, capture_output=True, text=True,
            env={**self.env, **(env or {})})
        if check and out.returncode != 0:
            self.fail(f"{script} {' '.join(args)} failed "
                      f"({out.returncode}):\n{out.stderr}")
        return out

    def run_driver(self, *args, check=False, env=None, timeout=120):
        out = subprocess.run(
            ["bash", str(SCRIPTS / "issues_run.sh"), *args],
            cwd=self.repo, capture_output=True, text=True, timeout=timeout,
            env={**self.env, **(env or {})})
        if check and out.returncode != 0:
            self.fail(f"issues_run.sh {' '.join(args)} failed "
                      f"({out.returncode}):\n{out.stdout}\n{out.stderr}")
        return out

    def claude_calls(self):
        """Every `claude` command line the driver ran, in order."""
        if not self.claude_log.exists():
            return []
        return [json.loads(line) for line in
                self.claude_log.read_text().splitlines() if line.strip()]


def issue(number, title="t", body="", comments=()):
    return {"number": number, "title": title, "body": body,
            "comments": [{"body": c} for c in comments]}
