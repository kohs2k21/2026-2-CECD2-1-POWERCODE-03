"""One bounded pilot rule contract, independent of the legacy full executor."""
from pilot_io import *
from jsonschema import Draft202012Validator

def schema():
    condition={'anyOf':[{'type':'null'},{'type':'object','properties':{'op':{'const':'is_missing'}},'required':['op'],'additionalProperties':False},
        {'type':'object','properties':{'op':{'enum':['eq','gt','lt']},'value':{'type':['boolean','number']}},'required':['op','value'],'additionalProperties':False},
        {'type':'object','properties':{'op':{'const':'gt'},'baseline':{'type':'string'}},'required':['op','baseline'],'additionalProperties':False}]}
    properties={'v':{'const':'1'},'feature':{'type':'string'},'sha':{'type':'string','pattern':'^[a-f0-9]{64}$'},'role':{'enum':['HR','SR']},
        'state':{'enum':['proposed','deferred','request_evidence','no_applicable']},'basis':{'enum':['ddl_not_null','observed_diagnostic','reference_p95','insufficient','none']},
        'condition':condition,'evidence':{'type':'array','items':{'type':'string'},'uniqueItems':True},'needed':{'type':'array','items':{'type':'string'},'uniqueItems':True},'reason':{'type':'string','minLength':1}}
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}

def observational_condition(feature):
    expression=feature['plan']['expression'];typ=feature['dtype']
    if typ=='boolean':return {'op':'eq','value':True}
    if expression['op']=='parent_match_count':return {'op':'eq','value':0}
    if expression['op']=='datetime_diff':return {'op':'lt','value':0}
    return {'op':'is_missing'}

def validate_output(input_context,target,feature):
    errors=[{'path':'/'+ '/'.join(map(str,e.absolute_path)),'error':e.message}for e in Draft202012Validator(schema()).iter_errors(target)]
    def fail(code):errors.append({'code':code})
    if errors:return {'passed':False,'errors':errors}
    if target['feature']!=feature['id']or target['sha']!=feature['definition_sha256']:fail('feature_definition_reference')
    if target['role']!=('HR'if input_context['request']=='confirmed_HR_review'else 'SR'):fail('requested_role')
    if input_context['feature']['sha']!=feature['definition_sha256']or input_context['version']['source']!=feature['source_version']:fail('input_source_definition_version')
    source=input_context['scope'];summary=input_context['measurement'];condition=target['condition'];baseline=input_context['baseline'];constraints=input_context['constraints']
    if not source.get('period')or source.get('rows')!=feature['measurement']['summary']['row_count']or source['filter']!=feature['plan']['population']['filter']:fail('scope_reconstruction')
    if summary!=feature['measurement']['summary']:fail('actual_measurement_match')
    allowed={input_context['measurement_id']}|{x['id']for x in constraints}|({baseline['id']}if baseline.get('state')=='verified'else set())
    if not set(target['evidence'])<=allowed:fail('unprovided_evidence')
    if target['state']in {'proposed','deferred'}:
        if condition is None:fail('executable_or_held_condition_missing')
    elif condition is not None:fail('conditionless_review_or_nonproposal_required')
    if condition:
        if condition.get('op')!='is_missing' and 'baseline'not in condition:
            value=condition['value'];typ=feature['dtype']
            if typ=='boolean'and type(value)is not bool:fail('boolean_comparison_value')
            if typ!='boolean'and type(value)not in {int,float}:fail('numeric_comparison_value')
        if target['basis']=='ddl_not_null':
            if target['role']!='HR'or not constraints or condition!={'op':'eq','value':True}:fail('confirmed_DDL_direction_or_scope')
            if not any(x['id']in target['evidence']for x in constraints):fail('DDL_evidence_missing')
        elif target['basis']=='observed_diagnostic':
            if target['role']!='SR'or condition!=observational_condition(feature):fail('unsupported_observational_condition')
            valid=summary['valid_count']>0
            if target['state']=='proposed'and not valid:fail('zero_valid_observations')
            if target['state']=='deferred'and(valid or not target['needed']):fail('unjustified_observation_hold')
            if input_context['measurement_id']not in target['evidence']:fail('measured_observation_evidence_missing')
        elif target['basis']=='reference_p95':
            if target['role']!='SR'or baseline.get('state')!='verified'or condition!={'op':'gt','baseline':baseline.get('id')}:fail('verified_baseline_reference')
            if baseline.get('definition_sha256')!=feature['definition_sha256']or baseline.get('unit')!=feature['unit']:fail('baseline_definition_unit')
            if baseline.get('reference_period')!=source['period']or baseline.get('population')!=feature['plan']['population']or baseline.get('source_version')!=feature['source_version']:fail('baseline_period_population_source')
            if baseline.get('value')!=summary.get('p95')or baseline.get('method')!=summary.get('quantile_method'):fail('measured_baseline_value_method')
            if baseline['id']not in target['evidence']:fail('baseline_evidence_missing')
        else:fail('condition_basis')
    if target['state']=='request_evidence'and not target['needed']:fail('additional_information_missing')
    if target['role']=='HR'and target['state']=='proposed'and target['basis']!='ddl_not_null':fail('unconfirmed_business_HR')
    if target['state']=='no_applicable'and(target['basis']!='none'or target['needed']):fail('nonproposal_structure')
    return {'passed':not errors,'errors':errors,'scope_restored':not any(x.get('code')=='scope_reconstruction'for x in errors)}
