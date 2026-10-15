"""Continuous, restartable settings collection; unverified log shapes fail closed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from datetime import datetime,timezone
import uuid
import psycopg
from dotenv import load_dotenv
from collector.api_client import from_environment, SourceUnavailable
from collector.telemetry import store_snapshot,store_sample,monitor_preview


def project_interface(value):
    if not isinstance(value,dict) or not isinstance(value.get("interfaceId"),str) or not value["interfaceId"] or type(value.get("revision")) is not int or value["revision"]<1:
        raise ValueError("source_interface_identity_rejected")
    common=value.get("commonInfo",{})
    processes=value.get("processInfo",[])
    if not isinstance(common,dict) or not isinstance(processes,list):
        raise ValueError("source_interface_contract_rejected")
    safe={"interfaceId":value["interfaceId"],"revision":value["revision"],
        "commonInfo":{key:common[key] for key in ("messageBodyLogging","transactionLogging","status","retryCount","retryInterval","responseTimeout","compression","continueOnError") if key in common},
        "processInfo":[]}
    for process in processes:
        if not isinstance(process,dict) or not isinstance(process.get("processId"),str):
            raise ValueError("source_process_contract_rejected")
        adapter=process.get("adapterInfo",{})
        if not isinstance(adapter,dict):
            raise ValueError("source_adapter_contract_rejected")
        item={key:process[key] for key in ("processId","processOrder","dependProcessId","processHub","channelId") if key in process}
        item["adapterType"]=adapter.get("type")
        safe["processInfo"].append(item)
    # Deliberately exclude all adapter settings, SQL, data columns, BODY and connection secrets.
    def scalars(value):
        if isinstance(value,dict):
            return all(scalars(v) for v in value.values())
        if isinstance(value,list):
            return all(scalars(v) for v in value)
        return value is None or type(value) in (str,int,bool)
    if not scalars(safe):
        raise ValueError("source_catalog_scalar_rejected")
    return safe


def ensure_source(conn,key):
    identifier=uuid.uuid5(uuid.NAMESPACE_URL,"customer-api:"+key)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,
            clock_basis,field_catalog,created_at) VALUES(%s,%s,'Customer API','customer-api','api-transcript-v1',
            '{"status":"unknown"}'::jsonb,'{}'::jsonb,clock_timestamp()) ON CONFLICT(dataset_key) DO NOTHING""", (identifier,key))
        cur.execute("SELECT id FROM ops_sources WHERE dataset_key=%s",(key,))
        return cur.fetchone()[0]


def catalog_page(conn,api,source):
    # Single collector per source, using a session advisory lock held by run().
    with conn.cursor() as cur:
        cur.execute("SELECT checkpoint FROM ops_api_poll_state WHERE source_id=%s AND endpoint='interfaces'",(source,))
        row=cur.fetchone()
    page=int(row[0].get("currentPage",0)) if row else 0
    conn.commit()
    response=api.interfaces(page)
    values=[project_interface(v) for v in response["content"]]
    next_page=page+1 if page+1<response["totalPages"] else 0
    stored = None
    with conn.transaction():
        stored = store_snapshot(conn,source,"interfaces",values,"catalog",api.last_receipt["finished_at"] if getattr(api,"last_receipt",None) else datetime.now(timezone.utc))
        with conn.cursor() as cur:
            for value in values:
                digest=hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()
                cur.execute("""INSERT INTO ops_api_catalog(source_id,interface_id,revision,content_hash,configuration)
                    VALUES(%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                    (source,value["interfaceId"],value["revision"],digest,json.dumps(value)))
                cur.execute("SELECT content_hash FROM ops_api_catalog WHERE source_id=%s AND interface_id=%s AND revision=%s",
                    (source,value["interfaceId"],value["revision"]))
                saved=cur.fetchone()
                if saved is None or saved[0] != digest:
                    raise ValueError("source_revision_conflict")
            cur.execute("""INSERT INTO ops_api_poll_state(source_id,endpoint,checkpoint,last_attempt_at,last_success_at)
                VALUES(%s,'interfaces',%s::jsonb,clock_timestamp(),clock_timestamp())
                ON CONFLICT(source_id,endpoint) DO UPDATE SET checkpoint=excluded.checkpoint,
                last_attempt_at=excluded.last_attempt_at,last_success_at=excluded.last_success_at,error_code=NULL""",
                (source,json.dumps({"currentPage":next_page,"pagination":"offset_catalog_only"})))
    return {"nextPage":next_page,"snapshotId":stored[0],"snapshotCreated":stored[1],"itemCount":len(values)}


def progress_poll(conn,api,source):
    values=api.in_progress()
    # Empty [] is a valid lookup result. It does not prove complete arrival coverage.
    # The attachment has no non-empty T/P/M example; do not guess execution/revision keys.
    error="monitoring_log_contract_required"
    with conn.transaction():
        stored = store_snapshot(conn,source,"in-progress",monitor_preview(values),"monitoring_contract_required",
            api.last_receipt["finished_at"] if getattr(api,"last_receipt",None) else datetime.now(timezone.utc))
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO ops_api_poll_state(source_id,endpoint,checkpoint,last_attempt_at,last_success_at,error_code)
                VALUES(%s,'in-progress',%s::jsonb,clock_timestamp(),clock_timestamp(),%s)
                ON CONFLICT(source_id,endpoint) DO UPDATE SET checkpoint=excluded.checkpoint,
                last_attempt_at=excluded.last_attempt_at,last_success_at=excluded.last_success_at,error_code=excluded.error_code""",
                (source,json.dumps({"count":len(values),"logIngestionReady":False,"arrivalCoverage":"unknown"}),error))
    return {"snapshotId":stored[0],"snapshotCreated":stored[1],"itemCount":len(values)}


def delivery(conn,source):
    """After-commit optimization only; failures leave the persisted job for polling."""
    import urllib.request
    from generation_client import NoRedirect
    from urllib.parse import urlsplit
    url=os.environ.get("DETECTOR_DELIVERY_URL","")
    token=os.environ.get("DETECTOR_DELIVERY_TOKEN","")
    parsed=urlsplit(url)
    if not url:
        return
    if parsed.scheme not in ("http","https") or parsed.hostname not in {"detector","127.0.0.1","localhost"} or parsed.username or parsed.password or parsed.query or parsed.fragment or len(token)<32:
        raise ValueError("detector_delivery_configuration_rejected")
    with conn.cursor() as cur:
        cur.execute("""SELECT q.payload FROM powercode_queue.detection_logs q
            JOIN run_jobs j ON j.id::text=q.payload->>'jobId'
            WHERE q.status='pending' AND q.available_at<=clock_timestamp() AND q.payload->>'sourceId'=%s
            ORDER BY q.created_at LIMIT 10""", (str(source),))
        rows=cur.fetchall()
    conn.commit()
    for (payload,) in rows:
        request=urllib.request.Request(url.rstrip("/")+"/detect",data=json.dumps({"jobId":payload["jobId"],
            "inputManifestHash":payload["inputManifestHash"]}).encode(),headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=2) as response:
                response.read(4096)
        except (OSError,TimeoutError):
            pass


def run(*,once=False):
    load_dotenv(Path(__file__).resolve().parents[1]/".env")
    key=os.environ["COLLECTOR_SOURCE_KEY"]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        source=ensure_source(conn,key);conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(81735,hashtext(%s))",(str(source),))
            if not cur.fetchone()[0]:
                raise RuntimeError("collector_source_already_running")
        conn.commit()
        interval=float(os.environ.get("COLLECTOR_POLL_SECONDS","2"))
        if not .1 <= interval <= 3600:
            raise ValueError("invalid_collector_poll_interval")
        while True:
            cycle_started=time.monotonic()
            # Reload token every poll to permit operator rotation without an image rebuild.
            api=from_environment()
            for endpoint,callback in (("interfaces",catalog_page),("in-progress",progress_poll)):
                try:
                    metadata=callback(conn,api,source)
                    # Callback commits observation/snapshot + checkpoint; measure the actual final commit separately.
                    conn.commit()
                    if api.last_receipt:
                        commit_ms=(time.monotonic()-api.last_receipt["received_monotonic"])*1000 if "received_monotonic" in api.last_receipt else None
                        store_sample(conn,source,endpoint,api.last_receipt,commit_ms,interval,
                            snapshot_id=metadata["snapshotId"],snapshot_created=metadata["snapshotCreated"],item_count=metadata["itemCount"])
                        conn.commit()
                except Exception as exc:
                    conn.rollback()
                    code=str(exc) if isinstance(exc,(SourceUnavailable,ValueError)) else "collection_failed"
                    with conn.cursor() as cur:
                        cur.execute("""INSERT INTO ops_api_poll_state(source_id,endpoint,last_attempt_at,error_code)
                            VALUES(%s,%s,clock_timestamp(),%s) ON CONFLICT(source_id,endpoint)
                            DO UPDATE SET last_attempt_at=excluded.last_attempt_at,error_code=excluded.error_code""",(source,endpoint,code[:100]))
                    if api.last_receipt:
                        store_sample(conn,source,endpoint,api.last_receipt,None,interval,error_code=code[:100])
                    conn.commit()
            delivery(conn,source)
            if once:
                return
            # Start-to-start cadence; requests never overlap to compensate for a slow API.
            time.sleep(max(0,interval-(time.monotonic()-cycle_started)))

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--once",action="store_true")
    run(once=parser.parse_args().once)
