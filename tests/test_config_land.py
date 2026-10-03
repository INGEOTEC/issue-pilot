"""Landing .issue-pilot.json on the base branch through a pull request.

init has to get the configuration onto origin's base branch, because that is
where every other command starts from and where a local-only configuration is
refused.  The script does it with a branch and a pull request, so it also works
on a protected base; these tests run it against a bare origin and a fake `gh`.
"""
import json
import pathlib
import subprocess
import unittest

from support import SCRIPTS, PilotTestCase

HELPER = SCRIPTS / "config_land.sh"
BRANCH = "configure-issue-pilot"
URL = "https://github.com/example/repo/pull/1"


class ConfigLand(PilotTestCase):
    config = {"base_branch": "main", "test_command": "true"}

    def setUp(self):
        super().setUp()
        self._git("push", "-q", "origin", "main")
        self.write_file(self.config)

    def write_file(self, config):
        # Uncommitted, as init leaves it.
        (self.repo / ".issue-pilot.json").write_text(json.dumps(config))

    def land(self, *args, env=None, check=True):
        out = subprocess.run(["bash", str(HELPER), *args], cwd=self.repo,
                             capture_output=True, text=True,
                             env={**self.env, **(env or {})})
        if check and out.returncode != 0:
            self.fail(f"config_land.sh failed ({out.returncode}):\n{out.stderr}")
        return out

    def origin_git(self, *args):
        return subprocess.run(["git", "-C", str(self.origin), *args], text=True,
                              capture_output=True).stdout.strip()

    def pr_calls(self):
        log = pathlib.Path(self.env["FAKE_GH_PR_LOG"])
        return [json.loads(line) for line in log.read_text().splitlines()] \
            if log.exists() else []

    def land_on_origin(self, name):
        """Put a commit on origin/main that the local clone does not have."""
        other = pathlib.Path(self.tmp.name) / ("other-" + name)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)],
                       check=True, capture_output=True)
        (other / name).write_text("only on origin\n")
        for cmd in (["git", "-c", "user.email=o@x", "-c", "user.name=o", "add", name],
                    ["git", "-c", "user.email=o@x", "-c", "user.name=o", "commit", "-qm", name],
                    ["git", "push", "-q", "origin", "HEAD:main"]):
            subprocess.run(cmd, cwd=other, check=True, capture_output=True)

    def assert_refused(self, out, cause):
        """Exit 2, the cause named, and nothing committed, pushed or branched."""
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn(cause, out.stderr)
        self.assertEqual(out.stdout, "")
        self.assertEqual(self.git("branch", "--list", BRANCH), "")
        self.assertEqual(self.origin_git("branch", "--list", BRANCH), "")
        self.assertEqual(self.pr_calls(), [])

    # --- landing ---------------------------------------------------------

    def test_pushes_a_one_commit_branch_and_opens_the_pull_request(self):
        before = self.git("rev-parse", "main")
        out = self.land()
        self.assertEqual(out.stdout, URL + "\n")

        branch = self.origin_git("rev-parse", BRANCH)
        self.assertEqual(self.origin_git("rev-list", "--count", f"main..{BRANCH}"), "1")
        self.assertEqual(self.origin_git("rev-parse", f"{BRANCH}^"), before)
        self.assertEqual(
            self.origin_git("diff-tree", "--no-commit-id", "--name-only", "-r", branch),
            ".issue-pilot.json")
        self.assertEqual(self.origin_git("log", "-1", "--format=%s", BRANCH),
                         "Configure issue-pilot for this repository")

        (call,) = self.pr_calls()
        args = call["args"]
        self.assertEqual(args[args.index("--base") + 1], "main")
        self.assertEqual(args[args.index("--title") + 1],
                         "Configure issue-pilot for this repository")
        self.assertEqual(call["head"], BRANCH)
        self.assertNotIn("--draft", args)

        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("rev-parse", "main"), self.git("rev-parse", "origin/main"))
        self.assertEqual(self.git("rev-parse", "main"), before)

    def test_the_body_lists_the_settings_and_ends_with_the_attribution(self):
        self.write_file({**self.config, "model": "opus"})
        self.land()
        (call,) = self.pr_calls()
        body = call["body"]
        self.assertIn("`model`: `opus`", body)
        self.assertIn("`test_command`: `true`", body)
        self.assertIn("Nothing in issue-pilot runs until this file is on the base branch", body)
        self.assertTrue(body.rstrip().endswith(
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)"))

    def test_a_modified_tracked_file_is_landed_too(self):
        self.write_config(self.config)
        self._git("push", "-q", "origin", "main")
        self.write_file({**self.config, "model": "opus"})
        self.land()
        shown = self.origin_git("show", f"{BRANCH}:.issue-pilot.json")
        self.assertEqual(json.loads(shown)["model"], "opus")
        self.assertEqual(self.origin_git("rev-list", "--count", f"main..{BRANCH}"), "1")

    def test_draft_is_passed_exactly_when_pr_draft_is_true(self):
        self.write_file({**self.config, "pr_draft": True})
        self.land()
        self.assertIn("--draft", self.pr_calls()[0]["args"])

    def test_draft_is_not_passed_when_pr_draft_is_false(self):
        self.write_file({**self.config, "pr_draft": False})
        self.land()
        self.assertNotIn("--draft", self.pr_calls()[0]["args"])

    def test_without_merge_the_pull_request_is_left_open(self):
        out = self.land()
        self.assertIn("merge it", out.stderr)
        self.assertEqual(self.origin_git("branch", "--list", BRANCH).strip(), BRANCH)
        self.assertNotEqual(self.origin_git("rev-parse", "main"),
                            self.origin_git("rev-parse", BRANCH))

    # --- --merge ---------------------------------------------------------

    def test_merge_puts_the_file_on_the_base_and_cleans_up_the_branch(self):
        out = self.land("--merge")
        self.assertEqual(out.stdout, URL + "\n")
        self.assertIn(".issue-pilot.json", self.origin_git("ls-tree", "--name-only", "main"))
        self.assertEqual(self.git("rev-parse", "main"), self.git("rev-parse", "origin/main"))
        self.assertTrue((self.repo / ".issue-pilot.json").exists())
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("branch", "--list", BRANCH), "")
        self.assertEqual(self.git("branch", "-r", "--list", f"origin/{BRANCH}"), "")
        self.assertEqual(self.origin_git("branch", "--list", BRANCH), "")

    def test_a_refused_merge_still_leaves_the_pull_request_open(self):
        out = self.land("--merge", env={"FAKE_GH_MERGE_REFUSED": "1"})
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout, URL + "\n")
        self.assertIn("by hand", out.stderr)
        self.assertEqual(self.origin_git("branch", "--list", BRANCH).strip(), BRANCH)
        self.assertNotIn(".issue-pilot.json", self.origin_git("ls-tree", "--name-only", "main"))
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(self.git("status", "--porcelain"), "")

    # --- refusals --------------------------------------------------------

    def test_an_extra_file_is_refused(self):
        (self.repo / "scratch.txt").write_text("stray\n")
        before = self.git("log", "--oneline")
        out = self.land(check=False)
        self.assert_refused(out, "scratch.txt")
        self.assertIn("git stash -u", out.stderr)
        self.assertEqual(self.git("log", "--oneline"), before)

    def test_an_extra_change_to_a_tracked_file_is_refused(self):
        (self.repo / "README.md").write_text("changed\n")
        self.assert_refused(self.land(check=False), "README.md")

    def test_nothing_to_land_is_refused(self):
        (self.repo / ".issue-pilot.json").unlink()
        self.assert_refused(self.land(check=False), "nothing to land")

    def test_a_feature_branch_is_refused_and_base_sync_is_named(self):
        self._git("checkout", "-q", "-b", "feat/elsewhere")
        out = self.land(check=False)
        self.assert_refused(out, "feat/elsewhere")
        self.assertIn("base_sync.sh", out.stderr)

    def test_a_local_base_ahead_of_origin_is_refused(self):
        (self.repo / "local.txt").write_text("unpushed\n")
        self._git("add", "local.txt")
        self._git("commit", "-qm", "unpushed")
        before = self.git("rev-parse", "main")
        out = self.land(check=False)
        self.assert_refused(out, "1 commit(s) ahead")
        self.assertIn("base_sync.sh", out.stderr)
        self.assertEqual(self.git("rev-parse", "main"), before)

    def test_a_local_base_behind_origin_is_refused(self):
        self.land_on_origin("later.txt")
        before = self.origin_git("rev-parse", "main")
        out = self.land(check=False)
        self.assert_refused(out, "1 behind")
        self.assertIn("base_sync.sh", out.stderr)
        self.assertEqual(self.origin_git("rev-parse", "main"), before)

    def test_a_leftover_local_branch_is_refused(self):
        self._git("branch", BRANCH)
        out = self.land(check=False)
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("git branch -D", out.stderr)
        self.assertEqual(self.origin_git("branch", "--list", BRANCH), "")
        self.assertEqual(self.pr_calls(), [])
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")

    def test_a_leftover_branch_on_origin_is_refused(self):
        self.origin_git("branch", BRANCH, "main")
        out = self.land(check=False)
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("git push origin --delete", out.stderr)
        self.assertEqual(self.git("branch", "--list", BRANCH), "")
        self.assertEqual(self.pr_calls(), [])

    # --- failures --------------------------------------------------------

    def test_a_failed_push_puts_the_file_back_and_leaves_no_branch(self):
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'push rejected' >&2\nexit 1\n")
        hook.chmod(0o755)
        before = self.git("rev-parse", "main")
        out = self.land(check=False)
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("could not push", out.stderr)
        self.assertEqual(self.git("branch", "--list", BRANCH), "")
        self.assertEqual(self.git("rev-parse", "main"), before)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(self.git("status", "--porcelain"), "?? .issue-pilot.json")
        self.assertEqual(self.pr_calls(), [])

    def test_a_failed_fetch_is_exit_1(self):
        self._git("remote", "set-url", "origin", str(self.origin) + "-missing")
        out = self.land(check=False)
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("could not fetch", out.stderr)


if __name__ == "__main__":
    unittest.main()
