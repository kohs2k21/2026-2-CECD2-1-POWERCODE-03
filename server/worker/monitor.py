"""활성 변수의 결측률 변화를 감시하고 재학습 검토 신호만 기록한다."""

from __future__ import annotations

import argparse
import os
import time
import uuid
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv


def _rates(conn: Any, feature_set_id: uuid.UUID, *, snapshot_id: uuid.UUID | None,
           source_id: uuid.UUID, window_hours: int) -> dict[str, tuple[int, float]]:
    with conn.cursor() as cur:
        if snapshot_id is not None:
            cur.execute(
                """SELECT j.key, count(*)::bigint,
                          count(*) FILTER (WHERE j.value = 'null'::jsonb)::bigint
                     FROM data_feature_materializations AS f
                     JOIN data_snapshot_members AS m ON m.observation_id = f.observation_id
                     CROSS JOIN LATERAL jsonb_each(f.values_json) AS j(key, value)
                    WHERE f.feature_set_id = %s AND m.snapshot_id = %s AND m.split = 'train'
                    GROUP BY j.key""",
                (feature_set_id, snapshot_id),
            )
        else:
            cur.execute(
                """SELECT j.key, count(*)::bigint,
                          count(*) FILTER (WHERE j.value = 'null'::jsonb)::bigint
                     FROM data_feature_materializations AS f
                     JOIN data_observations AS o ON o.id = f.observation_id
                     CROSS JOIN LATERAL jsonb_each(f.values_json) AS j(key, value)
                    WHERE f.feature_set_id = %s AND o.source_id = %s
                      AND o.persisted_at >= clock_timestamp() - (%s * interval '1 hour')
                    GROUP BY j.key""",
                (feature_set_id, source_id, window_hours),
            )
        rows = cur.fetchall()
    return {name: (count, missing / count) for name, count, missing in rows if count}


def measure_once(conn: Any, *, window_hours: int = 24, missing_delta: float = .10) -> int:
    """데이터 부족은 정상으로 간주하지 않고 별도 사유로 남긴다."""
    if not 1 <= window_hours <= 168 or not 0 <= missing_delta <= 1:
        raise ValueError("invalid_monitor_settings")
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.active_config_version_id, c.source_id
                 FROM ops_system_state AS s
                 JOIN ml_config_versions AS c ON c.id = s.active_config_version_id
                WHERE s.id = 1""",
        )
        active = cur.fetchone()
        if active is None:
            return 0
        config_id, source_id = active
        cur.execute(
            """SELECT feature_set_id, training_settings->>'snapshotId'
                 FROM ml_config_models WHERE config_version_id = %s""",
            (config_id,),
        )
        models = cur.fetchall()
    written = 0
    for feature_set_id, snapshot_text in models:
        snapshot_id = uuid.UUID(snapshot_text) if snapshot_text else None
        baseline = _rates(conn, feature_set_id, snapshot_id=snapshot_id,
                          source_id=source_id, window_hours=window_hours) if snapshot_id else {}
        recent = _rates(conn, feature_set_id, snapshot_id=None,
                        source_id=source_id, window_hours=window_hours)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT output_name FROM ml_feature_set_members
                    WHERE feature_set_id = %s AND model_input ORDER BY output_position""",
                (feature_set_id,),
            )
            names = [row[0] for row in cur.fetchall()]
            for name in names:
                base_count, base_rate = baseline.get(name, (0, None))
                recent_count, recent_rate = recent.get(name, (0, None))
                enough = base_count >= 20 and recent_count >= 20
                review = bool(enough and recent_rate - base_rate > missing_delta)
                reason = "missing_rate_increase" if review else None if enough else "insufficient_population"
                cur.execute(
                    """INSERT INTO ops_feature_drift_measurements
                         (id, config_version_id, feature_set_id, feature_name,
                          baseline_count, recent_count, baseline_missing_rate,
                          recent_missing_rate, review_required, reason_code)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (uuid.uuid4(), config_id, feature_set_id, name, base_count,
                     recent_count, base_rate, recent_rate, review, reason),
                )
                written += 1
    return written


def run(*, once: bool = False, interval_seconds: int = 300) -> None:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL이 필요하다")
    window = int(os.environ.get("DRIFT_WINDOW_HOURS", "24"))
    delta = float(os.environ.get("DRIFT_MISSING_DELTA", "0.10"))
    with psycopg.connect(database_url) as conn:
        while True:
            measure_once(conn, window_hours=window, missing_delta=delta)
            conn.commit()
            if once:
                return
            time.sleep(interval_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    run(once=arguments.once)
