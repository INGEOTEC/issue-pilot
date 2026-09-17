"""install.sh, the plain-files route -- no coverage of it before this.

Confirms it ships `.claude-plugin/plugin.json` alongside `scripts/` and
`hooks/`, since a missing manifest is exactly what used to make the version
status line degrade to silence on this install path.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.sh"


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.pilot_home = root / "pilot-home"
        self.env = dict(os.environ)
        self.env.update({
            "CLAUDE_CONFIG_DIR": str(root / "claude-config"),
            "ISSUE_PILOT_HOME": str(self.pilot_home),
        })

    def install(self):
        # --hook is deliberately not passed: writing into settings.json is not
        # what this test is about, and the installer backs up and rewrites it.
        out = subprocess.run(["bash", str(INSTALL)], capture_output=True,
                             text=True, env=self.env)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out

    def test_install_ships_the_manifest_next_to_scripts_and_hooks(self):
        self.install()
        lib = self.pilot_home / "lib"
        self.assertTrue((lib / "scripts").is_dir())
        self.assertTrue((lib / "hooks").is_dir())
        manifest = lib / ".claude-plugin" / "plugin.json"
        self.assertTrue(manifest.exists())

        source_version = json.loads(
            (ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
        self.assertEqual(json.loads(manifest.read_text())["version"], source_version)

        out = subprocess.run(
            [sys.executable, str(lib / "scripts" / "pilot_config.py"), "version"],
            capture_output=True, text=True)
        self.assertEqual(out.stdout.splitlines()[0], source_version)
        self.assertEqual(out.stdout.splitlines()[1], str(lib))

    def test_reinstalling_leaves_exactly_one_manifest(self):
        self.install()
        self.install()
        manifests = list((self.pilot_home / "lib").rglob("plugin.json"))
        self.assertEqual(len(manifests), 1)
        self.assertEqual(
            json.loads(manifests[0].read_text())["version"],
            json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"])


if __name__ == "__main__":
    unittest.main()
