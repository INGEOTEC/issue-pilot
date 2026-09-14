"""The hook that refuses /issue-pilot:plan with nothing to plan.

The plan is written from what was said.  With nothing said there is nothing to
write it from, and a model asked anyway will produce something plausible -- the
one outcome this workflow exists to rule out.
"""
import json
import pathlib
import subprocess
import sys
import unittest

HOOK = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "require_request.py"


class RequireRequest(unittest.TestCase):
    def hook(self, payload):
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run([sys.executable, str(HOOK)], input=stdin,
                              capture_output=True, text=True)

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

    def test_a_payload_it_cannot_read_never_blocks_anything(self):
        for payload in ("{not json", "", json.dumps({"no": "prompt"}),
                        json.dumps({"prompt": None})):
            with self.subTest(payload=payload):
                self.assertEqual(self.hook(payload).returncode, 0)


if __name__ == "__main__":
    unittest.main()
