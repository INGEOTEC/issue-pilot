"""Per-repository configuration, and the precedence between its three sources.

The point of these tests is the precedence: a project setting must beat the
default, and a single run's environment must beat the project.
"""
import json
import os
import pathlib
import subprocess
import sys
import unittest

from support import SCRIPTS, PilotTestCase


class Configuration(PilotTestCase):
    def get(self, key, env=None):
        return self.run_script("pilot_config.py", "get", key,
                               env=env).stdout.strip()

    def test_defaults_apply_with_no_config_file(self):
        self.assertEqual(self.get("model"), "sonnet")
        self.assertEqual(self.get("branch_prefix"), "issues")
        self.assertEqual(self.get("max_attempts"), "3")

    def test_unset_settings_print_nothing(self):
        # The shell reads these with $(...); an unset one has to be empty, not
        # the word "None".
        self.assertEqual(self.get("test_command"), "")
        self.assertEqual(self.get("base_branch"), "")
        self.assertEqual(self.get("issue_labels"), "")

    def test_the_project_file_overrides_the_defaults(self):
        self.write_config({"model": "opus", "test_command": "make test"})
        self.assertEqual(self.get("model"), "opus")
        self.assertEqual(self.get("test_command"), "make test")

    def test_the_environment_overrides_the_project_file(self):
        self.write_config({"model": "opus"})
        self.assertEqual(self.get("model", env={"ISSUE_PILOT_MODEL": "haiku"}),
                         "haiku")

    def test_typed_settings_survive_the_environment(self):
        self.assertEqual(self.get("max_attempts",
                                  env={"ISSUE_PILOT_MAX_ATTEMPTS": "5"}), "5")
        self.assertEqual(self.get("pr_draft",
                                  env={"ISSUE_PILOT_PR_DRAFT": "true"}), "True")
        self.assertEqual(self.get("issue_labels",
                                  env={"ISSUE_PILOT_ISSUE_LABELS": "a, b"}), "a,b")

    def test_watch_is_on_by_default(self):
        self.assertEqual(self.get("watch"), "True")

    def test_watch_can_be_turned_off_by_the_project_file(self):
        self.write_config({"watch": False})
        self.assertEqual(self.get("watch"), "False")

    def test_watch_can_be_turned_off_by_the_environment(self):
        self.assertEqual(self.get("watch", env={"ISSUE_PILOT_WATCH": "0"}), "False")
        self.write_config({"watch": True})
        self.assertEqual(self.get("watch", env={"ISSUE_PILOT_WATCH": "0"}), "False")

    def test_an_unknown_setting_is_rejected(self):
        out = self.run_script("pilot_config.py", "get", "nonsense", check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("unknown setting", out.stderr)

    def test_an_unknown_key_in_the_file_warns_but_does_not_stop_a_run(self):
        self.write_config({"modle": "opus"})
        out = self.run_script("pilot_config.py", "get", "model")
        self.assertIn("modle", out.stderr)
        self.assertEqual(out.stdout.strip(), "sonnet")

    def test_a_broken_config_file_says_where(self):
        (self.repo / ".issue-pilot.json").write_text("{not json")
        out = self.run_script("pilot_config.py", "show", check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn(".issue-pilot.json", out.stderr)

    def test_the_base_branch_falls_back_to_the_default_branch_on_github(self):
        out = self.run_script("pilot_config.py", "base-branch",
                              env={"FAKE_GH_DEFAULT_BRANCH": "develop"})
        self.assertEqual(out.stdout.strip(), "develop")

    def test_a_configured_base_branch_does_not_ask_github(self):
        self.write_config({"base_branch": "release"})
        out = self.run_script("pilot_config.py", "base-branch",
                              env={"FAKE_GH_DEFAULT_BRANCH": "develop"})
        self.assertEqual(out.stdout.strip(), "release")

    def check(self, **env):
        return self.run_script("pilot_config.py", "check", check=False, env=env)

    def test_check_refuses_a_repository_with_no_configuration(self):
        out = self.check()
        self.assertEqual(out.returncode, 1)
        self.assertIn("is missing", out.stderr)
        self.assertIn("/issue-pilot:init", out.stderr)

    def test_check_accepts_a_configuration_that_says_what_green_means(self):
        self.write_config({"test_command": "pytest -q"})
        out = self.check()
        self.assertEqual(out.returncode, 0)
        self.assertIn(".issue-pilot.json", out.stdout)

    def test_check_refuses_a_configuration_without_a_test_command(self):
        # Everything else has a sensible fallback; this one does not.
        self.write_config({"model": "opus"})
        out = self.check()
        self.assertEqual(out.returncode, 1)
        self.assertIn("test_command", out.stderr)

    def test_check_refuses_a_typo_that_the_runtime_would_tolerate(self):
        self.write_config({"test_command": "true", "modle": "opus"})
        out = self.check()
        self.assertEqual(out.returncode, 1)
        self.assertIn("modle", out.stderr)

    def test_check_refuses_a_file_that_is_not_json(self):
        (self.repo / ".issue-pilot.json").write_text("{not json")
        out = self.check()
        self.assertEqual(out.returncode, 1)
        self.assertIn("not valid JSON", out.stderr)

    def test_detect_proposes_the_projects_own_test_command(self):
        (self.repo / "Makefile").write_text("test:\n\tpytest\n")
        proposal = json.loads(self.run_script("pilot_config.py", "detect").stdout)
        self.assertEqual(proposal["test_command"], "make test")

    def test_detect_falls_back_through_the_ecosystems(self):
        cases = [
            ({"package.json": '{"scripts": {"test": "jest"}}'}, "npm test"),
            ({"pyproject.toml": "[project]\nname = 'x'\n"}, "pytest -q"),
            ({"Cargo.toml": "[package]\n"}, "cargo test"),
            ({"go.mod": "module x\n"}, "go test ./..."),
        ]
        for files, expected in cases:
            with self.subTest(expected=expected):
                for name, body in files.items():
                    (self.repo / name).write_text(body)
                proposal = json.loads(
                    self.run_script("pilot_config.py", "detect").stdout)
                self.assertEqual(proposal["test_command"], expected)
                for name in files:
                    (self.repo / name).unlink()

    def test_detect_admits_when_it_cannot_tell(self):
        proposal = json.loads(self.run_script("pilot_config.py", "detect").stdout)
        self.assertIsNone(proposal["test_command"])
        self.assertEqual(proposal["base_branch"], "main")

    def test_state_lives_under_issue_pilot_home(self):
        out = self.run_script("pilot_config.py", "path")
        self.assertTrue(out.stdout.strip().startswith(str(self.home)))


class NotesFile(PilotTestCase):
    """Where a run's answers live: per repository, and cleared on request."""

    def notes(self, *args, cwd=None):
        out = subprocess.run(
            [sys.executable, str(SCRIPTS / "pilot_config.py"), "notes-file", *args],
            cwd=cwd or self.repo, capture_output=True, text=True, env=self.env)
        self.assertEqual(out.returncode, 0, out.stderr)
        return pathlib.Path(out.stdout.strip())

    def other_repo(self):
        other = pathlib.Path(self.tmp.name) / "other" / "repo"
        other.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=other, check=True, capture_output=True)
        return other

    def test_the_path_is_under_notes_and_names_the_repository_and_branch(self):
        path = self.notes("56")
        self.assertEqual(path.parent, self.home / "state" / "notes")
        slug = os.path.realpath(self.repo).strip("/").replace("/", "-")
        self.assertEqual(path.name, "%s-issues-56.txt" % slug)

    def test_two_repositories_get_two_paths(self):
        other = self.other_repo()
        self.assertNotEqual(self.notes("56"), self.notes("56", cwd=other))

    def test_plain_notes_file_deletes_nothing(self):
        path = self.notes("56")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("answers\n")
        legacy = self.home / "state" / "issues-notes-issues-56.txt"
        legacy.write_text("old\n")
        self.assertEqual(self.notes("56"), path)
        self.assertTrue(path.exists())
        self.assertTrue(legacy.exists())

    def test_fresh_removes_the_file_at_the_path(self):
        path = self.notes("56")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("answers of an earlier run\n")
        self.assertEqual(self.notes("--fresh", "56"), path)
        self.assertFalse(path.exists())
        self.assertTrue(path.parent.is_dir())

    def test_fresh_works_when_there_is_nothing_to_remove(self):
        path = self.notes("--fresh", "56")
        self.assertFalse(path.exists())
        self.assertTrue(path.parent.is_dir())

    def test_fresh_removes_the_legacy_files_and_spares_everything_else(self):
        state = self.home / "state"
        state.mkdir(parents=True, exist_ok=True)
        legacy = [state / "issues-notes-issues-56.txt", state / "issues-notes-issues-1-2.txt"]
        spared = [state / "issues-run-home-u-foo.json", state / "issues-driver-home-u-foo.json",
                  state / "guard.json", state / "issues-notes.txt", state / "notes-issues-56.txt"]
        for f in legacy + spared:
            f.write_text("x\n")
        a_directory = state / "issues-notes-a-directory.txt"
        a_directory.mkdir()
        (a_directory / "inner.txt").write_text("x\n")

        other_notes = state / "notes" / "somewhere-else-issues-56.txt"
        other_notes.parent.mkdir()
        other_notes.write_text("another repository\n")
        legacy_in_notes = state / "notes" / "issues-notes-issues-56.txt"
        legacy_in_notes.write_text("a new-format name that happens to look old\n")

        self.notes("--fresh", "56")

        for f in legacy:
            self.assertFalse(f.exists(), f)
        for f in spared + [other_notes, legacy_in_notes, a_directory / "inner.txt"]:
            self.assertTrue(f.exists(), f)

    def test_fresh_leaves_the_notes_of_another_repository_with_the_same_issues(self):
        other = self.other_repo()
        theirs = self.notes("--fresh", "56", cwd=other)
        theirs.write_text("their answers\n")
        mine = self.notes("--fresh", "56")
        self.assertNotEqual(mine, theirs)
        self.assertEqual(theirs.read_text(), "their answers\n")

    def test_the_drivers_interview_path_asks_for_a_fresh_notes_file(self):
        # Phase 1 needs a terminal, so it cannot be run here: check the call.
        text = (SCRIPTS / "issues_run.sh").read_text()
        self.assertIn('notes-file --fresh "${ARGS[@]}"', text)


if __name__ == "__main__":
    unittest.main()
