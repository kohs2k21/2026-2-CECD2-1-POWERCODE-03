"""Evidence-bound role records and minimum non-rewriting Rule verification."""
from decimal import Decimal
import copy, re, json, math
import jsonschema
from .io import *
from .calculations import compile_feature
from .contracts import rule_schema, rule_system

def value_condition(v, c):
    if c['op'] == 'is_missing':
        return v is None
    if v is None:
        return None
    rhs = c['value']
    if type(v) is not bool and type(rhs) is not bool and isinstance(v, (int, float, Decimal)) and isinstance(rhs, (int, float, Decimal)):
        (v, rhs) = (Decimal(str(v)), Decimal(str(rhs)))
    if type(v) is bool or type(rhs) is bool:
        if type(v) is not bool or type(rhs) is not bool:
            raise TypeError('Boolean/numeric condition mismatch')
    if c['op'] == 'eq':
        return v == rhs
    if c['op'] == 'gt':
        return v > rhs
    if c['op'] == 'lt':
        return v < rhs
    raise ValueError('Unsupported Rule condition')

def counts(values, c):
    out = {'rows': len(values), 'evaluable': 0, 'satisfied': 0, 'not_satisfied': 0, 'unassessed': 0}
    for v in values:
        q = value_condition(v, c)
        if q is None:
            out['unassessed'] += 1
        else:
            out['evaluable'] += 1
            out['satisfied' if q else 'not_satisfied'] += 1
    return out

def range_proof(dataset, f):
    x = f['plan']['expression']
    op = x['op']
    upper = None
    lower = None
    why = None
    if op in ['child_count', 'conditional_count', 'group_count']:
        lower = 0
        why = 'Physical captured count, not business expectation'
    if op == 'child_count':
        upper = len(dataset.rows[dataset.engine.relations[x['relationship_id']]['from_table']])
    if op == 'parent_match_count':
        lower = 0
        upper = dataset.engine.relationship_bounds[x['relationship_id']]['maximum_parent_matches']
        why = 'Exact captured parent key multiplicity, not a mandatory-parent contract'
    if f['dtype'] == 'boolean':
        return {'min': False, 'max': True, 'kind': 'Boolean', 'scope': dataset.source_version}
    if op == 'divide':
        (a, b) = x['args']
        if a.get('op') == 'conditional_count' and b.get('op') == 'child_count' and (a['relationship_id'] == b['relationship_id']):
            (lower, upper, why) = (0, 1, 'Conditional child rows form a subset of the same captured children')
    return {'min': lower, 'max': upper, 'kind': 'certified_construction' if why else 'unknown', 'reason': why, 'observed_extrema_are_not_bounds': True, 'scope': dataset.source_version}

def baseline(dataset, reference, proposal, compiled, plan):
    f = compiled['compiled_feature']
    policy = plan.get('reference_policy', {})
    minimum = policy.get('minimum_n', 20)
    if type(minimum) is not int or minimum < 1:
        raise InputFailure('Explicit positive reference minimum required')
    b = {'id': None, 'state': 'unprovided', 'value': None, 'unit': f['unit'], 'reference_valid_n': None, 'minimum_n': minimum, 'reference_period': None, 'comparison': policy.get('operator', 'gt'), 'quantile_method': 'linear_(n-1)*p', 'missing': 'Exclude unavailable reference values; preserve current unavailable as unassessed', 'update_policy': 'Frozen offline comparison; no automatic update', 'expiry_policy': 'Valid only for exact pinned definition, source, unit and scope; not an online lifetime policy', 'fallback': 'none', 'reference_source_version': None, 'reference_definition_sha': None}
    if reference is None:
        b['needed'] = ['need_reference']
        return b
    rp = reference.manifest['period']
    ap = dataset.manifest['period']
    if rp.get('end_exclusive') and ap.get('start_inclusive') and (rp['end_exclusive'] > ap['start_inclusive']):
        raise InputFailure('Reference overlaps/follows application period')
    if reference.manifest.get('split') != 'train':
        raise InputFailure('Numerical reference must be approved TRAIN')
    ref = compile_feature(reference, proposal, 'reference_measurement')
    if not ref['passed']:
        b.update(state='incompatible', needed=['need_compatible_reference'], reason=ref['errors'])
        return b
    rf = ref['compiled_feature']
    m = rf['measurement']['summary']
    b.update(reference_valid_n=m['valid_count'], reference_period=rp, reference_source_version=rf['source_version'], reference_definition_sha=rf['definition_sha'], reference_measurement_sha=rf['measurement']['measurement_sha256'])
    if rf['semantic_definition_sha'] != f['semantic_definition_sha'] or rf['unit'] != f['unit'] or rf['dtype'] != f['dtype']:
        b.update(state='incompatible', needed=['need_compatible_reference'])
    elif f['dtype'] not in ['number', 'integer']:
        b.update(state='type_inapplicable', needed=[], reason='This datatype has no numerical quantile; no unit request can change its type.')
    elif f['unit'] is None:
        b.update(state='unit_unconfirmed', needed=['need_verified_unit'])
    elif m['valid_count'] < minimum:
        b.update(state='insufficient', needed=['need_reference_minimum_n'])
    else:
        b.update(state='verified', value=m['p95'], needed=[])
    b['id'] = 'baseline_' + digest(b)[:24]
    return b

def make_rule_record(dataset, reference, proposal, c, plan):
    dataset.assert_unchanged()
    f = c['compiled_feature']
    m = f['measurement']['summary']
    roles = plan['requests']
    if not roles or len(set(roles)) != len(roles) or set(roles) - {'hr', 'observed', 'baseline'}:
        raise InputFailure('Explicit distinct supported role requests required')
    constraint = []
    x = f['plan']['expression']
    if x['op'] == 'is_missing' and x['args'][0]['op'] == 'column':
        n = x['args'][0]
        constraint = [e for e in dataset.constraints if (e['table'], e['column']) == (n['table'], n['column'])]
    b = baseline(dataset, reference, proposal, c, plan) if 'baseline' in roles else {'id': None, 'state': 'not_requested', 'reference_valid_n': None, 'value': None, 'minimum_n': None}
    observed = plan.get('observed_condition')
    if 'observed' in roles and observed is None:
        raise InputFailure('Observed diagnostic needs an explicit public purpose/condition; no universal is_missing fallback')
    features = {'id': f['id'], 'sha': f['definition_sha'], 'meaning': f['meaning'], 'purpose': f['purpose'], 'dtype': f['dtype'], 'unit': f['unit'], 'expression': f['plan']['expression'], 'grain': f['plan']['grain'], 'population': f['plan']['population'], 'missing_policy': f['plan']['missing_policy'], 'operator_state_proof': f['possible_states']}
    source = 'source_' + dataset.source_version[:20]
    pop = 'population_' + digest(f['plan']['population'])[:20]
    availability = 'availability_' + digest(m)[:20]
    mapping = 'mapping_' + digest(f['source_columns'])[:20]
    try:
        ce = counts(c['values'], observed) if observed else None
    except (TypeError,ValueError,KeyError) as e:
        raise InputFailure('/rule_plan/observed_condition contradicts the verified feature dtype '+f['dtype']+': '+str(e)+'. Correct the declared public diagnostic plan; the compiler will not replace it.')
    facts = [source, availability, mapping]
    inp = {'contract': 'evidence-bound-selected-role-final1.0', 'requests': roles, 'public_analysis_request': {'question': plan['question'], 'considered_observed_condition': observed}, 'feature': features, 'definition_sha': f['definition_sha'], 'source_version': dataset.source_version, 'measurement_id': f['measurement_id'], 'measurement_sha': f['measurement']['measurement_sha256'], 'measurement': m, 'constraints': constraint, 'baseline': b, 'scope': {'period': dataset.manifest['period'], 'table_rows': {k: len(v) for (k, v) in dataset.rows.items()}, 'population': f['plan']['population'], 'manifest_sha': sha(dataset.path), 'data_class': dataset.manifest['data_class']}, 'range_proof': range_proof(dataset, f), 'sources': {'calculation_and_relationship_key_columns': f['source_columns']}, 'observed_diagnostic_contract': {'condition': observed, 'purpose': plan['question'], 'baseline_required': False, 'minimum_current_observations': None}, 'condition_evaluation': {'condition': observed, 'statistics': ce} if ce else None, 'application_context': {'fact_ids': facts, 'population_ref': pop, 'evaluation_preconditions': [source, availability, mapping], 'source_certificate': {'source_version': dataset.source_version, 'numeric_valid': m['valid_count'], 'retained_rows': m['row_count'], 'calculation_available': True}}, 'condition_scope_facts': {'valid_value_observations': m['valid_count'], 'numeric_comparison_evaluable': m['valid_count'] if f['dtype'] in ['number','integer'] else None, 'result_missingness_test_evaluable': m['row_count'], 'retained_feature_unavailable_possible': f['possible_states']['unavailable_possible'], 'source_missing_cause': 'Result unavailable alone does not prove raw source null, parsing, denominator, relation or incident cause'}, 'availability': f['availability'], 'unknown': dataset.manifest.get('unknown', []), 'known_evidence_ids': facts + [pop, f['measurement_id']] + [q['id'] for q in constraint] + ([b['id']] if b.get('id') else []), 'allowed_needed_keys': ['need_reference', 'need_reference_minimum_n', 'need_compatible_reference', 'need_verified_unit', 'need_valid_observations', 'need_confirmed_constraint', 'need_business_contract']}
    from .official_facts import feature_semantics
    inp['official_computation_facts'] = feature_semantics(c,dataset)
    inp['AI_prose_is_not_calculation_authority'] = True
    return {'input': inp, 'messages': [{'role': 'system', 'content': rule_system()}, {'role': 'user', 'content': json.dumps(inp, ensure_ascii=False, separators=(',', ':'), default=json_default)}], 'compilation': c, 'proposal': proposal}

def validate_rule(record, pred):
    i = record['input']
    f = i['feature']
    m = i['measurement']
    b = i['baseline']
    errors = []

    def err(path, code, actual, allowed=None):
        errors.append({'path': path, 'code': code, 'actual': actual, 'allowed': allowed})
    for x in jsonschema.Draft202012Validator(rule_schema(i['requests'])).iter_errors(pred):
        err('/' + '/'.join(map(str, x.absolute_path)), 'schema', x.instance, x.message)
    if errors:
        return errors
    if pred['feature'] != f['id'] or pred['sha'] != f['sha']:
        err('/feature/sha', 'immutable_reference', [pred['feature'], pred['sha']], [f['id'], f['sha']])
    if sorted((d['request'] for d in pred['decisions'])) != sorted(i['requests']):
        err('/decisions', 'role_coverage', [d['request'] for d in pred['decisions']], i['requests'])
    for (n, d) in enumerate(pred['decisions']):
        p = '/decisions/' + str(n) + '/'
        role = d['request']
        state = d['state']
        condition = d['condition']
        if condition is not None:
            try:
                counts(record['compilation']['values'], condition)
            except (TypeError,ValueError) as e:
                err(p+'condition','condition_feature_type_conflict',condition,{'feature_dtype':f['dtype'],'public_observed_condition':i['observed_diagnostic_contract']['condition'],'reason':str(e),'preserve':'Keep role, feature definition/source and scope. Regenerate a type-compatible supported condition; do not change the feature calculation.'})
                continue
        if set(d['evidence']) - set(i['known_evidence_ids']):
            err(p + 'evidence', 'unprovided_evidence', d['evidence'], i['known_evidence_ids'])
        if set(d['needed']) - set(i['allowed_needed_keys']):
            err(p + 'needed', 'unsupported_needed_resource', d['needed'], i['allowed_needed_keys'])
        if 'need_verified_unit' in d['needed'] and f['unit'] is not None:
            err(p + 'needed', 'verified_unit_already_provided', d['needed'], f['unit'])
        if d['population_ref'] != i['application_context']['population_ref']:
            err(p + 'population_ref', 'scope_reference', d['population_ref'], i['application_context']['population_ref'])
        for field in ['precondition_refs', 'required_evaluation_basis']:
            if set(d[field]) - set(i['known_evidence_ids']):
                err(p + field, 'unprovided_evidence', d[field], i['known_evidence_ids'])
        if state == 'proposed':
            if condition is None or d['needed']:
                err(p + 'condition/needed', 'proposed_contract', d, {'condition': 'justified executable', 'needed': []})
        if state == 'no_applicable' and (condition is not None or d['needed']):
            err(p + 'condition/needed', 'no_applicable_contract', d, {'condition': None, 'needed': []})
        if state == 'request_evidence' and (condition is not None or not d['needed']):
            err(p + 'condition/needed', 'request_evidence_contract', d, {'condition': None, 'needed': 'specific absent evidence'})
        if state == 'deferred' and (condition is None or not d['needed']):
            err(p + 'condition/needed', 'deferred_contract', d, {'condition': 'known unchanged condition', 'needed': 'application observation/availability shortage'})
        if condition:
            if condition['op'] != 'is_missing':
                value = condition['value']
                if f['dtype'] == 'boolean' and (type(value) is not bool or condition['op'] != 'eq'):
                    err(p + 'condition', 'Boolean_condition_type', condition, 'eq Boolean or is_missing')
                if f['dtype'] != 'boolean' and (type(value) not in [int, float] or f['dtype'] not in ['number', 'integer']):
                    err(p + 'condition', 'numeric_condition_type', condition, 'numeric comparison or is_missing')
            try:
                stats = counts(record['compilation']['values'], condition)
            except (TypeError, ValueError, KeyError) as e:
                err(p + 'condition', 'condition_evaluation', condition, str(e))
                continue
            if role == 'observed' and condition != i['observed_diagnostic_contract']['condition']:
                err(p + 'condition', 'public_diagnostic_replaced', condition, i['observed_diagnostic_contract']['condition'])
            if role == 'observed' and state == 'proposed' and (stats['evaluable'] == 0):
                err(p + 'state', 'no_evaluable_observations', 'proposed', {'state': 'deferred', 'known_condition': condition})
            if role == 'observed' and state == 'deferred' and (stats['evaluable'] > 0):
                err(p + 'state', 'observations_are_available', 'deferred', {'evaluable_rows': stats['evaluable'], 'satisfied': stats['satisfied']})
        if role == 'hr' and state == 'proposed':
            ids = {x['id'] for x in i['constraints']}
            if d['basis'] != 'ddl_not_null' or not ids.intersection(d['evidence']) or condition != {'op': 'eq', 'value': True}:
                err(p + 'basis/evidence/condition', 'HR_contract', d, {'basis': 'ddl_not_null', 'evidence': sorted(ids), 'condition': {'op': 'eq', 'value': True}})
        if role == 'observed' and state in ['proposed', 'deferred']:
            if d['basis'] != 'observed_diagnostic' or i['measurement_id'] not in d['evidence']:
                err(p + 'basis/evidence', 'observed_grounding', d, {'basis': 'observed_diagnostic', 'measurement_id': i['measurement_id']})
        if role == 'baseline' and state in ['proposed', 'deferred']:
            if b['state'] != 'verified':
                err(p + 'state', 'unusable_reference', state, b)
            if d['basis'] != 'reference_p95' or condition != {'op': b['comparison'], 'value': b['value']} or (not {i['measurement_id'], b['id']} <= set(d['evidence'])) or (d['baseline_use_ref'] != b['id']):
                err(p + 'condition/evidence', 'baseline_grounding', d, b)
            if state == 'proposed' and m['valid_count'] == 0:
                err(p + 'state', 'no_current_numeric_observations', state, 'deferred with known comparison')
            proof = i['range_proof']
            upper = proof.get('max')
            lower = proof.get('min')
            if state == 'proposed' and condition and (condition['op'] == 'gt' and upper is not None and (type(upper) is not bool) and (upper <= condition['value']) or (condition['op'] == 'lt' and lower is not None and (type(lower) is not bool) and (lower >= condition['value']))):
                err(p + 'condition', 'structurally_unreachable', condition, proof)
        if role == 'baseline' and state == 'request_evidence' and (b['state'] == 'verified') and any((re.search('reference|baseline|unit', x, re.I) for x in d['needed'])):
            err(p + 'needed', 'provided_baseline_requested_again', d['needed'], b)
        for field in ['reason', 'warning_meaning', 'unavailable_reason']:
            text = d[field]
            for mat in re.finditer('(\\d[\\d,]*)\\s+(?:evaluable|usable|valid)\\s+(?:rows|observations)', text, re.I):
                claimed = int(mat.group(1).replace(',', ''))
                isref = bool(re.search('reference|train', text[max(0, mat.start() - 28):mat.start()], re.I))
                numeric = bool(re.search('numeric|numerical', text[max(0, mat.start() - 20):mat.start()], re.I))
                expected = b.get('reference_valid_n') if isref else m['valid_count'] if numeric or (condition and condition['op'] != 'is_missing') else m['row_count']
                if claimed != expected:
                    err(p + field, 'quoted_count_scope', text, {'expected': expected, 'reference': b.get('reference_valid_n'), 'numeric': m['valid_count'], 'retained': m['row_count']})
            if re.search('\\b(?:current )?activations?\\s+(?:count\\s+)?(?:is\\s+|of\\s+|=)?0\\b', text, re.I) and condition and (counts(record['compilation']['values'], condition)['satisfied'] != 0) and (not re.search('(?:even with|does not block|regardless|if|when|would)', text, re.I)):
                err(p + field, 'current_activation_count_contradiction', text, counts(record['compilation']['values'], condition))
            if len(text) >= 318 and (not re.search('[.!?][\\"\\\']?$', text.rstrip())):
                err(p + field, 'field_boundary_unfinished_explanation', text, 'Preserve the verified condition/state/evidence; write a short complete factual explanation under the field limit. No code replacement text.')
            tail = re.search('\\b(?:the|and|or|by|because|when|with|from|as)\\s*$', text, re.I)
            if tail:
                err(p + field, 'unfinished_explanation', text, 'Complete factual sentence')
    if not any(e['code']=='schema'for e in errors):
        from .official_facts import rule_description_conflicts
        errors += rule_description_conflicts(record,pred)
    return errors
