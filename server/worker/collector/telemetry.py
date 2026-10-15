"""Append measurements and sanitized snapshots; no auth headers or raw BODY storage."""
from datetime import datetime, timezone
import hashlib
import json
import math
import uuid

# A preview projection, not an inferred execution/revision mapping contract.
FIELDS = {"transactionId","interfaceId","processId","messageId","processHub","channelId","status",
    "responseCode","retryCount","successCount","errorCount","totalCount","startTime","endTime",
    "TRANSACTION_ID","INTERFACE_ID","PROCESS_ID","MESSAGE_ID","STATUS","RESPONSE_CODE","RETRY_COUNT",
    "SUCCESS_COUNT","ERROR_COUNT","TOTAL_COUNT","START_TIME","END_TIME"}


def monitor_preview(values):
    output=[]
    for value in values:
        if not isinstance(value,dict):
            output.append({"recognizedFields":{},"unrecognizedFieldCount":0,"shape":"non_object"})
            continue
        recognized={key:item for key,item in value.items() if key in FIELDS and
            (item is None or isinstance(item,(str,int,bool)) or isinstance(item,float) and math.isfinite(item))}
        output.append({"recognizedFields":recognized,"unrecognizedFieldCount":len(value)-len(recognized),"shape":"object"})
    return output


def store_snapshot(conn,source,endpoint,values,mapping_status,received_at):
    encoded=json.dumps(values,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    digest=hashlib.sha256(encoded.encode()).hexdigest()
    identifier=uuid.uuid5(uuid.NAMESPACE_URL,f"api-snapshot:{source}:{endpoint}:{digest}")
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO data_api_snapshots(id,source_id,endpoint,content_hash,values_json,mapping_status,item_count,received_at)
            VALUES(%s,%s,%s,%s,%s::jsonb,%s,%s,%s) ON CONFLICT(source_id,endpoint,content_hash) DO NOTHING RETURNING id""",
            (identifier,source,endpoint,digest,encoded,mapping_status,len(values),received_at))
        created=cur.fetchone() is not None
        if not created:
            cur.execute("SELECT id FROM data_api_snapshots WHERE source_id=%s AND endpoint=%s AND content_hash=%s",(source,endpoint,digest))
            identifier=cur.fetchone()[0]
    return identifier,created


def store_sample(conn,source,endpoint,receipt,commit_ms,interval, *, snapshot_id=None,snapshot_created=None,item_count=None,error_code=None):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO ops_api_poll_samples(id,source_id,endpoint,started_at,finished_at,duration_ms,
            db_commit_ms,http_status,response_code,error_code,response_bytes,item_count,snapshot_id,snapshot_created,poll_interval_seconds)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (uuid.uuid4(),source,endpoint,receipt["started_at"],receipt["finished_at"],receipt["duration_ms"],commit_ms,
            receipt["http_status"],receipt["response_code"],error_code or receipt["error_code"],receipt["response_bytes"],
            item_count,snapshot_id,snapshot_created,interval))
