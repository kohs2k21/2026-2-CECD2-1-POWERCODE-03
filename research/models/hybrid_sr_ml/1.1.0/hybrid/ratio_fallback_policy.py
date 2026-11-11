"""Evaluate a declared alternative when a known count denominator is zero."""
from dataclasses import dataclass
from decimal import Decimal,InvalidOperation
import hashlib,json

VERSION='count-ratio-alternative-evaluation1.0'
ALTERNATIVES={'rule_021:baseline':'rule_015:baseline','rule_022:baseline':'rule_016:baseline',
 'rule_023:baseline':None,'rule_024:baseline':'rule_015:baseline','rule_025:baseline':None,
 'rule_028:baseline':'rule_016:baseline','rule_030:baseline':'rule_029:baseline',
 'rule_043:baseline':'rule_038:baseline','rule_044:baseline':'rule_045:baseline',
 'rule_046:baseline':'rule_032:baseline','rule_047:baseline':'rule_033:baseline',
 'rule_051:baseline':'rule_041:baseline','rule_052:baseline':'rule_042:baseline',
 'rule_053:baseline':'rule_039:baseline'}
COUNTER_RULES={'rule_021:baseline','rule_022:baseline','rule_023:baseline','rule_024:baseline','rule_025:baseline','rule_028:baseline'}
COUNTER_COLUMNS={'rule_021:baseline':('TOTAL_COUNT','SUCCESS_COUNT'),'rule_022:baseline':('TOTAL_COUNT','ERROR_COUNT'),
 'rule_023:baseline':('ERROR_COUNT','TOTAL_COUNT'),'rule_024:baseline':('ERROR_COUNT','SUCCESS_COUNT'),
 'rule_025:baseline':('SUCCESS_COUNT','TOTAL_COUNT'),'rule_028:baseline':('SUCCESS_COUNT','ERROR_COUNT')}
STATES={'warning','nonwarning','unavailable'}

def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def finite_count(x):
    if x is None or isinstance(x,bool):return None
    try:v=Decimal(str(x))
    except (InvalidOperation,ValueError,TypeError):return None
    return v if v.is_finite() and v>=0 else None

@dataclass(frozen=True)
class Result:
    state:str
    used_fallback:bool
    reason:str
    actual_rule:str|None=None

def decide(rule_id,numerator,denominator,original_state,alternative_state=None,counters=None):
    if original_state not in STATES:raise ValueError('Unknown original state')
    if rule_id not in ALTERNATIVES:return Result(original_state,False,'not_allowlisted')
    n,d=finite_count(numerator),finite_count(denominator)
    if n is None or d is None:return Result('unavailable',False,'count_operand_missing_nonfinite_or_negative')
    if d>0:return Result(original_state,False,'original_positive_denominator_comparison')
    if original_state!='unavailable':raise ValueError('Zero denominator must retain original undefined comparison')
    if rule_id in COUNTER_RULES:
        if not isinstance(counters,dict):return Result('unavailable',False,'need_all_three_counter_values')
        total,success,error=(finite_count(counters.get(k)) for k in ['TOTAL_COUNT','SUCCESS_COUNT','ERROR_COUNT'])
        if any(v is None for v in (total,success,error)):return Result('unavailable',False,'counter_source_unavailable')
        values=dict(TOTAL_COUNT=total,SUCCESS_COUNT=success,ERROR_COUNT=error)
        nk,dk=COUNTER_COLUMNS[rule_id]
        if n!=values[nk] or d!=values[dk]:return Result('unavailable',False,'counter_operand_binding_mismatch')
        actual='fallback:counter_partition_consistency'
        if total!=success+error:return Result('warning',True,'reported_total_differs_from_success_plus_error',actual)
        if ALTERNATIVES[rule_id] is None:return Result('nonwarning',True,'counter_partition_consistency_evaluated',actual)
    if alternative_state not in STATES:return Result('unavailable',False,'alternative_rule_result_missing')
    return Result(alternative_state,True,'existing_difference_reference_evaluated','fallback:'+rule_id+'=>'+ALTERNATIVES[rule_id])

def make_registry(profile):
    d=profile['definition'];rules={r['id']:r for r in d['rules']};features={f['id']:f['feature'] for f in d['features']};records=[]
    if len(rules)!=len(d['rules']):raise ValueError('Duplicate source rule')
    for rid,aid in ALTERNATIVES.items():
        r=rules[rid];f=features[r['feature_id']];x=f['expression']
        if r['role']!='numeric_SR' or x['op']!='divide' or r['feature_sha']!=f['sha']:raise ValueError('Ratio binding changed')
        operand_metadata=[]
        for node in x['args']:
            if node['op'] in ('child_count','conditional_count'):
                rel=next(q for q in d['schema']['relationships'] if q['id']==node['relationship_id'])
                if rel['to_table']!=r['table']:raise ValueError('Ratio relation grain mismatch')
                operand_metadata.append(dict(operation=node['op'],relation=rel,predicate=node.get('where')))
            elif node['op']=='column':
                table=next(t for t in d['schema']['tables'].values() if t['name']==r['table']);c=next(c for c in table['columns'] if c['name']==node['column'])
                if node['table']!=r['table'] or c['unit']['status']!='verified' or c['unit']['name']!='count':raise ValueError('Count unit unverified')
                operand_metadata.append(dict(table=node['table'],column=c))
            else:raise ValueError('Unsupported ratio operand')
        alt=None
        if aid is not None:
            ar=rules[aid];af=features[ar['feature_id']]
            if ar['role']!='numeric_SR' or ar['feature_sha']!=af['sha'] or af['expression']['op']!='subtract':raise ValueError('Alternative binding changed')
            if (af['grain'],af['population'],af['group_by'])!=(f['grain'],f['population'],f['group_by']):raise ValueError('Alternative population/grain differs')
            if af['expression']['args'][1]!=x['args'][1]:raise ValueError('Alternative denominator operand mismatch')
            if rid not in ('rule_024:baseline','rule_028:baseline') and af['expression']['args'][0]!=x['args'][0]:raise ValueError('Alternative numerator operand mismatch')
            b=ar['baseline']
            if b['state']!='verified' or b['reference_split']!='train' or b['reference_valid_n']<b['minimum_n'] or b['value']!=ar['condition']['value'] or b['operator']!=ar['condition']['op'] or b['unit']!=af['unit']:raise ValueError('Alternative reference unavailable')
            alt=dict(rule=ar,feature=af)
        counter_columns=[]
        if rid in COUNTER_RULES:
            table=next(t for t in d['schema']['tables'].values() if t['name']==r['table'])
            counter_columns=[next(c for c in table['columns'] if c['name']==k) for k in ['TOTAL_COUNT','SUCCESS_COUNT','ERROR_COUNT']]
            if any(c['unit']['status']!='verified' or c['unit']['name']!='count' for c in counter_columns):raise ValueError('Counter unit unverified')
        row=dict(rule=r,feature=f,alternative=alt,operand_metadata=operand_metadata,trigger='known finite nonnegative numerator and denominator==0',counter_partition_check=rid in COUNTER_RULES,counter_columns=counter_columns)
        row['binding_sha256']=digest(row);records.append(row)
    return dict(version=VERSION,mode='actual_alternative_evaluation',not_applicable_used=False,ratio_value_imputation=None,thresholds_recalibrated=False,ML_features_changed=False,bindings=records,
      counter_definition_evidence=dict(source='User-provided PROCESS column-description image in this conversation',quoted_definition='TOTAL_COUNT: 전체건수 (성공건수 + 실패건수 = 전체건수); SUCCESS_COUNT: 성공건수; ERROR_COUNT: 실패건수',use='Source-consistency SR diagnostic; database enforcement and incident labels unverified; HR unchanged'),
      adoption_scope='Research execution policy authorized by user; no automatic external-service deployment')
