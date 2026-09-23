"""The run state: the only thing that survives the `/clear` between issues.

Every guarantee the driver relies on is here -- that `next` hands out issues in
order, that a blocked run stops, that `--resume` can reopen it -- so these tests
are really tests of the driver's control flow.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import unittest

from support import SCRIPTS, PilotTestCase, issue


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
        self.assertNotIn("/issue-pilot:close", out)

    def test_a_pull_request_elsewhere_says_the_issues_must_be_closed(self):
        # GitHub only honours "Closes #n" on a merge into the default branch.
        self.init(1)
        self.run_script("issues_state.py", "done", "1")
        self.run_script("issues_state.py", "pr", "https://gh/pr/7",
                        "--number", "7", "--base", "develop")
        out = self.run_script("issues_state.py", "status").stdout
        self.assertIn("will NOT close the issues", out)
        self.assertIn("/issue-pilot:close", out)

    def test_status_points_at_the_pull_request_when_everything_is_done(self):
        self.init(1)
        self.run_script("issues_state.py", "done", "1")
        self.assertIn("/issue-pilot:pr", self.run_script("issues_state.py", "status").stdout)

    def test_engine_records_the_model_and_status_reads_it_back(self):
        self.init()
        self.run_script("issues_state.py", "engine", "--model", "opus", "--effort", "low")
        state = self.state()
        self.assertEqual(state["model"], "opus")
        self.assertEqual(state["effort"], "low")
        out = self.run_script("issues_state.py", "status").stdout
        self.assertIn("model      : opus, effort low", out)

    def test_status_says_nothing_about_the_model_when_it_was_never_recorded(self):
        # A state file written before `engine` existed has no such field, and
        # must still print a full, correct status -- this is the regression
        # that matters, since status is the one command people run to find out
        # what a half-finished run is doing.
        self.init()
        state = self.state()
        self.assertNotIn("model", state)
        out = self.run_script("issues_state.py", "status")
        self.assertEqual(out.returncode, 0)
        self.assertNotIn("model", out.stdout)

    def test_status_names_the_running_version_and_where_it_runs_from(self):
        # Not hardcoded: this very commit changes the version, and a literal
        # would fail again at every future release.
        self.init()
        manifest = json.loads((SCRIPTS.parent / ".claude-plugin" / "plugin.json").read_text())
        out = self.run_script("issues_state.py", "status").stdout
        self.assertIn(f"issue-pilot: {manifest['version']}", out)
        self.assertIn(str(SCRIPTS.parent), out)

    def test_status_on_a_copy_with_no_manifest_reports_the_version_as_unknown(self):
        # The pre-change install.sh layout: scripts/ and hooks/, no
        # .claude-plugin/ beside them.  Status must still work, not merely
        # avoid a traceback.
        self.init()
        lib = pathlib.Path(self.tmp.name) / "lib"
        shutil.copytree(SCRIPTS, lib / "scripts")
        out = subprocess.run(
            [sys.executable, str(lib / "scripts" / "issues_state.py"), "status"],
            cwd=self.repo, capture_output=True, text=True, env=self.env)
        self.assertEqual(out.returncode, 0)
        self.assertIn("issue-pilot: unknown", out.stdout)
        self.assertIn(str(lib), out.stdout)


class FixItems(RunState):
    """Review fixes: a plan item of their own, applied by the same driver.

    Not a GitHub issue -- see #10 -- so they are added and driven entirely
    through this state, on a run whose issues are all already done.
    """

    def description_file(self, text="fix the thing\nmore detail\n"):
        path = pathlib.Path(self.tmp.name) / "fix.txt"
        path.write_text(text)
        return path

    def fix_add(self, *, issues=None, description="fix the thing", check=True):
        args = ["fix-add", "--description-file", str(self.description_file(description))]
        if issues:
            args += ["--issues"] + [str(n) for n in issues]
        return self.run_script("issues_state.py", *args, check=check)

    def finish_all(self):
        self.init()
        for n in (1, 2, 3):
            self.run_script("issues_state.py", "done", str(n))

    def test_fix_add_appends_a_pending_fix_with_its_dependencies(self):
        self.finish_all()
        out = self.fix_add(issues=[1, 3])
        self.assertEqual(out.stdout.strip(), "fix-1")
        state = self.state()
        item = state["plan"][-1]
        self.assertEqual(item["issue"], "fix-1")
        self.assertEqual(item["depends_on"], [1, 3])
        self.assertFalse(item["clear_before"])
        self.assertEqual(state["status"]["fix-1"], "pending")
        self.assertEqual(state["attempts"]["fix-1"], 0)

    def test_fix_add_with_no_issues_starts_a_fresh_conversation(self):
        self.finish_all()
        self.fix_add()
        item = self.state()["plan"][-1]
        self.assertEqual(item["depends_on"], [])
        self.assertTrue(item["clear_before"])

    def test_a_second_fix_is_fix_2_after_fix_1_in_the_plan(self):
        self.finish_all()
        self.fix_add()
        out = self.fix_add()
        self.assertEqual(out.stdout.strip(), "fix-2")
        self.assertEqual([p["issue"] for p in self.state()["plan"][-2:]],
                         ["fix-1", "fix-2"])

    def test_fix_add_is_refused_once_a_pull_request_is_recorded(self):
        self.finish_all()
        self.run_script("issues_state.py", "pr", "https://gh/pr/1",
                        "--number", "1", "--base", "main", "--closes-automatically")
        out = self.fix_add(check=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("pull request", out.stderr)

    def test_fix_add_is_refused_while_an_issue_is_still_pending(self):
        self.init()
        self.run_script("issues_state.py", "done", "1")
        out = self.fix_add(check=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("#2", out.stderr)
        self.assertIn("#3", out.stderr)

    def test_fix_add_is_refused_while_the_run_is_blocked(self):
        self.finish_all()
        self.fix_add()
        self.run_script("issues_state.py", "block", "fix-1", "--reason", "needs more thought")
        out = self.fix_add(check=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("blocked", out.stderr)

    def test_fix_add_is_refused_when_a_named_issue_is_not_in_the_run(self):
        self.finish_all()
        out = self.fix_add(issues=[99], check=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("#99", out.stderr)

    def test_fix_add_is_refused_when_the_description_is_empty(self):
        self.finish_all()
        out = self.fix_add(description="   \n", check=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("empty", out.stderr)

    def test_next_hands_out_a_pending_fix_with_its_spec(self):
        self.finish_all()
        self.fix_add(issues=[1])
        _, payload = self.next_issue()
        self.assertEqual(payload["issue"], "fix-1")
        self.assertEqual(payload["fix"]["issues"], [1])
        self.assertIn("fix the thing", payload["fix"]["description"])

    def test_next_exits_3_once_the_fix_is_done(self):
        self.finish_all()
        self.fix_add()
        self.run_script("issues_state.py", "done", "fix-1")
        out, payload = self.next_issue()
        self.assertEqual(out.returncode, 3)
        self.assertTrue(payload["finished"])

    def test_done_block_unblock_attempt_all_work_on_a_fix_id(self):
        self.finish_all()
        self.fix_add()
        self.run_script("issues_state.py", "attempt", "fix-1")
        self.assertEqual(self.state()["attempts"]["fix-1"], 1)

        self.run_script("issues_state.py", "block", "fix-1", "--reason", "x")
        self.assertEqual(self.state()["blocked"]["issue"], "fix-1")

        self.run_script("issues_state.py", "unblock")
        self.assertEqual(self.state()["status"]["fix-1"], "pending")
        self.assertIsNone(self.state()["blocked"])

        self.run_script("issues_state.py", "done", "fix-1", "--commit", "cafefeed")
        self.assertEqual(self.state()["status"]["fix-1"], "done")
        self.assertEqual(self.state()["commits"]["fix-1"], "cafefeed")

    def test_status_lists_a_fix_in_the_table_with_its_title(self):
        self.finish_all()
        self.fix_add(issues=[1], description="do the thing better\nmore detail\n")
        out = self.run_script("issues_state.py", "status").stdout
        self.assertIn("fix-1", out)
        self.assertIn("do the thing better", out)


if __name__ == "__main__":
    unittest.main()
