"""The release tool: tags and GitHub releases for the versions that have none.

It runs inside a throwaway repository with a bare `origin`, against the fake
`gh`.  `publish` must never be run in the checkout itself: it is the real GitHub
repository, and it would publish every missing release.
"""
import json
import pathlib
import subprocess
import sys

from support import ROOT, PilotTestCase

TOOL = ROOT / ".github" / "scripts" / "release.py"


def changelog(*versions):
    return "# Changelog\n\n## [Unreleased]\n\n" + "".join(
        "## [%s] - 2026-01-01\n\n### Added\n\n- version %s\n\n" % (v, v)
        for v in reversed(versions))


class ReleaseTest(PilotTestCase):

    def release(self, *args, check=True):
        out = subprocess.run([sys.executable, str(TOOL), *args], cwd=self.repo,
                             capture_output=True, text=True, env=self.env)
        if check and out.returncode != 0:
            self.fail("release.py %s failed (%d):\n%s" % (" ".join(args), out.returncode,
                                                        out.stderr))
        return out

    def commit_version(self, version, sections=None, message=None):
        """Commit a manifest at `version` and a changelog with `sections`."""
        (self.repo / ".claude-plugin").mkdir(exist_ok=True)
        (self.repo / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"version": version}))
        (self.repo / "CHANGELOG.md").write_text(changelog(*(sections or [version])))
        self._git("add", "-A")
        self._git("commit", "-qm", message or "release %s" % version)
        return self.git("rev-parse", "HEAD")

    def history(self):
        """0.1.0, 0.2.0 and 0.3.0 in three commits, with only v0.1.0 tagged."""
        shas = {}
        for version in ("0.1.0", "0.2.0", "0.3.0"):
            shas[version] = self.commit_version(
                version, sections=[v for v in ("0.1.0", "0.2.0", "0.3.0") if v <= version])
        self._git("tag", "-a", "v0.1.0", "-m", "x", shas["0.1.0"])
        self._git("push", "-q", "origin", "main", "v0.1.0")
        return shas

    def origin_tags(self):
        return subprocess.run(["git", "-C", str(self.origin), "tag", "-l"], text=True,
                              capture_output=True).stdout.split()

    def releases(self):
        log = pathlib.Path(self.env["FAKE_GH_RELEASE_LOG"])
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    def test_notes_of_the_real_changelog(self):
        out = subprocess.run([sys.executable, str(TOOL), "notes", "0.6.0"], cwd=ROOT,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.startswith("### "), out.stdout[:60])
        self.assertNotIn("## [", out.stdout)
        self.assertNotIn("0.7.0", out.stdout.splitlines()[0])
        missing = subprocess.run([sys.executable, str(TOOL), "notes", "9.9.9"], cwd=ROOT,
                                 capture_output=True, text=True)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("CHANGELOG.md has no section for 9.9.9", missing.stderr)

    def test_the_current_version_has_a_changelog_section(self):
        version = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
        out = subprocess.run([sys.executable, str(TOOL), "notes", version], cwd=ROOT,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_versions_and_pending(self):
        shas = self.history()
        listed = self.release("versions").stdout.splitlines()
        self.assertEqual(listed, ["%s %s" % (v, shas[v]) for v in ("0.1.0", "0.2.0", "0.3.0")])
        self.assertEqual(self.release("pending").stdout.splitlines(),
                         ["0.3.0 " + shas["0.3.0"]])
        self.assertEqual(self.release("pending", "--all").stdout.splitlines(),
                         ["0.2.0 " + shas["0.2.0"], "0.3.0 " + shas["0.3.0"]])

    def test_a_reverted_version_is_listed_once_at_its_first_commit(self):
        shas = self.history()
        self.commit_version("0.2.0", sections=["0.1.0", "0.2.0", "0.3.0"])
        versions = [l.split()[0] for l in self.release("versions").stdout.splitlines()]
        self.assertEqual(versions, ["0.1.0", "0.2.0", "0.3.0"])
        self.assertIn("0.2.0 " + shas["0.2.0"], self.release("versions").stdout)

    def test_publish_the_current_version(self):
        shas = self.history()
        out = self.release("publish")
        self.assertEqual(out.stdout.strip(), "released v0.3.0 at " + shas["0.3.0"][:7])
        self.assertEqual(self.origin_tags(), ["v0.1.0", "v0.3.0"])
        self.assertEqual(self.git("cat-file", "-t", "v0.3.0"), "tag")
        self.assertEqual(self.git("rev-parse", "v0.3.0^{commit}"), shas["0.3.0"])
        self.assertEqual(subprocess.run(
            ["git", "-C", str(self.origin), "rev-parse", "v0.3.0^{commit}"],
            text=True, capture_output=True).stdout.strip(), shas["0.3.0"])
        (call,) = self.releases()
        self.assertEqual(call["args"][0], "v0.3.0")
        self.assertIn("--verify-tag", call["args"])
        self.assertEqual(call["args"][call["args"].index("--title") + 1], "issue-pilot 0.3.0")
        self.assertIn("--latest=true", call["args"])
        self.assertIn("- version 0.3.0", call["notes"])
        self.assertNotIn("## [", call["notes"])
        self.assertNotIn("version 0.2.0", call["notes"])
        again = self.release("publish")
        self.assertIn("nothing to release", again.stdout)
        self.assertEqual(len(self.releases()), 1)

    def test_publish_all_goes_oldest_first_and_only_the_highest_is_latest(self):
        shas = self.history()
        out = self.release("publish", "--all")
        self.assertEqual(out.stdout.splitlines(),
                         ["released v0.2.0 at " + shas["0.2.0"][:7],
                          "released v0.3.0 at " + shas["0.3.0"][:7]])
        for version in ("0.2.0", "0.3.0"):
            self.assertEqual(self.git("rev-parse", "v%s^{commit}" % version), shas[version])
            self.assertEqual(self.git("cat-file", "-t", "v" + version), "tag")
        first, second = self.releases()
        self.assertEqual(first["args"][0], "v0.2.0")
        self.assertIn("--latest=false", first["args"])
        self.assertEqual(second["args"][0], "v0.3.0")
        self.assertIn("--latest=true", second["args"])
        self.assertIn("nothing to release: every version has a tag",
                      self.release("publish", "--all").stdout)

    def test_versions_compare_as_numbers(self):
        self.commit_version("0.9.0")
        self.commit_version("0.10.0", sections=["0.9.0", "0.10.0"])
        self.release("publish", "--all")
        calls = {c["args"][0]: c["args"] for c in self.releases()}
        self.assertIn("--latest=true", calls["v0.10.0"])
        self.assertIn("--latest=false", calls["v0.9.0"])

    def test_a_missing_changelog_section_creates_nothing(self):
        self.history()
        self.commit_version("0.4.0", sections=["0.1.0", "0.2.0", "0.3.0"])
        out = self.release("publish", check=False)
        self.assertEqual(out.returncode, 1)
        self.assertIn("0.4.0", out.stderr)
        self.assertEqual(self.git("tag", "-l"), "v0.1.0")
        self.assertEqual(self.origin_tags(), ["v0.1.0"])
        self.assertEqual(self.releases(), [])

    def test_a_missing_section_in_a_backfill_names_every_version_and_creates_nothing(self):
        self.commit_version("0.1.0")
        self.commit_version("0.2.0", sections=["0.1.0"])
        self.commit_version("0.3.0", sections=["0.3.0"])
        out = self.release("publish", "--all", check=False)
        self.assertEqual(out.returncode, 1)
        self.assertIn("0.2.0", out.stderr)
        self.assertEqual(self.git("tag", "-l"), "")
        self.assertEqual(self.releases(), [])

    def test_dry_run_creates_nothing(self):
        shas = self.history()
        out = self.release("publish", "--all", "--dry-run")
        self.assertEqual(out.stdout.splitlines(),
                         ["would release v0.2.0 at " + shas["0.2.0"][:7],
                          "would release v0.3.0 at " + shas["0.3.0"][:7]])
        self.assertEqual(self.git("tag", "-l"), "v0.1.0")
        self.assertEqual(self.origin_tags(), ["v0.1.0"])
        self.assertEqual(self.releases(), [])

    def test_a_commit_that_does_not_change_the_version_releases_nothing(self):
        self.history()
        self.release("publish")
        (self.repo / "other.txt").write_text("x\n")
        self._git("add", "other.txt")
        self._git("commit", "-qm", "unrelated")
        out = self.release("publish")
        self.assertIn("nothing to release: v0.3.0 already exists", out.stdout)
        self.assertEqual(len(self.releases()), 1)

    def test_a_failed_release_keeps_the_tag_and_says_how_to_finish(self):
        self.history()
        out = subprocess.run([sys.executable, str(TOOL), "publish"], cwd=self.repo,
                             capture_output=True, text=True,
                             env={**self.env, "FAKE_GH_RELEASE_FAIL": "1"})
        self.assertEqual(out.returncode, 1)
        self.assertIn("gh release create v0.3.0 --verify-tag", out.stderr)
        self.assertIn("v0.3.0", self.origin_tags())


class WorkflowTextTest(PilotTestCase):
    """No YAML parser in the standard library: the text is what is checked."""

    def test_the_release_workflow(self):
        text = (ROOT / ".github" / "workflows" / "release.yml").read_text()
        for needle in ("branches: [main]", "workflow_dispatch:", "backfill:",
                       "type: boolean", "contents: write", "fetch-depth: 0",
                       "release.py publish --all", "release.py publish\n",
                       "BACKFILL: ${{ inputs.backfill }}"):
            self.assertIn(needle, text)
        # The input goes through the environment, never into the script itself.
        script = text[text.index("run: |\n          if"):]
        self.assertNotIn("${{", script)

    def test_ci_checks_that_the_version_has_a_changelog_section(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("The version in plugin.json must have a CHANGELOG section", text)
        self.assertIn('release.py notes "$version"', text)
