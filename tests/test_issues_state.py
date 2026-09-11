"""The run state: the only thing that survives the `/clear` between issues.

Every guarantee the driver relies on is here -- that `next` hands out issues in
order, that a blocked run stops, that `--resume` can reopen it -- so these tests
are really tests of the driver's control flow.
"""
import json
import unittest

from support import PilotTestCase, issue


class RunState(PilotTestCase):
    def setUp(self):
        super().setUp()
        self.write_issues({1: issue(1, "first"),
                           2: issue(2, "second", body="builds on #1"),
                           3: issue(3, "third")})

    def init(self, *numbers, **kwargs):
        args = [str(n) for n in (numbers or (1, 2, 3))]
        args += ["--branch", kwargs.get("branch", "issues-1-2-3")]
        if "notes" in kwargs:
            args += ["--notes", kwargs["notes"]]
        return self.run_script("issues_state.py", "init", *args)

    def next_issue(self):
        out = self.run_script("issues_state.py", "next", check=False)
        return out, (json.loads(out.stdout) if out.stdout.strip() else {})

    def state(self):
        return json.loads(self.run_script("issues_state.py", "show").stdout)

    def test_init_records_plan_status_and_notes(self):
        self.init(notes="answers from phase 1")
        state = self.state()
        self.assertEqual(state["branch"], "issues-1-2-3")
        self.assertEqual(state["notes"], "answers from phase 1")
        self.assertEqual(state["status"], {"1": "pending", "2": "pending",
                                           "3": "pending"})
        self.assertEqual([p["issue"] for p in state["plan"]], [1, 2, 3])

    def test_without_a_run_every_command_says_so(self):
        out = self.run_script("issues_state.py", "show", check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("no run in progress", out.stderr)

    def test_next_hands_out_pending_issues_in_order(self):
        self.init()
        _, first = self.next_issue()
        self.assertEqual(first["issue"], 1)
        self.assertTrue(first["clear_before"])
        self.assertEqual(first["remaining"], [1, 2, 3])

        self.run_script("issues_state.py", "done", "1", "--commit", "abc123")
        _, second = self.next_issue()
        self.assertEqual(second["issue"], 2)
        # #2 names #1, so it must keep the conversation.
        self.assertFalse(second["clear_before"])
        self.assertEqual(second["depends_on"], [1])

    def test_next_exits_3_when_the_run_is_finished(self):
        self.init(1)
        self.run_script("issues_state.py", "done", "1")
        out, payload = self.next_issue()
        self.assertEqual(out.returncode, 3)
        self.assertTrue(payload["finished"])
        self.assertIsNone(payload["blocked"])

    def test_done_records_the_commit(self):
        self.init()
        self.run_script("issues_state.py", "done", "1", "--commit", "deadbeef")
        self.assertEqual(self.state()["commits"]["1"], "deadbeef")

    def test_a_blocked_run_stops_even_with_issues_left(self):
        self.init()
        self.run_script("issues_state.py", "block", "1", "--reason", "tests are red")
        out, payload = self.next_issue()
        self.assertEqual(out.returncode, 3)
        self.assertEqual(payload["blocked"]["issue"], 1)
        self.assertEqual(payload["blocked"]["reason"], "tests are red")

    def test_unblock_hands_the_issue_out_again(self):
        self.init()
        self.run_script("issues_state.py", "block", "2", "--reason", "x")
        self.run_script("issues_state.py", "unblock")
        _, payload = self.next_issue()
        self.assertEqual(payload["issue"], 1)
        self.assertEqual(self.state()["status"]["2"], "pending")
        self.assertIsNone(self.state()["blocked"])

    def test_unblock_without_a_blocked_issue_is_not_an_error(self):
        self.init()
        out = self.run_script("issues_state.py", "unblock")
        self.assertIn("nothing was blocked", out.stdout)

    def test_finishing_a_blocked_issue_clears_the_flag(self):
        # --resume reopens the issue and a later attempt finishes it; leaving
        # the flag behind would stop a run that is no longer stuck.
        self.init()
        self.run_script("issues_state.py", "block", "1", "--reason", "x")
        self.run_script("issues_state.py", "unblock")
        self.run_script("issues_state.py", "done", "1")
        self.assertIsNone(self.state()["blocked"])
        _, payload = self.next_issue()
        self.assertEqual(payload["issue"], 2)

    def test_the_session_is_recorded(self):
        self.init()
        self.assertIsNone(self.state()["session"])
        self.run_script("issues_state.py", "session", "0f0f0f0f-0000-4000-8000-000000000001")
        self.assertEqual(self.state()["session"], "0f0f0f0f-0000-4000-8000-000000000001")

    def test_attempts_are_counted(self):
        self.init()
        self.run_script("issues_state.py", "attempt", "1")
        self.run_script("issues_state.py", "attempt", "1")
        self.assertEqual(self.state()["attempts"]["1"], 2)

    def test_state_is_per_repository(self):
        self.init()
        path = self.run_script("issues_state.py", "path").stdout.strip()
        self.assertIn(str(self.repo).strip("/").replace("/", "-"), path)
        self.assertTrue(path.startswith(str(self.home)))

    def test_status_reads_the_run_out_loud(self):
        self.init(notes="do not touch the parser")
        self.run_script("issues_state.py", "done", "1", "--commit", "c0ffee1234")
        self.run_script("issues_state.py", "block", "2", "--reason", "needs a decision")
        out = self.run_script("issues_state.py", "status").stdout
        self.assertIn("issues-1-2-3", out)
        self.assertIn("c0ffee123", out)
        self.assertIn("STOPPED on #2", out)
        self.assertIn("needs a decision", out)
        self.assertIn("do not touch the parser", out)

    def test_a_pull_request_into_the_default_branch_needs_no_follow_up(self):
        self.init(1)
        self.run_script("issues_state.py", "done", "1")
        self.run_script("issues_state.py", "pr", "https://gh/pr/7",
                        "--number", "7", "--base", "main",
                        "--closes-automatically")
        out = self.run_script("issues_state.py", "status").stdout
        self.assertIn("https://gh/pr/7 -> main", out)
        self.assertNotIn("issues-close", out)

    def test_a_pull_request_elsewhere_says_the_issues_must_be_closed(self):
        # GitHub only honours "Closes #n" on a merge into the default branch.
        self.init(1)
        self.run_script("issues_state.py", "done", "1")
        self.run_script("issues_state.py", "pr", "https://gh/pr/7",
                        "--number", "7", "--base", "develop")
        out = self.run_script("issues_state.py", "status").stdout
        self.assertIn("will NOT close the issues", out)
        self.assertIn("issues-close", out)

    def test_status_points_at_the_pull_request_when_everything_is_done(self):
        self.init(1)
        self.run_script("issues_state.py", "done", "1")
        self.assertIn("issues-pr", self.run_script("issues_state.py", "status").stdout)


if __name__ == "__main__":
    unittest.main()
