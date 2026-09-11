"""The driver's control flow, exercised end to end against a fake `claude`.

What matters here is not that code gets written -- the fake writes a file -- but
that the driver makes the right decisions around it: when a conversation is
cleared, when a dead session is continued, when a run stops for good, and what it
refuses to start.
"""
import json
import time
import unittest

from support import PilotTestCase, issue


class Run(PilotTestCase):
    def setUp(self):
        super().setUp()
        self.write_issues({1: issue(1, "first"),
                           2: issue(2, "second", body="builds on #1"),
                           3: issue(3, "third")})
        self.write_config({"test_command": "true"})

    def plan(self, *actions):
        return {"FAKE_CLAUDE_PLAN": json.dumps(list(actions))}

    def state(self):
        return json.loads(self.run_script("issues_state.py", "show").stdout)

    def test_a_run_implements_every_issue_and_stops_at_the_pull_request(self):
        out = self.run_driver("--no-interview", "1", "2", check=True)
        self.assertEqual(self.state()["status"], {"1": "done", "2": "done"})
        self.assertIn("issues-pr", out.stdout)
        # One pull request at the end means the driver must not open one itself.
        self.assertNotIn("pr create", out.stdout)

    def test_the_branch_is_named_after_the_issues_and_cut_from_the_base(self):
        self.run_driver("--no-interview", "1", "2", check=True)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"),
                         "issues-1-2")
        self.assertEqual(self.state()["base_branch"], "main")

    @staticmethod
    def session_of(call):
        """(flag, id) for how a call addressed its conversation."""
        for flag in ("--session-id", "--resume"):
            if flag in call:
                return flag, call[call.index(flag) + 1]
        return None, None

    def test_an_independent_issue_starts_a_fresh_conversation(self):
        self.run_driver("--no-interview", "1", "2", check=True)
        first, second = self.claude_calls()
        # #1 has no history worth keeping; #2 names #1, so it must keep it --
        # by id, never as "the most recent conversation in this directory".
        self.assertEqual(self.session_of(first)[0], "--session-id")
        self.assertEqual(self.session_of(second)[0], "--resume")
        self.assertEqual(self.session_of(first)[1], self.session_of(second)[1])
        for call in (first, second):
            self.assertNotIn("--continue", call)

    def test_unrelated_issues_each_start_clean(self):
        self.run_driver("--no-interview", "1", "3", check=True)
        first, second = self.claude_calls()
        self.assertEqual(self.session_of(first)[0], "--session-id")
        self.assertEqual(self.session_of(second)[0], "--session-id")
        self.assertNotEqual(self.session_of(first)[1], self.session_of(second)[1])

    def test_the_current_conversation_is_kept_in_the_state(self):
        self.run_driver("--no-interview", "1", check=True)
        self.assertEqual(self.state()["session"],
                         self.session_of(self.claude_calls()[0])[1])

    def test_every_session_runs_on_the_configured_model(self):
        # Not on whatever model the caller happens to be using.
        self.write_config({"test_command": "true", "model": "opus", "effort": "low"})
        self.run_driver("--no-interview", "1", check=True)
        call = self.claude_calls()[0]
        self.assertEqual(call[call.index("--model") + 1], "opus")
        self.assertEqual(call[call.index("--effort") + 1], "low")

    def test_the_autonomy_rules_reach_every_session(self):
        self.run_driver("--no-interview", "1", check=True)
        call = self.claude_calls()[0]
        rules = call[call.index("--append-system-prompt") + 1]
        self.assertIn("if you end your turn, the process ends", rules.lower())

    def test_an_unconfigured_repository_stops_the_run(self):
        # The one thing a run cannot work out for itself is what green means.
        (self.repo / ".issue-pilot.json").unlink()
        out = self.run_driver("--no-interview", "1")
        self.assertEqual(out.returncode, 78)
        self.assertIn("not configured", out.stderr)
        self.assertIn("/issue-pilot:init", out.stderr)
        self.assertEqual(self.claude_calls(), [])

    def test_a_dirty_tree_stops_the_run_before_anything_happens(self):
        (self.repo / "README.md").write_text("modified\n")
        out = self.run_driver("--no-interview", "1")
        self.assertEqual(out.returncode, 2)
        self.assertIn("not clean", out.stderr)
        self.assertIn("README.md", out.stderr)
        self.assertEqual(self.claude_calls(), [])

    def test_untracked_files_count_as_dirty_and_the_way_out_is_named(self):
        # The run's commits must contain only the run's work; a stray notebook
        # swept into "implement issue #1" is worse than the stash it costs.
        (self.repo / "scratch.ipynb").write_text("{}\n")
        out = self.run_driver("--no-interview", "1")
        self.assertEqual(out.returncode, 2)
        self.assertIn("scratch.ipynb", out.stderr)
        self.assertIn("git stash -u", out.stderr)
        self.assertEqual(self.claude_calls(), [])

    def test_sync_refuses_to_discard_unpushed_commits(self):
        # setUp committed the configuration locally and never pushed it, so
        # main is ahead of origin/main: exactly what --sync must not erase.
        out = self.run_driver("--sync", "--no-interview", "1")
        self.assertEqual(out.returncode, 2)
        self.assertIn("ahead of origin/main", out.stderr)
        self.assertIn("configure issue-pilot", out.stderr)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(self.claude_calls(), [])

    def test_sync_brings_a_stale_base_up_to_date(self):
        self._git("push", "-q", "origin", "main")
        (self.repo / "upstream.txt").write_text("landed on origin\n")
        self._git("add", "upstream.txt")
        self._git("commit", "-qm", "upstream change")
        self._git("push", "-q", "origin", "main")
        self._git("reset", "-q", "--hard", "HEAD~1")        # now behind by one
        self.assertNotEqual(self.git("rev-parse", "main"),
                            self.git("rev-parse", "origin/main"))
        self.run_driver("--sync", "--no-interview", "1", check=True)
        # The run branch was cut from origin's main, not the stale local one.
        self.assertEqual(self.git("merge-base", "issues-1", "origin/main"),
                         self.git("rev-parse", "origin/main"))
        self.assertEqual(self.state()["status"]["1"], "done")

    def test_plan_only_shows_the_plan_and_touches_nothing(self):
        out = self.run_driver("--plan-only", "1", "2", "3", check=True)
        self.assertIn("#1", out.stdout)
        self.assertIn("fresh conversation", out.stdout)
        self.assertIn("continues the previous one", out.stdout)
        self.assertIn("depends on #1", out.stdout)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(self.git("branch", "--list", "issues-1-2-3"), "")
        self.assertFalse(list(self.home.glob("state/issues-run-*.json")))
        self.assertEqual(self.claude_calls(), [])

    def test_the_answers_gathered_beforehand_reach_the_run(self):
        notes = self.repo.parent / "notes.txt"
        notes.write_text("do not touch the parser\n")
        self.run_driver("--notes-file", str(notes), "1", check=True)
        self.assertIn("do not touch the parser", self.state()["notes"])

    def test_an_empty_notes_file_is_refused(self):
        notes = self.repo.parent / "empty.txt"
        notes.write_text("")
        out = self.run_driver("--notes-file", str(notes), "1")
        self.assertEqual(out.returncode, 2)
        self.assertIn("missing or empty", out.stderr)

    def test_a_session_that_died_mid_task_is_continued(self):
        # "touch" commits work but never marks the issue done -- the shape of a
        # session that ended its turn while work was in flight.
        out = self.run_driver("--no-interview", "1", env=self.plan("touch", "done"),
                              check=True)
        self.assertEqual(self.state()["status"]["1"], "done")
        self.assertEqual(self.state()["attempts"]["1"], 2)
        first, second = self.claude_calls()
        self.assertEqual(self.session_of(second),
                         ("--resume", self.session_of(first)[1]))
        self.assertIn("attempt 2/3", out.stdout)

    def test_a_declared_blocker_stops_the_run_without_retrying(self):
        out = self.run_driver("--no-interview", "1", "3", env=self.plan("block"))
        self.assertEqual(out.returncode, 1)
        self.assertEqual(self.state()["blocked"]["issue"], 1)
        # One attempt on #1, and #3 never started.
        self.assertEqual(len(self.claude_calls()), 1)
        self.assertEqual(self.state()["status"]["3"], "pending")

    def test_two_attempts_that_change_nothing_give_up_early(self):
        # Sessions dying before they can do anything will not be fixed by more
        # of them, so the driver stops without using its third attempt.
        out = self.run_driver("--no-interview", "1",
                              env=self.plan("nothing", "nothing", "nothing"))
        self.assertEqual(out.returncode, 1)
        self.assertEqual(len(self.claude_calls()), 2)
        self.assertIn("without changing anything",
                      self.state()["blocked"]["reason"])

    def test_a_halted_usage_guard_stops_the_run_between_issues(self):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "usage-guard.halt").write_text("7d window at 95%\n")
        out = self.run_driver("--no-interview", "1")
        self.assertEqual(out.returncode, 1)
        self.assertIn("usage guard halted", out.stderr)
        self.assertEqual(self.claude_calls(), [])

    def test_every_attempt_is_logged(self):
        self.run_driver("--no-interview", "1", env=self.plan("touch", "done"),
                        check=True)
        logs = sorted((self.home / "logs" / "issues-1").glob("issue-1-attempt-*.log"))
        self.assertEqual([p.name for p in logs],
                         ["issue-1-attempt-1.log", "issue-1-attempt-2.log"])
        self.assertIn("claude exited with status", logs[0].read_text())

    def test_detaching_without_answers_is_refused(self):
        # A detached run has nobody to interview, so it must be told up front.
        out = self.run_driver("--detach", "1")
        self.assertEqual(out.returncode, 2)
        self.assertIn("nobody to ask", out.stderr)
        self.assertEqual(self.claude_calls(), [])

    def test_a_detached_run_returns_at_once_and_keeps_going(self):
        started = time.time()
        out = self.run_driver("--detach", "--no-interview", "1", "2", check=True)
        self.assertLess(time.time() - started, 20)
        self.assertIn("background", out.stdout)

        logdir = self.home / "logs" / "issues-1-2"
        self.assertTrue((logdir / "driver.pid").exists())

        deadline = time.time() + 60
        while time.time() < deadline:
            status = json.loads(
                self.run_script("issues_state.py", "show", check=False).stdout
                or "{}").get("status", {})
            if status and all(v == "done" for v in status.values()):
                break
            time.sleep(0.5)
        else:
            self.fail("the detached run never finished:\n" +
                      (logdir / "driver.log").read_text())

        self.assertIn("issues-pr", (logdir / "driver.log").read_text())


if __name__ == "__main__":
    unittest.main()
