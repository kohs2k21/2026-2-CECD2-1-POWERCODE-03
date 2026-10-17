"""Local-only role boundaries and read-only capture from the initial schema."""
import contextlib,io,json,os,tempfile,uuid
from pathlib import Path
import psycopg
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from psycopg import sql
from collector.storage import ingest_observation
from demo.sources import postgres_rows
from demo.runtime import prepare,play
from detector.runner import process_detection_message
from pg_queue import claim

dsn=os.environ['DATABASE_URL']
from urllib.parse import urlsplit
parsed=urlsplit(dsn)
assert parsed.hostname in ('localhost','127.0.0.1') and parsed.path=='/qa_pipeline'
infra=Path(os.environ.get('RUNTIME_INFRA_ROOT',str(Path(__file__).resolve().parents[2]/'infra')))
admin=dsn.rsplit('/',1)[0]+'/postgres'
legacy_name='qa_demo_legacy_'+uuid.uuid4().hex[:10]
legacy=dsn.rsplit('/',1)[0]+'/'+legacy_name
with psycopg.connect(admin,autocommit=True) as c:c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(legacy_name)))
with psycopg.connect(legacy,autocommit=True) as c:
    for name in ('001_postgres_queues.sql','002_core_erd.sql'):c.execute(((infra/'migrations')/name).read_text())
sid=uuid.uuid4()
with psycopg.connect(legacy) as c:
    c.execute("INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at) VALUES(%s,%s,'legacy','original','v1','{}','{}',clock_timestamp())",(sid,str(sid)))
    for i in range(1000):
        eid=uuid.uuid4()
        c.execute("INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at) VALUES(%s,%s,'P',%s,'{}','legacy',clock_timestamp())",(eid,sid,str(i)))
        ingest_observation(c,source_id=sid,layer='P',dedup_key=str(i),raw={'status':'S','success_count':10+i%50,'error_count':i%3,'total_count':100+i%50},execution_id=eid,revision_no=1)
assert len(list(postgres_rows(legacy,sid,10000)))==1000
with psycopg.connect(dsn) as c:
    c.execute((infra/'scripts/demo-role.sql').read_text())
    c.execute((infra/'scripts/database-roles.sql').read_text())
demo_dsn=dsn+'?options=-c%20role%3Dpowercode_demo'
detector_dsn=dsn+'?options=-c%20role%3Dpowercode_detector'
with tempfile.TemporaryDirectory() as temp:
    key='role-'+uuid.uuid4().hex[:10]
    result=prepare(demo_dsn,Path(temp),key=key,kind='postgres',source_dsn=legacy,origin_source=str(sid),baseline_limit=1000)
    assert result['status']=='ready'
    with contextlib.redirect_stdout(io.StringIO()):sent=play(demo_dsn,key=key,pass_key='ten-per-second',rate=10,stop_after=3)
    with psycopg.connect(dsn) as c:
        jobs=c.execute('SELECT r.job_id,r.sent_at FROM data_demo_deliveries r JOIN ops_demo_passes p ON p.id=r.pass_id JOIN ops_demo_runs d ON d.id=p.run_id WHERE d.run_key=%s ORDER BY r.sequence',(key,)).fetchall()
        assert len(jobs)==3
        gaps=[(jobs[i+1][1]-jobs[i][1]).total_seconds() for i in range(2)]
        assert all(g>=.09 for g in gaps),gaps
    os.environ['DATABASE_URL']=detector_dsn
    with psycopg.connect(detector_dsn) as c:
        for identifier,_ in jobs:
            message=claim(c,'detection',message_id=identifier);c.commit()
            process_detection_message(c,message,Path(temp));c.commit()
    with psycopg.connect(dsn) as c:
        assert c.execute('SELECT count(*) FROM run_process_decisions WHERE job_id=ANY(%s) AND ml_status=%s',([r[0] for r in jobs],'succeeded')).fetchone()[0]==3
    try:
        with psycopg.connect(demo_dsn) as c:c.execute('UPDATE ops_system_state SET revision=revision+1')
    except psycopg.errors.InsufficientPrivilege:pass
    else:raise AssertionError('demo role can change active pointer')
print(f'PASS Python {sys.version_info.major}.{sys.version_info.minor}: source-only 001/002 schema, 1,000 rows -> actual CPU training by restricted demo role')
print('PASS: rate 10 actual send gaps >= 90ms, 3/3 decisions by restricted detector role, demo active-pointer UPDATE denied')
