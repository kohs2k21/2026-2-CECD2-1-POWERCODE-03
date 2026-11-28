"""A separate service contract: never reinterpret category-k decisions as research OR.

The detector, severity and scores remain frozen. This module explains saved
method results; it does not infer physical incident causes or rerun ML.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import importlib
from pathlib import Path
import re
import sys
from uuid import UUID
from jsonschema import ValidationError

from xai_runtime.artifacts import InputError, canonical, digest, read, require, sha
from xai_runtime.engine import LABELS
from xai_runtime.evidence_selection import VERSION as SELECTION_VERSION, validate_selection

CONTRACT = 'xai-service-report/1.0.0'
SNAPSHOT = Path(__file__).resolve().parent / 'native'
SHA = re.compile(r'^[0-9a-f]{64}$')
OPS = {'gt': lambda a,b:a>b, 'gte':lambda a,b:a>=b, 'ge':lambda a,b:a>=b,
       'lt':lambda a,b:a<b, 'lte':lambda a,b:a<=b, 'le':lambda a,b:a<=b,
       'eq':lambda a,b:a==b, 'ne':lambda a,b:a!=b, 'neq':lambda a,b:a!=b}
BLOCKED = {'body', 'request_body', 'response_body', 'message_body', 'ec_message_body',
           'authorization', 'password', 'access_token', 'refresh_token'}
CHECKS = {
    'review_input_completeness': '평가 불가인 방법의 입력·수집·실행 상태를 확인하세요.',
    'review_source_operation': '발동한 HR 조건과 원천 운영 기록을 확인하세요. 물리적 원인은 미확인입니다.',
    'review_rule_threshold': 'SR 관측값·단위와 승인된 비교 기준을 확인하세요.',
    'review_model_baseline': 'ML 점수와 학습·전처리·판정선 버전을 확인하세요. 점수만으로 원인을 확정할 수 없습니다.',
}


def native_modules():
    """Import unchanged, SHA-pinned production decision code; no DB or model calls."""
    manifest = read(SNAPSHOT / 'source.json')
    for pin in manifest['files']:
        require(sha(SNAPSHOT / pin['path']) == pin['sha256'], 'native_source_sha_mismatch')
    if str(SNAPSHOT) not in sys.path:
        sys.path.insert(0, str(SNAPSHOT))
    modules = {name: importlib.import_module('detector.' + name)
               for name in ('decision', 'policy', 'method_results', 'hard_rules', 'evidence_v4')}
    # A backend worker may already have its own detector package imported.
    for name, mod in list(sys.modules.items()):
        rel = name.replace('.', '/') + '.py'
        pin = next((p for p in manifest['files'] if p['path'] == rel), None)
        if pin and getattr(mod, '__file__', None):
            require(sha(mod.__file__) == pin['sha256'], 'loaded_detector_version_mismatch')
    return modules


def uuid_text(value):
    require(isinstance(value, str), 'invalid_uuid')
    try:
        result = str(UUID(value))
    except ValueError as exc:
        raise InputError('invalid_uuid') from exc
    require(result == value.lower(), 'invalid_uuid')
    return result


def clean_tree(value):
    """Reject body/credential columns instead of silently stripping signed input."""
    if isinstance(value, dict):
        require(all(isinstance(k, str) and k.lower() not in BLOCKED for k in value), 'body_or_secret_field_rejected')
        for item in value.values(): clean_tree(item)
    elif isinstance(value, list):
        for item in value: clean_tree(item)


def number(value):
    require(not isinstance(value, bool) and value is not None, 'invalid_numeric_evidence')
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InputError('invalid_numeric_evidence') from exc
    require(n.is_finite(), 'nonfinite_numeric_evidence')
    return n


def _validate_method(method, native):
    mid = method['methodId']
    require(method['category'] == native['method_results'].method_category(mid), 'method_category_mismatch')
    require(isinstance(method['methodVersion'], str) and bool(method['methodVersion']), 'missing_method_version')
    status, hit, cause = method['status'], method['anomalous'], method['cause']
    require(status in ('succeeded', 'not_ready', 'failed'), 'invalid_method_status')
    require(hit is None or type(hit) is bool, 'invalid_method_decision')
    require(cause != 'pending', 'final_decision_has_pending_method')
    require((status == 'succeeded' and type(hit) is bool and cause is None) or
            (status != 'succeeded' and hit is not False and cause in native['method_results'].CAUSES),
            'invalid_method_result')
    require(method.get('riskLevel') in (None, 'info', 'warning', 'critical'), 'invalid_method_risk')
    require(method.get('riskLevel') == method.get('severity'), 'method_severity_mismatch')
    require(hit is True or method.get('riskLevel') is None, 'nonwarning_method_risk')
    require(isinstance(method['evidence'], list), 'invalid_method_evidence')
    native['method_results'].method_result(mid, method['methodVersion'], status, hit,
        code=method.get('code'), severity=method.get('severity'), cause=cause,
        failure_code=method.get('failureCode'), evidence=method['evidence'])
    if method['category'] == 'ml' and status == 'succeeded':
        detail = method.get('scoreDetail')
        require(isinstance(detail, dict), 'missing_ML_score_detail')
        raw, threshold = number(detail['raw']), number(detail['threshold'])
        require(number(method['score']) == raw, 'ML_score_mismatch')
        direction = detail['direction']
        require(direction in ('higher_is_anomaly', 'lower_is_anomaly'), 'invalid_score_direction')
        require((raw >= threshold if direction == 'higher_is_anomaly' else raw <= threshold) is hit,
                'ML_score_decision_mismatch')
    if method['category'] == 'soft' and status == 'succeeded' and method.get('observedValue') is not None:
        c = method['condition']
        require(c['operator'] in OPS, 'unsupported_SR_operator')
        require(OPS[c['operator']](number(method['observedValue']), number(c['threshold'])) is hit,
                'SR_value_decision_mismatch')


def _sentence(method, rules, process_input=None):
    hit, status = method['anomalous'], method['status']
    mid = method['methodId']
    if status != 'succeeded':
        return f"{mid}: 평가 불가 ({method['cause']}). 탐지 실행 사유이며 물리적 장애 원인을 뜻하지 않습니다."
    state = '경고' if hit else '비경고'
    if method['category'] == 'hard':
        rule = rules.get(mid)
        condition = rule['conditionText'] if rule and rule['methodVersion'] == method['methodVersion'] else '저장된 관계·프로토콜 조건'
        observed = method['evidence']
        fields = {'hard:process-status':['status'], 'hard:negative-duration':['start_time_raw','end_time_raw'],
                  'hard:success-with-error':['status','error_count'],
                  'hard:count-conservation':['status','total_count','success_count','error_count']}.get(mid)
        if process_input is not None and fields:
            values = {k:process_input[k] for k in fields}
            return f'{mid}: {condition} — {state}. 같은 관측 리비전의 입력값: {canonical(values)}.'
        return f'{mid}: {condition} — {state}. 저장 근거: {canonical(observed)}. 저장되지 않은 원시 비교값은 미확인입니다.'
    if method['category'] == 'soft' and method.get('condition'):
        c = method['condition']
        return (f"{mid}: {c['field']} 값 {method.get('observedValue')} {c.get('unit')}에 "
                f"{c['operator']} {c['threshold']}를 적용한 저장 판정은 {state}입니다.")
    if method['category'] == 'ml':
        detail = method.get('scoreDetail')
        return f'{mid}: {state}. 저장 점수·판정선: {canonical(detail)}. 이상 점수는 장애 원인의 증명이 아닙니다.'
    return f'{mid}: 저장된 조건 판정은 {state}입니다. 상세 원천 근거를 확인하세요.'


def explain_detail(item, selection=None):
    """Accept the authenticated analysis detail projection, not current raw CSV.

    All methods remain visible, including positives below category k and methods
    which could not execute. Optional LLM IDs only affect the summary order.
    """
    original = digest(item)
    clean_tree(item)
    require(len(canonical(item).encode()) <= 256000, 'explanation_input_too_large')
    decision_id = uuid_text(item['decisionId'])
    source_id = uuid_text(item['sourceId'])
    observation_id = uuid_text(item['processObservationId'])
    evidence = item['evidence']
    require(type(evidence['schemaVersion']) is int and evidence['schemaVersion'] == 4, 'unsupported_detector_evidence')
    require(evidence['observation']['id'] == observation_id, 'observation_binding_mismatch')
    for key in ('inputManifestHash', 'ruleSetHash'):
        require(isinstance(evidence[key], str) and bool(SHA.fullmatch(evidence[key])), 'invalid_' + key)
    if item.get('inputManifestHash') is not None:
        require(item['inputManifestHash'] == evidence['inputManifestHash'], 'input_manifest_binding_mismatch')
    for key in ('ruleSetRevision', 'detectionPolicyRevision', 'activeModelSetRevision'):
        require(type(evidence[key]) is int and evidence[key] >= 0, 'invalid_revision')
    native = native_modules()
    methods = evidence['methodResults']
    require(isinstance(methods, list) and bool(methods), 'missing_method_results')
    for method in methods: _validate_method(method, native)
    if evidence.get('statusRulePolicy'):
        require(evidence['statusRulePolicy']['sourceId'] == source_id, 'status_policy_source_mismatch')
    process_input = item.get('processInput')
    if process_input is not None:
        fields = {'observationId','status','start_time_raw','end_time_raw','total_count','success_count','error_count'}
        require(isinstance(process_input,dict) and set(process_input)==fields, 'invalid_process_input')
        require(process_input['observationId']==observation_id, 'raw_observation_binding_mismatch')
        hr = native['hard_rules']
        mapping = evidence.get('statusRulePolicy',{}).get('mapping',{})
        levels = tuple((code,mapping.get(code.removeprefix('PROCESS_STATUS_'),level))
                       for code,level in hr.STATUS_LEVELS.items())
        selected = hr.HardRuleSelection(tuple(r['id'] for r in hr.HARD_RULES[:4]),levels)
        expected = {m['methodId']:m for m in hr.evaluate_hard_rules(selected,process_input)}
        for method in methods:
            if method['methodId'] in expected and method['status']=='succeeded':
                actual=expected[method['methodId']]
                require(all(method[k]==actual[k] for k in ('methodVersion','status','anomalous','code','severity','evidence')),
                        'HR_observed_value_decision_mismatch')
    policy = native['policy'].parse_policy_v2(evidence['detectionPolicy'])
    decided = native['decision'].decide(policy, methods)
    for key in ('methodResults', 'categoryResults', 'composite'):
        require(decided[key] == evidence[key], 'saved_' + key + '_mismatch')
    require(evidence['anomalous'] is decided['composite']['anomalous'], 'saved_anomalous_mismatch')
    if item.get('status') is not None:
        require(item['status'] == decided['composite']['status'], 'decision_status_mismatch')
    basis = set(decided['composite']['basisMethods'])
    rules = {r['id']: r for r in native['hard_rules'].HARD_RULES}
    facts = []
    for method in methods:
        fid = 'method-' + digest(dict(decision=decision_id, method=method['methodId']))[:24]
        facts.append(dict(id=fid, kind={'hard':'HR', 'soft':'SR', 'ml':'ML'}[method['category']],
            state='warning' if method['anomalous'] is True else 'nonwarning' if method['status'] == 'succeeded' else 'unavailable',
            contributes_to_final=method['methodId'] in basis,
            source=dict(sourceId=source_id, processObservationId=observation_id,
                        transactionId=item.get('transactionId'), processId=item.get('processId')),
            method=deepcopy(method), text=_sentence(method, rules, process_input)))
    adverse = [f for f in facts if f['state'] in ('warning', 'unavailable')]
    shortlist = [f['id'] for f in sorted(adverse or facts, key=lambda f: (f['state'] != 'unavailable', not f['contributes_to_final']))[:3]]
    selection_status = 'code_default'
    if selection is not None:
        # Same evidence-ID-only output, separate service input contract. Never drop full detail.
        view = dict(facts=[dict(f, kind='observation' if f['state']=='nonwarning' else
                         'unavailable' if f['state']=='unavailable' else 'logical_rule') for f in facts])
        try:
            validate_selection(view, selection)
            shortlist = selection['summary_evidence_ids']
            selection_status = 'validated_LLM_selection'
        except (InputError, ValidationError, ValueError, KeyError, TypeError):
            selection_status = 'code_fallback_invalid_LLM_selection'
    checks = []
    for fact in facts:
        key = ('review_input_completeness' if fact['state'] == 'unavailable' else
               {'HR':'review_source_operation','SR':'review_rule_threshold','ML':'review_model_baseline'}[fact['kind']])
        if fact['state'] in ('warning', 'unavailable'):
            checks.append(dict(code=key, evidence_ids=[fact['id']], text=CHECKS[key]))
    risk = deepcopy(decided['composite']['risk'])
    risk['label'] = LABELS.get(risk['level'], '심각도 미정' if risk['status']=='unrated' else '해당 없음')
    result = dict(contract=CONTRACT, decisionId=decision_id, sourceId=source_id,
        processObservationId=observation_id, transactionId=item.get('transactionId'), processId=item.get('processId'),
        input_sha256=original, evidence_sha256=digest(evidence),
        decision=deepcopy(decided['composite']), categoryResults=deepcopy(decided['categoryResults']),
        severity=risk, facts=facts, summary_evidence_ids=shortlist, nextChecks=checks,
        causeStatus='unconfirmed', cause=dict(status='unconfirmed', statement=None, evidence_refs=[]),
        versions=dict(detector_source_commit=read(SNAPSHOT/'source.json')['commit'],
            ruleSetRevision=evidence['ruleSetRevision'], ruleSetHash=evidence['ruleSetHash'],
            detectionPolicyRevision=evidence['detectionPolicyRevision'],
            activeModelSetRevision=evidence['activeModelSetRevision'], inputManifestHash=evidence['inputManifestHash'],
            selection_contract=SELECTION_VERSION, execution=selection_status),
        limitations=['탐지 근거 설명이며 실제 장애 원인은 미확인입니다.',
                     '서비스 심각도는 저장된 탐지기 정책 값입니다. 연구 통계 3분위 정책을 적용하지 않았습니다.',
                     '미저장 원시 비교값과 ML 원인 기여도는 추측하지 않습니다.'])
    result['report_sha256'] = digest(result)
    require(digest(item) == original, 'source_mutated')
    return result
