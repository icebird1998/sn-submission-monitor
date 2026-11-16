"""Offline regression tests: no real credentials, browsers, SMTP or submission requests."""
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from submission_monitor.app import App, EXIT_CHECK, EXIT_DELIVERY, safe_url
from submission_monitor.cli import main
from submission_monitor.config import load_config
from submission_monitor.errors import ConfigError, StateError
from submission_monitor.storage import atomic_json, read_state, run_lock

ROOT = Path(__file__).resolve().parents[1]


def configuration(platform="em"):
    result = {
        "platform": platform,
        "em": {"base": "https://www.editorialmanager.com/example", "email": "author@example.test",
               "password": "private-test-password", "watch": ["EXAMPLE-D-26-001"]},
        "sn": {"email": "author@example.test", "password": "private-test-password", "watch": "all"},
        "mts": {"base": "https://mts-ncomms.nature.com", "email": "author@example.test",
                "password": "private-test-password", "watch": "single", "ms_id": "123456"},
        "notify": {"toast": False, "email": {"enabled": True, "smtp_host": "smtp.example.test",
                    "smtp_user": "sender@example.test", "smtp_auth_code": "private-test-smtp-code",
                    "smtp_port": 465, "to": "receiver@example.test"}},
        "fail_alert_after": 2,
    }
    return result


def row(status="With Editor", number="EXAMPLE-D-26-001", date="Nov 1, 2026"):
    return {"number": number, "title": "Synthetic manuscript", "status": status,
            "status_date": date, "journal": "Synthetic journal", "url": "https://www.editorialmanager.com/example/Default.aspx",
            "signature": status + "|" + date}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.path = self.directory / "config.json"
        self.cfg = configuration()
        self.path.write_text(json.dumps(self.cfg), encoding="utf-8")
        self.app = self.create_app()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.app.close)

    def create_app(self):
        return App(SimpleNamespace(config=str(self.path), toast=True, headed=False))

    def write_cfg(self):
        self.path.write_text(json.dumps(self.cfg), encoding="utf-8")

    def collect(self, status="With Editor", number="EXAMPLE-D-26-001", date="Nov 1, 2026"):
        return lambda app, platform: {number: row(status, number, date)}

    @patch("submission_monitor.app.send_email")
    def test_first_run_is_silent_then_change_and_unchanged(self, send):
        self.assertEqual(self.app.check(self.collect()), 0)
        send.assert_not_called()
        self.assertEqual(self.app.check(self.collect("Under Review")), 0)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.app.check(self.collect("Under Review")), 0)
        self.assertEqual(send.call_count, 1)
        state = self.app.load_state()
        self.assertFalse(state["_outbox"])
        self.assertEqual(state["_meta"]["consecutive_failures"], 0)

    def test_failed_email_survives_process_and_retries_without_change(self):
        self.app.check(self.collect())
        with patch("submission_monitor.app.send_email", side_effect=OSError("temporary SMTP failure")):
            self.assertEqual(self.app.check(self.collect("Under Review")), EXIT_DELIVERY)
        state = self.app.load_state()
        self.assertEqual(state["em"]["EXAMPLE-D-26-001"]["status"], "Under Review")
        self.assertEqual(len(state["_outbox"]), 1)
        self.assertEqual(state["_outbox"][0]["channels"]["email"]["attempts"], 1)
        app2 = self.create_app()
        self.addCleanup(app2.close)
        with patch("submission_monitor.app.send_email") as send:
            self.assertEqual(app2.check(self.collect("Under Review")), 0)
            send.assert_called_once()
        self.assertEqual(app2.load_state()["_outbox"], [])

    def test_toast_success_does_not_hide_email_failure(self):
        self.app.cfg["notify"]["toast"] = True
        self.app.check(self.collect())
        with patch("submission_monitor.app.sys.platform", "win32"), patch("submission_monitor.app.send_toast") as toast, \
                patch("submission_monitor.app.send_email", side_effect=OSError("SMTP down")):
            self.assertEqual(self.app.check(self.collect("Under Review")), EXIT_DELIVERY)
            toast.assert_called_once()
        state = self.app.load_state()
        self.assertTrue(state["_outbox"][0]["channels"]["toast"]["done"])
        self.assertFalse(state["_outbox"][0]["channels"]["email"]["done"])
        with patch("submission_monitor.app.sys.platform", "win32"), patch("submission_monitor.app.send_toast") as toast, \
                patch("submission_monitor.app.send_email"):
            self.assertEqual(self.app.check(self.collect("Under Review")), 0)
            toast.assert_not_called()

    def test_missing_or_empty_status_never_erases_baseline(self):
        self.app.check(self.collect())
        good = self.app.load_state()["em"]
        self.assertEqual(self.app.check(lambda *_: {"id": {"status": " "}}), EXIT_CHECK)
        self.assertEqual(self.app.load_state()["em"], good)

    def test_failure_and_recovery_alerts_once_per_episode(self):
        self.app.check(self.collect())
        def failed(*_):
            raise RuntimeError("No connection https://id.elsevier.com/callback?token=private")
        with patch("submission_monitor.app.send_email") as send:
            self.assertEqual(self.app.check(failed), EXIT_CHECK)
            send.assert_not_called()
            self.assertEqual(self.app.check(failed), EXIT_CHECK)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(self.app.check(failed), EXIT_CHECK)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(self.app.check(self.collect()), 0)
            self.assertEqual(send.call_count, 2)
        self.assertEqual(self.app.load_state()["_meta"]["consecutive_failures"], 0)

    def test_two_platforms_fail_independently(self):
        self.cfg["platform"] = ["em", "mts"]
        self.write_cfg()
        self.app.cfg = load_config(self.path)
        counts = {"em": 0, "mts": 0}
        def collect(app, platform):
            counts[platform] += 1
            if platform == "em":
                raise RuntimeError("EM down")
            return {"123456": row(number="123456")}
        self.assertEqual(self.app.check(collect), EXIT_CHECK)
        self.assertEqual(counts, {"em": 1, "mts": 1})
        self.assertTrue(self.app.load_state()["mts"])
        self.assertFalse(self.app.load_state()["em"])

    @patch("submission_monitor.app.send_email")
    def test_new_submission_after_initial_baseline_is_not_silent(self, send):
        self.app.cfg["em"]["watch"] = "all"
        self.app.check(self.collect())
        def expanded(*_):
            return {"EXAMPLE-D-26-001": row(), "EXAMPLE-D-26-002": row(number="EXAMPLE-D-26-002")}
        self.assertEqual(self.app.check(expanded), 0)
        send.assert_called_once()
        self.assertIn("新发现投稿", send.call_args.args[2])

    @patch("submission_monitor.app.send_email")
    def test_same_title_number_assignment_and_revisions(self, send):
        self.app.cfg["em"]["watch"] = "all"
        self.app.check(self.collect(number=""))
        self.app.check(self.collect(number="EXAMPLE-D-26-001"))
        self.assertEqual(len(self.app.load_state()["em"]), 1)
        send.assert_not_called()
        self.app.check(self.collect("Under Review", "EXAMPLE-D-26-001R1"))
        self.assertEqual(len(self.app.load_state()["em"]), 1)
        send.assert_called_once()

    def test_legacy_baseline_migration_does_not_notify(self):
        item = row()
        state = {"sn": {}, "mts": {}, "em": {"EXAMPLE-D-26-001": item},
                 "_meta": {"consecutive_failures": 0, "alert_sent": False}}
        atomic_json(self.app.state_file, state)
        with patch("submission_monitor.app.send_email") as send:
            self.assertEqual(self.app.check(self.collect()), 0)
            send.assert_not_called()
        self.assertEqual(self.app.load_state()["schema_version"], 2)

    def test_scope_switch_resets_baseline_and_cancels_old_unsent_private_events(self):
        self.app.check(self.collect())
        with patch("submission_monitor.app.send_email", side_effect=OSError("down")):
            self.app.check(self.collect("Under Review"))
        self.app.cfg["em"]["base"] = "https://www.editorialmanager.com/another"
        with patch("submission_monitor.app.send_email") as send:
            self.assertEqual(self.app.check(self.collect("With Editor")), 0)
            send.assert_not_called()
        self.assertEqual(self.app.load_state()["_outbox"], [])

    def test_changed_recipient_does_not_receive_queued_history(self):
        self.app.check(self.collect())
        with patch("submission_monitor.app.send_email", side_effect=OSError("down")):
            self.app.check(self.collect("Under Review"))
        self.app.cfg["notify"]["email"]["to"] = ["other@example.test"]
        with patch("submission_monitor.app.send_email") as send:
            self.assertEqual(self.app.check(self.collect("Under Review")), 0)
            send.assert_not_called()

    def test_test_email_never_changes_state(self):
        self.app.check(self.collect())
        before = self.app.state_file.read_bytes()
        with patch("submission_monitor.app.send_email") as send:
            self.app.test_email()
        send.assert_called_once()
        self.assertEqual(self.app.state_file.read_bytes(), before)

    def test_no_channels_only_logs_and_does_not_claim_smtp_success(self):
        self.app.cfg["notify"]["email"]["enabled"] = False
        self.app.check(self.collect())
        with patch("submission_monitor.app.send_email") as send:
            self.assertEqual(self.app.check(self.collect("Under Review")), 0)
            send.assert_not_called()
        self.assertFalse(self.app.load_state()["_outbox"])

    def test_reset_keeps_outbox_and_requires_cli_confirmation(self):
        self.app.check(self.collect())
        with patch("submission_monitor.app.send_email", side_effect=OSError("down")):
            self.app.check(self.collect("Under Review"))
        before = self.app.load_state()["_outbox"]
        self.assertEqual(main(["--config", str(self.path), "--reset"]), 2)
        self.assertTrue(self.app.load_state()["em"])
        self.app.reset()
        self.assertEqual(self.app.load_state()["_outbox"], before)
        self.assertFalse(self.app.load_state()["em"])

    def test_sensitive_values_and_auth_queries_are_redacted(self):
        value = "private-test-password author@example.test private-test-smtp-code " \
                "https://id.elsevier.com/login?state=secret&code=private " \
                "https://www.editorialmanager.com/example/default.aspx?token=secret"
        output = self.app.redact(value)
        for forbidden in ("private-test-password", "author@example.test", "private-test-smtp-code", "state=secret", "token=secret"):
            self.assertNotIn(forbidden, output)

    def test_corrupt_state_fails_closed_without_overwrite(self):
        self.app.state_file.write_text('{"broken":', encoding="utf-8")
        with self.assertRaises(StateError):
            self.app.check(self.collect())
        self.assertEqual(self.app.state_file.read_text(encoding="utf-8"), '{"broken":')

    def test_os_lock_excludes_another_process_and_recovers(self):
        code = "from submission_monitor.storage import run_lock; import sys; " \
               "\ntry:\n with run_lock(sys.argv[1]): pass\nexcept Exception as e:\n print(type(e).__name__); sys.exit(4)"
        with run_lock(self.app.lock_file):
            process = subprocess.run([sys.executable, "-c", code, str(self.app.lock_file)],
                                     cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(process.returncode, 4, process.stderr)
            self.assertIn("AlreadyRunning", process.stdout)
        with run_lock(self.app.lock_file):
            pass

    def test_atomic_write_failure_preserves_original(self):
        atomic_json(self.app.state_file, {"value": 1})
        with patch("submission_monitor.storage.os.replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                atomic_json(self.app.state_file, {"value": 2})
        self.assertEqual(json.loads(self.app.state_file.read_text(encoding="utf-8")), {"value": 1})
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_config_checks_and_environment_override(self):
        self.cfg["em"]["password"] = ""
        self.write_cfg()
        with self.assertRaises(ConfigError):
            load_config(self.path)
        with patch.dict(os.environ, {"SNM_EM_PASSWORD": "from-env"}):
            loaded = load_config(self.path)
        self.assertEqual(loaded["em"]["password"], "from-env")
        self.assertEqual(self.cfg["em"]["password"], "")

    def test_config_rejects_unsafe_hosts_platform_and_interval(self):
        for change in ({"platform": "unknown"}, {"interval_minutes": 1}):
            bad = copy.deepcopy(self.cfg)
            bad.update(change)
            self.path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(self.path)
        for url in ("http://www.editorialmanager.com/example", "https://attacker.invalid/example",
                    "https://www.editorialmanager.com/example?key=private"):
            bad = copy.deepcopy(self.cfg)
            bad["em"]["base"] = url
            self.path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(self.path)

    def test_cli_error_returns_nonzero_and_mts_has_no_browser_dependency(self):
        self.assertEqual(main(["--config", str(self.directory / "missing.json"), "--once"]), 2)
        self.cfg["platform"] = "mts"
        self.write_cfg()
        with patch("submission_monitor.cli.doctor", return_value=0):
            self.assertEqual(main(["--config", str(self.path), "--doctor"]), 0)
        self.assertEqual(main(["--config", str(self.path), "--loop", "0"]), 2)

    def test_core_scope_excludes_other_manuscripts_and_rejects_missing_target(self):
        def extra(*_):
            return {"EXAMPLE-D-26-001": row(), "EXAMPLE-D-26-002": row(number="EXAMPLE-D-26-002")}
        self.assertEqual(self.app.check(extra), 0)
        self.assertEqual(set(self.app.load_state()["em"]), {"EXAMPLE-D-26-001"})
        before = self.app.load_state()["em"]
        self.assertEqual(self.app.check(lambda *_: {}), EXIT_CHECK)
        self.assertEqual(self.app.load_state()["em"], before)

    @patch("submission_monitor.app.send_email")
    def test_coexisting_revisions_are_not_migrated_back_and_forth(self, send):
        self.app.cfg["em"]["watch"] = "all"
        def both(*_):
            return {"EXAMPLE-D-26-001": row(),
                    "EXAMPLE-D-26-001R1": row("Under Review", "EXAMPLE-D-26-001R1")}
        self.assertEqual(self.app.check(both), 0)
        self.assertEqual(self.app.check(both), 0)
        self.assertEqual(self.app.check(both), 0)
        self.assertEqual(len(self.app.load_state()["em"]), 2)
        send.assert_not_called()

    def test_disabled_channel_pauses_pending_delivery(self):
        self.app.check(self.collect())
        with patch("submission_monitor.app.send_email", side_effect=OSError("down")):
            self.app.check(self.collect("Under Review"))
        self.app.cfg["notify"]["email"]["enabled"] = False
        with patch("submission_monitor.app.send_email") as send:
            self.assertEqual(self.app.check(self.collect("Under Review")), EXIT_DELIVERY)
            send.assert_not_called()
        self.assertEqual(len(self.app.load_state()["_outbox"]), 1)
        self.app.cfg["notify"]["email"]["enabled"] = True
        with patch("submission_monitor.app.send_email") as send:
            self.assertEqual(self.app.check(self.collect("Under Review")), 0)
            send.assert_called_once()

    def test_tightening_watch_keeps_existing_target_change_and_pending_event(self):
        self.app.cfg["em"]["watch"] = "all"
        self.app.check(self.collect())
        with patch("submission_monitor.app.send_email", side_effect=OSError("down")):
            self.app.check(self.collect("Under Review"))
        self.app.cfg["em"]["watch"] = ["EXAMPLE-D-26-001"]
        with patch("submission_monitor.app.send_email") as send:
            self.assertEqual(self.app.check(self.collect("Under Review")), 0)
            send.assert_called_once()
        self.assertEqual(self.app.load_state()["em"]["EXAMPLE-D-26-001"]["status"], "Under Review")

    def test_mid_update_exception_does_not_partially_change_observations(self):
        self.app.cfg["em"]["watch"] = "all"
        self.app.check(self.collect())
        before = self.app.load_state()["em"]
        def invalid(*_):
            bad = row(number="EXAMPLE-D-26-002")
            bad["url"] = object()
            return {"EXAMPLE-D-26-001": row("Under Review"), "EXAMPLE-D-26-002": bad}
        self.assertEqual(self.app.check(invalid), EXIT_CHECK)
        self.assertEqual(self.app.load_state()["em"], before)
        self.assertEqual(self.app.load_state()["_outbox"], [])

    def test_disabled_email_bad_recipient_never_crashes_redaction(self):
        self.app.cfg["notify"]["email"].update(enabled=False, to=None)
        self.assertEqual(self.app.redact("diagnostic"), "diagnostic")

    def test_logs_escape_line_breaks(self):
        self.app.log("one\\n[forged] two\\rthree")
        self.app.close()
        lines = self.app.log_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn(r"one\n[forged] two\rthree", lines[0])

    def test_unsafe_urls_not_sent_in_notifications(self):
        self.assertEqual(safe_url("javascript:view(123)"), "")
        self.assertEqual(safe_url("https://www.editorialmanager.com/example/auth.asp?id=secret"),
                         "https://www.editorialmanager.com/example/Default.aspx")
        self.assertEqual(safe_url("https://mts-ncomms.nature.com/cgi-bin/main.plex?ms_id_key=private"),
                         "https://mts-ncomms.nature.com/cgi-bin/main.plex")


if __name__ == "__main__":
    unittest.main()
