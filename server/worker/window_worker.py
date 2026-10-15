"""DB 이력·수집 watermark를 이용한 v12 시간창 탐지."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import load_dotenv

from window_runtime import inputs, model


def register(conn, source_id, algorithm, history_start, lateness_seconds, bundle):
    _, model_hash = model(str(bundle), algorithm)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO ops_window_monitors
            (id,source_id,algorithm,model_hash,history_start,lateness_seconds,synthetic_prior_accepted)
            VALUES (%s,%s,%s,%s,%s,%s,true) ON CONFLICT DO NOTHING""",
                    (uuid.uuid4(), source_id, algorithm, model_hash, history_start, lateness_seconds))
    conn.commit()


def measure_once(conn, bundle, now=None):
    now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("""SELECT m.id,m.source_id,m.algorithm,m.model_hash,m.history_start,m.lateness_seconds,
                       m.synthetic_prior_accepted,s.clock_basis
                       FROM ops_window_monitors m JOIN ops_sources s ON s.id=m.source_id WHERE m.enabled""")
        monitors = cur.fetchall()
    conn.commit()
    count = 0
    for monitor_id, source_id, algorithm, expected_hash, start, lateness, accepted, clock in monitors:
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_try_advisory_xact_lock(81735,hashtext(%s))", (str(monitor_id),))
                    if not cur.fetchone()[0]:
                        continue
                    if not accepted or clock.get("status") != "verified" or clock.get("timezone") != "Asia/Seoul" or clock.get("arrivalEventsAvailable") is not True:
                        raise ValueError("verified_kst_source_clock_required")
                    # 모든 P partition이 수집 완료된 시점까지만 0건을 의미 있는 입력으로 쓴다.
                    cur.execute("""SELECT min(completed_until), bool_and(error_code IS NULL AND last_success_at IS NOT NULL
                                   AND completed_until IS NOT NULL AND query_from IS NOT NULL),
                                   max(query_from) FROM ops_collection_checkpoints WHERE source_id=%s AND layer='P'""", (source_id,))
                    coverage, healthy, coverage_start = cur.fetchone()
                    if coverage is None or not healthy or coverage_start is None or coverage_start > start:
                        raise ValueError("collection_coverage_unavailable")
                    until = min(coverage, now - timedelta(seconds=lateness)).replace(second=0, microsecond=0)
                    if until <= start:
                        continue
                    cur.execute("""SELECT id,execution_id,revision_no,persisted_at,allowed_values
                                   FROM data_observations WHERE source_id=%s AND layer='P'
                                   AND execution_id IS NOT NULL AND persisted_at>=%s AND persisted_at<=%s
                                   ORDER BY execution_id,revision_no LIMIT 100001""", (source_id, start, now))
                    records = cur.fetchall()
                    if len(records) > 100000:
                        raise ValueError("window_observation_limit_exceeded")
                    native, completed, rejected = inputs(records, start, until, clock["timezone"], lateness)
                    if native.empty:
                        continue
                    detector, actual_hash = model(str(bundle), algorithm)
                    if actual_hash != expected_hash:
                        raise ValueError("window_model_hash_changed")
                    result = detector.predict(native, completed)
                    input_hash = hashlib.sha256(json.dumps(
                        [(str(r[0]), str(r[3]), r[4]) for r in records], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    for _, row in result.iterrows():
                        begun = pd.Timestamp(row["window_start"]).tz_localize("Asia/Seoul").to_pydatetime()
                        margins = {name: float(row[name + "_margin"]) for name in ("global", "adapter", "ensemble")}
                        anomalies = {name: bool(row[name + "_anomaly"]) for name in margins}
                        evidence = {"algorithm": algorithm, "scoreContract": "margin_gt_zero",
                                    "syntheticTrafficPrior": True, "expertAvailable": bool(row["expert_available"]),
                                    "trafficAssessed": bool(row["traffic_assessed"]), "latencyAssessed": bool(row["latency_assessed"]),
                                    "lateOrInvalidObservations": rejected, "watermark": until.isoformat()}
                        cur.execute("""INSERT INTO run_window_decisions
                            (id,monitor_id,source_id,stream_key,adapter_type,process_id,window_start,window_end,
                             model_hash,input_hash,margins,anomalies,evidence)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)
                            ON CONFLICT (monitor_id,stream_key,window_start) DO NOTHING""",
                            (uuid.uuid4(), monitor_id, source_id, row["case_id"], row["adapterType"], row["processId"],
                             begun, begun + timedelta(minutes=1), actual_hash, input_hash,
                             json.dumps(margins, allow_nan=False), json.dumps(anomalies), json.dumps(evidence)))
                        count += cur.rowcount
                    cur.execute("UPDATE ops_window_monitors SET last_error=NULL WHERE id=%s", (monitor_id,))
        except ValueError as exc:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("UPDATE ops_window_monitors SET last_error=%s WHERE id=%s", (str(exc)[:100], monitor_id))
            conn.commit()
    return count


def main():
    load_dotenv(Path(__file__).parent / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--register-source", type=uuid.UUID)
    parser.add_argument("--algorithm", choices=["if", "kmeans"], default="if")
    parser.add_argument("--history-start")
    parser.add_argument("--lateness-seconds", type=int, default=120)
    parser.add_argument("--accept-synthetic-prior", action="store_true")
    args = parser.parse_args()
    bundle = Path(os.environ["RESEARCH_BUNDLE_ROOT"]).resolve(strict=True)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        if args.register_source:
            if not args.accept_synthetic_prior or not args.history_start or not 0 <= args.lateness_seconds <= 3600:
                parser.error("history-start and explicit accept-synthetic-prior are required")
            start = datetime.fromisoformat(args.history_start)
            if start.tzinfo is None:
                parser.error("history-start must include timezone")
            register(conn, args.register_source, args.algorithm, start, args.lateness_seconds, bundle)
            return
        while True:
            measure_once(conn, bundle)
            if args.once:
                return
            time.sleep(10)


if __name__ == "__main__":
    main()
