"""What the usage guard does with a reading.

The guard's whole job is to tell apart a window it can wait out from one it
cannot, so that is what these tests pin down.  `curl` is replaced by a stub that
writes whatever headers the test wants, which is the only way to reach the
interesting branches without waiting five hours for a real window to fill.
"""
import json
import pathlib
import subprocess
import unittest

from support import PilotTestCase

GUARD = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "usage-guard.sh"


class UsageGuard(PilotTestCase):
    def setUp(self):
        super().setUp()
        self.install_stub("curl")
        self.env["USAGE_GUARD_INTERVAL"] = "0"   # measure on every call
        self.home.mkdir(parents=True, exist_ok=True)

    def guard(self, *args, headers="", code="200", **env):
        return subprocess.run(
            ["bash", str(GUARD), *args], capture_output=True, text=True,
            env={**self.env, "FAKE_HEADERS": headers, "FAKE_CODE": code, **env})

    @staticmethod
    def headers(**windows):
        """headers(**{'5h': (0.1, 3600)}) -> the raw header block."""
        lines = ["HTTP/1.1 200 OK"]
        for window, (utilization, reset_in) in windows.items():
            import time
            lines.append(f"anthropic-ratelimit-unified-{window}-utilization: {utilization}")
            lines.append(f"anthropic-ratelimit-unified-{window}-reset: {int(time.time()) + reset_in}")
        return "\r\n".join(lines) + "\r\n\r\n"

    @property
    def halt_file(self):
        return self.home / "usage-guard.halt"

    def test_check_reports_every_window_the_api_mentions(self):
        out = self.guard("--check", headers=self.headers(**{
            "5h": (0.1, 3600), "7d": (0.42, 200000), "overage": (0.0, 900000)}))
        windows = {w["window"]: w for w in json.loads(out.stdout)["windows"]}
        self.assertEqual(set(windows), {"5h", "7d", "overage"})
        self.assertEqual(windows["5h"]["percent"], 10)
        self.assertEqual(windows["7d"]["percent"], 42)

    def test_check_never_blocks(self):
        out = self.guard("--check", headers=self.headers(**{"7d": (0.99, 500000)}))
        self.assertEqual(out.returncode, 0)
        self.assertFalse(self.halt_file.exists())

    def test_a_percentage_is_understood_as_well_as_a_fraction(self):
        out = self.guard("--check", headers=self.headers(**{"5h": (37, 3600)}))
        windows = json.loads(out.stdout)["windows"]
        self.assertEqual(windows[0]["percent"], 37)

    def test_a_quiet_window_lets_the_tool_call_through(self):
        out = self.guard(headers=self.headers(**{"5h": (0.1, 3600),
                                                 "7d": (0.2, 200000)}))
        self.assertEqual(out.returncode, 0)
        self.assertFalse(self.halt_file.exists())

    def test_a_nearly_exhausted_weekly_window_halts_instead_of_sleeping(self):
        # Sleeping until a weekly reset is not a pause, it is a hang.
        out = self.guard(headers=self.headers(**{"5h": (0.1, 3600),
                                                 "7d": (0.95, 300000)}))
        self.assertEqual(out.returncode, 2)          # 2 blocks the tool call
        self.assertIn("7d", out.stderr)
        self.assertTrue(self.halt_file.exists())
        self.assertIn("7d", self.halt_file.read_text())

    def test_the_long_window_action_can_be_turned_off(self):
        out = self.guard(headers=self.headers(**{"7d": (0.95, 300000)}),
                         USAGE_GUARD_LONG_ACTION="ignore")
        self.assertEqual(out.returncode, 0)
        self.assertFalse(self.halt_file.exists())

    def test_clear_forgets_a_halt(self):
        self.guard(headers=self.headers(**{"7d": (0.95, 300000)}))
        self.assertTrue(self.halt_file.exists())
        out = self.guard("--clear")
        self.assertEqual(out.returncode, 0)
        self.assertFalse(self.halt_file.exists())

    def test_a_short_window_over_threshold_waits_it_out(self):
        # MAX_WAIT is what bounds the sleep, so the test sets it to a second
        # and asserts on the log rather than on the clock.
        out = self.guard(headers=self.headers(**{"5h": (0.9, 1)}),
                         USAGE_GUARD_MAX_WAIT="1")
        self.assertEqual(out.returncode, 0)
        self.assertIn("throttling", (self.home / "usage-guard.log").read_text())

    def test_a_failed_probe_never_breaks_the_session(self):
        for code in ("401", "500", "429"):
            with self.subTest(code=code):
                out = self.guard(headers="HTTP/1.1 %s\r\n\r\n" % code, code=code)
                self.assertEqual(out.returncode, 0)

    def test_without_credentials_it_gets_out_of_the_way(self):
        env = dict(self.env)
        env.pop("CLAUDE_CODE_OAUTH_TOKEN")
        # HOME too, or it would find the developer's own credentials file.
        out = subprocess.run(["bash", str(GUARD)], capture_output=True, text=True,
                             env={**env, "HOME": str(self.home)})
        self.assertEqual(out.returncode, 0)


if __name__ == "__main__":
    unittest.main()
