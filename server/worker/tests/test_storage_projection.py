"""원천 데이터 투영에서 민감 정보와 형식 오류를 확인한다."""

import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.storage import ingest_observation, project_observation


class StorageProjectionTests(unittest.TestCase):
    def test_sensitive_and_unknown_fields_are_excluded_for_all_layers(self):
        for layer, field in (("T", "transaction_id"), ("P", "process_id"), ("M", "message_id")):
            with self.subTest(layer=layer):
                allowed, missing = project_observation(layer, {
                    field: "safe-id", "BODY": "private", "messageBody": "private",
                    "Authorization": "private", "password": "private",
                })
                self.assertEqual(allowed, {field: "safe-id"})
                self.assertNotIn(field, missing)

    def test_nested_value_in_allowed_field_is_rejected(self):
        with self.assertRaises(ValueError):
            project_observation("P", {"process_id": {"BODY": "private"}})

    def test_unverified_process_is_stored_without_detection_request(self):
        with self.assertRaises(ValueError):
            ingest_observation(
                None, source_id=uuid.uuid4(), layer="P", dedup_key="v1:sample",
                raw={"process_id": "p1"}, detection_eligible=True,
            )


if __name__ == "__main__":
    unittest.main()
