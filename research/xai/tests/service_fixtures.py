"""Synthetic integration fixtures, NOT confirmed enterprise incidents."""
from copy import deepcopy
import hashlib
import json
from uuid import UUID
from service_bridge.adapter import native_modules
from service_bridge.worker import INPUT
from xai_runtime.artifacts import digest

SOURCE = '10000000-0000-4000-8000-000000000001'
OBS = '20000000-0000-4000-8000-000000000001'
DECISION = '30000000-0000-4000-8000-000000000001'
JOB = '40000000-0000-4000-8000-000000000001'


def fixture(kind='hr', k_soft=1):
    n=native_modules(); hr=n['hard_rules']; mr=n['method_results']
    selected=hr.read_hard_rule_snapshot([dict(id=r['id'], enabled=True, methodVersion=r['methodVersion'],
        severity=r['severity']) for r in hr.HARD_RULES[:4]])
    raw=dict(status='S',start_time_raw='2026-08-10 10:00:02',end_time_raw='2026-08-10 10:00:01',
             total_count='4',success_count='3',error_count='2')
    if kind in ('normal','partial','ml','soft','k','relation','protocol','transaction'):
        raw.update(start_time_raw='2026-08-10 10:00:00',total_count='5',error_count='2',status='F')
        if kind!='transaction': raw.update(status='S',total_count='3',error_count='0')
    if kind=='missing': raw.update(status='M')
    methods=hr.evaluate_hard_rules(selected,raw)
    methods=[mr.with_elapsed(m,1.0+i) for i,m in enumerate(methods)]
    if kind=='partial':
        methods.append(mr.with_elapsed(mr.unresolved_result('ml:iforest','if-1','timeout'),7.0))
    if kind in ('soft','k'):
        for mid,value in [('soft:delay',200),('soft:count',0)]:
            methods.append(mr.with_elapsed(mr.method_result(mid,'soft-rule-v1','succeeded',value>100,
                code='SOFT_DELAY',severity='warning',observedValue=str(value),
                condition=dict(field=mid,operator='gt',threshold='100',unit='ms')),5.0))
    if kind=='ml':
        methods.append(mr.with_elapsed(mr.ml_method_result('ml:iforest','if-1','50000000-0000-4000-8000-000000000001',
            0.75,dict(value=0.5,direction='higher_is_anomaly'),'reference-p99',code='ML_IFOREST'),5.0))
    evaluated=None
    if kind in ('relation','protocol'):
        mid='hard:rel-12345678' if kind=='relation' else 'hard:proto-12345678'
        selected=hr.HardRuleSelection(selected.enabled_ids,selected.status_level_items,
            relation_rule_levels=((mid,'critical'),) if kind=='relation' else (),
            protocol_rule_levels=((mid,'critical'),) if kind=='protocol' else ())
        methods.append(mr.with_elapsed(mr.method_result(mid,'relation-v1' if kind=='relation' else 'protocol-v1',
            'succeeded',True,code='SYNTHETIC_CONTRACT_FIXTURE',severity='critical',
            evidence=[dict(result='hit',expected='A→B→C',observed='A→C',fixture=True)]),5.0))
        evaluated=selected.enabled_ids+(mid,)
    if kind=='transaction':
        from detector.relation_inputs import RelationInputs
        from datetime import datetime,timezone
        selected=hr.HardRuleSelection(selected.enabled_ids+(hr.TRANSACTION_FAILED,),selected.status_level_items)
        rel=RelationInputs(datetime.now(timezone.utc),SOURCE,OBS,'synthetic-tx',1,
            dict(observationId=OBS,status='F',responseCode='ERR_TEST'),False,True,(),(),False,False)
        methods.append(mr.with_elapsed(hr.evaluate_transaction_failed(rel,selected),6.0))
    policy=n['policy'].parse_policy_v2(dict(version=2,policyKey='category-k-of-n-or-v1',
                                           k=dict(hard=1,soft=k_soft,ml=1),deadlineMs=3000))
    evidence=n['evidence_v4'].build_evidence(policy,methods,
        n['evidence_v4'].PinnedRevisions(1,digest(dict(fixture_rule_set=True)),1,1),selected,
        source_id=SOURCE,telemetry=dict(scope='synthetic_integration_fixture'),
        observation=dict(id=OBS,revisionNo=1),input_manifest_hash=digest(raw),evaluated_hard_ids=evaluated)
    return dict(decisionId=DECISION,sourceId=SOURCE,processObservationId=OBS,
                transactionId='synthetic-tx',processId='synthetic-process',status=evidence['composite']['status'],
                inputManifestHash=evidence['inputManifestHash'],evidence=evidence,
                processInput=dict(observationId=OBS,**{k:raw[k] for k in ('status','start_time_raw','end_time_raw',
                                                                       'total_count','success_count','error_count')}))


def job_for(detail):
    raw=json.dumps(detail,ensure_ascii=False,separators=(',',':'))
    return dict(input=dict(sourceId=detail['sourceId'],decisionId=detail['decisionId'],
                           savedEvidence=deepcopy(detail),savedEvidenceJson=raw),
        llmSpec=dict(evidence_version=INPUT,input_evidence_hash=hashlib.sha256(raw.encode()).hexdigest(),
                     output_schema_version='xai-service-report/1.0.0',
                     prompt_version='xai-service-code/1.0.0',model_version='code-grounded-v1'))
