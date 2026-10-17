"""Local isolated PostgreSQL: real CPU model, paced sender and durable decisions."""
import contextlib
import csv
import io
import os
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit
import psycopg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from demo.runtime import prepare,play,status,preflight
from demo.sources import postgres_sources,postgres_rows
from detector.runner import process_detection_message
from pg_queue import claim
from integration_research_pipeline import source,observe


def main():
    dsn=os.environ['DATABASE_URL'];parsed=urlsplit(dsn)
    assert parsed.hostname in ('localhost','127.0.0.1') and parsed.path in ('/qa_demo_replay','/qa_pipeline')
    suffix=uuid.uuid4().hex[:10];key='csv-'+suffix
    with tempfile.TemporaryDirectory() as temp, psycopg.connect(dsn) as conn:
        root=Path(temp)/'artifacts';root.mkdir()
        before_state=conn.execute('SELECT row_to_json(s) FROM ops_system_state s ORDER BY s.id').fetchall();conn.commit()
        assert preflight(conn)['ready'];conn.commit()
        paths=[Path(temp)/name for name in ('EC_TRANSACTION_MONITORING.csv','EC_PROCESS_MONITORING.csv','EC_MESSAGE_BODY.csv')]
        with paths[0].open('w',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['TRANSACTION_ID','PROCESS_COUNT','BODY','RESPONSE_MESSAGE']);writer.writerow(['t',1,'BODY_SECRET','RESPONSE_SECRET'])
        with paths[2].open('w',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['TRANSACTION_ID','PROCESS_ID','MESSAGE_ID','DATA_SIZE','BODY','RESPONSE_MESSAGE']);writer.writerow(['t','p','m',123,'BODY_SECRET','RESPONSE_SECRET'])
        with paths[1].open('w',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['TRANSACTION_ID','PROCESS_ID','RETRY_COUNT','SUCCESS_COUNT','ERROR_COUNT','TOTAL_COUNT','STATUS','BODY'])
            for i in range(1012):
                writer.writerow([f't{i}',f'p{i}',0,10+i%50,i%3 if i<1000 else 100000,100+i%50,'S','BODY_SECRET'])
        kwargs=dict(key=key,kind='csv',process_csv=str(paths[1]),transaction_csv=str(paths[0]),body_csv=str(paths[2]),baseline_limit=1000)
        result=prepare(dsn,root,**kwargs)
        assert result['status']=='ready' and result['inputCount']==1012 and result['contextCounts']=={'T':1,'M':1},result
        same=prepare(dsn,root,**kwargs);assert same['configVersionId']==result['configVersionId']
        try:prepare(dsn,root,**{**kwargs,'baseline_limit':1001})
        except ValueError as exc:assert str(exc)=='run_settings_changed_use_new_run_key'
        else:raise AssertionError('changed run settings accepted')
        config=result['configVersionId'];sid=result['sourceId']
        feature_kinds=conn.execute('SELECT DISTINCT definition_kind FROM ml_feature_versions WHERE definition_key LIKE %s',('demo_%',)).fetchall()
        assert feature_kinds==[('operator_defined',)],feature_kinds
        assert conn.execute('SELECT count(*) FROM run_applications WHERE target_config_version_id=%s',(config,)).fetchone()[0]==0
        assert conn.execute('SELECT row_to_json(s) FROM ops_system_state s ORDER BY s.id').fetchall()==before_state
        raw=conn.execute('SELECT allowed_values::text FROM data_observations WHERE source_id=%s',(sid,)).fetchall()
        assert 'BODY_SECRET' not in str(raw) and 'RESPONSE_SECRET' not in str(raw)
        conn.commit()
        with contextlib.redirect_stdout(io.StringIO()):
            interrupted=play(dsn,key=key,pass_key='one',rate=10000,stop_after=7)
            assert interrupted['passes'][0]['sent']==7 and interrupted['passes'][0]['senderStatus']=='interrupted'
            try:play(dsn,key=key,pass_key='one',rate=9999)
            except ValueError as exc:assert str(exc)=='pass_settings_changed_use_new_pass_key'
            else:raise AssertionError('pass settings changed')
            assert status(dsn,key,'one')['passes'][0]['senderStatus']=='interrupted'
            completed=play(dsn,key=key,pass_key='one',rate=10000)
            assert completed['passes'][0]['sent']==1012 and completed['passes'][0]['senderStatus']=='completed'
            assert play(dsn,key=key,pass_key='one',rate=10000)['passes'][0]['sent']==1012
            repeated=play(dsn,key=key,pass_key='two',rate=10000,repeat=2,stop_after=1020)
            assert repeated['passes'][0]['sent']==1020
        deliveries=conn.execute('SELECT count(*),count(DISTINCT observation_id),count(DISTINCT job_id) FROM data_demo_deliveries r JOIN ops_demo_passes p ON p.id=r.pass_id WHERE p.run_id=(SELECT id FROM ops_demo_runs WHERE run_key=%s)',(key,)).fetchone()
        assert deliveries==(2032,2032,2032),deliveries
        ids=conn.execute('SELECT r.job_id FROM data_demo_deliveries r JOIN ops_demo_passes p ON p.id=r.pass_id WHERE p.run_id=(SELECT id FROM ops_demo_runs WHERE run_key=%s) AND p.pass_key=%s AND (r.sequence<8 OR r.sequence>=1000) ORDER BY r.sequence',(key,'one')).fetchall();conn.commit()
        for (identifier,) in ids:
            message=claim(conn,'detection',message_id=identifier);conn.commit()
            assert message is not None
            process_detection_message(conn,message,root);conn.commit()
        report=status(dsn,key,'one')['passes'][0]
        assert report['decided']==20 and report['complete']==20 and report['failed']==0,report
        assert report['observedSendRate']>0 and report['enqueueDecisionP95Ms']>=0
        saved=conn.execute("SELECT d.ml_status,d.rule_status,d.anomaly_score,d.evidence FROM run_process_decisions d JOIN data_demo_deliveries r ON r.job_id=d.job_id JOIN ops_demo_passes p ON p.id=r.pass_id WHERE p.run_id=(SELECT id FROM ops_demo_runs WHERE run_key=%s)",(key,)).fetchall()
        assert all(r[0]=='succeeded' and r[1]=='skipped' and r[2] is not None and r[3]['demo']['historicalReplay'] for r in saved),saved[:1]
        assert conn.execute('SELECT count(*) FROM run_decision_events e JOIN run_process_decisions d ON d.id=e.decision_id JOIN data_demo_deliveries r ON r.job_id=d.job_id JOIN ops_demo_passes p ON p.id=r.pass_id WHERE p.run_id=(SELECT id FROM ops_demo_runs WHERE run_key=%s)',(key,)).fetchone()[0]==20
        conn.commit()
        # PostgreSQL input is copied through READ ONLY transactions; originals stay unchanged.
        original=source(conn,'demo-origin-'+suffix)
        for i in range(1000):
            observe(conn,original,f'o{i}',{'status':'S','success_count':str(10+i%50),'error_count':str(i%3),'total_count':str(100+i%50),'BODY':'BODY_SECRET'})
        before=conn.execute('SELECT id,content_hash,allowed_values FROM data_observations WHERE source_id=%s ORDER BY id',(original,)).fetchall();conn.commit()
        assert any(r['sourceId']==str(original) and r['processCount']==1000 for r in postgres_sources(dsn))
        assert len(list(postgres_rows(dsn,original,2000)))==1000
        pg=prepare(dsn,root,key='pg-'+suffix,kind='postgres',origin_source=str(original),baseline_limit=1000)
        assert pg['status']=='ready' and pg['inputCount']==1000
        assert conn.execute('SELECT id,content_hash,allowed_values FROM data_observations WHERE source_id=%s ORDER BY id',(original,)).fetchall()==before
        assert conn.execute('SELECT row_to_json(s) FROM ops_system_state s ORDER BY s.id').fetchall()==before_state
        conn.commit()
        for query,params in [
            ("UPDATE data_demo_inputs SET values_json='{}' WHERE run_id=(SELECT id FROM ops_demo_runs WHERE run_key=%s)",(key,)),
            ("UPDATE ops_demo_runs SET config_version_id=NULL WHERE run_key=%s",(key,))]:
            try:conn.execute(query,params);conn.commit()
            except psycopg.Error:conn.rollback()
            else:raise AssertionError('immutable demo data rewritten')
        print('PASS: actual CPU IF trained from 1,000 CSV reference rows; T/M safe metadata imported')
        print('PASS: 2,032 unique observation/job deliveries; interruption/resume, completed-pass idempotency, repeat/new pass')
        print('PASS: 20/20 actual ML decisions + durable events, Rule skipped, demo evidence, measured send/decision latency')
        print('PASS: PostgreSQL capture + actual CPU training, original rows/global active configuration unchanged')
        print('PASS: immutable input/config and changed-setting rejection')
        print({'testedDemoReport':report})

if __name__=='__main__':main()
