"""격리 DB: 생성 계약 시험 응답 + 실제 IF + 실제 v12 전체 실행 경로."""
from __future__ import annotations
import hashlib
import json
import os
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector.storage import ingest_observation
from detector.applications import process_application_once
from detector.runner import process_detection_message
from feature_worker import process_feature_message
from generation_service import serve
from pg_queue import claim, enqueue
from trainer.runner import run
from window_worker import measure_once, register
from window_runtime import model as window_model
from research_fixture import ContractFixture


def job(conn, spec):
    identifier = uuid.uuid4()
    request_hash = hashlib.sha256(json.dumps(spec).encode()).hexdigest()
    recommendation = spec["kind"] in {"recommend_feature", "recommend_rule"}
    llm = {"evidence_version": "source-period-request-v1", "input_evidence_hash": request_hash,
           "model_version": "feature_v5" if spec["kind"] == "recommend_feature" else "rule_v2",
           "prompt_version": "feature-rule-design-v0.2", "output_schema_version": "safe-process-derived-v2",
           "reuse_key": str(identifier)}
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO run_jobs
            (id,kind,status,requested_by_actor_id,requester_role,request_hash,input_spec,max_attempts,attempt_count,next_run_at,created_at,
             contract_version,queue,producer_kind,producer_key,idempotency_key,llm_spec)
            VALUES (%s,%s,'queued','integration-admin','admin',%s,%s::jsonb,3,0,clock_timestamp(),clock_timestamp(),
                    %s,%s,%s,%s,%s,%s::jsonb)""",
            (identifier, spec["kind"], request_hash, json.dumps(spec),
             "v03" if recommendation else None, "llm_recommendation" if recommendation else None,
             "user" if recommendation else None, "integration-admin" if recommendation else None,
             str(identifier), json.dumps(llm) if recommendation else None))
    enqueue(conn, "llm_recommendation" if recommendation else "training", {"schemaVersion": 1, "jobId": str(identifier)})
    conn.commit()
    run(once=True,role="llm" if recommendation else "trainer")
    with conn.cursor() as cur:
        cur.execute("SELECT status,result_summary,error_code FROM run_jobs WHERE id=%s", (identifier,))
        status, result, error = cur.fetchone()
    conn.commit()
    assert status == "succeeded", (status, error, result)
    return result


def source(conn, name, clock=None):
    identifier = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
            VALUES (%s,%s,%s,'synthetic-test','v1',%s::jsonb,'{}'::jsonb,clock_timestamp())""",
                    (identifier, str(identifier), name, json.dumps(clock or {})))
    conn.commit()
    return identifier


def observe(conn, source_id, key, raw, label=None, persisted=None, live=False):
    execution_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at)
            VALUES (%s,%s,'P',%s,'{}'::jsonb,'test-v1',clock_timestamp())""", (execution_id, source_id, key))
    result = ingest_observation(conn, source_id=source_id, layer="P", dedup_key=key, raw=raw,
                               execution_id=execution_id, revision_no=1, detection_eligible=live)
    with conn.cursor() as cur:
        if label:
            cur.execute("""INSERT INTO data_label_evidence(id,observation_id,revision_no,label,reason_code,created_by_actor_id)
                VALUES (%s,%s,1,%s,'TEST_SCENARIO','integration-admin')""", (uuid.uuid4(), result.observation_id, label))
        if persisted:
            cur.execute("UPDATE data_observations SET persisted_at=%s WHERE id=%s", (persisted, result.observation_id))
    conn.commit()
    return result


def main():
    dsn = os.environ["DATABASE_URL"]
    parsed = urlsplit(dsn)
    assert parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path == "/qa_research_pipeline"
    bundle = Path(os.environ["RESEARCH_BUNDLE_ROOT"])
    server = serve(ContractFixture(bundle), "integration-test-token-32-characters", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ.update(DESIGN_MODEL_URL=f"http://127.0.0.1:{server.server_port}",
                      DESIGN_MODEL_TOKEN="integration-test-token-32-characters", FEATURE_DESIGN_PROVIDER="research")
    try:
        with tempfile.TemporaryDirectory() as directory, psycopg.connect(dsn) as conn:
            root = Path(directory)
            os.environ["ARTIFACT_ROOT"] = directory
            sid = source(conn, "Research pipeline fixture")
            for index in range(1000):
                observe(conn, sid, f"reference-{index}", {"status":"S", "error_count": str(index % 7), "total_count": "100"},
                        "normal" if index < 700 else "anomaly" if index % 2 else "normal")
            now = datetime.now(timezone.utc)
            period = {"sourceId": str(sid), "from": (now - timedelta(hours=1)).isoformat(),
                      "until": (now + timedelta(hours=1)).isoformat()}
            features = job(conn, {**period, "kind": "recommend_feature"})
            assert features["provider"] == "feature_v5" and features["materializedCount"] == 1000
            rules = job(conn, {**period, "kind": "recommend_rule", "featureSetId": features["featureSetId"], "snapshotId":features["snapshotId"]})
            assert rules["provider"] == "rule_v2" and len(rules["suggestions"]) == 1
            selected = rules["suggestions"][0]
            rule_id = uuid.uuid4()
            rule = {"schemaVersion": 1, "state": "draft", "condition": selected["condition"], "outcome": selected["outcome"]}
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO ml_rule_versions
                    (id,rule_key,version,rule_spec,input_spec,evidence_spec,content_hash,created_by_actor_id,created_at)
                    VALUES (%s,%s,1,%s::jsonb,%s::jsonb,%s::jsonb,%s,'integration-admin',clock_timestamp())""",
                    (rule_id, selected["ruleKey"], json.dumps(rule), json.dumps({"featureSetId": features["featureSetId"]}),
                     json.dumps(selected["evidenceSpec"]), hashlib.sha256(json.dumps(rule).encode()).hexdigest()))
            conn.commit()
            trained = job(conn, {**period, "kind": "train", "featureSetId": features["featureSetId"], "ruleVersionIds": [str(rule_id)],
                "snapshotId":features["snapshotId"],"detectionPolicy":{"mode":"parallel","deadlineMs":3000,"combination":"any_anomaly"}})
            evaluated = job(conn, {"sourceId": str(sid), "kind": "evaluate", "configVersionId": trained["configVersionId"],
                                  "snapshotId": trained["snapshotId"], "purpose": "validation"})
            assert evaluated["applicability"] == "valid"
            with conn.cursor() as cur:
                cur.execute("INSERT INTO ops_system_state(id,revision,detector_state,changed_at) VALUES (1,0,'{}',clock_timestamp())")
                cur.execute("""INSERT INTO run_applications
                    (id,operation,target_config_version_id,expected_system_revision,evaluation_id,requested_by_actor_id,
                     requester_role,approved_at,request_hash,status,gate_evidence,transition_audit,created_at)
                    VALUES (%s,'apply',%s,0,%s,'integration-admin','admin',clock_timestamp(),'fixture','queued','{}','{}',clock_timestamp())""",
                    (uuid.uuid4(), trained["configVersionId"], evaluated["evaluationId"]))
            conn.commit()
            assert process_application_once(conn, root, "fixture-detector")
            conn.commit()
            live = observe(conn, sid, "live", {"status":"S", "error_count": "90", "total_count": "100"}, live=True)
            message = claim(conn, "detection", source_id=str(sid)); conn.commit()
            assert message is not None
            process_detection_message(conn, message, root); conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT status,ml_status,rule_status,decision_code FROM run_process_decisions WHERE process_observation_id=%s", (live.observation_id,))
                assert cur.fetchone() == ("complete", "succeeded", "succeeded", "SR_DIAGNOSTIC")
            conn.commit()
            print("PASS: generation HTTP contract -> derived feature -> grounded SR -> real IF -> evaluate -> apply -> decision")
            start = now.replace(second=0, microsecond=0) - timedelta(minutes=10)
            window_source = source(conn, "v12 fixture", {"status": "verified", "timezone": "Asia/Seoul", "arrivalEventsAvailable": True})
            for minute in (0, 1, 2, 3, 4, 6, 7):
                begun = start + timedelta(minutes=minute)
                observe(conn, window_source, f"window-{minute}",
                        {"adapter_type": "DB.READER", "process_id": "fixture-P", "total_count": "1",
                         "start_time_raw": begun.isoformat(), "end_time_raw": (begun + timedelta(milliseconds=100)).isoformat()},
                        persisted=begun + timedelta(seconds=1))
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO ops_collection_checkpoints(id,source_id,layer,partition_key,query_from,query_until,
                    cursor_state,completed_until,last_success_at,revision)
                    VALUES (%s,%s,'P','fixture',%s,%s,'{}',%s,%s,1)""",
                    (uuid.uuid4(), window_source, start, start + timedelta(minutes=8), start + timedelta(minutes=8), now))
            conn.commit()
            for algorithm in ("if", "kmeans"):
                register(conn, window_source, algorithm, start, 0, bundle)
            assert measure_once(conn, bundle, now) == 16
            window_model.cache_clear()
            assert measure_once(conn, bundle, now) == 0
            with conn.cursor() as cur:
                cur.execute("SELECT count(*),count(DISTINCT model_hash) FROM run_window_decisions")
                assert cur.fetchone() == (16, 2)
                cur.execute("SELECT count(*) FROM run_llm_results WHERE model_version IN ('feature_v5','rule_v2')")
                assert cur.fetchone()[0] == 2
            print("PASS: actual v12 IF/K-means, 8 contiguous minutes including empty minute, durable idempotent decisions")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
