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

    def test_the_tracker_settings_have_their_defaults(self):
        self.assertEqual(self.get("tracker"), "github")
        self.assertEqual(self.get("issues_dir"), "issues")
        self.assertEqual(self.get("landing"), "pull_request")
        shown = json.loads(self.run_script("pilot_config.py", "show").stdout)
        self.assertEqual((shown["tracker"], shown["issues_dir"], shown["landing"]),
                         ("github", "issues", "pull_request"))

    def test_the_environment_overrides_the_tracker(self):
        self.write_config({"test_command": "true", "tracker": "github"})
        self.assertEqual(self.get("tracker", env={"ISSUE_PILOT_TRACKER": "local"}), "local")

    def test_check_refuses_an_unknown_tracker(self):
        self.write_config({"test_command": "true", "tracker": "jira"})
        out = self.check()
        self.assertEqual(out.returncode, 1)
        self.assertIn("tracker", out.stderr)
        self.assertIn("jira", out.stderr)

    def test_check_refuses_an_unknown_landing(self):
        self.write_config({"test_command": "true", "landing": "yolo"})
        out = self.check()
        self.assertEqual(out.returncode, 1)
        self.assertIn("landing", out.stderr)

    def test_check_refuses_an_issues_dir_that_leaves_the_repository(self):
        for bad in ("/tmp/issues", "../issues", "a/../../b", "", "  ", 7):
            with self.subTest(issues_dir=bad):
                self.write_config({"test_command": "true", "issues_dir": bad})
                out = self.check()
                self.assertEqual(out.returncode, 1)
                self.assertIn("issues_dir", out.stderr)

    def test_check_accepts_the_valid_values(self):
        for landing in ("pull_request", "merge", "push", "branch"):
            self.write_config({"test_command": "true", "tracker": "local",
                               "issues_dir": "docs/tickets", "landing": landing,
                               "base_branch": "main"})
            self.assertEqual(self.check().returncode, 0, landing)

    def fake_gh_that_logs(self):
        """A `gh` that records any call and fails; returns the log's path."""
        log = self.repo.parent / "gh-calls.log"
        gh = self.bindir / "gh"
        gh.write_text("#!/bin/sh\necho \"$@\" >> %s\nexit 1\n" % log)
        gh.chmod(0o755)
        return log

    def test_uses_github_for_the_three_configurations(self):
        def uses():
            return self.run_script("pilot_config.py", "uses-github").stdout.strip()
        self.assertEqual(uses(), "yes")
        self.write_config({"test_command": "true", "tracker": "local"})
        self.assertEqual(uses(), "yes")
        self.write_config({"test_command": "true", "tracker": "local", "landing": "merge"})
        self.assertEqual(uses(), "no")
        self.write_config({"test_command": "true", "tracker": "github", "landing": "merge"})
        self.assertEqual(uses(), "yes")

    def test_remote_prints_origin_only_when_it_exists(self):
        self.assertEqual(self.run_script("pilot_config.py", "remote").stdout, "origin\n")
        self._git("remote", "remove", "origin")
        out = self.run_script("pilot_config.py", "remote")
        self.assertEqual((out.returncode, out.stdout), (0, ""))

    def test_without_github_the_base_branch_never_calls_gh(self):
        log = self.fake_gh_that_logs()
        self.write_config({"test_command": "true", "tracker": "local", "landing": "merge",
                           "base_branch": "release"})
        out = self.run_script("pilot_config.py", "base-branch")
        self.assertEqual(out.stdout.strip(), "release")
        self.write_config({"test_command": "true", "tracker": "local", "landing": "merge"})
        self._git("checkout", "-q", "-b", "wip")
        out = self.run_script("pilot_config.py", "base-branch")
        self.assertEqual(out.stdout.strip(), "wip")
        out = self.run_script("pilot_config.py", "default-branch")
        self.assertEqual(out.stdout, "\n")
        self.assertFalse(log.exists() and log.read_text())

    def test_with_github_a_gh_that_cannot_answer_falls_back_to_head(self):
        log = self.fake_gh_that_logs()
        out = self.run_script("pilot_config.py", "base-branch")
        self.assertEqual(out.stdout.strip(), "main")
        self.assertTrue(log.read_text())

    def test_check_wants_a_base_branch_when_there_is_no_github(self):
        problem = "base_branch is not set, and without GitHub there is nothing to ask for a default"
        self.write_config({"test_command": "true", "tracker": "local", "landing": "merge"})
        out = self.check()
        self.assertEqual(out.returncode, 1)
        self.assertIn(problem, out.stderr)
        self.write_config({"test_command": "true", "tracker": "local", "landing": "merge",
                           "base_branch": "  "})
        self.assertIn(problem, self.check().stderr)
        self.write_config({"test_command": "true", "tracker": "local", "landing": "merge",
                           "base_branch": "main"})
        self.assertEqual(self.check().returncode, 0)
        # With GitHub there is a default to ask for, so nothing is missing.
        self.write_config({"test_command": "true"})
        self.assertEqual(self.check().returncode, 0)
        self.write_config({"test_command": "true", "tracker": "local"})
        self.assertEqual(self.check().returncode, 0)

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


GITHUB_URL = "https://github.com/example/repo.git"
OVERLEAF_URL = "https://git.overleaf.com/abc123"


class Hosting(PilotTestCase):
    """Where the remote is, judged offline, and what init proposes because of it."""

    def setUp(self):
        super().setUp()
        self.gh_log = self.repo.parent / "gh-calls.log"
        gh = self.bindir / "gh"
        gh.write_text("#!/bin/sh\necho \"$@\" >> %s\nexit 1\n" % self.gh_log)
        gh.chmod(0o755)

    def hosting(self):
        return json.loads(self.run_script("pilot_config.py", "hosting").stdout)

    def detect(self):
        return json.loads(self.run_script("pilot_config.py", "detect").stdout)

    def no_gh_call(self):
        self.assertFalse(self.gh_log.exists() and self.gh_log.read_text())

    def test_a_github_url(self):
        self._git("remote", "set-url", "origin", GITHUB_URL)
        self.assertEqual(self.hosting(), {"origin": True, "github": True})

    def test_any_other_url(self):
        self._git("remote", "set-url", "origin", OVERLEAF_URL)
        self.assertEqual(self.hosting(), {"origin": True, "github": False})

    def test_no_remote(self):
        self._git("remote", "remove", "origin")
        self.assertEqual(self.hosting(), {"origin": False, "github": False})
        self.no_gh_call()

    def test_detect_proposes_github_and_a_pull_request_on_github(self):
        self._git("remote", "set-url", "origin", GITHUB_URL)
        proposal = self.detect()
        self.assertEqual((proposal["tracker"], proposal["landing"]), ("github", "pull_request"))

    def test_detect_proposes_local_and_merge_elsewhere_without_asking_gh(self):
        for setup in (lambda: self._git("remote", "set-url", "origin", OVERLEAF_URL),
                      lambda: self._git("remote", "remove", "origin")):
            setup()
            self._git("checkout", "-q", "-B", "wip")
            proposal = self.detect()
            self.assertEqual((proposal["tracker"], proposal["landing"]), ("local", "merge"))
            self.assertEqual(proposal["base_branch"], "wip")
            self.no_gh_call()


class NoGh(PilotTestCase):
    """A machine that has never heard of GitHub must still get past base_sync.sh.

    Corrects a review finding (fix-1, found after the fact): with the defaults
    and no `gh` installed, the base-branch lookup was a traceback.
    """

    def run_without_gh(self, *args):
        env = {"PATH": self.path_without_gh()}
        return self.run_script("pilot_config.py", *args, env=env)

    def test_the_base_branch_is_the_current_one(self):
        self._git("checkout", "-q", "-b", "wip")
        out = self.run_without_gh("base-branch")
        self.assertEqual(out.stdout.strip(), "wip")
        self.assertNotIn("Traceback", out.stderr)

    def test_the_default_branch_is_empty(self):
        out = self.run_without_gh("default-branch")
        self.assertEqual((out.returncode, out.stdout), (0, "\n"))
        self.assertNotIn("Traceback", out.stderr)


class Exclude(PilotTestCase):
    def exclude(self, *paths, check=True):
        return self.run_script("pilot_config.py", "exclude", *paths, check=check)

    def tracking(self, path):
        return self.run_script("pilot_config.py", "tracking", path).stdout.strip()

    def excludes(self):
        path = self.repo / ".git" / "info" / "exclude"
        return path.read_text().splitlines() if path.exists() else []

    def test_a_file_and_a_directory_are_excluded_with_anchored_patterns(self):
        (self.repo / ".issue-pilot.json").write_text("{}")
        (self.repo / "issues").mkdir()
        (self.repo / "issues" / "0001-x.md").write_text("# x\n")
        out = self.exclude(".issue-pilot.json", "issues/")
        self.assertEqual(out.stdout.splitlines(),
                         ["excluded /.issue-pilot.json", "excluded /issues/"])
        self.assertIn("/.issue-pilot.json", self.excludes())
        self.assertIn("/issues/", self.excludes())
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual((self.tracking(".issue-pilot.json"), self.tracking("issues/"), self.tracking("issues")),
                         ("ignored", "ignored", "ignored"))

    def test_a_second_exclude_adds_nothing(self):
        self.exclude(".issue-pilot.json", "issues/")
        before = self.excludes()
        out = self.exclude(".issue-pilot.json", "issues/")
        self.assertEqual(out.stdout.splitlines(),
                         ["already excluded /.issue-pilot.json", "already excluded /issues/"])
        self.assertEqual(self.excludes(), before)

    def test_an_existing_directory_gets_a_directory_pattern_without_a_slash_given(self):
        (self.repo / "issues").mkdir()
        self.assertEqual(self.exclude("issues").stdout.strip(), "excluded /issues/")

    def test_the_info_directory_is_created_when_missing_and_existing_lines_are_kept(self):
        info = self.repo / ".git" / "info"
        for child in info.iterdir():
            child.unlink()
        info.rmdir()
        self.exclude("issues/")
        self.assertEqual(self.excludes(), ["/issues/"])
        self.exclude("notes.txt")
        self.assertEqual(self.excludes(), ["/issues/", "/notes.txt"])

    def test_a_pattern_is_added_after_a_last_line_with_no_newline(self):
        info = self.repo / ".git" / "info"
        info.mkdir(exist_ok=True)
        (info / "exclude").write_text("*.log")
        self.exclude("issues/")
        self.assertEqual(self.excludes(), ["*.log", "/issues/"])

    def test_a_path_git_tracks_is_refused_and_nothing_is_written(self):
        (self.repo / "issues").mkdir()
        (self.repo / "issues" / "0001-x.md").write_text("# x\n")
        self._git("add", "issues")
        self._git("commit", "-qm", "issue")
        before = self.excludes()
        out = self.exclude(".issue-pilot.json", "issues/", check=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("issues", out.stderr)
        self.assertIn("git rm -r --cached issues", out.stderr)
        # Not even the path that was fine: all or nothing.
        self.assertEqual(self.excludes(), before)
        self.assertEqual(self.tracking("issues/"), "versioned")

    def test_a_path_outside_the_repository_is_refused(self):
        out = self.exclude("../elsewhere", check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(self.excludes(), self.excludes())

    def test_tracking_says_versioned_for_anything_not_ignored(self):
        self.assertEqual(self.tracking("README.md"), "versioned")
        self.assertEqual(self.tracking("issues/"), "versioned")

    def test_everything_ignored_works_end_to_end_with_a_clean_status(self):
        # The configuration is written but not committed, and kept out of git:
        # the sync, the tracker and the stamp must all work with nothing to show.
        self.exclude(".issue-pilot.json", "issues/")
        (self.repo / ".issue-pilot.json").write_text(json.dumps(
            {"test_command": "true", "base_branch": "main", "tracker": "local", "landing": "merge"}))
        self._git("push", "-q", "origin", "main")
        self.assertEqual(self.run_script("pilot_config.py", "check").returncode, 0)
        sync = subprocess.run(["bash", str(SCRIPTS / "base_sync.sh")], cwd=self.repo,
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(sync.returncode, 0, sync.stderr)
        body = self.repo.parent / "body.md"
        body.write_text("Body.\n")
        created = self.run_script("issues_tracker.py", "create", "--title", "First",
                                  "--body-file", str(body)).stdout.splitlines()
        self.assertEqual(created, ["1", "issues/0001-first.md"])
        stamp = self.run_script("issue_base.py", "stamp").stdout.strip()
        self.assertEqual(stamp, "_Planned against `main` at `%s`._" % self.git("rev-parse", "main"))
        self.assertEqual(self.git("status", "--porcelain"), "")
        committed = self.run_script("issues_tracker.py", "commit", "-m", "x").stdout.strip()
        self.assertEqual(committed, "nothing to commit: issues are not kept in git here")
        # The file survives a checkout to another branch and back.
        self._git("checkout", "-q", "-b", "elsewhere")
        self._git("checkout", "-q", "main")
        self.assertTrue((self.repo / "issues" / "0001-first.md").exists())


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
