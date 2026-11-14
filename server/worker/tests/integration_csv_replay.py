"""격리된 PostgreSQL에서 CSV 재생의 저장·중복·큐 범위를 확인한다."""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.replay_csv import CsvInput, replay


def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("격리된 시험 DB의 DATABASE_URL이 필요하다")
    dataset_key = f"csv-replay-test-{uuid.uuid4()}"
    replay_key = str(uuid.uuid4())
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        transaction = root / "transaction.csv"
        process = root / "process.csv"
        body = root / "body.csv"
        transaction.write_text(
            "TRANSACTION_ID,START_TIME,PROCESS_TIME,BODY\n"
            "tx-1,2026-10-10 12:00:00,25,private-transaction\n", encoding="utf-8"
        )
        process.write_text(
            "TRANSACTION_ID,PROCESS_ID,RETRY_COUNT,STATUS,Authorization\n"
            "tx-1,proc-1,0,ok,private-token\n"
            "tx-1,proc-2,,failed,private-token\n", encoding="utf-8"
        )
        body.write_text(
            "MESSAGE_ID,MESSAGE_IN,MESSAGE_OUT,MESSAGE_BODY\n"
            "msg-1,private-in,private-out,private-body\n", encoding="utf-8"
        )
        inputs = [CsvInput("T", transaction), CsvInput("P", process), CsvInput("BODY", body)]
        dry = replay(inputs, dataset_key=dataset_key, replay_key=replay_key,
                     rate=0, apply=False)
        assert dry.read == 4 and dry.eligible_process == 1
        first = replay(inputs, dataset_key=dataset_key, replay_key=replay_key,
                       rate=0, apply=True, dsn=dsn)
        second = replay(inputs, dataset_key=dataset_key, replay_key=replay_key,
                        rate=0, apply=True, dsn=dsn)
    assert first.stored == 4 and first.queued == 1 and first.unverified_process == 1
    assert second.stored == 0 and second.duplicate == 4 and second.queued == 0
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM ops_sources WHERE dataset_key = %s", (dataset_key,))
            source_id = cur.fetchone()[0]
            cur.execute(
                """SELECT layer::text, allowed_values, clock_status, parsed_times, source_log_occurred_at,
                          source_log_time_raw, api_received_at FROM data_observations
                     WHERE source_id = %s ORDER BY layer, dedup_key""",
                (source_id,),
            )
            rows = cur.fetchall()
            assert len(rows) == 4
            # 030 source time (contracts/collector-metrics.md 1.1): T has START_TIME only and the csv-replay source is
            # unverified -> assumed Asia/Seoul; P rows and the BODY reference (M) have no time columns -> absent.
            # CSV replay leaves api_received_at NULL.
            by_layer = {}
            for row in rows:
                by_layer.setdefault(row[0], []).append(row)
            assert sorted((layer, len(items)) for layer, items in by_layer.items()) == [("M", 1), ("P", 2), ("T", 1)], by_layer
            t = by_layer["T"][0]
            assert t[2:6] == ("assumed_tz", {"rule": "source-time-v1", "field": "start", "basis": "assumed_tz"},
                              datetime(2026, 10, 10, 3, tzinfo=timezone.utc), "2026-10-10 12:00:00"), t
            for row in by_layer["P"] + by_layer["M"]:
                assert row[2:6] == ("absent", {"rule": "source-time-v1"}, None, None), row
            assert all(row[6] is None for row in rows)
            assert all("private-" not in str(row) for row in rows)
            assert any(row[0] == "M" and row[1] == {"message_id": "msg-1"} for row in rows)
            cur.execute(
                """SELECT count(*) FROM powercode_queue.detection_logs AS q
                     JOIN data_observations AS o
                       ON q.payload->>'observationId' = o.id::text
                    WHERE o.source_id = %s""",
                (source_id,),
            )
            assert cur.fetchone()[0] == 1
    print("CSV replay integration passed: T/P/BODY projection, idempotent replay, one P queue message")


if __name__ == "__main__":
    main()
