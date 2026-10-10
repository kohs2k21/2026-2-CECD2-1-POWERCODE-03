"""CSV 재생의 필드 제외, 원천 키 검증, 재생 속도 설정을 확인한다."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.replay_csv import CsvInput, execution_identity, iter_csv_rows, replay


class CsvReplayTests(unittest.TestCase):
    def test_headers_project_only_allowed_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "body.csv"
            path.write_text(
                "MESSAGE_ID,MESSAGE_IN,MESSAGE_OUT,MESSAGE_BODY\n"
                "m-1,secret-in,secret-out,secret-body\n", encoding="utf-8"
            )
            rows = list(iter_csv_rows(CsvInput("BODY", path)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].allowed, {"message_id": "m-1"})
        self.assertIsNone(rows[0].identity)

    def test_large_body_field_is_read_but_not_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "body.csv"
            path.write_text(
                "MESSAGE_ID,MESSAGE_BODY\n"
                f"m-1,{'x' * (256 * 1024)}\n", encoding="utf-8"
            )
            rows = list(iter_csv_rows(CsvInput("BODY", path)))
        self.assertEqual(rows[0].allowed, {"message_id": "m-1"})

    def test_process_requires_transaction_process_and_retry(self):
        self.assertEqual(
            execution_identity("P", {"transaction_id": "t", "process_id": "p", "retry_count": "0"}),
            ("t", "p", "0"),
        )
        self.assertIsNone(execution_identity("P", {"transaction_id": "t", "process_id": "p"}))
        self.assertIsNone(execution_identity("P", {"transaction_id": "t", "process_id": "p", "retry_count": "-1"}))

    def test_dry_run_does_not_require_database_or_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "process.csv"
            path.write_text(
                "TRANSACTION_ID,PROCESS_ID,RETRY_COUNT,STATUS,Authorization\n"
                "t,p,0,ok,secret\n"
                "t,p,,ok,secret\n", encoding="utf-8"
            )
            counts = replay([CsvInput("P", path)], dataset_key="csv-test",
                            replay_key="fixed-file", rate=100, apply=False)
        self.assertEqual(counts.read, 2)
        self.assertEqual(counts.eligible_process, 1)
        self.assertEqual(counts.unverified_process, 1)
        self.assertEqual(counts.stored, 0)
        self.assertEqual(counts.queued, 0)


if __name__ == "__main__":
    unittest.main()
