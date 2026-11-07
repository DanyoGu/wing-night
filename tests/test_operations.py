from contextlib import closing
import csv
import gc
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("operations", ROOT / "deploy" / "operations.py")
operations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operations)
spec = importlib.util.spec_from_file_location("report_backend", ROOT / "server.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "source.sqlite"
        self.store = backend.Store(self.source)
        result, self.host = self.store.perform("create", {})
        self.room = result["room"]
        _, self.judge = self.store.perform("join", {"room": self.room, "name": "=SUM(1,2)<b>"})
        self.store.perform("start", {"room": self.room}, self.host)
        self.store.perform("vote", {"room": self.room, "round": 0, "grades": [5, 4, 3, 2]}, self.judge)
        self.copy = Path(self.temp.name) / "snapshot.sqlite"

    def tearDown(self):
        gc.collect()
        self.temp.cleanup()

    def test_consistent_private_report_and_exclusions(self):
        operations.snapshot(self.source, self.copy)
        self.store.perform("vote", {"room": self.room, "round": 0, "grades": [1, 1, 1, 1]}, self.judge)
        body, csv_data, rooms = operations.render_report(self.copy, "2026-11-09T04:00:00Z", set())
        self.assertEqual(rooms[0]["totals"], [9, 5])
        self.assertEqual(rooms[0]["rounds"][0], [9, 5, 1])
        self.assertEqual(rooms[0]["phase"], "started")
        self.assertIn(b"unfinished room", body)
        self.assertIn(b"subsequent edits", body)
        for secret in (self.host, self.judge, backend.digest(self.host), backend.digest(self.judge)):
            self.assertNotIn(secret.encode(), body + csv_data + json.dumps(rooms).encode())
        rows = list(csv.reader(io.StringIO(csv_data.decode("utf-8-sig"))))
        self.assertEqual(rows[1][5], "'=SUM(1,2)<b>")
        self.assertEqual(rows[1][-2:], ["9", "5"])
        body, csv_data, rooms = operations.render_report(self.copy, "time", {self.room})
        self.assertEqual(rooms, [])
        self.assertNotIn(self.room.encode(), body + csv_data)

    def test_missing_source_does_not_create_database(self):
        missing = Path(self.temp.name) / "missing.sqlite"
        with self.assertRaises(RuntimeError):
            operations.snapshot(missing, self.copy)
        self.assertFalse(missing.exists())

    def test_backup_retries_are_bounded(self):
        with patch.object(operations, "urlopen", side_effect=operations.URLError("offline")) as opened:
            with patch.object(operations.time, "sleep"):
                with self.assertRaises(operations.URLError):
                    operations.request("https://example.invalid")
        self.assertEqual(opened.call_count, 4)

    def test_invalid_grade_fails_report(self):
        operations.snapshot(self.source, self.copy)
        with closing(sqlite3.connect(self.copy)) as db:
            db.execute("UPDATE votes SET grades='[5,5,5,9]'")
            db.commit()
        with self.assertRaises(RuntimeError):
            operations.render_report(self.copy, "time", set())


if __name__ == "__main__":
    unittest.main()
