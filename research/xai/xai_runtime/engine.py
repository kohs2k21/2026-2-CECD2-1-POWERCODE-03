"""Validate evidence/plans and render code-owned text, preserving saved decisions."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import operator
from jsonschema import Draft202012Validator, ValidationError
from .artifacts import DESIGN, InputError, canonical, digest, read, require

OPS = dict(gt=operator.gt, ge=operator.ge, lt=operator.lt, le=operator.le, eq=operator.eq, ne=operator.ne)
LABELS = dict(info='참고', warning='주의', critical='위험')
RANK = dict(info=1, warning=2, critical=3)
SCHEMAS = {k: Draft202012Validator(read(DESIGN / 'contracts' / (k + '.schema.json')))
           for k in ('evidence', 'explanation_plan', 'explanation_result', 'input_rejection')}


def rejection(packet_id, reason):
    code = ('severity_configuration_error' if 'severity_' in reason or 'missing_rule' in reason else
            'source_integrity_error' if any(s in reason for s in ('sha', 'artifact', 'capture', 'file')) else
            'source_binding_error' if any(s in reason for s in ('source', 'identity', 'binding')) else
            'decision_mismatch' if any(s in reason for s in ('decision', 'witness', 'coverage', 'mismatch')) else 'invalid_packet')
    result = dict(schema_version='xai-input-rejection/1.0.0', packet_id=packet_id, status='input_rejected',
                  error_code=code, message_code='explanation_input_requires_review', detector_decision_unchanged=True)
    SCHEMAS['input_rejection'].validate(result)
    return result


def decimal(value):
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InputError('invalid_numeric_evidence') from exc
    require(d.is_finite(), 'nonfinite_numeric_evidence')
    return d


def combine(states):
    states = list(states)
    require(bool(states) and set(states) <= {'warning', 'nonwarning', 'unavailable'}, 'invalid_states')
    return 'warning' if 'warning' in states else 'unavailable' if 'unavailable' in states else 'nonwarning'


def severity(facts, decision, policy=None):
    warnings = [f for f in facts if f['state'] == 'warning']
    positive = sorted({f['payload']['method_id'] for f in warnings})
    policy_status = 'not_configured' if policy is None else 'approved' if policy.get('approved_for_operations') else 'draft'
    if policy is not None:
        require(policy.get('contract') == 'xai-rule-severity/1.0.0' and isinstance(policy.get('rules'), dict), 'severity_configuration_error')
        require(isinstance(policy.get('version'), str) and bool(policy['version']), 'severity_configuration_error')
        require(isinstance(policy.get('approved_for_operations'), bool), 'severity_configuration_error:approval_flag_type')
        require(all(isinstance(k, str) and isinstance(v, dict) and v.get('level') in RANK for k, v in policy['rules'].items()), 'severity_configuration_error:invalid_level')
    result = dict(status='not_applicable', level=None, label='해당 없음', label_table_version='xai-three-level-v2',
                  policy_version='not_configured' if policy is None else policy['version'],
                  policy_sha256=digest(policy if policy is not None else {'status': 'not_configured'}),
                  determined_by=[], unrated_methods=[], known_level_is_lower_bound=False, source_risk_ref=None)
    if decision != 'warning':
        return result, dict(policy_status=policy_status, reason='no_warning', approved_for_operations=False if policy is None else bool(policy.get('approved_for_operations')))
    rated, unrated = [], []
    for f in warnings:
        p = f['payload']; mid = p['method_id']
        if f['kind'] == 'ml_score' or policy is None:
            unrated.append(mid)
            continue
        rule_id = p.get('alternative_rule_id') or p.get('rule_id')
        entry = policy['rules'].get(rule_id)
        require(isinstance(entry, dict) and entry.get('level') in RANK, 'severity_configuration_error:missing_rule:' + str(rule_id))
        rated.append((mid, entry['level']))
    result.update(status='unrated', label='심각도 미정', unrated_methods=sorted(set(unrated)))
    if rated:
        level = max((level for _, level in rated), key=RANK.__getitem__)
        result.update(status='rated', level=level, label=LABELS[level],
                      determined_by=sorted({mid for mid, v in rated if v == level}),
                      known_level_is_lower_bound=bool(unrated))
    require(set(result['determined_by'] + result['unrated_methods']) <= set(positive), 'severity_basis_mismatch')
    return result, dict(policy_status=policy_status,
                        reason='rule_policy_not_configured' if policy is None and any(f['kind'] != 'ml_score' for f in warnings) else 'highest_defined_rule_level' if rated else 'only_unrated_methods',
                        unrated_method_reasons={f['payload']['method_id']: 'ml_severity_undefined' if f['kind'] == 'ml_score' else 'rule_policy_not_configured' for f in warnings if f['payload']['method_id'] in result['unrated_methods']},
                        approved_for_operations=False if policy is None else bool(policy.get('approved_for_operations')))


def validate_packet(packet):
    SCHEMAS['evidence'].validate(packet)
    facts = {f['id']: f for f in packet['facts']}
    sources = {s['source_id']: s for s in packet['source_refs']}
    require(len(facts) == len(packet['facts']) and len(sources) == len(packet['source_refs']), 'duplicate_evidence_identity')
    physical = {(s['table'], s['source_row_id']) for s in sources.values()}
    require(len(physical) == len(sources), 'duplicate_physical_source')
    for s in sources.values():
        require(s['transaction_ref'] == packet['scope']['transaction_ref'], 'cross_transaction_source')
        # An immutable source-row identity is sufficient even when a business key is missing.
        require(bool(s['source_row_id']), 'missing_physical_row_identity')
    for f in facts.values():
        require(set(f['source_refs']) <= set(sources), 'unknown_source')
        require(f['state'] not in ('warning', 'unavailable') or f['required'], 'required_flag_missing')
        p = f['payload']
        if f['kind'] in ('rule_comparison', 'ml_score', 'ratio_alternative'):
            key = {'rule_comparison': 'observed', 'ml_score': 'score', 'ratio_alternative': 'alternative_value'}[f['kind']]
            expected = 'warning' if OPS[p['operator']](decimal(p[key]), decimal(p['threshold'])) else 'nonwarning'
            require(f['state'] == expected, 'numeric_decision_mismatch')
        if f['kind'] == 'ml_score':
            require(p['operator'] in (('gt', 'ge') if p['score_direction'] == 'larger_is_more_anomalous' else ('lt', 'le')), 'score_direction_mismatch')
            require(any(sources[s]['table'] == p['score_grain'] for s in f['source_refs']), 'ML_source_grain_mismatch')
        if f['kind'] == 'ratio_alternative':
            require(decimal(p['denominator']) == 0, 'fallback_without_zero_denominator')
        if p.get('role') == 'HR':
            require(p['reference']['kind'] == 'contract', 'HR_without_contract_reference')
        if f['kind'] == 'ml_attribution':
            other = facts.get(p['score_evidence_id'], {})
            require(other.get('kind') == 'ml_score' and set(other['source_refs']) == set(f['source_refs']), 'attribution_source_mismatch')
    if packet['decision']['policy'] == 'research_or':
        require(combine(f['state'] for f in facts.values() if f['state'] != 'context') == packet['decision']['state'], 'saved_OR_decision_mismatch')
    basis = sorted({f['payload']['method_id'] for f in facts.values() if f['state'] == 'warning'})
    require(basis == sorted(packet['decision']['basis_method_ids']), 'saved_positive_basis_mismatch')
    s = packet['severity']
    require(set(s['determined_by'] + s['unrated_methods']) <= set(basis), 'severity_method_outside_basis')
    if packet['decision']['state'] != 'warning':
        require(s['status'] == 'not_applicable' and s['level'] is None and s['label'] == '해당 없음' and not s['determined_by'] and not s['unrated_methods'] and not s['known_level_is_lower_bound'], 'nonwarning_severity_changed')
    elif s['status'] == 'rated':
        require(s['level'] in LABELS and s['label'] == LABELS[s['level']] and bool(s['determined_by']), 'severity_label_or_basis_mismatch')
        require(s['known_level_is_lower_bound'] == bool(s['unrated_methods']), 'mixed_severity_lower_bound_missing')
    else:
        require(s['status'] == 'unrated' and s['level'] is None and s['label'] == '심각도 미정' and not s['determined_by'] and bool(s['unrated_methods']) and not s['known_level_is_lower_bound'], 'unrated_severity_changed')
    return facts


def default_plan(packet):
    # All required facts are retained. Optional nonwarnings remain in the saved packet.
    selected = [f for f in packet['facts'] if f['required']]
    if not selected:
        selected = packet['facts'][:1]
    kinds = {f['kind'] for f in selected}
    preferred = []
    if 'unavailable' in kinds: preferred.append('review_input_completeness')
    if 'logical_rule' in kinds: preferred.append('review_source_operation')
    if kinds & {'rule_comparison', 'ratio_alternative'}: preferred.append('review_rule_threshold')
    if 'ml_score' in kinds: preferred.append('review_model_baseline')
    return dict(schema_version='xai-plan/1.0.0', packet_id=packet['packet_id'],
                items=[dict(template_id=f['kind'], evidence_id=f['id']) for f in selected],
                next_check_codes=[c for c in dict.fromkeys(preferred) if c in packet['allowed_checks']][:3])


def validate_plan(packet, plan):
    SCHEMAS['explanation_plan'].validate(plan)
    require(plan['packet_id'] == packet['packet_id'], 'cross_packet_plan')
    facts = {f['id']: f for f in packet['facts']}
    selected = [i['evidence_id'] for i in plan['items']]
    require(len(selected) == len(set(selected)) and set(selected) <= set(facts), 'unknown_or_duplicate_plan_fact')
    require({f['id'] for f in facts.values() if f['required']} <= set(selected), 'required_fact_omitted')
    require(all(i['template_id'] == facts[i['evidence_id']]['kind'] for i in plan['items']), 'wrong_template')
    require(set(plan['next_check_codes']) <= set(packet['allowed_checks']), 'disallowed_check')


def render(packet, supplied_plan=None, failure_reason=None):
    facts = validate_packet(packet)
    plan = default_plan(packet) if supplied_plan is None else supplied_plan
    fallback = failure_reason
    try:
        require(failure_reason is None, failure_reason or '')
        validate_plan(packet, plan)
    except (InputError, ValidationError) as exc:
        fallback = failure_reason or ('invalid_plan:' + getattr(exc, 'message', str(exc)))[:300]
        plan = default_plan(packet)
        validate_plan(packet, plan)
    registry = read(DESIGN / 'template_registry.json')
    sentences = []
    for item in plan['items']:
        f = facts[item['evidence_id']]; p = f['payload']; kind = f['kind']
        values = dict(p); unit = p.get('unit')
        values['unit_label'] = unit['symbol'] if unit and unit['status'] == 'known' else '(단위 미확인)' if unit and unit['status'] == 'unknown' else ''
        values['state_label'] = registry['state_labels'][f['state']]
        if 'operator' in p: values['operator_label'] = registry['operator_labels'][p['operator']]
        if 'score_direction' in p: values['score_direction_label'] = registry['score_direction_labels'][p['score_direction']]
        if 'reason_code' in p: values['reason_label'] = registry['reason_labels'][p['reason_code']]
        templates = {
            'rule_comparison': '{feature_name} 값은 {observed}{unit_label}입니다. 적용 조건 「{threshold}{unit_label} {operator_label}」의 판정은 {state_label}입니다.',
            'ml_score': '{algorithm} 점수({score_name})는 {score}입니다. 적용 조건 「{threshold} {operator_label}」의 판정은 {state_label}이며, 점수는 {score_direction_label}입니다.',
            'ratio_alternative': '분모가 {denominator}이어서 원래 비율은 계산할 수 없습니다. 실제 대체 규칙 {alternative_rule_id}에서 {alternative_feature_name} 값 {alternative_value}{unit_label}을 계산했습니다. 조건 「{threshold}{unit_label} {operator_label}」의 판정은 {state_label}입니다.',
            **{k: v for k, v in registry['templates'].items() if k not in ('rule_comparison', 'ml_score', 'ratio_alternative')},
        }
        text = templates[kind].format(**values)
        sentences.append(dict(**item, text=text, source_refs=f['source_refs']))
    result = dict(schema_version='xai-result/1.1.0', packet_id=packet['packet_id'],
                  status='template_fallback' if fallback else 'completed', scope=deepcopy(packet['scope']),
                  decision=deepcopy(packet['decision']), severity=deepcopy(packet['severity']), input_evidence_sha256=digest(packet),
                  sentences=sentences, next_checks=[dict(code=c, text=registry['checks'][c]) for c in plan['next_check_codes']],
                  cause_status='not_applicable' if packet['decision']['state'] == 'nonwarning' else 'unconfirmed',
                  contribution_status='computed' if any(f['kind'] == 'ml_attribution' for f in packet['facts']) else 'not_computed' if any(f['kind'] == 'ml_score' for f in packet['facts']) else 'not_applicable',
                  fallback_reason=fallback, versions=dict(prompt='xai-prompt/1.0.0', templates='xai-runtime-templates/1.0.0', adapter_sha256=None, execution='template_only'))
    SCHEMAS['explanation_result'].validate(result)
    return result
