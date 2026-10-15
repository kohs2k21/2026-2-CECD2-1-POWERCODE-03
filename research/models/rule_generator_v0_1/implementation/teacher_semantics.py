"""Bounded pilot teacher checks; the old project executor is unchanged."""
from pilot_io import *
from v1_validator import validate_output, observational_condition, schema

UNITS = {'count','ratio','dimensionless','ms','s','us'}


def missing_reference_fact(i, f):
    if f['dtype']=='boolean':
        return None  # The supplied Boolean type is known: numeric policy is inapplicable.
    if f['unit'] not in UNITS:
        return 'physical_or_comparable_calculation_unit'
    if i['measurement']['valid_count']==0:
        return 'valid_reference_values'
    if i['measurement']['valid_count']<20:
        return 'minimum_reference_sample'
    return None


def semantic_check(i, target, f):
    result = validate_output(i,target,f)
    errors = result['errors'].copy()
    def fail(code): errors.append({'code':code})
    if not isinstance(target,dict):
        return {'passed':False,'errors':[{'code':'object_required'}]}
    if i['feature']['dtype']!=f['dtype'] or i['feature']['unit']!=f['unit']:
        fail('input_verified_type_unit_mismatch')
    if i['feature']['expression']!=f['plan']['expression'] or i['feature']['grain']!=f['plan']['grain']:
        fail('input_expression_grain_mismatch')
    if i['measurement_sha']!=digest(f['measurement']): fail('measurement_sha')
    if i['version']['schema']!=f['schema_version']: fail('schema_version')
    req = i['request']; state = target.get('state')
    if state=='proposed' and target.get('needed'):fail('proposed_with_unresolved_requirements')
    if state=='deferred' and target.get('needed')!=['usable_calculated_observations']:fail('hold_only_actual_observation_gap')
    if req=='confirmed_HR_review':
        confirmed = bool(i['constraints'])
        for c in i['constraints']:
            col = c['target']
            matching = [s for s in i['sources']['columns'] if (s['table'],s['column'])==(col['table'],col['column'])]
            if not matching or matching[0]['nullable'] is not False: fail('HR_NOT_NULL_source_declaration')
            if f['plan']['expression']!={'op':'is_missing','args':[col]}: fail('HR_projection_exact_source')
            if not set(c['schema_evidence_ids']).issubset(set(matching[0]['evidence_ids'])):fail('HR_authoritative_evidence_binding')
        if state!=('proposed' if confirmed else 'no_applicable'):fail('confirmed_HR_retention_or_nonapplication')
    elif req=='observational_diagnostic':
        expected = 'proposed' if i['measurement']['valid_count']>0 else 'deferred'
        if state!=expected:fail('usable_observations_vs_zero_true')
        if target.get('condition')!=observational_condition(f):fail('typed_diagnostic_preserved')
    elif req=='numeric_reference_comparison':
        b = i['baseline']
        if f['dtype']=='boolean':
            if state!='no_applicable' or target.get('needed'):fail('known_boolean_is_not_missing_type_evidence')
        elif b['state']=='verified':
            if state!='proposed' or target.get('basis')!='reference_p95':fail('available_baseline_not_recovered')
            if b['sha']!=digest({k:v for k,v in b.items()if k!='sha'}):fail('baseline_sha')
            if b['n']!=i['measurement']['valid_count'] or b['n']<20:fail('baseline_sample_condition')
            if b['missing']!='exclude from reference and propagate unavailable at comparison':fail('baseline_missing')
            if b['operator']!='gt' or b['unit']!=f['unit']:fail('baseline_comparison_unit')
        else:
            need = missing_reference_fact(i,f)
            if need is None:fail('baseline_preparation_missing_despite_sufficient_facts')
            if state!='request_evidence' or target.get('needed')!=[need]:fail('request_only_actually_missing_fact')
    else: fail('unsupported_pilot_request')
    if f['unit']=='ratio':
        e=f['plan']['expression']
        if e['op']!='divide' or any(x['op'] not in {'child_count','conditional_count'}for x in e['args']):
            fail('ratio_requires_verified_count_operands')
    return {'passed':not errors,'errors':errors,'policy':'pilot-teacher-semantic-2.0.0'}


def make_target(i,f):
    t={'v':'1','feature':f['id'],'sha':f['definition_sha256'],'role':'HR'if i['request']=='confirmed_HR_review'else 'SR',
       'state':'proposed','basis':'observed_diagnostic','condition':None,'evidence':[],'needed':[],'reason':''}
    n=i['measurement']['valid_count']
    if i['request']=='confirmed_HR_review':
        if i['constraints']:
            t.update(basis='ddl_not_null',condition={'op':'eq','value':True},evidence=[i['constraints'][0]['id']],
                     reason='The supplied confirmed NOT NULL declaration applies to this exact missing-value indicator. True means violation; zero observed violations does not remove the contract.')
        else:
            t.update(state='no_applicable',basis='none',reason='No supplied confirmed constraint yields a rule on this exact derived calculation. Observed variation cannot create a business contract.')
    elif i['request']=='observational_diagnostic':
        t.update(condition=observational_condition(f),evidence=[i['measurement_id']])
        if n==0:
            t.update(state='deferred',needed=['usable_calculated_observations'],
                     reason='The known typed diagnostic condition is retained, but there are zero usable calculated observations. Unavailable results are neither false nor zero.')
        else:
            t['reason']='Usable calculated observations support this diagnostic in the supplied scope, including zero true events. No incident, clock-synchronization or business-duration claim is made.'
    else:
        if f['dtype']=='boolean':
            t.update(state='no_applicable',basis='none',reason='The supplied type is confirmed Boolean. This numeric p95 comparison policy does not apply; type or unit evidence is not missing.')
        elif i['baseline']['state']=='verified':
            b=i['baseline']
            t.update(basis='reference_p95',condition={'op':'gt','baseline':b['id']},evidence=[i['measurement_id'],b['id']],
                     reason='Use the supplied verified p95 reference with this exact definition, unit, source and population. This comparison is a snapshot diagnostic, not an incident threshold.')
        else:
            need=missing_reference_fact(i,f)
            t.update(state='request_evidence',basis='insufficient',needed=[need],
                     reason={'physical_or_comparable_calculation_unit':'The arithmetic definition is known, but physical-unit compatibility remains unconfirmed. A comparable numeric reference needs that evidence.',
                             'valid_reference_values':'The unit is known, but no usable calculated reference observations exist. Obtain valid reference values without inventing a cutoff.',
                             'minimum_reference_sample':'The unit and existing values are known, but the reference sample does not meet the supplied minimum of 20. More eligible reference observations are needed.'}[need])
    return t
