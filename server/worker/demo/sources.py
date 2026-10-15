"""Read-only source capture; no PGDATA files, credentials or BODY are staged."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from typing import Iterator
import uuid
import psycopg
from collector.replay_csv import CsvInput,iter_csv_rows,_canonical_header
from collector.storage import project_observation


def project_process(raw, typed=None):
    value={_canonical_header(k):v for k,v in raw.items() if isinstance(k,str)} if isinstance(raw,dict) else {}
    if typed:
        value.update({k:v for k,v in typed.items() if v is not None})
    value.pop('response_message',None)
    allowed,_=project_observation('P',value)
    return allowed


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def postgres_sources(dsn):
    with psycopg.connect(dsn,options='-c default_transaction_read_only=on',connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT s.id,s.dataset_key,s.display_name,count(p.observation_id)
                FROM ops_sources s LEFT JOIN data_observations o ON o.source_id=s.id AND o.layer='P'
                LEFT JOIN data_processes p ON p.observation_id=o.id
                WHERE s.ingestion_method<>'demo-replay' GROUP BY s.id ORDER BY s.created_at,s.id""")
            return [{'sourceId':str(r[0]),'datasetKey':r[1],'displayName':r[2],'processCount':r[3]} for r in cur.fetchall()]


def postgres_rows(dsn,source_id,limit) -> Iterator[dict]:
    with psycopg.connect(dsn,options='-c default_transaction_read_only=on',connect_timeout=10) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        label_available=conn.execute("SELECT to_regclass('public.data_label_evidence') IS NOT NULL").fetchone()[0]
        source=conn.execute('SELECT ingestion_method FROM ops_sources WHERE id=%s',(source_id,)).fetchone()
        if source is None or source[0]=='demo-replay':
            raise ValueError('original_source_required')
        label_join="""LEFT JOIN LATERAL(SELECT label::text FROM data_label_evidence WHERE observation_id=o.id
                ORDER BY revision_no DESC LIMIT 1) l ON true""" if label_available else ''
        label_select='l.label' if label_available else 'NULL::text'
        with conn.cursor(name='demo_original_read') as cur:
            cur.itersize=500
            cur.execute(f"""SELECT o.id,o.execution_id,o.allowed_values,row_to_json(p),{label_select}
                FROM data_observations o JOIN data_processes p ON p.observation_id=o.id {label_join}
                WHERE o.source_id=%s AND o.layer='P' ORDER BY o.persisted_at,o.id LIMIT %s""",(source_id,limit))
            for identifier,execution,raw,typed,label in cur:
                yield {'raw':project_process(raw,typed),'group':str(execution or identifier),
                    'originObservationId':str(identifier),'label':label}


def csv_rows(path:Path,limit:int) -> Iterator[dict]:
    from itertools import islice
    # Hash the actual file as provenance; ignore excluded values when staging.
    initial=path.stat()
    hasher=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):hasher.update(block)
    fingerprint=hasher.hexdigest()
    for row in islice(iter_csv_rows(CsvInput('P',path)),limit):
        group=json.dumps(row.identity,separators=(',',':')) if row.identity else f'row:{row.row_number}'
        yield {'raw':project_process(row.allowed),'group':f'csv:{fingerprint}:{group}',
               'originObservationId':None,'label':None}
    final=path.stat()
    if (initial.st_ino,initial.st_size,initial.st_mtime_ns)!=(final.st_ino,final.st_size,final.st_mtime_ns):
        raise ValueError('csv_changed_during_capture')


def numeric_features(rows):
    """Measured deterministic CPU demo profile; no LLM or fabricated rules/scores."""
    definitions=[]
    for field in ('success_count','error_count','total_count','retry_count'):
        values=[]
        for row in rows:
            raw=row['raw'].get(field)
            try:
                value=float(raw)
                if not isinstance(raw,bool) and math.isfinite(value):values.append(value)
            except (TypeError,ValueError,OverflowError):pass
        if len(values)>=max(1,math.ceil(len(rows)*.9)) and len(set(values))>1:
            definitions.append({'name':'demo_'+field,'expression':{'op':'field','field':'P.'+field}})
    if not definitions:
        raise ValueError('demo_reference_has_no_observed_varying_numeric_feature')
    return definitions
