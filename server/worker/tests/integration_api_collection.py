"""Local-only continuous Collector transcript fixture + persisted latency samples."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import psycopg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collector.api_runner import run
from collector.report import report,judge


def main():
    dsn=os.environ['DATABASE_URL'];parsed=urlsplit(dsn)
    assert parsed.hostname in ('localhost','127.0.0.1') and parsed.path=='/qa_pipeline'
    state={'round':0,'requests':0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def respond(self,value):
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'responseCode':'0000','responseObject':value}).encode())
        def do_POST(self):
            state['requests']+=1
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert self.path=='/configuration/interfaces/list' and body['body']['currentPage']==0
            assert self.headers['Authorization']=='Bearer local-collector-token'
            self.respond({'totalPages':1,'content':[{'interfaceId':'I','revision':1,'commonInfo':{'status':'E'},'processInfo':[]}]})
        def do_GET(self):
            state['requests']+=1
            assert self.path=='/monitoring/transactions/in-progress'
            if state['round']==2:
                self.send_response(401);self.end_headers();return
            self.respond([] if state['round']==0 else [{'transactionId':'T1','status':'RUNNING','messageBody':'sensitive','accessToken':'secret'}])
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    os.environ.update(COLLECTOR_SOURCE_KEY='local-customer-collection',COLLECTOR_CONFIGURATION_URL=base,
        COLLECTOR_MONITORING_URL=base,COLLECTOR_HTTP_HOST='127.0.0.1',COLLECTOR_ACCESS_TOKEN='local-collector-token',
        COLLECTOR_POLL_SECONDS='2',DETECTOR_DELIVERY_URL='')
    try:
        for index in range(3):
            state['round']=index;run(once=True)
        assert state['requests']==6
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*),count(*) FILTER(WHERE error_code='source_auth_rejected'),count(*) FILTER(WHERE db_commit_ms IS NOT NULL) FROM ops_api_poll_samples p JOIN ops_sources s ON s.id=p.source_id WHERE s.dataset_key='local-customer-collection'")
                assert cur.fetchone()==(6,1,5)
                cur.execute("SELECT values_json FROM data_api_snapshots a JOIN ops_sources s ON s.id=a.source_id WHERE s.dataset_key='local-customer-collection' AND endpoint='in-progress' ORDER BY item_count")
                snapshots=[row[0] for row in cur.fetchall()]
                assert len(snapshots)==2 and snapshots[0]==[]
                saved=json.dumps(snapshots)
                assert 'T1' in saved and 'sensitive' not in saved and 'secret' not in saved
                cur.execute("SELECT count(*) FROM data_observations o JOIN ops_sources s ON s.id=o.source_id WHERE s.dataset_key='local-customer-collection'")
                assert cur.fetchone()[0]==0 # No fabricated T/P revision contract.
            value=report(conn,'local-customer-collection',10)
            assert value['sourceLatencyMs'] is None
            monitoring=[row for row in value['endpoints'] if row['endpoint']=='in-progress'][0]
            assert monitoring['requests']==3 and monitoring['nonempty_responses']==1 and monitoring['empty_responses']==1
            assert judge(value,error_rate=0,require_nonempty=True,min_samples=3)['status']=='failed'
        print('PASS: repeated polling, deduplicated sanitized snapshots, 6 latency samples, auth error, nonempty response and SLO failure; no source-latency claim')
    finally:server.shutdown();server.server_close()

if __name__=='__main__':main()
