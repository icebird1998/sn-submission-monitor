import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from submission_monitor.cli import init_config, main, parser
from submission_monitor.config import default_config
from submission_monitor.errors import ConfigError

ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def test_entry_points_version_and_help(self):
        for command in ([sys.executable, "monitor.py", "--version"],
                        [sys.executable, "-m", "submission_monitor", "--version"],
                        [sys.executable, "monitor.py", "--help"]):
            process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertTrue(process.stdout.strip())

    def test_init_never_overwrites_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            path.write_text('{"keep":"original"}', encoding="utf-8")
            with self.assertRaises(ConfigError):
                init_config(path)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"keep":"original"}')

    def test_init_noninteractive_fails_instead_of_hanging(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            process = subprocess.run([sys.executable, "monitor.py", "--config", str(path), "--init"],
                                     cwd=ROOT, input="", capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(process.returncode, 2, process.stderr)
            self.assertFalse(path.exists())

    def test_default_path_prefers_local_but_supports_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(default_config(root), root / ".local" / "config.json")
            (root / "config.json").touch()
            self.assertEqual(default_config(root), root / "config.json")
            (root / ".local").mkdir()
            (root / ".local" / "config.json").touch()
            self.assertEqual(default_config(root), root / ".local" / "config.json")

    def test_public_examples_are_synthetic_and_need_credentials(self):
        from submission_monitor.config import load_config
        examples = [ROOT / "config.example.json", *sorted((ROOT / "examples").glob("*.json"))]
        self.assertGreaterEqual(len(examples), 4)
        for path in examples:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(cfg.get("platform"), ("em", "sn", "mts"))
            self.assertIn("example.com", cfg[cfg["platform"]]["email"])
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ConfigError):
                    load_config(path)

    def test_false_test_email_success_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            cfg = {"platform": "mts", "mts": {"base": "https://mts-ncomms.nature.com",
                   "email": "author@example.test", "password": "synthetic-password", "watch": "all"},
                   "notify": {"toast": False, "email": {"enabled": False}}}
            path.write_text(json.dumps(cfg), encoding="utf-8")
            with patch("submission_monitor.app.send_email") as send:
                self.assertNotEqual(main(["--config", str(path), "--test-email"]), 0)
                send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
