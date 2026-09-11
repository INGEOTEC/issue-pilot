"""Putting the repository on the base branch, at the tip origin has for it.

Planning and running both start from there, so this is the one place that
decides what "there" means: the local base branch brought up to origin, never
past it, and never over somebody's uncommitted or unpushed work.
"""
import pathlib
import subprocess
import unittest

from support import SCRIPTS, PilotTestCase

HELPER = SCRIPTS / "base_sync.sh"


class BaseSync(PilotTestCase):
    def setUp(self):
        super().setUp()
        self.write_config({"test_command": "true"})
        self._git("push", "-q", "origin", "main")

    def sync(self, check=True):
        out = subprocess.run(["bash", str(HELPER)], cwd=self.repo,
                             capture_output=True, text=True, env=self.env)
        if check and out.returncode != 0:
            self.fail(f"base_sync.sh failed:\n{out.stderr}")
        return out

    def land_on_origin(self, name, body="only on origin\n"):
        """Put a commit on origin/main that the local clone does not have."""
        other = pathlib.Path(self.tmp.name) / ("other-" + name)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)],
                       check=True, capture_output=True)
        (other / name).write_text(body)
        for cmd in (["git", "-c", "user.email=o@x", "-c", "user.name=o", "add", name],
                    ["git", "-c", "user.email=o@x", "-c", "user.name=o", "commit", "-qm", name],
                    ["git", "push", "-q", "origin", "HEAD:main"]):
            subprocess.run(cmd, cwd=other, check=True, capture_output=True)

    def test_moves_from_a_feature_branch_onto_the_base_at_origins_tip(self):
        self.land_on_origin("upstream.txt")
        self._git("checkout", "-q", "-b", "feat/elsewhere")
        (self.repo / "local.txt").write_text("only here\n")
        self._git("add", "local.txt")
        self._git("commit", "-qm", "local work")

        out = self.sync()
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(self.git("rev-parse", "main"), self.git("rev-parse", "origin/main"))
        self.assertEqual(out.stdout.strip(), self.git("rev-parse", "origin/main"))
        self.assertTrue((self.repo / "upstream.txt").exists())
        self.assertFalse((self.repo / "local.txt").exists())
        # The feature branch is where it was.
        self.assertIn("local.txt", self.git("ls-tree", "--name-only", "feat/elsewhere"))

    def test_a_stale_local_base_is_fast_forwarded(self):
        self.land_on_origin("later.txt")
        self._git("fetch", "-q", "origin")
        self.assertNotEqual(self.git("rev-parse", "main"), self.git("rev-parse", "origin/main"))
        self.sync()
        self.assertEqual(self.git("rev-parse", "main"), self.git("rev-parse", "origin/main"))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_a_missing_local_base_is_created_from_origin(self):
        self._git("checkout", "-q", "-b", "other")
        self._git("branch", "-D", "main")
        self.sync()
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(self.git("rev-parse", "main"), self.git("rev-parse", "origin/main"))

    def test_is_repeatable_and_follows_origin(self):
        first = self.sync().stdout.strip()
        self.land_on_origin("even-later.txt")
        second = self.sync().stdout.strip()
        self.assertNotEqual(first, second)
        self.assertEqual(second, self.git("rev-parse", "origin/main"))

    def test_a_dirty_tree_is_refused_and_the_way_out_is_named(self):
        self._git("checkout", "-q", "-b", "feat/elsewhere")
        (self.repo / "scratch.ipynb").write_text("{}")
        out = self.sync(check=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("scratch.ipynb", out.stderr)
        self.assertIn("git stash -u", out.stderr)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "feat/elsewhere")

    def test_unpushed_commits_on_the_base_stop_it_and_are_kept(self):
        (self.repo / "unpushed.txt").write_text("not on origin\n")
        self._git("add", "unpushed.txt")
        self._git("commit", "-qm", "unpushed work")
        out = self.sync(check=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("ahead of origin/main", out.stderr)
        self.assertIn("unpushed work", out.stderr)
        self.assertEqual(self.git("rev-list", "--count", "origin/main..main"), "1")

    def test_an_unreachable_origin_is_an_error_not_a_fallback(self):
        self._git("remote", "set-url", "origin", str(self.repo.parent / "gone.git"))
        out = self.sync(check=False)
        self.assertEqual(out.returncode, 1)
        self.assertIn("could not fetch", out.stderr)


if __name__ == "__main__":
    unittest.main()
