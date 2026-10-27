"""The hook that refuses /issue-pilot:plan with nothing to plan.

The plan is written from what was said.  With nothing said there is nothing to
write it from, and a model asked anyway will produce something plausible -- the
one outcome this workflow exists to rule out.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

HOOK = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "require_request.py"


class RequireRequest(unittest.TestCase):
    def setUp(self):
        # Nothing here may read or touch the real ~/.claude/issue-pilot: the
        # hook looks for the run state under ISSUE_PILOT_HOME, in the
        # repository the payload's `cwd` names.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name).resolve()
        self.home = root / "pilot-home"
        self.repo = root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("ISSUE_PILOT_")}
        self.env["ISSUE_PILOT_HOME"] = str(self.home)

    def hook(self, payload, cwd=True):
        if not isinstance(payload, str):
            payload = dict(payload)
            if cwd:
                payload.setdefault("cwd", str(self.repo))
            payload = json.dumps(payload)
        return subprocess.run([sys.executable, str(HOOK)], input=payload,
                              capture_output=True, text=True, env=self.env)

    def state_path(self):
        slug = str(self.repo).strip("/").replace("/", "-")
        return self.home / "state" / f"issues-run-{slug}.json"

    def write_state(self, pull_request=None, raw=None):
        path = self.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw if raw is not None
                        else json.dumps({"pull_request": pull_request}))

    def test_a_bare_command_is_refused_and_told_why(self):
        for prompt in ("/issue-pilot:plan", "/issue-pilot:plan   ",
                       "  /issue-pilot:plan\n"):
            with self.subTest(prompt=prompt):
                out = self.hook({"prompt": prompt})
                self.assertEqual(out.returncode, 2)          # 2 blocks the prompt
                self.assertIn("needs to be told what to plan", out.stderr)
                self.assertIn("/issue-pilot:plan add a", out.stderr)

    def test_update_without_an_issue_number_is_refused_too(self):
        out = self.hook({"prompt": "/issue-pilot:plan --update"})
        self.assertEqual(out.returncode, 2)

    def test_a_request_goes_through(self):
        for prompt in ("/issue-pilot:plan add a --format flag to export",
                       "/issue-pilot:plan --update 42",
                       "/issue-pilot:plan --update #42",
                       "/issue-pilot:plan\nsplit the reader out of the CLI"):
            with self.subTest(prompt=prompt):
                out = self.hook({"prompt": prompt})
                self.assertEqual(out.returncode, 0)
                self.assertEqual(out.stderr, "")

    def test_every_other_prompt_is_none_of_its_business(self):
        for prompt in ("/issue-pilot:run 1 2", "/issue-pilot:planning",
                       "/issue-pilot:issue-plan", "issue-plan", "hello", ""):
            with self.subTest(prompt=prompt):
                self.assertEqual(self.hook({"prompt": prompt}).returncode, 0)

    def test_a_bare_fix_is_refused_and_told_why(self):
        for prompt in ("/issue-pilot:fix", "/issue-pilot:fix   ",
                       "  /issue-pilot:fix\n"):
            with self.subTest(prompt=prompt):
                out = self.hook({"prompt": prompt})
                self.assertEqual(out.returncode, 2)          # 2 blocks the prompt
                self.assertIn("needs to be told what the review found", out.stderr)
                self.assertIn("/issue-pilot:fix the export flag", out.stderr)

    def test_a_fix_with_a_finding_goes_through(self):
        self.write_state(pull_request=None)
        for prompt in ("/issue-pilot:fix the export flag is ignored when --quiet is set",
                       "/issue-pilot:fix\nthe retry counter never resets"):
            with self.subTest(prompt=prompt):
                out = self.hook({"prompt": prompt})
                self.assertEqual(out.returncode, 0)
                self.assertEqual(out.stderr, "")

    def test_fix_has_no_update_exception(self):
        # --update rereads an existing issue; a fix has no issue to reread, so
        # this is just ordinary fix text and must go through, not be refused.
        self.write_state(pull_request=None)
        out = self.hook({"prompt": "/issue-pilot:fix --update 42"})
        self.assertEqual(out.returncode, 0)

    def test_fixing_is_not_the_fix_command(self):
        self.assertEqual(self.hook({"prompt": "/issue-pilot:fixing something"}).returncode, 0)

    def test_a_fix_with_no_run_state_is_refused_and_plan_suggested(self):
        out = self.hook({"prompt": "/issue-pilot:fix add a --format flag"})
        self.assertEqual(out.returncode, 2)
        self.assertIn("no finished run to fix", out.stderr)
        self.assertIn("/issue-pilot:plan add a --format flag", out.stderr)
        self.assertNotIn("pull request by hand", out.stderr)

    def test_a_fix_text_with_braces_is_echoed_untouched(self):
        out = self.hook({"prompt": "/issue-pilot:fix handle {} and {x} in the name"})
        self.assertEqual(out.returncode, 2)
        self.assertIn("/issue-pilot:plan handle {} and {x} in the name", out.stderr)

    def test_a_fix_on_a_run_with_a_pull_request_is_refused_too(self):
        self.write_state(pull_request={"number": 9, "url": "https://example.com/9"})
        out = self.hook({"prompt": "/issue-pilot:fix add a --format flag"})
        self.assertEqual(out.returncode, 2)
        self.assertIn("/issue-pilot:plan add a --format flag", out.stderr)
        self.assertIn("into that pull\n  request by hand", out.stderr)

    def test_a_fix_on_a_run_without_a_pull_request_goes_through(self):
        # Finished, or with pending/blocked items: fix.md step 1 tells those apart.
        self.write_state(pull_request=None)
        out = self.hook({"prompt": "/issue-pilot:fix add a --format flag"})
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stderr, "")

    def test_the_fix_is_looked_up_from_a_subdirectory_of_the_repository(self):
        self.write_state(pull_request={"number": 9})
        sub = self.repo / "pkg" / "deep"
        sub.mkdir(parents=True)
        out = self.hook({"prompt": "/issue-pilot:fix x", "cwd": str(sub)})
        self.assertEqual(out.returncode, 2)

    def test_any_doubt_about_the_repository_lets_a_fix_through(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        # No state at all would block in the repository, so these all show the
        # doubt is what lets the prompt through.
        cases = {
            "no cwd": {"prompt": "/issue-pilot:fix x"},
            "cwd null": {"prompt": "/issue-pilot:fix x", "cwd": None},
            "cwd not a string": {"prompt": "/issue-pilot:fix x", "cwd": 7},
            "cwd missing on disk": {"prompt": "/issue-pilot:fix x",
                                    "cwd": str(self.repo / "gone")},
            "cwd outside git": {"prompt": "/issue-pilot:fix x",
                                "cwd": outside.name},
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                out = self.hook(payload, cwd=False)
                self.assertEqual(out.returncode, 0)
                self.assertEqual(out.stderr, "")

    def test_a_state_file_it_cannot_read_lets_a_fix_through(self):
        for raw in ("{not json", "", "[]", "null"):
            with self.subTest(raw=raw):
                self.write_state(raw=raw)
                out = self.hook({"prompt": "/issue-pilot:fix x"})
                self.assertEqual(out.returncode, 0)
                self.assertEqual(out.stderr, "")

    def test_the_hook_never_creates_the_state_directory(self):
        self.hook({"prompt": "/issue-pilot:fix x"})
        self.hook({"prompt": "/issue-pilot:plan add a flag"})
        self.assertFalse(self.home.exists())

    def test_the_home_is_found_like_pilot_config_does(self):
        del self.env["ISSUE_PILOT_HOME"]
        self.env["CLAUDE_CONFIG_DIR"] = str(self.home.parent / "claude-dir")
        self.home = self.home.parent / "claude-dir" / "issue-pilot"
        self.write_state(pull_request={"number": 9})
        self.assertEqual(self.hook({"prompt": "/issue-pilot:fix x"}).returncode, 2)

    def test_a_bare_fix_and_plan_do_not_depend_on_the_state(self):
        self.write_state(pull_request={"number": 9})
        self.assertEqual(self.hook({"prompt": "/issue-pilot:fix"}).returncode, 2)
        self.assertIn("needs to be told what the review found",
                      self.hook({"prompt": "/issue-pilot:fix"}).stderr)
        self.assertEqual(self.hook({"prompt": "/issue-pilot:plan add a flag"}).returncode, 0)

    def test_a_payload_it_cannot_read_never_blocks_anything(self):
        for payload in ("{not json", "", json.dumps({"no": "prompt"}),
                        json.dumps({"prompt": None})):
            with self.subTest(payload=payload):
                self.assertEqual(self.hook(payload).returncode, 0)


if __name__ == "__main__":
    unittest.main()
