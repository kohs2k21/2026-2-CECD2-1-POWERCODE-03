import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from detector.policy import Policy,parse_policy,execute
from collector.api_client import CustomerAPI,SourceUnavailable
from collector.api_runner import project_interface
from explanation import validate_output

class PolicyTests(unittest.TestCase):
    def result(self,anomaly=False):
        return {"status":"succeeded","anomalous":anomaly,"evidence":[]}
    def test_parallel_starts_both_without_serial_dependency(self):
        barrier=threading.Barrier(2)
        def callback():
            barrier.wait(timeout=.5);return self.result()
        result=execute(Policy("parallel",1000,"any_anomaly"),callback,callback)
        self.assertEqual(result["status"],"complete")
        self.assertFalse(result["anomalous"])
    def test_late_ml_cannot_change_final_rule_hit(self):
        finished=threading.Event()
        def late():
            time.sleep(.18);finished.set();return self.result(False)
        result=execute(Policy("parallel",100,"any_anomaly"),lambda:self.result(True),late)
        self.assertEqual(result["status"],"partial")
        self.assertTrue(result["anomalous"])
        self.assertEqual(result["ml"]["evidence"][0]["reason"],"deadline_exceeded")
        self.assertTrue(finished.wait(.5))
        self.assertEqual(result["ml"]["status"],"not_ready")
    def test_ml_only_never_calls_rules_or_falls_back(self):
        def forbidden():
            self.fail("rule callback ran")
        result=execute(Policy("ml_only",100,"selected_path"),forbidden,
            lambda:{"status":"not_ready","anomalous":None,"evidence":[]})
        self.assertIsNone(result["anomalous"])
        self.assertEqual(result["status"],"unavailable")
        self.assertEqual(result["rule"]["status"],"skipped")
    def test_failed_ml_retains_confirmed_hit_from_incomplete_rules(self):
        def broken():
            raise RuntimeError("unavailable GPU")
        result=execute(Policy("parallel",100,"any_anomaly"),
            lambda:{"status":"not_ready","anomalous":True,"code":"KNOWN_HIT","evidence":[]},broken)
        self.assertEqual(result["status"],"partial");self.assertTrue(result["anomalous"])
    def test_preparation_consumes_the_same_deadline(self):
        def forbidden():
            self.fail("expired computation started")
        result=execute(Policy("parallel",100,"any_anomaly"),forbidden,forbidden,started_at=time.monotonic()-.2)
        self.assertEqual(result["status"],"unavailable")
        self.assertEqual(result["rule"]["evidence"][0]["reason"],"deadline_exceeded")

    def test_parallel_empty_rules_are_unavailable(self):
        from detector.computation import compute
        context={"policy":Policy("parallel",100,"any_anomaly",True),"rules":[],"models":[],"definitions":[]}
        result=compute(context,{},None)
        self.assertEqual(result["rule"]["status"],"not_ready")
        self.assertEqual(result["status"],"unavailable")
        self.assertIsNone(result["anomalous"])

    def test_explicit_policy_and_bounds(self):
        for value in ({},{"detectionPolicy":{"mode":"parallel","deadlineMs":True,"combination":"any_anomaly"}},
                      {"detectionPolicy":{"mode":"parallel","deadlineMs":10,"combination":"any_anomaly"}}):
            with self.assertRaises(ValueError):parse_policy(value,required=True)

    def test_slow_ml_does_not_exhaust_rule_capacity(self):
        started=threading.Barrier(3)
        release=threading.Event()
        def slow():
            started.wait(timeout=.5);release.wait(.5);return self.result()
        threads=[threading.Thread(target=lambda:execute(Policy("ml_only",100,"selected_path"),lambda:self.result(),slow)) for _ in range(2)]
        for thread in threads:thread.start()
        started.wait(timeout=.5)
        try:
            result=execute(Policy("rule_only",100,"selected_path"),lambda:self.result(True),lambda:self.result())
            self.assertEqual(result["status"],"complete")
        finally:
            release.set()
            for thread in threads:thread.join()

class CollectorTests(unittest.TestCase):
    def test_real_transcript_request_contract(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                requests.append((self.path,self.headers.get('Authorization'),json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200);self.end_headers()
                self.wfile.write(json.dumps({"responseCode":"0000","responseObject":{"totalPages":1,"content":[]}}).encode())
            def do_GET(self):
                self.send_response(401);self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            base=f'http://127.0.0.1:{server.server_port}'
            api=CustomerAPI(base,base,'test-only',allowed_http_host='127.0.0.1')
            self.assertEqual(api.interfaces(0)['content'],[])
            self.assertEqual(requests,[('/configuration/interfaces/list','Bearer test-only',
                {'body':{'searchConditions':[],'currentPage':0,'pageSize':10}})])
            with self.assertRaisesRegex(SourceUnavailable,'source_auth_rejected'):api.in_progress()
        finally:server.shutdown();server.server_close()
    def test_catalog_excludes_adapter_secrets_and_body(self):
        source={'interfaceId':'I','revision':1,'commonInfo':{'status':'E','password':'secret'},
            'processInfo':[{'processId':'P','adapterInfo':{'type':'DB.READER','password':'secret','tableInfo':['sensitive']}}],
            'messageBody':'secret'}
        projected=project_interface(source)
        self.assertNotIn('secret',json.dumps(projected));self.assertNotIn('tableInfo',json.dumps(projected))
        self.assertEqual(projected['processInfo'][0]['adapterType'],'DB.READER')
    def test_http_requires_exact_operator_host_and_no_embedded_credentials(self):
        for url in ('http://other.test','http://token:secret@127.0.0.1','http://127.0.0.1/?token=x'):
            with self.assertRaises(ValueError):CustomerAPI(url,url,'token',allowed_http_host='127.0.0.1')

class ExplanationTests(unittest.TestCase):
    def test_invented_claims_or_fact_ids_are_rejected(self):
        facts=[{'id':'fact_0','name':'ruleStatus','value':'succeeded'}]
        for value in ({'factIds':['missing'],'nextChecks':[]},
                      {'factIds':['fact_0'],'nextChecks':[],'cause':'Database failure'},
                      {'factIds':['fact_0'],'nextChecks':['restart_server']}):
            with self.assertRaises(ValueError):validate_output(value,facts)
        result=validate_output({'factIds':['fact_0'],'nextChecks':['review_source_operation']},facts)
        self.assertEqual(result['causeStatus'],'unconfirmed')
        self.assertEqual(result['contributionStatus'],'not_computed')
if __name__=='__main__':unittest.main()
