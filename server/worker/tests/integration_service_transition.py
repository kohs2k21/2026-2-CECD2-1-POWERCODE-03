"""Local isolated DB: modes, durable ownership, queue isolation and catalog restart."""
import hashlib
import json
import os
import sys
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit
import psycopg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from integration_research_pipeline import observe,job
from collector.api_runner import catalog_page,progress_poll
from detector.jobs import enqueue_observation
from detector.runner import process_detection_message
from detector.service import serve
from pg_queue import claim,enqueue
from trainer.runner import run
import generation_client

class Fixtures:
    def generate(self,kind,request):
        assert kind=='explain'
        return {'truncated':False,'output':{'factIds':[f['id'] for f in request['facts']],'nextChecks':['review_input_completeness']}}

class Catalog:
    broken=False
    def interfaces(self,page):
        return {'totalPages':2,'content':[{'interfaceId':'I','revision':1,
            'commonInfo':{'status':'CHANGED' if self.broken else 'E'},'processInfo':[]}]}
    def in_progress(self):return []


def main():
    dsn=os.environ['DATABASE_URL'];parsed=urlsplit(dsn)
    assert parsed.hostname in ('localhost','127.0.0.1') and parsed.path=='/qa_research_pipeline'
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT source_id,configuration_spec,id FROM ml_config_versions ORDER BY created_at LIMIT 1')
            sid,config,old_config=cur.fetchone()
            cur.execute('SELECT selection_spec FROM data_snapshots WHERE id=%s',(config['trainingSnapshotId'],))
            period=cur.fetchone()[0]
        conn.commit()
        test_key='transition-'+str(uuid.uuid4())
        api=Catalog()
        assert catalog_page(conn,api,sid)["nextPage"]==1;conn.commit()
        with psycopg.connect(dsn) as restart:
            assert catalog_page(restart,api,sid)["nextPage"]==0;restart.commit()
        api.broken=True
        try:catalog_page(conn,api,sid)
        except ValueError as exc:assert str(exc)=='source_revision_conflict'
        else:raise AssertionError('changed revision accepted')
        conn.rollback()
        progress_poll(conn,api,sid);conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT checkpoint->>'currentPage' FROM ops_api_poll_state WHERE source_id=%s AND endpoint='interfaces'",(sid,))
            assert cur.fetchone()[0]=='0'
            cur.execute("SELECT error_code,checkpoint->>'logIngestionReady' FROM ops_api_poll_state WHERE source_id=%s AND endpoint='in-progress'",(sid,))
            assert cur.fetchone()==('monitoring_log_contract_required','false')
        conn.commit()
        print('PASS: catalog page restart, revision-conflict rollback, no fabricated log coverage')
        trained=job(conn,{'kind':'train','sourceId':str(sid),'from':period['from'],'until':period['until'],
            'featureSetId':config['featureSetId'],'ruleVersionIds':config['ruleVersionIds'],
            'snapshotId':config['trainingSnapshotId'],'detectionPolicy':{'mode':'rule_only','deadlineMs':1000,'combination':'selected_path'}})
        evaluated=job(conn,{'kind':'evaluate','sourceId':str(sid),'configVersionId':trained['configVersionId'],
            'snapshotId':trained['snapshotId'],'purpose':'validation'})
        assert evaluated['applicability']=='valid' and evaluated['scoredCount']>0
        live=observe(conn,sid,test_key+'-rule-only-live',{'error_count':'90','total_count':'100'})
        payload=enqueue_observation(conn,live.observation_id,config_id=trained['configVersionId']);conn.commit()
        conn.execute("SET TIME ZONE 'Asia/Seoul'")
        same=enqueue_observation(conn,live.observation_id,config_id=trained['configVersionId']);conn.commit()
        assert same['jobId']==payload['jobId'] and same['inputManifestHash']==payload['inputManifestHash']
        conn.execute("SET TIME ZONE 'UTC'");conn.commit()
        message=claim(conn,'detection',message_id=uuid.UUID(payload['jobId']));conn.commit()
        process_detection_message(conn,message,None);conn.commit()
        assert claim(conn,'detection',message_id=uuid.UUID(payload['jobId'])) is None;conn.commit()
        with conn.cursor() as cur:
            cur.execute('SELECT id,status,rule_status,ml_status,evidence,job_id FROM run_process_decisions WHERE job_id=%s',(payload['jobId'],))
            row=cur.fetchone()
            assert row[1:4]==('complete','succeeded','skipped') and row[4]['anomalous'] is True
            cur.execute('SELECT count(*) FROM data_feature_materializations WHERE observation_id=%s',(live.observation_id,));assert cur.fetchone()[0]==1
            cur.execute('SELECT count(*) FROM run_decision_events WHERE decision_id=%s',(row[0],));assert cur.fetchone()[0]==1
            decision_id=row[0]
        conn.commit()
        # Missing artifacts in parallel leaves confirmed Rule results intact.
        other=observe(conn,sid,test_key+'-parallel-missing-artifact',{'error_count':'90','total_count':'100'})
        partial=enqueue_observation(conn,other.observation_id,config_id=old_config);conn.commit()
        message=claim(conn,'detection',message_id=uuid.UUID(partial['jobId']));conn.commit()
        process_detection_message(conn,message,None);conn.commit()
        with conn.cursor() as cur:
            cur.execute('SELECT status,evidence FROM run_process_decisions WHERE job_id=%s',(partial['jobId'],));result=cur.fetchone()
            assert result[0]=='partial' and result[1]['anomalous'] is True
        conn.commit()
        print('PASS: Rule-only candidate/evaluation/decision, selected paths, parallel model outage retains anomaly')
        evidence={'decisionId':str(decision_id),'status':'complete','ruleStatus':'succeeded','mlStatus':'skipped'}
        digest=hashlib.sha256(json.dumps(evidence,separators=(',',':')).encode()).hexdigest()
        explanation=uuid.uuid4();request={'kind':'explain','sourceId':str(sid),'decisionId':str(decision_id),
            'configVersionId':trained['configVersionId'],'savedEvidence':evidence,'savedEvidenceJson':json.dumps(evidence,separators=(',',':'))}
        llm={'evidence_version':'saved-decision-v1','input_evidence_hash':digest,'model_version':'test-base',
            'prompt_version':'grounded-facts-v1','output_schema_version':'fact-ids-checks-v1','reuse_key':str(explanation)}
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO run_jobs(id,kind,status,requested_by_actor_id,requester_role,idempotency_key,
                request_hash,input_spec,input_config_version_id,input_decision_id,contract_version,queue,
                producer_kind,producer_key,llm_spec,max_attempts,attempt_count,next_run_at,created_at)
                VALUES(%s,'explain','queued','test-user','user',%s,%s,%s::jsonb,%s,%s,'v03','llm_explanation',
                'user','test-user',%s::jsonb,3,0,clock_timestamp(),clock_timestamp())""",
                (explanation,str(explanation),digest,json.dumps(request),trained['configVersionId'],decision_id,json.dumps(llm)))
        explanation_message=enqueue(conn,'llm_explanation',{'schemaVersion':1,'jobId':str(explanation)});conn.commit()
        run(once=True) # Trainer must leave explanation queued.
        with conn.cursor() as cur:
            cur.execute('SELECT status FROM run_jobs WHERE id=%s',(explanation,));assert cur.fetchone()[0]=='queued'
        conn.commit();generation_client.set_local_model(Fixtures())
        try:
            from trainer.runner import _claim_job,_execute,_finish
            msg=claim(conn,'llm_explanation',message_id=explanation_message);conn.commit()
            owned,attempt,token=_claim_job(conn,msg,'fixture-llm');conn.commit()
            result=_execute(conn,owned,attempt,None,msg,token);conn.commit()
            _finish(conn,msg,owned,attempt,token,result,None,True);conn.commit()
        finally:generation_client.set_local_model(None)
        with conn.cursor() as cur:
            cur.execute('SELECT output,decision_id FROM run_llm_results WHERE job_id=%s',(explanation,));output=cur.fetchone()
            assert output[0]['causeStatus']=='unconfirmed' and output[1]==decision_id
        conn.commit()
        # Reclaimed attempt fences the old owner; direct delivery and poll share exact queue id.
        new=observe(conn,sid,test_key+'-direct-fenced',{'error_count':'90','total_count':'100'})
        incoming=enqueue_observation(conn,new.observation_id,config_id=trained['configVersionId']);conn.commit()
        stale=claim(conn,'detection',message_id=uuid.UUID(incoming['jobId']));conn.commit()
        with conn.cursor() as cur:
            cur.execute("UPDATE powercode_queue.detection_logs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(stale.id,))
        conn.commit()
        fresh=claim(conn,'detection',message_id=stale.id);conn.commit()
        try:process_detection_message(conn,stale,None)
        except RuntimeError:pass
        else:raise AssertionError('stale owner published')
        conn.rollback()
        # Stale owner cannot finalize; subsequent retry expires its short attempt lease in this simulated race.
        with conn.cursor() as cur:
            cur.execute("UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s AND status='running'",(incoming['jobId'],))
        conn.commit()
        process_detection_message(conn,fresh,None);conn.commit()
        with conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM run_process_decisions WHERE job_id=%s',(incoming['jobId'],));assert cur.fetchone()[0]==1
        conn.commit()
        # Keep fixed work executable after an active-pointer change.
        with conn.cursor() as cur:
            cur.execute('SELECT active_config_version_id FROM ops_system_state WHERE id=1')
            previous_active=cur.fetchone()[0]
        waiting=observe(conn,sid,test_key+'-pinned',{'error_count':'90','total_count':'100'})
        pinned=enqueue_observation(conn,waiting.observation_id,config_id=trained['configVersionId']);conn.commit()
        try:
            conn.execute('UPDATE ops_system_state SET active_config_version_id=NULL WHERE id=1');conn.commit()
            from detector.runner import run as detect_once
            detect_once(once=True)
            assert conn.execute('SELECT status FROM run_jobs WHERE id=%s',(pinned['jobId'],)).fetchone()[0]=='succeeded'
            conn.commit()
        finally:
            conn.execute('UPDATE ops_system_state SET active_config_version_id=%s WHERE id=1',(previous_active,));conn.commit()
        exhausted=observe(conn,sid,test_key+'-exhausted',{'error_count':'90','total_count':'100'})
        item=enqueue_observation(conn,exhausted.observation_id,config_id=trained['configVersionId']);conn.commit()
        msg=claim(conn,'detection',message_id=uuid.UUID(item['jobId']),max_attempts=1);conn.commit()
        owned,attempt,token=_claim_job(conn,msg,'crashed-test');conn.commit()
        conn.execute("UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(attempt,))
        conn.execute("UPDATE powercode_queue.detection_logs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(msg.id,));conn.commit()
        assert claim(conn,'detection',message_id=msg.id,max_attempts=1) is None;conn.commit()
        from pg_queue import reconcile_dead_jobs
        reconcile_dead_jobs(conn,'detection');conn.commit()
        assert conn.execute('SELECT status,error_code FROM run_jobs WHERE id=%s',(owned['id'],)).fetchone()==('failed','queue_exhausted')
        conn.commit()
        print('PASS: frozen job after pointer change; exhausted crashed attempt becomes terminal failed')
        print('PASS: Trainer/LLM queue isolation, user explanation lineage, stale delivery cannot publish, single decision event')

if __name__=='__main__':main()
