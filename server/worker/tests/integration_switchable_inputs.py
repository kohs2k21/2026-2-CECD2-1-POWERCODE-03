"""Local-only: real input HTTP/UI APIs + generated fixture + real CPU Rule/IF pipeline."""
import csv,json,os,shutil,socket,subprocess,sys,tempfile,threading,time,uuid
from datetime import datetime,timedelta,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import psycopg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collector.api_client import CustomerAPI
from collector.input_store import ensure_source,stage_csv,switch
from collector.input_runner import tick,read_state
from detector.runner import process_detection_message
from detector.jobs import claim_waiting_input
from pg_queue import claim
from trainer.runner import run
from research_fixture import ContractFixture
from generation_service import serve


def main():
    dsn=os.environ['DATABASE_URL'];parsed=urlsplit(dsn)
    assert parsed.hostname in ('localhost','127.0.0.1') and parsed.path=='/qa_pipeline'
    bundle=Path(os.environ['RESEARCH_BUNDLE_ROOT']);backend=Path(__file__).resolve().parents[2]/'backend'
    generated=serve(ContractFixture(bundle),'input-contract-test-token-32-characters',port=0)
    threading.Thread(target=generated.serve_forever,daemon=True).start()
    state={'records':[],'callback':None,'fail':False,'calls':0}
    class API(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            assert self.headers['Authorization']=='Bearer source-test-token'
            assert self.path=='/monitoring/transactions/in-progress';state['calls']+=1
            if state['callback']:
                fn=state['callback'];state['callback']=None;fn()
            self.send_response(401 if state['fail'] else 200);self.end_headers()
            if not state['fail']:self.wfile.write(json.dumps({'responseCode':'0000','responseObject':state['records']}).encode())
        def do_POST(self):
            assert self.path=='/configuration/interfaces/list'
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'responseCode':'0000','responseObject':{'totalPages':1,'content':[]}}).encode())
    api=ThreadingHTTPServer(('127.0.0.1',0),API);threading.Thread(target=api.serve_forever,daemon=True).start()
    api_url=f'http://127.0.0.1:{api.server_port}'
    factory=lambda:CustomerAPI(api_url,api_url,'source-test-token',allowed_http_host='127.0.0.1')
    child=None
    try:
        with tempfile.TemporaryDirectory() as temp,psycopg.connect(dsn) as conn:
            temp=Path(temp);root=temp/'artifacts';root.mkdir()
            os.environ.update(ARTIFACT_ROOT=str(root),FEATURE_DESIGN_PROVIDER='research',
                DESIGN_MODEL_URL=f'http://127.0.0.1:{generated.server_port}',DESIGN_MODEL_TOKEN='input-contract-test-token-32-characters')
            sid=ensure_source(conn,'shared-input-'+uuid.uuid4().hex[:10]);conn.commit()
            files={}
            specs=[('T','EC_TRANSACTION_MONITORING.csv',['TRANSACTION_ID','PROCESS_COUNT'],[['t0',1]]),
                ('P','EC_PROCESS_MONITORING.csv',['TRANSACTION_ID','PROCESS_ID','RETRY_COUNT','ERROR_COUNT','TOTAL_COUNT'],[[f't{i}','p',0,i%7,100] for i in range(1000)]),
                ('M','EC_MESSAGE_MONITORING.csv',['TRANSACTION_ID','PROCESS_ID','MESSAGE_ID','PROCESS_DT','SUCCESS_COUNT'],[['t0','p','m0','2026-10-01 10:00:00','\\N']]),
                ('B','EC_MESSAGE_BODY.csv',['MESSAGE_ID','MESSAGE_IN','MESSAGE_OUT','MESSAGE_BODY'],[['m0','BODY_SECRET','BODY_SECRET','BODY_SECRET']])]
            for layer,name,headers,rows in specs:
                files[layer]=temp/name
                with files[layer].open('w',newline='') as f:w=csv.writer(f);w.writerow(headers);w.writerows(rows)
            replay=stage_csv(conn,sid,'reference',files);conn.commit()
            assert stage_csv(conn,sid,'reference',files)==replay;conn.commit()
            sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
            env={'PATH':os.environ['PATH'],'DATABASE_URL':dsn,'JWT_SECRET':'input-local-api-secret','PORT':str(port),
                'USER_DATA_PATH':str(temp/'users.json'),'SEED_ADMIN_EMAIL':'admin@example.test','SEED_ADMIN_PASSWORD':'test-password',
                'SEED_USER_EMAIL':'user@example.test','SEED_USER_PASSWORD':'test-password'}
            child=subprocess.Popen([shutil.which('node'),str(backend/'dist/index.js')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            base=f'http://127.0.0.1:{port}'
            def call(path,body=None,token=None):
                request=Request(base+path,data=None if body is None else json.dumps(body).encode(),
                    headers={**({'Authorization':'Bearer '+token} if token else {}),'Content-Type':'application/json'})
                try:
                    with urlopen(request,timeout=10) as r:return r.status,json.load(r)
                except HTTPError as e:return e.code,json.load(e)
            for _ in range(60):
                try:
                    if call('/health')[0]==200:break
                except OSError:pass
                time.sleep(.1)
            admin=call('/api/auth/login',{'email':'admin@example.test','password':'test-password'})[1]['token']
            user=call('/api/auth/login',{'email':'user@example.test','password':'test-password'})[1]['token']
            assert call('/api/inputs?sourceId='+str(sid),token=user)[0]==403
            def change(mode,**options):
                current=call('/api/inputs?sourceId='+str(sid),token=admin)[1]['control']
                code,out=call('/api/inputs/'+str(sid)+'/mode',{'mode':mode,'expectedRevision':current['revision'],**options},admin)
                assert code==200,(code,out)
                return out
            first=change('csv',replayId=str(replay),rate=10000,repeat=1)
            assert call('/api/inputs/'+str(sid)+'/mode',{'mode':'paused','expectedRevision':0},admin)[0]==409
            def send_csv(n):
                start=read_state(conn,sid)['position']
                while read_state(conn,sid)['position']<start+n:
                    result=tick(conn,sid,collect_catalog=False)
                    assert 'errorCode' not in result,result
                    if result.get('idle'):time.sleep(result['wait'])
            send_csv(5);change('paused')
            paused=read_state(conn,sid)['position'];assert tick(conn,sid)['idle'];assert read_state(conn,sid)['position']==paused
            resumed=change('csv',replayId=str(replay),rate=10000,repeat=1);assert resumed['runId']==first['runId']
            send_csv(1003-paused);tick(conn,sid,collect_catalog=False)
            assert read_state(conn,sid)['status']=='completed'
            layers=dict(conn.execute('SELECT layer::text,count(*) FROM data_observations WHERE source_id=%s GROUP BY layer',(sid,)).fetchall());conn.commit()
            assert layers=={'T':1,'P':1000,'M':1,'B':1},layers
            assert conn.execute('SELECT count(*) FROM data_message_bodies WHERE source_id=%s AND collection_status=%s AND message_body IS NULL',(sid,'not_collected')).fetchone()[0]==1
            assert 'BODY_SECRET' not in str(conn.execute('SELECT values_json FROM data_input_replay_rows WHERE replay_id=%s',(replay,)).fetchall());conn.commit()
            now=datetime.now(timezone.utc);period={'sourceId':str(sid),'from':(now-timedelta(hours=1)).isoformat().replace('+00:00','Z'),'until':now.isoformat().replace('+00:00','Z')}
            def job(spec):
                code,response=call('/api/jobs',spec,admin);assert code==201,(code,response)
                run(once=True,role='llm' if spec['kind'].startswith('recommend') else 'trainer')
                row=conn.execute('SELECT status,result_summary,error_code FROM run_jobs WHERE id=%s',(response['id'],)).fetchone();conn.commit()
                assert row[0]=='succeeded',row
                return response['id'],row[1]
            _,features=job({**period,'kind':'recommend_feature'})
            assert features['materializedCount']==1000
            rid,rules=job({**period,'kind':'recommend_rule','snapshotId':features['snapshotId'],'featureSetId':features['featureSetId']})
            assert rules['suggestions']
            code,selected=call('/api/jobs/'+rid+'/recommendations/0/adopt',{},admin);assert code==201,(code,selected)
            _,trained=job({**period,'kind':'train','snapshotId':features['snapshotId'],'featureSetId':features['featureSetId'],
                'ruleVersionIds':[selected['id']],'detectionPolicy':{'mode':'parallel','deadlineMs':3000,'combination':'any_anomaly'}})
            _,evaluated=job({'kind':'evaluate','sourceId':str(sid),'configVersionId':trained['configVersionId'],'snapshotId':trained['snapshotId'],'purpose':'validation'})
            assert evaluated['applicability']=='unmeasured' # No invented normal labels or accuracy approval.
            config=trained['configVersionId']
            second=change('csv',replayId=str(replay),rate=10000,repeat=1,restart=True,configVersionId=config)
            send_csv(5)
            def decide_run(run_id):
                identifiers=conn.execute("SELECT o.id FROM data_observations o JOIN data_input_origins i ON i.observation_id=o.id WHERE i.run_id=%s AND o.layer='P' ORDER BY o.persisted_at",(run_id,)).fetchall();conn.commit()
                for (oid,) in identifiers:
                    qid=conn.execute("SELECT id FROM powercode_queue.detection_logs WHERE payload->>'observationId'=%s AND payload ? 'jobId'",(str(oid),)).fetchone()[0];conn.commit()
                    msg=claim(conn,'detection',message_id=qid);conn.commit();process_detection_message(conn,msg,root);conn.commit()
                return len(identifiers)
            csv_decisions=decide_run(second['runId']);assert csv_decisions>0
            state['records']=[{'layer':'P','values':{'TRANSACTION_ID':'api-live','PROCESS_ID':'p','RETRY_COUNT':0,'ERROR_COUNT':90,'TOTAL_COUNT':100}}]
            api_run=change('api',pollSeconds=.1)
            api_result=tick(conn,sid,api_factory=factory);assert api_result['accepted']==1,api_result
            time.sleep(.11);dup=tick(conn,sid,api_factory=factory,collect_catalog=False);assert dup['duplicates']==1,dup
            api_decisions=decide_run(api_run['runId']);assert api_decisions==1
            all_results=conn.execute("SELECT d.config_version_id,d.rule_status,d.ml_status,d.evidence FROM run_process_decisions d JOIN data_observations o ON o.id=d.process_observation_id WHERE o.source_id=%s",(sid,)).fetchall();conn.commit()
            assert all(str(r[0])==config and r[1:3]==('succeeded','succeeded') for r in all_results),all_results
            assert {r[3]['input']['mode'] for r in all_results}=={'api','csv'}
            # Switch while the API HTTP call is in flight; that response must not be admitted.
            old_count=conn.execute('SELECT count(*) FROM data_observations WHERE source_id=%s',(sid,)).fetchone()[0];conn.commit()
            state['records'][0]['values']['TRANSACTION_ID']='stale-api-response'
            state['callback']=lambda:change('csv',replayId=str(replay),rate=10000,repeat=1)
            time.sleep(.11);stale=tick(conn,sid,api_factory=factory,collect_catalog=False);assert stale.get('discarded')=='input_mode_changed',stale
            assert conn.execute('SELECT count(*) FROM data_observations WHERE source_id=%s',(sid,)).fetchone()[0]==old_count;conn.commit()
            assert read_state(conn,sid)['run']==uuid.UUID(second['runId'])
            # A real HTTP auth failure remains API mode until the operator switches.
            state['fail']=True;change('api',pollSeconds=.1);time.sleep(.11)
            failed=tick(conn,sid,api_factory=factory,collect_catalog=False);assert failed['errorCode']=='source_auth_rejected',failed
            assert read_state(conn,sid)['mode']=='api'
            change('csv',replayId=str(replay),rate=10000,repeat=1)
            waiting=claim_waiting_input(conn);conn.commit();assert waiting
            process_detection_message(conn,waiting,root);conn.commit()
            assert conn.execute('SELECT active_config_version_id FROM ops_system_state WHERE id=1').fetchone() in (None,(None,))
            conn.commit()
            print('PASS: API/CSV/pause/resume via admin HTTP; revision conflicts and user denial')
            print('PASS: four CSV streams -> shared DB -> feature generation/materialization -> Rule recommendation -> explicit adoption -> real CPU IF -> evaluation')
            print('PASS: same selected feature/Rule/model config produces successful Rule+ML decisions for CSV and API, saved origin evidence')
            print('PASS: API duplicates, in-flight response fence, actual Bearer HTTP/auth failure, manual fallback, waiting-input recovery')
            print('PASS: missing labels remain unmeasured; validation candidate does not change the production active pointer')
    finally:
        if child:
            child.terminate();child.wait(timeout=10)
        api.shutdown();api.server_close();generated.shutdown();generated.server_close()

if __name__=='__main__':main()
