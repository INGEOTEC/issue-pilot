"""The worktree the planning side reads code from.

A plan has to be written against the code the work will land on -- the tip of
the base branch on origin -- and not against whatever happens to be checked out,
which during real use is a feature branch or a stale base.
"""
import pathlib
import subprocess
import unittest

from support import SCRIPTS, PilotTestCase

HELPER = SCRIPTS / "base_worktree.sh"


class BaseWorktree(PilotTestCase):
    def setUp(self):
        super().setUp()
        self.write_config({"test_command": "true"})
        self._git("push", "-q", "origin", "main")

    def helper(self, *args, check=True):
        out = subprocess.run(["bash", str(HELPER), *args], cwd=self.repo,
                             capture_output=True, text=True, env=self.env)
        if check and out.returncode != 0:
            self.fail(f"base_worktree.sh {' '.join(args)} failed:\n{out.stderr}")
        return out

    def land_on_origin(self, name, body):
        """Put a commit on origin/main that the local clone does not have."""
        other = pathlib.Path(self.tmp.name) / "other"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)],
                       check=True, capture_output=True)
        (other / name).write_text(body)
        for cmd in (["git", "-c", "user.email=o@x", "-c", "user.name=o", "add", name],
                    ["git", "-c", "user.email=o@x", "-c", "user.name=o", "commit", "-qm", name],
                    ["git", "push", "-q", "origin", "HEAD:main"]):
            subprocess.run(cmd, cwd=other, check=True, capture_output=True)

    def test_the_worktree_is_origins_tip_not_the_local_checkout(self):
        self.land_on_origin("upstream.txt", "only on origin\n")
        self._git("checkout", "-q", "-b", "feat/elsewhere")
        (self.repo / "local.txt").write_text("only here\n")
        self._git("add", "local.txt")
        self._git("commit", "-qm", "local work")

        path = pathlib.Path(self.helper("add").stdout.strip())
        self.assertTrue((path / "upstream.txt").exists())
        self.assertFalse((path / "local.txt").exists())
        # And the caller's own checkout was not moved.
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "feat/elsewhere")

    def test_the_worktree_lives_outside_the_repository(self):
        path = pathlib.Path(self.helper("add").stdout.strip())
        self.assertTrue(str(path).startswith(str(self.home)))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_add_is_repeatable_and_follows_origin(self):
        first = self.helper("add").stdout.strip()
        self.land_on_origin("later.txt", "landed later\n")
        second = self.helper("add").stdout.strip()
        self.assertEqual(first, second)
        self.assertTrue((pathlib.Path(second) / "later.txt").exists())

    def test_remove_cleans_up(self):
        path = pathlib.Path(self.helper("add").stdout.strip())
        self.helper("remove")
        self.assertFalse(path.exists())
        self.assertNotIn(str(path), self.git("worktree", "list"))

    def test_an_unreachable_origin_is_an_error_not_a_fallback(self):
        self._git("remote", "set-url", "origin", str(self.repo.parent / "gone.git"))
        out = self.helper("add", check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("could not fetch", out.stderr)


if __name__ == "__main__":
    unittest.main()
