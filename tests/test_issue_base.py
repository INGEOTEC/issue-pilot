"""The planning commit an issue carries, and what has landed since.

An issue is planned against the base branch on a given day and implemented on
another; the stamp is what lets the run side see the gap instead of guessing.
"""
import pathlib
import subprocess
import unittest

from support import SCRIPTS, PilotTestCase, issue

SYNC = SCRIPTS / "base_sync.sh"


class IssueBase(PilotTestCase):
    def setUp(self):
        super().setUp()
        self.write_config({"test_command": "true"})
        self._git("push", "-q", "origin", "main")

    def sync(self):
        subprocess.run(["bash", str(SYNC)], cwd=self.repo, check=True,
                       capture_output=True, env=self.env)

    def land_on_origin(self, name):
        other = pathlib.Path(self.tmp.name) / ("other-" + name)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)],
                       check=True, capture_output=True)
        (other / name).write_text("landed on origin\n")
        for cmd in (["git", "-c", "user.email=o@x", "-c", "user.name=o", "add", name],
                    ["git", "-c", "user.email=o@x", "-c", "user.name=o", "commit", "-qm", "add " + name],
                    ["git", "push", "-q", "origin", "HEAD:main"]):
            subprocess.run(cmd, cwd=other, check=True, capture_output=True)

    def stamp(self, check=True):
        return self.run_script("issue_base.py", "stamp", check=check)

    def drift(self, *issues, check=True):
        return self.run_script("issue_base.py", "drift", *[str(n) for n in issues], check=check)

    def test_the_stamp_names_the_base_branch_and_origins_tip(self):
        self.sync()
        line = self.stamp().stdout.strip()
        self.assertEqual(line, "_Planned against `main` at `%s`._" % self.git("rev-parse", "origin/main"))

    def test_the_stamp_refuses_to_lie_about_where_the_plan_was_read(self):
        self._git("checkout", "-q", "-b", "feat/elsewhere")
        (self.repo / "x.txt").write_text("x\n")
        self._git("add", "x.txt")
        self._git("commit", "-qm", "elsewhere")
        out = self.stamp(check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("base_sync.sh", out.stderr)

    def test_an_issue_planned_against_the_tip_is_current(self):
        self.sync()
        tip = self.git("rev-parse", "origin/main")
        self.write_issues({1: issue(1, "one", body="## Context\n\n_Planned against `main` at `%s`._" % tip)})
        out = self.drift(1).stdout
        self.assertIn("#1  planned against main at %s -- the current tip" % tip[:9], out)
        self.assertIn("every issue with a recorded commit was planned against the current main", out)

    def test_what_landed_since_the_plan_is_listed_with_its_files(self):
        self.sync()
        planned = self.git("rev-parse", "origin/main")
        self.write_issues({1: issue(1, "one", body="_Planned against `main` at `%s`._" % planned),
                           2: issue(2, "two", body="written by hand")})
        self.land_on_origin("reader.py")
        self.land_on_origin("cli.py")
        self.sync()
        out = self.drift(1, 2).stdout
        self.assertIn("2 commit(s) landed since", out)
        self.assertIn("add reader.py", out)
        self.assertIn("add cli.py", out)
        self.assertIn("files changed since then (2)", out)
        self.assertIn("reader.py", out)
        self.assertIn("#2  no planning commit recorded", out)
        self.assertIn("1 issue(s) were planned against code that has since changed", out)

    def test_a_commit_this_clone_does_not_have_is_said_so(self):
        self.sync()
        self.write_issues({1: issue(1, "one", body="_Planned against `main` at `%s`._" % ("f" * 40))})
        out = self.drift(1).stdout
        self.assertIn("a commit this clone does not have", out)

    def test_json_for_scripts(self):
        import json
        self.sync()
        tip = self.git("rev-parse", "origin/main")
        self.write_issues({1: issue(1, "one", body="_Planned against `main` at `%s`._" % tip)})
        data = json.loads(self.run_script("issue_base.py", "drift", "--json", "1").stdout)
        self.assertEqual(data["tip"], tip)
        self.assertEqual(data["issues"][0]["state"], "current")


if __name__ == "__main__":
    unittest.main()
