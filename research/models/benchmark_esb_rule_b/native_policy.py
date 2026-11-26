"""Unmodified native validator functions extracted for portable CPU reuse.
See provenance.json for original file/function SHA pins. No dataset imports.
"""
from decimal import Decimal
import re


RULE_SYSTEM='''Ground each requested role. JSON {"v":"2","feature":"exact ID","sha":"exact definition SHA","decisions":[...]}. Decision exactly request,role,state,basis,condition,evidence,needed,reason. role HR/SR; state proposed/deferred/request_evidence/no_applicable; basis ddl_not_null/observed_diagnostic/reference_p95/none/insufficient. basis is a KIND; evidence contains provided IDs.
Typed conditions: {op:"eq",value:true} on Boolean; {op:"eq",value:0} for provided numeric parent absence diagnostic; {op:"is_missing"} for calculated unavailable; {op:"lt",value:0} for signed timestamp-coordinate diagnosis; {op:"gt",value:provided_p95} for verified reference comparison. HR true violates confirmed constraint; SR true activates diagnostic; unavailable separate. Exact raw NOT NULL missing indicator retains HR with current0 violations. No contract => HR no_applicable. Observed SR needs no historical baseline. valid>0,true0 is observed. Known condition with0 usable observations => deferred, preserving type and condition. Calculated null does not identify source-null cause.
Numeric reference needs compatible TRAIN-only baseline and BOTH measurement/baseline IDs, period/population/group/unit/n/minimum/method/comparator/missing/update/expiry. Verified upper bound excluding gt => no_applicable; observed maximum alone is not universal. Boolean cannot hide arbitrary numeric thresholds. Unknown unit/reference => request_evidence with specific missing ID. No invented numbers/code meanings/conservation/online necessity/clock/fault labels or extra normality/independence policy. No-applicable/deferred are not generated rules.'''


def validate_rule(inp,target):
    errors=[];known={inp['measurement_id']}|{c['id']for c in inp['constraints']}|({inp['baseline']['id']}if 'id'in inp['baseline']else set())
    if target['feature']!=inp['feature']['id'] or target['sha']!=inp['feature']['sha']:errors.append('feature_definition_ref')
    if {d['request']for d in target['decisions']}!=set(inp['requests']):errors.append('role_coverage')
    for d in target['decisions']:
        if set(d)!=set('request role state basis condition evidence needed reason'.split()):errors.append('fields')
        if d['basis']not in ['ddl_not_null','observed_diagnostic','reference_p95','none','insufficient'] or set(d['evidence'])-known:errors.append('basis/evidence')
        cond=d['condition'];state=d['state'];numeric=inp['feature']['dtype']!='boolean'
        if state in ['proposed','deferred']:
            if not isinstance(cond,dict):errors.append('missing_condition');continue
            if cond['op']=='eq' and type(cond['value'])is bool and numeric:errors.append('numeric_boolean')
            if d['request']=='hr' and (not inp['constraints'] or cond!={'op':'eq','value':True} or d['basis']!='ddl_not_null' or inp['constraints'][0]['id']not in d['evidence']):errors.append('HR_constraint')
            if d['request']=='observed' and cond!=inp['observed_diagnostic_contract']['condition']:errors.append('observation_direction')
            if d['request']=='baseline':
                b=inp['baseline']
                if b.get('state')!='verified' or cond!={'op':'gt','value':b['value']} or not {inp['measurement_id'],b['id']}<=set(d['evidence']):errors.append('baseline_refs')
                if b['range_proof']['upper']is not None and Decimal(str(b['value']))>=Decimal(str(b['range_proof']['upper'])):errors.append('unreachable')
            if state=='deferred' and inp['measurement']['valid_count']!=0:errors.append('false_observation_absence')
        elif cond is not None:errors.append('pending_condition')
        if state=='request_evidence' and not d['needed']:errors.append('missing_needed')
        if state not in ['proposed','deferred','request_evidence','no_applicable']:errors.append('state')
    return {'passed':not errors,'errors':errors,'scope':'typed conditions/constraints/refs/ranges; separate silver meaning review, not business truth'}


def expected_rule_state(inp,decision):
    req=decision['request'];f=inp['feature'];s=inp['measurement'];b=inp['baseline']
    if req=='hr':return 'proposed' if inp['constraints']else 'no_applicable'
    if req=='observed':
        ex=f['expression']
        if ex['op']in ['gt','gte','lt','lte','eq']:
            l,r=ex['args']
            if l['op']=='parent_match_count' and r['op']=='literal' and type(r['value'])in [int,float]:
                mx=inp['verified_relationship_bounds'][l['relationship_id']]['maximum_parent_matches'];z=r['value'];o=ex['op']
                if o=='gt' and z>=mx or o=='gte' and z>mx or o=='lt' and z<=0 or o=='lte' and z<0:return 'no_applicable'
        return 'proposed' if s['valid_count']>0 else 'deferred'
    if f['dtype']=='boolean':return 'no_applicable'
    if f['unit']is None or b['state']!='verified':return 'request_evidence'
    upper=b['range_proof']['upper']
    if upper is not None and Decimal(str(b['value']))>=Decimal(str(upper)):return 'no_applicable'
    return 'proposed' if s['valid_count']>0 else 'deferred'


def strict_rule(inp,target):
    check=validate_rule(inp,target);errors=list(check['errors'])
    for d in target['decisions']:
        expected=expected_rule_state(inp,d)
        if d['state']!=expected:errors.append('wrong_state/'+d['request']+'/'+expected)
        if d['request']=='hr':
            if d['role']!='HR':errors.append('HR_role')
        elif d['role']!='SR':errors.append('SR_role')
        if d['state']=='request_evidence':
            needed='need_verified_unit' if inp['feature']['unit']is None else 'need_reference_minimum_n' if inp['baseline']['state']=='insufficient_sample'else 'need_compatible_train_reference'
            if d['needed']!=[needed]:errors.append('false_or_wrong_evidence_request')
        if d['state']=='no_applicable' and d['basis']!='none':errors.append('no_app_basis')
        if d['state']=='proposed' and d['needed']:errors.append('proposed_unresolved_need')
    return {'passed':not errors,'errors':errors}


def rule_check(r,pred):
    out={'JSON_contract':False,'code':False,'condition_state':False,'evidence':False,'explanation_mechanical':False,'productive_rules':0,'errors':[],'decisions':[],'direct_meaning_review':'pending (mechanical checks are separate)'}
    try:
        assert isinstance(pred,dict)and set(pred)==set(['v','feature','sha','decisions'])and pred['v']=='2','bundle2_fields'
        assert isinstance(pred['decisions'],list)and len(pred['decisions'])==3,'three_role_coverage'
        assert len({d['request']for d in pred['decisions']})==3,'duplicate_role_request'
        for d in pred['decisions']:
            assert set(d)==set('request role state basis condition evidence needed reason'.split()),'decision_fields'
            assert all(isinstance(d[k],str)for k in ['request','role','state','basis','reason']),'decision_string_types'
            assert isinstance(d['evidence'],list)and isinstance(d['needed'],list),'reference_list_types'
        out['JSON_contract']=True;v=strict_rule(r['input'],pred);out['errors']=v['errors'];out['code']=v['passed']
        for d in pred['decisions']:
            errors=[];why=d['reason'].lower();inp=r['input']
            if not why.strip():errors.append('reason/empty')
            if any(x in why for x in ['requires normality','need normality','requires independence','need independence','must be independent','must be normal']):errors.append('reason/invented_reference_requirement')
            for hit in re.finditer(r'\b(?:independent|independence|normality|normal)\b',why):
                before=why[max(0,hit.start()-100):hit.start()]
                if not re.search(r'\b(?:no|not|without|unconfirmed|unknown)\b',before):errors.append('reason/unverified_normality_or_independence_assertion');break
            if d['state']=='deferred' and inp['measurement']['valid_count']>0:errors.append('reason/activation0_is_not_observation_absence')
            if d['state']=='request_evidence'and inp['feature']['unit'] is not None and any(x in why for x in ['unit unconfirmed','unit unknown','need verified unit','unit is missing']):errors.append('reason/confirmed_unit_ignored')
            if d['state']=='proposed'and d['request']=='baseline'and any(x in why for x in ['operational fault threshold','guaranteed fault','business failure boundary']):errors.append('reason/unverified_business_threshold')
            out['decisions'].append({'request':d['request'],'role':d['role'],'state':d['state'],'errors':errors})
            out['errors'].extend(errors)
        out['condition_state']=not any(any(z in e for z in ['state','direction','numeric_boolean','HR_constraint','role'])for e in out['errors'])
        out['evidence']=not any(any(z in e for z in ['evidence','refs','ref','unreachable','basis'])for e in out['errors'])
        out['explanation_mechanical']=not any(e.startswith('reason/')for e in out['errors'])
        if not out['errors']:out['productive_rules']=sum(d['state']=='proposed'for d in pred['decisions'])
    except Exception as e:out['errors'].append('output/'+str(e))
    return out
