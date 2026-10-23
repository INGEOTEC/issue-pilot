"""The run state: the only thing that survives the `/clear` between issues.

Every guarantee the driver relies on is here -- that `next` hands out issues in
order, that a blocked run stops, that `--resume` can reopen it -- so these tests
are really tests of the driver's control flow.
"""
import datetime
import json
import os
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


class QuickHelpers(PilotTestCase):
    """A run of three issues in a repository, and the files `quick` reads."""

    def setUp(self):
        super().setUp()
        self.write_issues({1: issue(1, "first"), 2: issue(2, "second"),
                           3: issue(3, "third")})
        self.run_script("issues_state.py", "init", "1", "2", "3",
                        "--branch", "issues-1-2-3")

    def quick(self):
        return self.run_script("issues_state.py", "quick").stdout

    def log_dir(self):
        d = self.home / "logs" / "issues-1-2-3"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def point_at(self, branch, pid):
        """Record the driver `--detach` would have launched, through the CLI."""
        self.run_script("issues_state.py", "driver-pointer", "--branch", branch,
                        "--pid", str(pid), "--log", str(self.home / "logs" / branch / "driver.log"))

    def stamp(self, ago=0):
        t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=ago)
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")

    def guard_log(self, *lines):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "usage-guard.log").write_text("\n".join(lines) + "\n")

    def lib_with_marking_guard(self):
        """A copy of the plugin whose usage-guard.sh leaves a marker when run."""
        lib = pathlib.Path(self.tmp.name) / "lib"
        shutil.copytree(SCRIPTS, lib / "scripts")
        (lib / "hooks").mkdir()
        self.marker = pathlib.Path(self.tmp.name) / "guard-ran"
        guard = lib / "hooks" / "usage-guard.sh"
        guard.write_text(f"#!/bin/sh\ntouch '{self.marker}'\necho GUARD-CHECK-OUTPUT\n")
        guard.chmod(0o755)
        return lib


class Quick(QuickHelpers):
    """`quick`: the answer `/issue-pilot:status` gives while the driver runs."""

    def test_a_live_driver_pid_is_running(self):
        (self.log_dir() / "driver.pid").write_text(f"{os.getpid()}\n")
        self.assertIn(f"driver     : running (pid {os.getpid()})", self.quick())

    def test_a_dead_driver_pid_is_not_running(self):
        proc = subprocess.Popen(["true"])
        proc.wait()
        (self.log_dir() / "driver.pid").write_text(f"{proc.pid}\n")
        self.assertIn(f"driver     : not running (pid {proc.pid})", self.quick())

    def test_no_driver_pid_is_not_detached(self):
        self.assertIn("driver     : not detached", self.quick())

    def test_without_a_run_it_says_so(self):
        shutil.rmtree(self.home / "state")
        out = self.run_script("issues_state.py", "quick", check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("no run in progress", out.stderr)

    def test_a_halt_file_is_shown_with_its_text(self):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "usage-guard.halt").write_text("weekly window at 97%\n")
        self.assertIn("guard      : halted -- weekly window at 97%", self.quick())

    def test_a_pause_that_has_not_ended_is_shown_with_its_window(self):
        self.guard_log(f"{self.stamp(60)} throttling: 5h window at 81% >= 70%, sleeping 3600s")
        out = self.quick()
        self.assertIn("guard      : paused until", out)
        self.assertIn("(5h at 81%)", out)

    def test_a_pause_followed_by_resuming_is_ok(self):
        self.guard_log(f"{self.stamp(60)} throttling: 5h window at 81% >= 70%, sleeping 3600s",
                       f"{self.stamp(10)} resuming after wait")
        self.assertIn("guard      : ok", self.quick())

    def test_a_pause_whose_sleep_has_ended_is_ok(self):
        self.guard_log(f"{self.stamp(7200)} throttling: 5h window at 81% >= 70%, sleeping 3600s")
        self.assertIn("guard      : ok", self.quick())

    def test_no_guard_files_is_ok(self):
        self.assertIn("guard      : ok", self.quick())

    def test_a_pause_is_found_in_the_tail_of_a_large_log(self):
        filler = [f"{self.stamp(5000)} ok: 5h window at 10% (threshold 70%)"] * 5000
        self.guard_log(*filler,
                       f"{self.stamp(5)} throttling: 5h window at 81% >= 70%, sleeping 3600s")
        self.assertIn("guard      : paused until", self.quick())

    def test_progress_counts_done_items_and_names_the_current_one(self):
        self.run_script("issues_state.py", "done", "1")
        self.assertIn("progress   : 1/3 done, current #2", self.quick())

    def test_progress_names_a_fix_as_current(self):
        for n in "123":
            self.run_script("issues_state.py", "done", n)
        desc = pathlib.Path(self.tmp.name) / "fix.txt"
        desc.write_text("do the thing better\n")
        self.run_script("issues_state.py", "fix-add", "--description-file", str(desc),
                        "--issues", "1")
        self.assertIn("progress   : 3/4 done, current fix-1", self.quick())

    def test_the_log_line_names_driver_log(self):
        self.assertIn("driver.log", self.quick())


class DriverPointer(QuickHelpers):
    """`driver-pointer` and what `quick` and `report` make of it."""

    def pointer(self, check=True):
        return self.run_script("issues_state.py", "driver-pointer", check=check)

    def test_no_pointer_exits_one(self):
        out = self.pointer(check=False)
        self.assertEqual(out.returncode, 1)
        self.assertEqual(out.stdout, "")

    def test_the_pointer_round_trips_and_sits_next_to_the_state_file(self):
        self.point_at("issues-7-8", 4242)
        data = json.loads(self.pointer().stdout)
        self.assertEqual(data["branch"], "issues-7-8")
        self.assertEqual(data["pid"], 4242)
        self.assertTrue(data["log"].endswith("issues-7-8/driver.log"))
        self.assertIn("started_at", data)
        state_file = pathlib.Path(self.run_script("issues_state.py", "path").stdout.strip())
        (file,) = state_file.parent.glob("issues-driver-*.json")
        self.assertEqual(file.name.replace("issues-driver-", "issues-run-"), state_file.name)

    def test_the_pointer_is_rewritten_by_the_next_detach(self):
        self.point_at("issues-7-8", 1)
        self.point_at("issues-9", 2)
        self.assertEqual(json.loads(self.pointer().stdout)["branch"], "issues-9")

    def test_an_unreadable_pointer_is_no_pointer(self):
        self.point_at("issues-7-8", 1)
        state_file = pathlib.Path(self.run_script("issues_state.py", "path").stdout.strip())
        (file,) = state_file.parent.glob("issues-driver-*.json")
        file.write_text("{not json")
        self.assertEqual(self.pointer(check=False).returncode, 1)

    def test_a_pointer_for_the_states_branch_behaves_as_driver_pid_does(self):
        (self.log_dir() / "driver.pid").write_text(f"{os.getpid()}\n")
        self.point_at("issues-1-2-3", os.getpid())
        out = self.quick()
        self.assertIn(f"driver     : running (pid {os.getpid()})", out)
        self.assertIn("progress   : 0/3 done", out)

    def test_the_pid_comes_from_the_pointers_branch_not_the_states(self):
        # The state names issues-1-2-3 and has a dead pid; the pointer names
        # another branch whose driver.pid is alive.
        proc = subprocess.Popen(["true"])
        proc.wait()
        (self.log_dir() / "driver.pid").write_text(f"{proc.pid}\n")
        other = self.home / "logs" / "issues-7-8"
        other.mkdir(parents=True)
        (other / "driver.pid").write_text(f"{os.getpid()}\n")
        self.point_at("issues-7-8", os.getpid())
        out = self.quick()
        self.assertIn(f"driver     : starting issues-7-8 (pid {os.getpid()})", out)
        self.assertIn("progress   : not initialised yet", out)
        self.assertIn("logs/issues-7-8/driver.log", out)
        self.assertNotIn("issues-1-2-3", out)

    def test_report_is_quick_while_the_pointed_driver_is_starting(self):
        other = self.home / "logs" / "issues-7-8"
        other.mkdir(parents=True)
        (other / "driver.pid").write_text(f"{os.getpid()}\n")
        self.point_at("issues-7-8", os.getpid())
        out = self.run_script("issues_state.py", "report").stdout
        self.assertTrue(out.startswith("report     : quick\n"))
        self.assertIn("starting issues-7-8", out)

    def test_status_names_the_pointers_log(self):
        other = self.home / "logs" / "issues-7-8"
        other.mkdir(parents=True)
        (other / "driver.pid").write_text(f"{os.getpid()}\n")
        self.point_at("issues-7-8", os.getpid())
        out = self.run_script("issues_state.py", "status").stdout
        self.assertIn("for issues-7-8", out)
        self.assertIn("logs/issues-7-8/driver.log", out)

    def test_a_dead_pointed_driver_says_it_stopped_before_initialising(self):
        proc = subprocess.Popen(["true"])
        proc.wait()
        other = self.home / "logs" / "issues-7-8"
        other.mkdir(parents=True)
        (other / "driver.pid").write_text(f"{proc.pid}\n")
        self.point_at("issues-7-8", proc.pid)
        out = self.quick()
        self.assertIn("stopped before initialising issues-7-8", out)


class Report(QuickHelpers):
    """`report`: the one command `/issue-pilot:status` runs; it chooses."""

    def report(self, lib=None):
        script = (lib / "scripts" if lib else SCRIPTS) / "issues_state.py"
        return subprocess.run([sys.executable, str(script), "report"], cwd=self.repo,
                              capture_output=True, text=True, env=self.env)

    def test_a_live_driver_gets_the_quick_answer_and_never_runs_the_guard(self):
        lib = self.lib_with_marking_guard()
        (self.log_dir() / "driver.pid").write_text(f"{os.getpid()}\n")
        (self.log_dir() / "driver.log").write_text("SECRET-LOG-LINE\n")
        out = self.report(lib)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.startswith("report     : quick\n"))
        self.assertIn("driver     : running", out.stdout)
        self.assertIn("progress   :", out.stdout)
        self.assertNotIn("SECRET-LOG-LINE", out.stdout)
        self.assertNotIn("GUARD-CHECK-OUTPUT", out.stdout)
        self.assertFalse(self.marker.exists())

    def assert_full(self, out, marker):
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.startswith("report     : full\n"))
        self.assertIn("repository :", out.stdout)  # the status table
        self.assertIn("--- usage ---", out.stdout)
        self.assertIn("GUARD-CHECK-OUTPUT", out.stdout)
        self.assertIn("--- driver.log", out.stdout)
        self.assertIn(marker, out.stdout)
        order = [out.stdout.index(x) for x in
                 ("repository :", "--- usage ---", "--- driver.log")]
        self.assertEqual(order, sorted(order))

    def test_a_dead_driver_gets_the_full_report(self):
        lib = self.lib_with_marking_guard()
        proc = subprocess.Popen(["true"])
        proc.wait()
        (self.log_dir() / "driver.pid").write_text(f"{proc.pid}\n")
        (self.log_dir() / "driver.log").write_text("one\nDRIVER-TAIL\n")
        self.assert_full(self.report(lib), "DRIVER-TAIL")
        self.assertTrue(self.marker.exists())

    def test_no_driver_pid_gets_the_full_report(self):
        lib = self.lib_with_marking_guard()
        (self.log_dir() / "driver.log").write_text("DRIVER-TAIL\n")
        self.assert_full(self.report(lib), "DRIVER-TAIL")

    def test_the_full_report_tails_only_the_last_lines_of_a_log(self):
        lib = self.lib_with_marking_guard()
        (self.log_dir() / "driver.log").write_text(
            "".join(f"line {i}\n" for i in range(200)))
        out = self.report(lib).stdout
        self.assertIn("line 199", out)
        self.assertIn("line 160", out)
        self.assertNotIn("line 159\n", out)

    def test_the_full_report_includes_the_most_recent_session_log(self):
        lib = self.lib_with_marking_guard()
        old = self.log_dir() / "issue-1-attempt-1.log"
        new = self.log_dir() / "issue-fix-1-attempt-1.log"
        old.write_text("OLD-SESSION\n")
        new.write_text("NEW-SESSION\n")
        os.utime(old, (1, 1))
        out = self.report(lib).stdout
        self.assertIn("NEW-SESSION", out)
        self.assertNotIn("OLD-SESSION", out)
        self.assertIn("--- issue-fix-1-attempt-1.log", out)

    def test_a_failing_guard_does_not_crash_the_report(self):
        lib = self.lib_with_marking_guard()
        guard = lib / "hooks" / "usage-guard.sh"
        guard.write_text("#!/bin/sh\necho boom >&2\nexit 7\n")
        out = self.report(lib)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("usage check failed (exit 7)", out.stdout)
        self.assertIn("boom", out.stdout)
        self.assertIn("--- driver.log", out.stdout)

    def test_a_missing_guard_does_not_crash_the_report(self):
        lib = self.lib_with_marking_guard()
        (lib / "hooks" / "usage-guard.sh").unlink()
        out = self.report(lib)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("usage check failed", out.stdout)

    def test_without_a_run_it_fails_as_status_does(self):
        shutil.rmtree(self.home / "state")
        out = self.report()
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("no run in progress", out.stderr)

    def test_status_md_is_one_unconditional_command(self):
        text = (SCRIPTS.parent / "commands" / "status.md").read_text()
        self.assertEqual(text.count("```bash"), 1)
        self.assertIn("issues_state.py\" report", text)
        self.assertNotIn("--check", text)
        self.assertNotIn("if the driver", text.lower())


class Wait(QuickHelpers):
    """`wait`: block while the detached driver lives, then notify and report."""

    def setUp(self):
        super().setUp()
        self.lib = self.lib_with_marking_guard()
        self.notify_log = pathlib.Path(self.env["FAKE_NOTIFY_LOG"])

    def start_wait(self, env=None):
        return subprocess.Popen(
            [sys.executable, str(self.lib / "scripts" / "issues_state.py"),
             "wait", "--interval", "0.1"], cwd=self.repo, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**self.env, **(env or {})})

    def wait_cmd(self, env=None):
        proc = self.start_wait(env)
        out, err = proc.communicate(timeout=30)
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)

    def dead_pid(self):
        proc = subprocess.Popen(["true"])
        proc.wait()
        return proc.pid

    def notifications(self):
        if not self.notify_log.exists():
            return []
        return self.notify_log.read_text().splitlines()

    def test_a_dead_driver_returns_promptly_with_the_full_report(self):
        (self.log_dir() / "driver.pid").write_text(f"{self.dead_pid()}\n")
        (self.log_dir() / "driver.log").write_text("one\nDRIVER-TAIL\n")
        out = self.wait_cmd()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.startswith("report     : full\n"))
        for part in ("repository :", "--- usage ---", "GUARD-CHECK-OUTPUT",
                     "--- driver.log", "DRIVER-TAIL"):
            self.assertIn(part, out.stdout)

    def test_the_report_is_the_one_report_prints_for_a_stopped_driver(self):
        (self.log_dir() / "driver.pid").write_text(f"{self.dead_pid()}\n")
        (self.log_dir() / "driver.log").write_text("DRIVER-TAIL\n")
        report = subprocess.run(
            [sys.executable, str(self.lib / "scripts" / "issues_state.py"), "report"],
            cwd=self.repo, capture_output=True, text=True, env=self.env)
        self.assertEqual(self.wait_cmd().stdout, report.stdout)

    def test_a_live_driver_keeps_it_waiting_until_the_driver_exits(self):
        child = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: (child.poll() is None and child.kill(), child.wait()))
        (self.log_dir() / "driver.pid").write_text(f"{child.pid}\n")
        proc = self.start_wait()
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
        with self.assertRaises(subprocess.TimeoutExpired):
            proc.wait(timeout=1)
        self.assertEqual(self.notifications(), [])
        child.kill()
        child.wait()  # reaped: an unreaped zombie still answers kill(pid, 0)
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, err)
        self.assertTrue(out.startswith("report     : full\n"))
        self.assertEqual(len(self.notifications()), 1)

    def test_a_new_pid_written_while_waiting_is_the_one_that_counts(self):
        first = subprocess.Popen(["sleep", "60"])
        second = subprocess.Popen(["sleep", "60"])
        for child in (first, second):
            self.addCleanup(lambda c=child: (c.poll() is None and c.kill(), c.wait()))
        (self.log_dir() / "driver.pid").write_text(f"{first.pid}\n")
        proc = self.start_wait()
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
        (self.log_dir() / "driver.pid").write_text(f"{second.pid}\n")
        first.kill()
        first.wait()
        with self.assertRaises(subprocess.TimeoutExpired):
            proc.wait(timeout=1)
        second.kill()
        second.wait()
        proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0)

    def test_no_driver_pid_returns_at_once_and_says_so(self):
        out = self.wait_cmd()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("no detached driver", out.stdout.splitlines()[0])
        self.assertIn("report     : full", out.stdout)
        self.assertIn("--- usage ---", out.stdout)
        self.assertEqual(self.notifications(), [])

    def test_the_notification_says_the_run_finished(self):
        for n in ("1", "2", "3"):
            self.run_script("issues_state.py", "done", n)
        (self.log_dir() / "driver.pid").write_text(f"{self.dead_pid()}\n")
        self.wait_cmd()
        (call,) = self.notifications()
        self.assertIn("issues-1-2-3", call)
        self.assertIn("Finished", call)

    def test_the_notification_says_what_is_pending(self):
        self.run_script("issues_state.py", "done", "1")
        (self.log_dir() / "driver.pid").write_text(f"{self.dead_pid()}\n")
        self.wait_cmd()
        (call,) = self.notifications()
        self.assertIn("pending", call)
        self.assertIn("#2", call)

    def test_the_notification_names_the_blocked_item(self):
        self.run_script("issues_state.py", "done", "1")
        self.run_script("issues_state.py", "block", "2", "--reason", "tests are red")
        (self.log_dir() / "driver.pid").write_text(f"{self.dead_pid()}\n")
        self.wait_cmd()
        (call,) = self.notifications()
        self.assertIn("Blocked on #2", call)
        self.assertIn("tests are red", call)

    def test_a_failing_notify_send_changes_nothing(self):
        (self.log_dir() / "driver.pid").write_text(f"{self.dead_pid()}\n")
        good = self.wait_cmd()
        failing = self.wait_cmd(env={"FAKE_NOTIFY_EXIT": "1"})
        self.assertEqual(failing.returncode, 0, failing.stderr)
        self.assertEqual(failing.stdout, good.stdout)
        self.assertEqual(len(self.notifications()), 2)

    def test_a_missing_notify_send_changes_nothing(self):
        (self.log_dir() / "driver.pid").write_text(f"{self.dead_pid()}\n")
        # A PATH with only the tools the script needs, and no notify-send.
        bare = pathlib.Path(self.tmp.name) / "bare-bin"
        bare.mkdir()
        for tool in ("git", "true", "sh"):
            (bare / tool).symlink_to(shutil.which(tool))
        out = self.wait_cmd(env={"PATH": str(bare)})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.startswith("report     : full\n"))
        self.assertEqual(self.notifications(), [])

    def test_a_hung_notify_send_does_not_hang_wait(self):
        (self.log_dir() / "driver.pid").write_text(f"{self.dead_pid()}\n")
        slow = pathlib.Path(self.tmp.name) / "slow-bin"
        slow.mkdir()
        (slow / "notify-send").write_text("#!/bin/sh\nsleep 60\n")
        (slow / "notify-send").chmod(0o755)
        script = self.lib / "scripts" / "issues_state.py"
        code = ("import sys; sys.path.insert(0, %r); import issues_state; "
                "issues_state.NOTIFY_TIMEOUT = 0.5; "
                "issues_state.notify('t', 'm'); print('returned')"
                % str(self.lib / "scripts"))
        out = subprocess.run([sys.executable, "-c", code], cwd=self.repo,
                             capture_output=True, text=True, timeout=30,
                             env={**self.env, "PATH": f"{slow}{os.pathsep}{self.env['PATH']}"})
        self.assertIn("returned", out.stdout, out.stderr)
        self.assertTrue(script.exists())

    def other_driver(self, pid, log=None):
        """A driver for issues-7-8 while the state still names issues-1-2-3."""
        other = self.home / "logs" / "issues-7-8"
        other.mkdir(parents=True, exist_ok=True)
        (other / "driver.pid").write_text(f"{pid}\n")
        if log is not None:
            (other / "driver.log").write_text(log)
        self.point_at("issues-7-8", pid)

    def rewrite_state_as(self, branch):
        self.run_script("issues_state.py", "init", "1", "2", "--branch", branch)

    def test_a_pointer_for_the_states_branch_waits_like_driver_pid(self):
        child = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: (child.poll() is None and child.kill(), child.wait()))
        self.point_at("issues-1-2-3", child.pid)
        (self.log_dir() / "driver.pid").write_text(f"{child.pid}\n")
        proc = self.start_wait()
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
        with self.assertRaises(subprocess.TimeoutExpired):
            proc.wait(timeout=1)
        child.kill()
        child.wait()
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, err)
        self.assertTrue(out.startswith("report     : full\n"))

    def test_a_driver_still_starting_another_run_keeps_wait_waiting(self):
        child = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: (child.poll() is None and child.kill(), child.wait()))
        self.other_driver(child.pid)
        proc = self.start_wait()
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
        with self.assertRaises(subprocess.TimeoutExpired):
            proc.wait(timeout=1)
        self.assertEqual(self.notifications(), [])

    def test_the_run_being_initialised_and_the_driver_stopping_gives_its_full_report(self):
        child = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: (child.poll() is None and child.kill(), child.wait()))
        self.other_driver(child.pid)
        proc = self.start_wait()
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
        self.rewrite_state_as("issues-7-8")
        child.kill()
        child.wait()
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, err)
        self.assertTrue(out.startswith("report     : full\n"))
        self.assertIn("branch     : issues-7-8", out)
        self.assertEqual(len(self.notifications()), 1)

    def test_a_driver_that_dies_before_initialising_exits_one_with_its_log(self):
        child = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: (child.poll() is None and child.kill(), child.wait()))
        self.other_driver(child.pid, log="one\nDRIVER-TAIL\n")
        proc = self.start_wait()
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
        child.kill()
        child.wait()
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 1, err)
        self.assertIn("driver for issues-7-8 stopped before initialising the run "
                      f"(pid {child.pid}); the state still describes issues-1-2-3", out)
        self.assertIn("DRIVER-TAIL", out)
        self.assertNotIn("report     : full", out)
        (call,) = self.notifications()
        self.assertIn("stopped before initialising", call)

    def test_without_a_run_it_fails_as_status_does(self):
        shutil.rmtree(self.home / "state")
        out = self.wait_cmd()
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("no run in progress", out.stderr)


class WatchInTheCommands(unittest.TestCase):
    """run.md and fix.md are the only instructions that start `wait`."""

    def text(self, name):
        return (SCRIPTS.parent / "commands" / name).read_text()

    def test_run_and_fix_start_wait_in_the_background_when_watch_is_on(self):
        for name in ("run.md", "fix.md"):
            text = self.text(name)
            for needle in ('pilot_config.py" get watch',
                           'issues_state.py" wait', "run_in_background"):
                self.assertTrue(needle in text, f"{name} lacks {needle}")

    def test_a_failed_detach_stops_before_the_wait(self):
        for name in ("run.md", "fix.md"):
            text = " ".join(self.text(name).split())
            for needle in ("exits non-zero", "do **not** start the background `wait`",
                           "stopped before initialising"):
                self.assertIn(needle, text, f"{name} lacks {needle}")

    def test_run_md_writes_the_notes_with_the_write_tool_in_one_step(self):
        text = self.text("run.md")
        self.assertIn("Write tool", text)
        self.assertIn("shell chain", text)

    def test_status_md_never_waits(self):
        self.assertNotIn("wait", self.text("status.md").replace("do not wait", ""))


if __name__ == "__main__":
    unittest.main()
