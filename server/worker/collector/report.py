"""Observe the running Collector's samples without issuing additional source requests."""
import argparse
import json
import os
from pathlib import Path
import time
import psycopg
from dotenv import load_dotenv


def report(conn,source_key,minutes):
    with conn.cursor() as cur:
        cur.execute("""WITH selected AS (
            SELECT p.*,extract(epoch FROM started_at-lag(started_at) OVER (PARTITION BY endpoint ORDER BY started_at)) AS gap
            FROM ops_api_poll_samples p JOIN ops_sources s ON s.id=p.source_id
            WHERE s.dataset_key=%s AND p.started_at>=clock_timestamp()-(%s*interval '1 minute'))
            SELECT endpoint,count(*) AS requests,count(*) FILTER(WHERE error_code IS NOT NULL) AS failures,
            count(*) FILTER(WHERE item_count=0 AND error_code IS NULL) AS empty_responses,
            count(*) FILTER(WHERE item_count>0 AND error_code IS NULL) AS nonempty_responses,
            percentile_cont(.50) WITHIN GROUP(ORDER BY duration_ms) AS p50_ms,
            percentile_cont(.95) WITHIN GROUP(ORDER BY duration_ms) AS p95_ms,
            percentile_cont(.99) WITHIN GROUP(ORDER BY duration_ms) AS p99_ms,
            percentile_cont(.95) WITHIN GROUP(ORDER BY db_commit_ms) AS persist_p95_ms,
            percentile_cont(.95) WITHIN GROUP(ORDER BY gap) AS poll_gap_p95_seconds,
            max(started_at) AS last_sample_at,sum(response_bytes) AS response_bytes,
            count(*) FILTER(WHERE snapshot_created) AS new_snapshots
            FROM selected GROUP BY endpoint ORDER BY endpoint""",(source_key,minutes))
        columns=[col.name for col in cur.description]
        rows=[dict(zip(columns,row)) for row in cur.fetchall()]
        for row in rows:
            row['error_rate']=row['failures']/row['requests']
            row['empty_rate']=row['empty_responses']/row['requests']
            row['last_sample_at']=row['last_sample_at'].isoformat()
        cur.execute("""SELECT endpoint,error_code,checkpoint FROM ops_api_poll_state p JOIN ops_sources s ON s.id=p.source_id
            WHERE s.dataset_key=%s ORDER BY endpoint""",(source_key,))
        states=[{'endpoint':row[0],'errorCode':row[1],'checkpoint':row[2]} for row in cur.fetchall()]
    return {'windowMinutes':minutes,'sourceKey':source_key,'endpoints':rows,'collectionState':states,
        'sourceLatencyMs':None,'sourceClockStatus':'unknown',
        'measurementScope':'HTTP response + receive-to-DB-commit + poll cadence; source occurrence latency unverified'}


def judge(value,*,p95_ms=None,error_rate=None,require_nonempty=False,min_samples=20):
    reasons=[]
    for endpoint in ('interfaces','in-progress'):
        rows=[row for row in value['endpoints'] if row['endpoint']==endpoint]
        if not rows or rows[0]['requests']<min_samples:
            reasons.append(endpoint+':insufficient_samples');continue
        row=rows[0]
        if p95_ms is not None and row['p95_ms']>p95_ms:reasons.append(endpoint+':p95_exceeded')
        if error_rate is not None and row['error_rate']>error_rate:reasons.append(endpoint+':error_rate_exceeded')
        if require_nonempty and endpoint=='in-progress' and not row['nonempty_responses']:
            reasons.append(endpoint+':no_nonempty_monitoring_response')
    specified=p95_ms is not None or error_rate is not None or require_nonempty
    return {'status':'failed' if reasons else 'passed' if specified else 'measured_without_slo',
        'reasonCodes':reasons,'limits':{'p95Ms':p95_ms,'errorRate':error_rate,'requireNonempty':require_nonempty,'minSamples':min_samples}}


def main():
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window-minutes',type=float,default=10)
    parser.add_argument('--watch-seconds',type=float,default=0)
    parser.add_argument('--every-seconds',type=float,default=5)
    parser.add_argument('--p95-limit-ms',type=float)
    parser.add_argument('--max-error-rate',type=float)
    parser.add_argument('--require-nonempty',action='store_true')
    parser.add_argument('--min-samples',type=int,default=20)
    args=parser.parse_args()
    if not 0<args.window_minutes<=10080 or args.watch_seconds<0 or args.every_seconds<.1 or args.min_samples<1 or (
        args.max_error_rate is not None and not 0<=args.max_error_rate<=1) or (args.p95_limit_ms is not None and args.p95_limit_ms<=0):
        parser.error('invalid measurement settings')
    end=time.monotonic()+args.watch_seconds
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        while True:
            value=report(conn,os.environ['COLLECTOR_SOURCE_KEY'],args.window_minutes);conn.commit()
            value['assessment']=judge(value,p95_ms=args.p95_limit_ms,error_rate=args.max_error_rate,
                require_nonempty=args.require_nonempty,min_samples=args.min_samples)
            print(json.dumps(value,ensure_ascii=False,allow_nan=False),flush=True)
            if time.monotonic()>=end:
                raise SystemExit(2 if value['assessment']['status']=='failed' else 0)
            time.sleep(min(args.every_seconds,max(.01,end-time.monotonic())))

if __name__=='__main__':main()
