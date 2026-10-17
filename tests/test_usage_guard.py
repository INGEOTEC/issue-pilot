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

    def test_a_recorded_halt_stops_an_autonomous_session_at_once(self):
        # No probe, no five-minute grace: the very next tool call is refused.
        self.halt_file.write_text("7d window at 95%\n")
        out = self.guard(code="500", ISSUE_PILOT_RUN="1")
        self.assertEqual(out.returncode, 2)
        self.assertIn("halted", out.stderr)
        self.assertIn("--clear", out.stderr)

    def test_a_recorded_halt_leaves_an_interactive_session_alone(self):
        # A person can read the halt in /issue-pilot:status and decide; a hook that
        # locked them out of Claude Code entirely would be worse than no guard.
        self.halt_file.write_text("7d window at 95%\n")
        out = self.guard(headers=self.headers(**{"5h": (0.1, 3600)}))
        self.assertEqual(out.returncode, 0)

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

    # ---- --check reports the verdict of the same code hook mode acts on

    def check(self, headers, **env):
        out = self.guard("--check", headers=headers, **env)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)      # exactly one object, or this raises

    def verdicts(self, report):
        return {w["window"]: w for w in report["windows"]}

    def test_check_calls_a_quiet_short_and_a_quiet_long_window_ok(self):
        windows = self.verdicts(self.check(self.headers(**{
            "5h": (0.1, 3600), "7d": (0.2, 300000)})))
        self.assertEqual((windows["5h"]["scope"], windows["5h"]["verdict"],
                          windows["5h"]["wait_seconds"]), ("short", "ok", 0))
        self.assertEqual((windows["7d"]["scope"], windows["7d"]["verdict"],
                          windows["7d"]["wait_seconds"]), ("long", "ok", 0))

    def test_check_reports_a_wait_without_waiting_or_halting(self):
        out = self.guard("--check", headers=self.headers(**{"5h": (0.9, 3600)}))
        self.assertEqual(out.returncode, 0)
        window = json.loads(out.stdout)["windows"][0]
        self.assertEqual((window["scope"], window["verdict"]), ("short", "wait"))
        self.assertGreater(window["wait_seconds"], 0)
        self.assertFalse(self.halt_file.exists())

    def test_check_reports_a_halt_without_writing_one(self):
        out = self.guard("--check", headers=self.headers(**{"7d": (0.95, 300000)}))
        self.assertEqual(out.returncode, 0)
        window = json.loads(out.stdout)["windows"][0]
        self.assertEqual((window["scope"], window["verdict"]), ("long", "halt"))
        self.assertFalse(self.halt_file.exists())

    def test_the_long_action_shows_in_the_verdict(self):
        for action in ("warn", "ignore", "halt"):
            with self.subTest(action=action):
                report = self.check(self.headers(**{"7d": (0.95, 300000)}),
                                    USAGE_GUARD_LONG_ACTION=action)
                self.assertEqual(report["windows"][0]["verdict"], action)
                self.assertEqual(report["thresholds"]["long_action"], action)

    def test_lowering_the_threshold_flips_ok_to_wait(self):
        headers = self.headers(**{"5h": (0.5, 3600)})
        before = self.check(headers)
        after = self.check(headers, USAGE_GUARD_THRESHOLD="40")
        self.assertEqual(before["windows"][0]["verdict"], "ok")
        self.assertEqual(before["thresholds"]["short"], 70)
        self.assertEqual(after["windows"][0]["verdict"], "wait")
        self.assertEqual(after["thresholds"]["short"], 40)

    def test_the_long_threshold_and_max_wait_are_reported_as_applied(self):
        report = self.check(self.headers(**{"7d": (0.8, 300000)}),
                            USAGE_GUARD_LONG_THRESHOLD="75",
                            USAGE_GUARD_MAX_WAIT="1000")
        self.assertEqual(report["thresholds"], {
            "short": 70, "long": 75, "long_action": "halt", "max_wait": 1000})
        self.assertEqual(report["windows"][0]["verdict"], "halt")

    def test_wait_seconds_is_capped_by_max_wait(self):
        window = self.check(self.headers(**{"5h": (0.9, 3600)}),
                            USAGE_GUARD_MAX_WAIT="5000")["windows"][0]
        self.assertEqual(window["scope"], "short")
        self.assertTrue(3600 < window["wait_seconds"] <= 3660)
        capped = self.check(self.headers(**{"5h": (0.9, 3600)}),
                            USAGE_GUARD_MAX_WAIT="3600")["windows"][0]
        self.assertEqual(capped["wait_seconds"], 3600)

    def test_a_halt_on_record_is_inside_the_json(self):
        self.assertIsNone(self.check(self.headers(**{"5h": (0.1, 3600)}))["halt"])
        self.halt_file.write_text("7d window at 95%\n")
        report = self.check(self.headers(**{"5h": (0.1, 3600)}))
        self.assertEqual(report["halt"], "7d window at 95%")

    def test_check_with_nothing_measured_is_still_one_object(self):
        out = self.guard("--check", headers="HTTP/1.1 429\r\n\r\n", code="429")
        self.assertEqual(out.returncode, 0)
        self.assertEqual(json.loads(out.stdout)["windows"], [])

    def test_check_without_credentials_keeps_its_error_object(self):
        env = dict(self.env)
        env.pop("CLAUDE_CODE_OAUTH_TOKEN")
        out = subprocess.run(["bash", str(GUARD), "--check"], capture_output=True,
                             text=True, env={**env, "HOME": str(self.home)})
        self.assertEqual(out.returncode, 0)
        report = json.loads(out.stdout)
        self.assertIn("error", report)
        self.assertNotIn("windows", report)

    def test_both_modes_classify_a_reading_the_same_way(self):
        headers = self.headers(**{"7d": (0.95, 300000)})
        self.assertEqual(self.check(headers)["windows"][0]["verdict"], "halt")
        out = self.guard(headers=headers)
        self.assertEqual(out.returncode, 2)
        self.assertTrue(self.halt_file.exists())
        # and the other way round: what --check calls ok, hook mode lets through
        self.halt_file.unlink()
        quiet = self.headers(**{"7d": (0.2, 300000)})
        self.assertEqual(self.check(quiet)["windows"][0]["verdict"], "ok")
        self.assertEqual(self.guard(headers=quiet).returncode, 0)
        self.assertFalse(self.halt_file.exists())

    def test_the_usage_command_runs_the_guard_and_measures_nothing_itself(self):
        text = (GUARD.parent.parent / "commands" / "usage.md").read_text()
        self.assertIn("usage-guard.sh\" --check", text)
        self.assertNotIn("curl", text)
        self.assertNotIn("anthropic-ratelimit", text)

    # ---- review fix-1: the Keychain as a credential, /api/oauth/usage as a reading

    @staticmethod
    def iso(seconds_from_now):
        import datetime
        when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            seconds=seconds_from_now)
        return when.isoformat()

    def oauth_body(self, limits=True, session=12, weekly=4, scoped=3,
                   weekly_reset=500000):
        """The /api/oauth/usage answer, trimmed to what the guard reads."""
        body = {
            "five_hour": {"utilization": float(session), "resets_at": self.iso(3600)},
            "seven_day": {"utilization": float(weekly), "resets_at": self.iso(weekly_reset)},
            "seven_day_opus": None,
            "extra_usage": {"is_enabled": True, "utilization": None},
            "spend": {},
        }
        if limits:
            body["limits"] = [
                {"kind": "session", "group": "session", "percent": session,
                 "resets_at": self.iso(3600), "scope": None, "is_active": True},
                {"kind": "weekly_all", "group": "weekly", "percent": weekly,
                 "resets_at": self.iso(weekly_reset), "scope": None, "is_active": False},
                {"kind": "weekly_scoped", "group": "weekly", "percent": scoped,
                 "resets_at": self.iso(weekly_reset),
                 "scope": {"model": {"id": None, "display_name": "Fable"},
                           "surface": None}, "is_active": False},
            ]
        return json.dumps(body)

    def curl_run(self, *args, **env):
        self.curl_log = self.home / "curl.log"
        out = self.guard(*args, FAKE_CURL_LOG=str(self.curl_log), **env)
        self.assertEqual(out.returncode in (0, 2), True, out.stderr)
        return out

    def curl_calls(self):
        """Which endpoints were hit, in order."""
        if not self.curl_log.exists():
            return []
        return ["oauth" if "/api/oauth/usage" in line else "messages"
                for line in self.curl_log.read_text().splitlines()]

    def oauth_check(self, body=None, **env):
        body = self.oauth_body() if body is None else body
        out = self.curl_run("--check", FAKE_OAUTH_CODE="200", FAKE_OAUTH_BODY=body, **env)
        return json.loads(out.stdout)

    def test_oauth_usage_is_read_and_normalised_without_a_model_request(self):
        report = self.oauth_check()
        windows = self.verdicts(report)
        self.assertEqual(set(windows), {"5h", "7d", "7d_fable"})
        self.assertEqual([windows[n]["percent"] for n in ("5h", "7d", "7d_fable")],
                         [12, 4, 3])
        self.assertTrue(abs(windows["5h"]["seconds_until_reset"] - 3600) <= 10)
        self.assertTrue(abs(windows["7d"]["seconds_until_reset"] - 500000) <= 10)
        self.assertEqual(report["source"], "oauth_usage")
        self.assertEqual({w["verdict"] for w in report["windows"]}, {"ok"})
        self.assertEqual(self.curl_calls(), ["oauth"])

    def test_without_limits_the_top_level_windows_are_used(self):
        report = self.oauth_check(self.oauth_body(limits=False))
        windows = self.verdicts(report)
        self.assertEqual(set(windows), {"5h", "7d"})
        self.assertEqual((windows["5h"]["percent"], windows["7d"]["percent"]), (12, 4))
        self.assertEqual(report["source"], "oauth_usage")

    def test_a_token_that_cannot_read_oauth_usage_falls_back_to_the_headers(self):
        for code in ("403", "401"):
            with self.subTest(code=code):
                out = self.curl_run("--check", FAKE_OAUTH_CODE=code,
                                    FAKE_HEADERS=self.headers(**{"5h": (0.3, 3600)}))
                report = json.loads(out.stdout)
                self.assertEqual(report["source"], "headers")
                self.assertEqual(report["windows"][0]["percent"], 30)
                self.assertEqual(self.curl_calls(), ["oauth", "messages"])
                self.curl_log.unlink()

    def test_a_rate_limited_usage_endpoint_backs_off_and_does_not_probe_on(self):
        import time
        before = int(time.time())
        out = self.curl_run("--check", FAKE_OAUTH_CODE="429",
                            FAKE_OAUTH_HEADERS="HTTP/1.1 429\r\nretry-after: 120\r\n\r\n",
                            FAKE_HEADERS=self.headers(**{"5h": (0.3, 3600)}))
        report = json.loads(out.stdout)
        self.assertEqual(report["windows"], [])
        self.assertIsNone(report["source"])
        self.assertEqual(self.curl_calls(), ["oauth"])
        until = int((self.home / "usage-guard.backoff").read_text())
        self.assertTrue(before + 120 <= until <= int(time.time()) + 120)

    def test_the_source_can_be_pinned_to_the_headers(self):
        out = self.curl_run("--check", USAGE_GUARD_SOURCE="headers",
                            FAKE_OAUTH_CODE="200", FAKE_OAUTH_BODY=self.oauth_body(),
                            FAKE_HEADERS=self.headers(**{"5h": (0.3, 3600)}))
        report = json.loads(out.stdout)
        self.assertEqual(report["source"], "headers")
        self.assertEqual(self.curl_calls(), ["messages"])

    def test_the_source_can_be_pinned_to_oauth_usage(self):
        out = self.curl_run("--check", USAGE_GUARD_SOURCE="oauth", FAKE_OAUTH_CODE="403",
                            FAKE_HEADERS=self.headers(**{"5h": (0.3, 3600)}))
        report = json.loads(out.stdout)
        self.assertEqual(report["windows"], [])
        self.assertIsNone(report["source"])
        self.assertEqual(self.curl_calls(), ["oauth"])

    def test_both_readings_classify_the_same_numbers_the_same_way(self):
        via_oauth = self.oauth_check(self.oauth_body(session=85), USAGE_GUARD_MAX_WAIT="5000")
        via_headers = self.check(self.headers(**{"5h": (0.85, 3600)}),
                                 USAGE_GUARD_MAX_WAIT="5000")
        a, b = self.verdicts(via_oauth)["5h"], self.verdicts(via_headers)["5h"]
        self.assertEqual(a["verdict"], "wait")
        for key in ("percent", "scope", "verdict"):
            self.assertEqual(a[key], b[key])
        self.assertTrue(abs(a["wait_seconds"] - b["wait_seconds"]) <= 10)

    def test_hook_mode_halts_on_a_reading_from_oauth_usage(self):
        out = self.curl_run(FAKE_OAUTH_CODE="200",
                            FAKE_OAUTH_BODY=self.oauth_body(weekly=95))
        self.assertEqual(out.returncode, 2)
        self.assertTrue(self.halt_file.exists())
        self.assertIn("7d", self.halt_file.read_text())
        self.assertEqual(self.curl_calls(), ["oauth"])
        self.assertIn("reading via oauth usage", (self.home / "usage-guard.log").read_text())

    def test_hook_mode_says_which_reading_it_used_for_the_headers(self):
        self.guard(headers=self.headers(**{"5h": (0.1, 3600)}))
        self.assertIn("reading via headers", (self.home / "usage-guard.log").read_text())

    # ---- the macOS Keychain, always through the stubs: never the real one

    def keychain_env(self, uname="Darwin", found="1", token=None):
        self.install_stub("uname")
        self.install_stub("security")
        empty = self.home / "empty-home"
        empty.mkdir(exist_ok=True)
        self.security_log = self.home / "security.log"
        secret = json.dumps({"claudeAiOauth": {"accessToken": "keychain-token"}})
        env = dict(self.env)
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        env.update({"HOME": str(empty), "FAKE_UNAME": uname, "FAKE_SECURITY_FOUND": found,
                    "FAKE_SECURITY_SECRET": secret,
                    "FAKE_SECURITY_LOG": str(self.security_log)})
        self.env = env

    def security_calls(self):
        return (self.security_log.read_text().splitlines()
                if self.security_log.exists() else [])

    def test_the_keychain_supplies_the_token_on_macos(self):
        self.keychain_env()
        out = self.guard("--check", headers=self.headers(**{"5h": (0.3, 3600)}))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["windows"][0]["percent"], 30)
        calls = self.security_calls()
        self.assertEqual(len(calls), 1)
        self.assertIn("find-generic-password", calls[0])
        self.assertIn("Claude Code-credentials", calls[0])
        self.assertIn("-w", calls[0].split())

    def test_the_keychain_is_never_consulted_outside_macos(self):
        self.keychain_env(uname="Linux")
        out = self.guard("--check", headers=self.headers(**{"5h": (0.3, 3600)}))
        self.assertIn("error", json.loads(out.stdout))
        self.assertEqual(self.security_calls(), [])

    def test_a_missing_keychain_item_is_the_no_credentials_path(self):
        self.keychain_env(found="0")
        out = self.guard("--check", headers=self.headers(**{"5h": (0.3, 3600)}))
        self.assertEqual(out.returncode, 0)
        self.assertIn("error", json.loads(out.stdout))

    def test_a_keychain_secret_that_is_not_credentials_is_the_no_credentials_path(self):
        self.keychain_env()
        self.env["FAKE_SECURITY_SECRET"] = "not json"
        out = self.guard("--check", headers=self.headers(**{"5h": (0.3, 3600)}))
        self.assertEqual(out.returncode, 0)
        self.assertIn("error", json.loads(out.stdout))

    def test_a_token_variable_wins_without_asking_the_keychain(self):
        self.keychain_env(token="from-the-environment")
        out = self.guard("--check", headers=self.headers(**{"5h": (0.3, 3600)}))
        self.assertEqual(json.loads(out.stdout)["windows"][0]["percent"], 30)
        self.assertEqual(self.security_calls(), [])

    def test_the_usage_command_names_the_source_and_both_readings(self):
        text = (GUARD.parent.parent / "commands" / "usage.md").read_text()
        self.assertIn("source", text)
        self.assertIn("oauth_usage", text)
        self.assertIn("headers", text)


if __name__ == "__main__":
    unittest.main()
