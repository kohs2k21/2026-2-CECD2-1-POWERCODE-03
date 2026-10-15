"""승인 구성의 Rule·IF 판정을 수행하고 결과를 멱등하게 저장한다."""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv

import model_adapters
from detector.input_reader import load_process_observation
from feature_worker import active_config, active_source
from pg_queue import ClaimedMessage, acknowledge, claim, fail


def process_detection_message(conn: Any, message: ClaimedMessage, root: Path | None) -> None:
    from detector.computation import load_context, compute, legacy_feature_hash
    from detector.jobs import enqueue_observation,input_config
    from trainer.runner import _claim_job, _finish
    from trainer.lease import TrainingLeaseHeartbeat, lock_training_ownership
    started = time.monotonic()
    observation = load_process_observation(conn, message)
    payload = message.payload
    if not payload.get("jobId"):
        # Bridge old feature/detection backlog to the immutable job protocol.
        with conn.transaction():
            pinned = payload.get("configVersionId") or input_config(conn,observation.source_id)
            if pinned is None:
                raise ValueError("active_config_unavailable")
            context = load_context(conn, pinned, observation.source_id)
            with conn.cursor() as cur:
                cur.execute("SELECT content_hash FROM data_observations WHERE id=%s", (observation.observation_id,))
                content_hash = cur.fetchone()[0]
                previous_hash = legacy_feature_hash(context, observation.observation_id, content_hash)
                if payload.get("inputManifestHash") not in (None, previous_hash):
                    raise ValueError("input_manifest_mismatch")
                cur.execute("SELECT 1 FROM run_process_decisions WHERE process_observation_id=%s AND config_version_id=%s", (observation.observation_id,pinned))
                if cur.fetchone() is None:
                    enqueue_observation(conn, observation.observation_id, config_id=pinned)
            if not acknowledge(conn, "detection", message):
                raise RuntimeError("detection_lease_lost")
        return
    claimed = _claim_job(conn, message, "detector")
    conn.commit()
    if claimed is None:
        return
    job, attempt_id, attempt_token = claimed
    heartbeat = TrainingLeaseHeartbeat(lambda: psycopg.connect(os.environ["DATABASE_URL"]),
        message,job["id"],attempt_id,attempt_token)
    heartbeat.start()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT j.input_spec,j.input_config_version_id,j.input_manifest_id,m.content_hash,m.input_as_of
                FROM run_jobs j JOIN run_input_manifests m ON m.id=j.input_manifest_id WHERE j.id=%s""", (job["id"],))
            fixed = cur.fetchone()
            if fixed is None or any(payload.get(key) != fixed[0].get(key) for key in
                    ("sourceId","observationId","configVersionId","inputManifestHash","inputManifestId")):
                raise ValueError("input_manifest_mismatch")
        config_id = fixed[1]
        context = load_context(conn, config_id, observation.source_id)
        with conn.cursor() as cur:
            cur.execute("SELECT content_hash FROM data_observations WHERE id=%s",(observation.observation_id,))
            source_hash=cur.fetchone()[0]
            cur.execute('SELECT ingestion_method,dataset_key FROM ops_sources WHERE id=%s',(observation.source_id,))
            source_mode=cur.fetchone()
        conn.commit()
        result = compute(context, observation.allowed_values, root, started_at=started)
        if heartbeat.lost:
            raise RuntimeError("detection_lease_lost")
        evidence = {"schemaVersion": 2,"rules":result["rule"]["evidence"],"models":result["ml"]["evidence"],
                    "inputManifestHash":fixed[3],"anomalous":result["anomalous"],
                    "requestedMode":result["requestedMode"],"actualPaths":result["actualPaths"],
                    "detectionPolicy":result["policy"],"ruleSeverities":result["rule"].get("severity",[])}
        if source_mode and source_mode[0]=='demo-replay':
            evidence['demo']={'historicalReplay':True,'runKey':source_mode[1].removeprefix('demo-replay:'),'scope':'Process replay; source occurrence latency unavailable'}
        if fixed[0].get('inputOrigin'):
            evidence['input']=fixed[0]['inputOrigin']
        code = result["rule"].get("code") or ("IF_ANOMALY" if result["ml"].get("anomalous") else None)
        with conn.transaction():
            if not lock_training_ownership(conn,message,job["id"],attempt_id,attempt_token):
                raise RuntimeError("detection_lease_lost")
            with conn.cursor() as cur:
                feature_result=next((result[path] for path in ("rule","ml") if "featureValues" in result[path]),None)
                if feature_result is not None and context["featureSetId"]:
                    from feature_runtime import input_manifest_hash
                    cur.execute("""INSERT INTO data_feature_materializations(observation_id,feature_set_id,
                        input_manifest_hash,values_json,missing_names) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb)
                        ON CONFLICT DO NOTHING""", (observation.observation_id,context["featureSetId"],
                        input_manifest_hash(str(observation.observation_id),source_hash,context["featureHash"]),
                        json.dumps(feature_result["featureValues"]),json.dumps(feature_result["missingFeatures"])))
                cur.execute("""INSERT INTO run_process_decisions
                    (id,execution_id,process_observation_id,config_version_id,input_manifest_hash,input_as_of,
                    status,rule_status,ml_status,decision_code,anomaly_score,evidence,decided_at,latency_status,
                    job_id,input_manifest_id,result_attempt_id,result_lease_token)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,clock_timestamp(),'unavailable',%s,%s,%s,%s)""",
                    (uuid.uuid4(),observation.execution_id,observation.observation_id,config_id,fixed[3],fixed[4],
                    result["status"],result["rule"]["status"],result["ml"]["status"],code,result["ml"].get("score"),
                    json.dumps(evidence),job["id"],fixed[2],attempt_id,attempt_token))
                cur.execute("""INSERT INTO ops_pipeline_measurements(id,observation_id,stage,config_version_id,duration_ms)
                    VALUES(%s,%s,'detector',%s,%s)""",(uuid.uuid4(),observation.observation_id,config_id,(time.monotonic()-started)*1000))
            _finish(conn,message,job,attempt_id,attempt_token,{"status":result["status"]},None,True)
    except Exception:
        conn.rollback()
        _finish(conn,message,job,attempt_id,attempt_token,None,"detection_processing_failed",False)
        conn.commit()
        raise
    finally:
        heartbeat.stop()


def run(*, once: bool = False, poll_seconds: float = 1.0) -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL이 필요하다")
    root = Path(os.environ["ARTIFACT_ROOT"]) if os.environ.get("ARTIFACT_ROOT") else None
    from detector.applications import prepare_active, process_application_once
    worker_id = f"detector-{uuid.uuid4()}"
    with psycopg.connect(database_url) as conn:
        try:
            prepare_active(conn, root)
            conn.commit()
        except (model_adapters.ModelNotConfigured, ValueError):
            conn.rollback()
            # Rule path remains available when model preparation fails.
        while True:
            process_application_once(conn, root, worker_id)
            conn.commit()
            config_id = active_config(conn)
            conn.commit()
            source_id = active_source(conn, config_id) if config_id else None
            conn.commit()
            # Keep a bounded recovery step so historical waiting inputs also make progress.
            if source_id:
                from feature_worker import process_feature_message
                legacy = claim(conn, "feature", source_id=str(source_id))
                conn.commit()
                if legacy:
                    try:
                        process_feature_message(conn, legacy, config_id)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        fail(conn, "feature", legacy, reason="feature_bridge_failed")
                        conn.commit()
            # Fixed jobs keep their pinned config even if the active source changes.
            message = claim(conn, "detection", job_kinds=("detect",))
            conn.commit()
            if message is None and source_id:
                message = claim(conn, "detection", source_id=str(source_id))
                conn.commit()
            if message is None:
                from detector.jobs import claim_waiting_input
                message=claim_waiting_input(conn)
                conn.commit()
            from pg_queue import reconcile_dead_jobs
            reconcile_dead_jobs(conn,"detection")
            conn.commit()
            if message is None:
                if once:
                    return
                time.sleep(poll_seconds)
                continue
            try:
                process_detection_message(conn, message, root)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                reason = str(exc) if isinstance(exc, ValueError) else "detection_failed"
                fail(conn, "detection", message, reason=reason[:100])
                conn.commit()
            if once:
                return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    run(once=arguments.once)
