import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from submission_monitor.errors import StateError
from submission_monitor.storage import atomic_json, new_state, read_state, run_lock

ROOT = Path(__file__).resolve().parents[1]


class StorageTests(unittest.TestCase):
    def test_unknown_or_invalid_state_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            cases = [{"schema_version": 999}, {"em": []}, {"_meta": []},
                     {"_outbox": "broken"}, {"_outbox": [{"subject": "x", "body": "y", "channels": []}]},
                     {"em": {"paper": "broken"}}]
            for case in cases:
                path.write_text(json.dumps(case), encoding="utf-8")
                with self.assertRaises(StateError):
                    read_state(path)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), case)

    def test_os_lock_is_released_after_process_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.lock"
            code = "import os,sys;from submission_monitor.storage import run_lock;" \
                   "guard=run_lock(sys.argv[1]);guard.__enter__();os._exit(19)"
            process = subprocess.run([sys.executable, "-c", code, str(path)], cwd=ROOT,
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 19, process.stderr)
            with run_lock(path):
                pass

    def test_new_state_files_and_two_generations_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "state.json"
            state = new_state()
            atomic_json(path, state)
            self.assertEqual(read_state(path), state)
            state["em"]["EXAMPLE-D-26-001"] = {"status": "With Editor"}
            atomic_json(path, state)
            self.assertEqual(read_state(path), state)


if __name__ == "__main__":
    unittest.main()
