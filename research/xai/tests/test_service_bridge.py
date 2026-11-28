from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import httpx
from xai_runtime.artifacts import InputError,digest
from service_bridge.adapter import explain_detail
from service_bridge.api import create_app
from service_bridge.client import AnalysisClient
from service_bridge.reviews import CAUSE,REVIEW,apply_feedback,feedback_payload
from service_bridge.worker import explain_job
from service_fixtures import DECISION,SOURCE,JOB,fixture,job_for


def cause(report,claim='operator_confirmed',statement='SYNTHETIC monitoring-confirmed test cause'):
    return dict(contract=CAUSE,report_sha256=report['report_sha256'],evidence_ids=[report['facts'][0]['id']],
        notes='Synthetic test only; not enterprise evidence.',claim=claim,statement=statement,
        support=[dict(type='incident_report',reference='SYNTHETIC-TICKET-001',sha256=digest('synthetic document'),
                      observed_at='2026-11-23T00:00:00+00:00')])


def row(record,actor='enterprise-test-actor'):
    return dict(id=str(uuid4()),actorId=actor,createdAt='2026-11-23T00:00:00Z',reason=json.dumps(record))


class NativeServiceTests(unittest.TestCase):
    def test_four_HR_conditions_and_three_warnings_are_preserved(self):
        d=fixture(); before=deepcopy(d); r=explain_detail(d)
        self.assertEqual(len(r['facts']),4)
        self.assertEqual(sum(f['state']=='warning' for f in r['facts']),3)
        self.assertEqual(r['decision'],d['evidence']['composite']);self.assertEqual(d,before)
        self.assertEqual(r['severity']['label'],'위험');self.assertEqual(r['causeStatus'],'unconfirmed')

    def test_transaction_failed_HR(self):
        r=explain_detail(fixture('transaction'))
        self.assertIn('hard:transaction-failed',r['decision']['basisMethods'])

    def test_actual_HR_inputs_bound_to_observation_and_compared(self):
        d=fixture();r=explain_detail(d)
        self.assertIn('"error_count":"2"',next(f['text'] for f in r['facts'] if f['method']['methodId']=='hard:success-with-error'))
        d['processInput']['error_count']='0'
        with self.assertRaisesRegex(InputError,'HR_observed_value_decision_mismatch'):explain_detail(d)
        d=fixture();d['processInput']['observationId']=str(uuid4())
        with self.assertRaisesRegex(InputError,'raw_observation_binding_mismatch'):explain_detail(d)

    def test_relation_and_protocol_method_fixtures_preserved(self):
        for kind in ('relation','protocol'):
            r=explain_detail(fixture(kind))
            self.assertIn('hard:'+('rel' if kind=='relation' else 'proto')+'-12345678',r['decision']['basisMethods'])
            self.assertEqual(len(r['facts']),5)

    def test_true_method_below_category_k_not_final_contribution(self):
        r=explain_detail(fixture('k',2));hit=next(f for f in r['facts'] if f['method']['methodId']=='soft:delay')
        self.assertEqual(hit['state'],'warning');self.assertFalse(hit['contributes_to_final'])
        self.assertFalse(r['decision']['anomalous']);self.assertEqual(r['decision']['basisMethods'],[])

    def test_unknown_status_and_partial_methods_never_normalized_to_normal(self):
        for kind in ('missing','partial'):
            r=explain_detail(fixture(kind));unknown=[f for f in r['facts'] if f['state']=='unavailable']
            self.assertTrue(unknown)
            self.assertTrue(all(any(f['id'] in c['evidence_ids'] for c in r['nextChecks']) for f in unknown))
            self.assertIn(r['decision']['status'],('partial','unavailable'))

    def test_ML_direction_and_unrated_service_risk(self):
        r=explain_detail(fixture('ml'))
        self.assertEqual(r['severity']['status'],'unrated')
        self.assertEqual(r['severity']['label'],'심각도 미정')
        d=fixture('ml');d['evidence']['methodResults'][-1]['scoreDetail']['threshold']='1.0'
        with self.assertRaisesRegex(InputError,'ML_score_decision'):explain_detail(d)

    def test_numeric_SR_value_not_rewritten(self):
        d=fixture('soft');r=explain_detail(d)
        f=next(f for f in r['facts'] if f['method']['methodId']=='soft:delay')
        self.assertIn('200',f['text']);self.assertIn('100',f['text'])
        d['evidence']['methodResults'][-2]['observedValue']='0'
        with self.assertRaisesRegex(InputError,'SR_value_decision'):explain_detail(d)

    def test_wrong_composite_category_observation_or_manifest_blocked(self):
        mutations=[lambda d:d['evidence']['composite'].update(anomalous=False),
            lambda d:d['evidence']['categoryResults']['hard'].update(n=100),
            lambda d:d['evidence']['observation'].update(id=str(uuid4())),
            lambda d:d.update(inputManifestHash='0'*64)]
        for change in mutations:
            d=fixture();change(d)
            with self.assertRaises(InputError):explain_detail(d)

    def test_body_and_nonfinite_values_rejected(self):
        d=fixture();d['evidence']['response_body']='excluded'
        with self.assertRaises(InputError):explain_detail(d)
        d=fixture('ml');d['evidence']['methodResults'][-1]['scoreDetail']['raw']='NaN'
        with self.assertRaises(InputError):explain_detail(d)

    def test_unauthorized_LLM_text_is_ignored_in_fallback(self):
        r=explain_detail(fixture(),dict(schema_version='xai-evidence-selection/1.0.0',
                                      summary_evidence_ids=['fake'],cause='network failure'))
        self.assertEqual(r['versions']['execution'],'code_fallback_invalid_LLM_selection')
        self.assertEqual(r['causeStatus'],'unconfirmed');self.assertEqual(len(r['facts']),4)

    def test_worker_exact_saved_hash_and_legacy_output_fields(self):
        job=job_for(fixture());r=explain_job(job)
        self.assertEqual(r['execution']['model_calls'],0)
        self.assertEqual(len(r['facts']),4);self.assertIn('report',r)
        job['input']['savedEvidenceJson']+=' '
        with self.assertRaisesRegex(InputError,'hash_mismatch'):explain_job(job)


class ReviewTests(unittest.TestCase):
    def setUp(self):self.report=explain_detail(fixture())

    def test_no_external_records_means_pending_not_fake_completion(self):
        r=apply_feedback(self.report,[])
        self.assertEqual(r['causeStatus'],'unconfirmed')
        self.assertEqual(r['enterprise_review']['independent_reviews'],0)

    def test_empty_allowlist_does_not_confirm_admin_attestation(self):
        r=apply_feedback(self.report,[row(cause(self.report))])
        self.assertEqual(r['causeStatus'],'unconfirmed')

    def test_enterprise_attestation_keeps_source_and_not_automatic_label(self):
        record=cause(self.report);p=feedback_payload(self.report,record)
        self.assertEqual(set(p),{'kind','reason','outcome'});self.assertEqual(p['outcome'],'undetermined')
        r=apply_feedback(self.report,[row(record)],['enterprise-test-actor'])
        self.assertEqual(r['causeStatus'],'operator_confirmed')
        self.assertIn('not independently verified',r['cause']['verification'])
        self.assertEqual(r['decision'],self.report['decision'])

    def test_cause_without_document_hash_or_foreign_report_rejected(self):
        for field,value in [('support',[]),('report_sha256','0'*64),('evidence_ids',['unknown'])]:
            record=cause(self.report);record[field]=value
            with self.assertRaises(ValueError):feedback_payload(self.report,record)

    def test_retraction_and_conflict_not_silently_resolved(self):
        records=[row(cause(self.report)),row(cause(self.report,'retracted'))]
        self.assertEqual(apply_feedback(self.report,records,['enterprise-test-actor'])['causeStatus'],'disputed')
        records=[row(cause(self.report)),row(cause(self.report,statement='Other synthetic cause'))]
        self.assertEqual(apply_feedback(self.report,records,['enterprise-test-actor'])['causeStatus'],'disputed')

    def test_usefulness_review_cannot_claim_model_superiority(self):
        record=dict(contract=REVIEW,report_sha256=self.report['report_sha256'],
            evidence_ids=[self.report['facts'][0]['id']],variant_id='code',accuracy=4,usefulness=4,
            actionability=3,notes='Synthetic review for test only')
        r=apply_feedback(self.report,[row(record)],['enterprise-test-actor'])
        self.assertEqual(r['enterprise_review']['independent_reviews'],1)
        self.assertEqual(r['enterprise_review']['LLM_better_than_code'],'not_established')
        record['variant_id']='LLM'
        with self.assertRaisesRegex(InputError,'review_variant_execution_mismatch'):feedback_payload(self.report,record)

    def test_duplicate_reviews_by_same_actor_are_not_independent_people(self):
        record=dict(contract=REVIEW,report_sha256=self.report['report_sha256'],
            evidence_ids=[self.report['facts'][0]['id']],variant_id='code',accuracy=4,usefulness=4,
            actionability=3,notes='Synthetic review for test only')
        r=apply_feedback(self.report,[row(record),row(record)],['enterprise-test-actor'])
        self.assertEqual(r['enterprise_review']['independent_reviews'],1)
        self.assertEqual(r['enterprise_review']['review_records'],2)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.detail=fixture();self.feedback=[];self.calls=[]
        self.admin=True
        self.transport=httpx.MockTransport(self.backend)

    def backend(self,request):
        self.calls.append((request.method,request.url.path))
        if request.headers.get('authorization')!='Bearer TEST_ONLY':return httpx.Response(401,json={})
        path=request.url.path
        if path.endswith('/feedback'):
            if not self.admin:return httpx.Response(403,json={})
            if request.method=='POST':
                p=json.loads(request.content);self.assertEqual(set(p),{'kind','reason','outcome'})
                record=row(json.loads(p['reason']));self.feedback.append(record)
                return httpx.Response(201,json=dict(id=DECISION,item=record))
            return httpx.Response(200,json=dict(id=DECISION,items=self.feedback))
        if path==f'/api/analysis/decisions/{DECISION}':return httpx.Response(200,json=self.detail)
        if path=='/api/jobs':
            self.assertEqual(json.loads(request.content),dict(kind='explain',sourceId=SOURCE,decisionId=DECISION))
            self.assertIn('idempotency-key',request.headers)
            return httpx.Response(201,json=dict(id=JOB,status='queued'))
        if path==f'/api/jobs/{JOB}':return httpx.Response(200,json=dict(id=JOB,status='succeeded',resultSummary=explain_job(job_for(self.detail))))
        return httpx.Response(404,json={})

    def test_backend_client_uses_existing_job_contract_and_full_evidence(self):
        c=AnalysisClient('http://backend.test','TEST_ONLY',transport=self.transport)
        try:
            r=c.explanation(DECISION);self.assertEqual(len(r['facts']),4)
            job=c.submit_explanation(SOURCE,DECISION,str(uuid4()))
            result=c.job(job['id']);self.assertEqual(result['resultSummary']['causeStatus'],'unconfirmed')
        finally:c.close()

    def test_real_ASGI_routes_delegate_auth_and_preserve_403_review_scope(self):
        app=create_app('http://backend.test',backend_transport=self.transport)
        with TestClient(app) as c:
            self.assertEqual(c.get('/v1/explanations/'+DECISION).status_code,401)
            response=c.get('/v1/explanations/'+DECISION,headers=dict(Authorization='Bearer TEST_ONLY'))
            self.assertEqual(response.status_code,200);self.assertEqual(response.json()['causeStatus'],'unconfirmed')
            self.admin=False
            response=c.get('/v1/explanations/'+DECISION,headers=dict(Authorization='Bearer TEST_ONLY'))
            self.assertEqual(response.json()['enterprise_review']['status'],'restricted')
            p=cause(explain_detail(self.detail))
            self.assertEqual(c.post('/v1/explanations/'+DECISION+'/reviews',json=p,
                                   headers=dict(Authorization='Bearer TEST_ONLY')).status_code,403)

    def test_review_write_does_not_allow_caller_actor_and_backend_records_identity(self):
        app=create_app('http://backend.test',enterprise_actor_ids=['enterprise-test-actor'],backend_transport=self.transport)
        with TestClient(app) as c:
            headers=dict(Authorization='Bearer TEST_ONLY');p=cause(explain_detail(self.detail))
            p['actorId']='spoof'
            self.assertEqual(c.post('/v1/explanations/'+DECISION+'/reviews',json=p,headers=headers).status_code,422)
            p.pop('actorId')
            self.assertEqual(c.post('/v1/explanations/'+DECISION+'/reviews',json=p,headers=headers).status_code,200)
            r=c.get('/v1/explanations/'+DECISION,headers=headers).json()
            self.assertEqual(r['causeStatus'],'operator_confirmed')

    def test_invalid_backend_origin_and_input_id_rejected(self):
        with self.assertRaises(InputError):AnalysisClient('http://user:pass@backend.test','TEST_ONLY')
        app=create_app('http://backend.test',backend_transport=self.transport)
        with TestClient(app) as c:
            self.assertEqual(c.get('/v1/explanations/not-a-uuid',headers=dict(Authorization='Bearer TEST_ONLY')).status_code,422)


if __name__=='__main__':unittest.main()
