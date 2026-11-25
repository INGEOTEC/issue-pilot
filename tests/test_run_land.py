"""Landing a finished run without a GitHub pull request: merge, push, branch.

`run_land.sh` pushes to a base branch, so it has to behave the same every time
and is tested against a real bare `origin`, with the failures that matter made
on purpose: the base moves during the run, a hook refuses the push, a conflict,
no remote at all.  A finished run is made the way the driver tests make one: the
fake `claude` commits `item-1.txt` and marks the item done.
"""
import json
import pathlib
import stat
import subprocess
import unittest

from support import SCRIPTS, PilotTestCase, issue

LAND = SCRIPTS / "run_land.sh"


class RunLand(PilotTestCase):
    def setUp(self):
        super().setUp()
        self.write_issues({1: issue(1, "first")})

    # -- scaffolding ---------------------------------------------------------

    def start(self, remote=True, versioned=True, **config):
        """Configure the project, create local issue 1 if asked, and finish a run."""
        cfg = {"test_command": "true", "base_branch": "main", **config}
        if cfg.get("tracker") == "local":
            issues = self.repo / "issues"
            issues.mkdir(exist_ok=True)
            (issues / "0001-first.md").write_text("# First\n\nDo the first thing.\n")
            if versioned:
                self._git("add", "issues")
                self._git("commit", "-qm", "add issue 1")
        self.write_config(cfg)
        self._git("push", "-q", "origin", "main")
        if not remote:
            self._git("remote", "remove", "origin")
        self.run_driver("--no-interview", "1", check=True)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "issues-1")

    def land(self, *args, check=False):
        out = subprocess.run(["bash", str(LAND), *args], cwd=self.repo,
                             capture_output=True, text=True, env=self.env)
        if check and out.returncode != 0:
            self.fail("run_land.sh failed (%d):\n%s" % (out.returncode, out.stderr))
        return out

    def state(self):
        return json.loads(self.run_script("issues_state.py", "show").stdout)

    def origin_head(self, branch="main"):
        return self.git("-C", str(self.origin), "rev-parse", "refs/heads/" + branch)

    def origin_branches(self):
        return self.git("-C", str(self.origin), "for-each-ref", "--format=%(refname:short)",
                        "refs/heads").split()

    def commit_on_origin(self, name, body="from somebody else\n"):
        other = pathlib.Path(self.tmp.name) / ("other-" + name)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)],
                       check=True, capture_output=True)
        (other / name).write_text(body)
        for cmd in (["git", "-c", "user.email=o@x", "-c", "user.name=o", "add", name],
                    ["git", "-c", "user.email=o@x", "-c", "user.name=o", "commit", "-qm", name],
                    ["git", "push", "-q", "origin", "HEAD:main"]):
            subprocess.run(cmd, cwd=other, check=True, capture_output=True)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=other, check=True,
                              capture_output=True, text=True).stdout.strip()

    def refuse_pushes(self):
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho refused by policy >&2\nexit 1\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        return hook

    def issue_text(self):
        return (self.repo / "issues" / "0001-first.md").read_text()

    # -- merge ---------------------------------------------------------------

    def test_merge_brings_the_moved_base_in_and_pushes_the_run_as_the_base(self):
        self.start(landing="merge")
        theirs = self.commit_on_origin("theirs.txt")
        out = self.land()
        self.assertEqual(out.returncode, 0, out.stderr)
        tip = self.git("rev-parse", "issues-1")
        # The base was merged into the run branch, so the run branch has it...
        self.assertEqual(subprocess.run(["git", "merge-base", "--is-ancestor", theirs, tip],
                                        cwd=self.repo).returncode, 0)
        # ...the remote's base is the run branch's tip, the local base is equal,
        # and the repository is on it.
        self.assertEqual(self.origin_head(), tip)
        self.assertEqual(self.git("rev-parse", "main"), tip)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        # The run branch itself was never pushed.
        self.assertEqual(self.origin_branches(), ["main"])
        self.assertTrue((self.repo / "theirs.txt").exists())
        self.assertTrue((self.repo / "item-1.txt").exists())
        self.assertRegex(out.stdout.strip(), r"^merged into main at [0-9a-f]+ and pushed to origin$")

    def test_a_merge_with_an_unmoved_base_makes_no_merge_commit(self):
        self.start(landing="merge")
        before = self.git("rev-parse", "issues-1")
        self.land(check=True)
        self.assertEqual(self.git("rev-parse", "issues-1"), before)
        self.assertEqual(self.origin_head(), before)

    def test_a_refused_push_is_exit_1_and_retrying_after_it_is_lifted_lands(self):
        self.start(landing="merge", tracker="local")
        local_base = self.git("rev-parse", "main")
        hook = self.refuse_pushes()
        out = self.land()
        self.assertEqual(out.returncode, 1)
        self.assertIn("refused by policy", out.stderr)
        self.assertIn("running this again retries", out.stderr)
        self.assertEqual(self.git("rev-parse", "main"), local_base)
        self.assertNotIn("pull_request", self.state())
        self.assertEqual(self.origin_head(), local_base)
        hook.unlink()
        self.land(check=True)
        self.assertEqual(self.origin_head(), self.git("rev-parse", "issues-1"))
        self.assertIn("pull_request", self.state())
        # The issue was commented once, not once per attempt.
        self.assertEqual(self.git("show", "main:issues/0001-first.md").count("issue-pilot:comment"), 1)

    def test_a_conflict_is_exit_2_with_nothing_merged_pushed_or_recorded(self):
        self.start(landing="merge")
        theirs = self.commit_on_origin("item-1.txt", "somebody else wrote this file\n")
        before = self.git("rev-parse", "issues-1")
        out = self.land()
        self.assertEqual(out.returncode, 2)
        self.assertIn("git merge origin/main", out.stderr)
        self.assertFalse((self.repo / ".git" / "MERGE_HEAD").exists())
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("rev-parse", "issues-1"), before)
        self.assertEqual(self.origin_head(), theirs)
        self.assertNotIn("pull_request", self.state())

    def test_merge_with_no_remote_fast_forwards_the_local_base(self):
        self.start(landing="merge", tracker="local", remote=False)
        out = self.land()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.git("rev-parse", "main"), self.git("rev-parse", "issues-1"))
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertRegex(out.stdout.strip(), r"^merged into main at [0-9a-f]+$")

    def test_a_local_base_ahead_of_origin_is_refused(self):
        self.start(landing="merge")
        self._git("checkout", "-q", "main")
        (self.repo / "unpushed.txt").write_text("mine\n")
        self._git("add", "unpushed.txt")
        self._git("commit", "-qm", "unpushed")
        self._git("checkout", "-q", "issues-1")
        out = self.land()
        self.assertEqual(out.returncode, 2)
        self.assertIn("local main has 1 commit(s) origin/main does not", out.stderr)
        self.assertEqual(self.origin_branches(), ["main"])

    # -- tests ---------------------------------------------------------------

    def test_failing_tests_land_nothing(self):
        self.start(landing="merge")
        self.write_config({"test_command": "false", "base_branch": "main", "landing": "merge"})
        before = self.origin_head()
        out = self.land()
        self.assertEqual(out.returncode, 2)
        self.assertIn("the tests fail on issues-1; nothing was landed", out.stderr)
        self.assertEqual(self.origin_head(), before)
        self.assertNotIn("pull_request", self.state())

    # -- push and branch -----------------------------------------------------

    def test_push_publishes_the_run_branch_and_leaves_the_base_alone(self):
        self.start(landing="push")
        base = self.origin_head()
        out = self.land()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.origin_head("issues-1"), self.git("rev-parse", "issues-1"))
        self.assertEqual(self.origin_head(), base)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(out.stdout.strip(), "branch issues-1 pushed to origin; merge it into main on your server")

    def test_push_without_a_remote_is_refused(self):
        self.start(landing="push", tracker="local", remote=False)
        out = self.land()
        self.assertEqual(out.returncode, 2)
        self.assertIn('landing "push" needs a remote named origin; use "merge" or "branch"', out.stderr)
        self.assertNotIn("pull_request", self.state())

    def test_branch_pushes_and_merges_nothing(self):
        self.start(landing="branch")
        base = self.origin_head()
        out = self.land()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.origin_branches(), ["main"])
        self.assertEqual(self.origin_head(), base)
        self.assertEqual(self.git("rev-parse", "main"), base)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(out.stdout.strip(), "branch issues-1 is ready; merge it into main by hand")

    # -- the issues ----------------------------------------------------------

    def test_versioned_local_issues_are_closed_in_one_commit_that_lands_with_the_run(self):
        self.start(landing="merge", tracker="local")
        item_commit = self.state()["commits"]["1"]
        self.land(check=True)
        text = self.git("show", "main:issues/0001-first.md")
        self.assertIn("state: closed", text)
        self.assertIn("Implemented on branch issues-1, commit %s. Landing: merge into main." % item_commit, text)
        closing = [l for l in self.git("log", "main", "--format=%s").splitlines() if l.startswith("Close issue")]
        self.assertEqual(closing, ["Close issue(s) #1 (landed by issue-pilot)"])
        self.assertEqual(self.git("show", "--name-only", "--format=", "HEAD"), "issues/0001-first.md")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_ignored_local_issues_are_closed_without_a_commit(self):
        with open(self.repo / ".git" / "info" / "exclude", "a") as out:
            out.write("issues/\n")
        self.start(landing="branch", tracker="local", versioned=False)
        self.land(check=True)
        self.assertIn("state: closed", self.issue_text())
        self.assertEqual(self.git("rev-list", "--count", "main..issues-1"), "1")

    def test_github_issues_are_commented_and_closed_after_a_merge(self):
        self.start(landing="merge")
        self.land(check=True)
        log = [json.loads(l) for l in pathlib.Path(self.env["FAKE_GH_ISSUE_LOG"]).read_text().splitlines()]
        self.assertEqual([c["cmd"] for c in log], ["comment", "close"])
        self.assertEqual(log[0]["args"][0], "1")
        self.assertRegex(log[0]["body"], r"^Implemented in commit [0-9a-f]{40}, merged into main\.")
        self.assertTrue(self.state()["pull_request"]["closes_automatically"])

    def test_github_issues_are_left_open_for_push_and_branch(self):
        for landing in ("push", "branch"):
            with self.subTest(landing=landing):
                self.setUp()
                self.start(landing=landing)
                self.land(check=True)
                log = pathlib.Path(self.env["FAKE_GH_ISSUE_LOG"])
                self.assertFalse(log.exists() and log.read_text())
                self.assertFalse(self.state()["pull_request"]["closes_automatically"])

    def test_the_pull_request_flow_closes_local_issues_on_the_branch(self):
        self.start(landing="pull_request", tracker="local")
        self.assertEqual(self.land().returncode, 2)
        out = self.land("--close-issues-only")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, "")
        self.assertIn("state: closed", self.issue_text())
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "issues-1")
        self.assertIn("Close issue(s) #1", self.git("log", "-1", "--format=%s"))
        self.assertNotIn("pull_request", self.state())

    def test_closing_only_does_nothing_for_github_issues(self):
        self.start(landing="pull_request")
        head = self.git("rev-parse", "HEAD")
        out = self.land("--close-issues-only")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        log = pathlib.Path(self.env["FAKE_GH_ISSUE_LOG"])
        self.assertFalse(log.exists() and log.read_text())

    # -- the record ----------------------------------------------------------

    def test_the_record_has_kind_head_and_base_and_status_shows_a_landed_line(self):
        self.start(landing="push")
        self.land(check=True)
        record = self.state()["pull_request"]
        self.assertEqual((record["kind"], record["base"]), ("push", "main"))
        self.assertEqual(record["head"], self.git("rev-parse", "issues-1"))
        self.assertEqual(record["url"], "branch issues-1 pushed to origin; merge it into main on your server")
        status = self.run_script("issues_state.py", "status").stdout
        self.assertIn("landed     : branch issues-1 pushed to origin; merge it into main on your server -> main", status)
        self.assertIn("/issue-pilot:close", status)

    def test_fix_add_is_refused_after_a_landing(self):
        self.start(landing="branch")
        self.land(check=True)
        desc = self.repo.parent / "desc.txt"
        desc.write_text("fix it\n")
        out = self.run_script("issues_state.py", "fix-add", "--description-file", str(desc), check=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("refused", out.stderr)

    # -- refusals ------------------------------------------------------------

    def test_an_unfinished_run_is_refused(self):
        self.write_config({"test_command": "true", "base_branch": "main", "landing": "branch"})
        self.run_script("issues_state.py", "init", "1", "--branch", "issues-1")
        out = self.land()
        self.assertEqual(out.returncode, 2)
        self.assertIn("not every issue is done", out.stderr)

    def test_a_blocked_run_is_refused(self):
        self.write_config({"test_command": "true", "base_branch": "main", "landing": "branch"})
        self._git("push", "-q", "origin", "main")
        self.run_driver("--no-interview", "1", env={"FAKE_CLAUDE_PLAN": json.dumps(["block"])})
        out = self.land()
        self.assertEqual(out.returncode, 2)
        self.assertIn("blocked", out.stderr)

    def test_a_run_already_landed_is_refused(self):
        self.start(landing="branch")
        self.land(check=True)
        out = self.land()
        self.assertEqual(out.returncode, 2)
        self.assertIn("this run was already landed", out.stderr)

    def test_no_run_at_all_is_refused(self):
        self.write_config({"test_command": "true", "base_branch": "main", "landing": "branch"})
        out = self.land()
        self.assertEqual(out.returncode, 2)
        self.assertIn("no run", out.stderr)

    def test_a_dirty_tree_is_refused(self):
        self.start(landing="branch")
        (self.repo / "scratch.txt").write_text("not committed\n")
        out = self.land()
        self.assertEqual(out.returncode, 2)
        self.assertIn("working tree is not clean", out.stderr)
        self.assertNotIn("pull_request", self.state())

    def test_a_pull_request_landing_needs_the_pull_request_command(self):
        self.start(landing="pull_request")
        out = self.land()
        self.assertEqual(out.returncode, 2)
        self.assertIn("lands through a GitHub pull request; /issue-pilot:pr opens it", out.stderr)

    def test_it_lands_from_another_branch_by_checking_the_run_branch_out(self):
        self.start(landing="branch")
        self._git("checkout", "-q", "main")
        self.land(check=True)
        self.assertIn("pull_request", self.state())


if __name__ == "__main__":
    unittest.main()
