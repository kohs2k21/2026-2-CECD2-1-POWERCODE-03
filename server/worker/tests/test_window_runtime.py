from __future__ import annotations
import unittest
from datetime import datetime, timedelta, timezone
from window_runtime import inputs


class WindowInputTests(unittest.TestCase):
    def test_empty_minutes_revision_dedup_and_late_arrival(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        raw = {"adapter_type": "DB.READER", "process_id": "p", "start_time_raw": start.isoformat(),
               "end_time_raw": (start + timedelta(seconds=1)).isoformat(), "total_count": "2"}
        records = [("a", "execution", 1, start + timedelta(seconds=2), raw),
                   ("b", "execution", 2, start + timedelta(seconds=3), raw),
                   ("c", "late", 1, start + timedelta(minutes=2), raw)]
        native, completed, rejected = inputs(records, start, start + timedelta(minutes=3), "Asia/Seoul", 0)
        self.assertEqual(native.execution_arrivals.tolist(), [1, 0, 0])
        self.assertEqual(len(completed), 1)
        self.assertGreater(rejected, 0)

    def test_unknown_clock_rejected(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "verified_kst_clock"):
            inputs([], start, start + timedelta(minutes=3), "UTC", 0)


if __name__ == "__main__":
    unittest.main()
