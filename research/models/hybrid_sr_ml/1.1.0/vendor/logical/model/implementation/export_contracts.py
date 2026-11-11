"""Export one AI-authored logical plan as a typed feature and a referencing rule.

This is a deterministic projection, not another learned feature discovery call.
Original model proposals are never edited and exported candidates are not activated.
"""
from pathlib import Path
import argparse,json
from run import PACKAGE
from specmodel.io import read,write,digest,sha
from discovery_runtime import POLICY
from typed_plan import POLICY as TYPED_RUNTIME_POLICY
FEATURE_CONTRACT="logical-feature-definition1.3-runtime-binding"

def export(pack,source):
    verify_source_pins(pack,source)
    features=[];rules=[];holds=[];feature_ids=set();rule_ids={}
    for c in pack['proposal']['candidates']:
        if c['plan']is None:
            holds.append({'proposal_id':c['id'],'state':'request_evidence','needed':c['needed'],'source_evidence':c['evidence'],'reason':c['reason']});continue
        plan=c['plan'];identity={'contract':FEATURE_CONTRACT,'calculation':plan,
            'schema':source['schema'],'documents':source['documents'],
            'operator_policy':pack['policy'],'typed_operator_runtime_policy':TYPED_RUNTIME_POLICY,
            'input_availability_policy':pack.get('input_availability_policy'),
            'predicate_consistency_policy':pack.get('predicate_consistency_policy'),
            'requires_complete_capture':c['requires_complete_capture'],
            'inferred_source_types':pack.get('inferred_source_types',{}),
            'missing_policy':'three-valued source logic; unknown is not zero or false; absence needs complete capture'}
        feature_sha=digest(identity);fid='logical_'+feature_sha
        feature={'contract':FEATURE_CONTRACT,'feature_id':fid,'definition_sha256':feature_sha,
            'calculation':plan,'data_type':'nullable_boolean','unit':'boolean',
            'grain':{'table':plan['select']['table'],'declared_keys':source['schema'].get(plan['select']['table'],{}).get('keys',[]),
                     'row_provenance_always_preserved':True},
            'population':'all admitted rows of the root table; the plan selects the applicable review population',
            'applicability_filter':plan['select']['filter'],
            'value_semantics':{'true':'this proposed logical predicate fires','false':'the predicate does not fire, including known non-applicability',
                              'null':'required source, relationship or capture evidence is unavailable'},
            'requires_complete_capture':c['requires_complete_capture'],
            'availability':'after the admitted source snapshot is available; no actual ingestion latency is assumed',
            'missing_policy':'three-valued source logic; unknown is not zero or false; absence needs complete capture',
            'source_schema_sha256':pack['physical_schema_sha'],'source_documents_sha256':pack['documents_sha'],
            'typed_operator_runtime_policy':TYPED_RUNTIME_POLICY,
            'source_evidence':c['evidence'],'AI_proposed_plan':True,'compiler_generated_feature_wrapper':True,
            'independent_Feature_LoRA_call':False,'not_counted_as_an_additional_model_discovery':True,
            'review_state':'pending_semantic_and_operational_review'}
        if fid not in feature_ids:features.append(feature);feature_ids.add(fid)
        rid='rule_'+digest({'feature_definition_sha':feature_sha,'role':c['role']})
        if rid in rule_ids:
            rule_ids[rid].setdefault('additional_model_proposal_ids',[]).append(c['id'])
            continue
        rules.append({'contract':'logical-rule-recommendation1.0','rule_id':rid,'model_proposal_id':c['id'],'role':c['role'],
            'condition':{'feature_id':fid,'feature_definition_sha256':feature_sha,'op':'is_true'},
            'normal_exceptions':c['normal_exceptions'],'source_evidence':c['evidence'],'needed':c['needed'],'reason':c['reason'],
            'unavailable_action':'unavailable; never no_alert by default','enabled':False,
            'numeric_threshold_adjustment':False,'rule_regeneration_required_for_calculation_change':True,
            'review_state':'pending_semantic_and_operational_review'})
        rule_ids[rid]=rules[-1]
    return {'feature_contracts':features,'rule_recommendations':rules,'evidence_requests':holds,
        'original_proposal_sha256':digest(pack['proposal']),'original_frozen_pack_definition_sha':pack['definition_sha'],
        'code_execution_valid':pack['execution']['executable_valid'],'semantic_correctness_not_proven_by_code':True,
        'ML_contract_caution':'New Boolean features require an explicitly retrained/validated ML input schema; frozen old models are unchanged.'}

def bind_values(contracts,logical_features):
    """Bind measured nullable values to the exact exported feature definitions.

    Legacy internal values are retained separately by the caller. No value is
    recalculated, imputed or sent to an LLM in this projection.
    """
    bindings={}
    for rule in contracts['rule_recommendations']:
        for proposal_id in [rule['model_proposal_id']]+rule.get('additional_model_proposal_ids',[]):
            bindings[proposal_id]=rule['condition']
    values=[]
    for original in logical_features:
        prefix='rule_predicate:'
        if not original['feature_id'].startswith(prefix):
            raise ValueError('Unsupported internal feature reference')
        proposal_id=original['feature_id'][len(prefix):]
        if proposal_id not in bindings:
            raise ValueError('Measured feature has no exported definition: '+proposal_id)
        reference=bindings[proposal_id]
        values.append({**original,'feature_id':reference['feature_id'],
            'feature_definition_sha256':reference['feature_definition_sha256'],
            'frozen_pack_definition_sha256':original['definition_sha'],
            'original_model_proposal_id':proposal_id,'data_type':'nullable_boolean'})
    return values

def verify_source_pins(pack,source):
    if pack.get('policy')!=POLICY or pack.get('execution',{}).get('policy')!=POLICY:
        raise ValueError('Unsupported or mismatched rule execution policy')
    if pack['execution'].get('executable_valid')is not True:
        raise ValueError('Source-invalid rule pack cannot be exported')
    if digest(source['schema'])!=pack['physical_schema_sha']:
        raise ValueError('Source schema SHA mismatch')
    if digest(source['documents'])!=pack['documents_sha']:
        raise ValueError('Source documents SHA mismatch')
    expected=digest({'proposal':pack['proposal'],'schema':source['schema'],'documents':source['documents']})
    if expected!=pack['definition_sha']:
        raise ValueError('Rule definition SHA mismatch')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--rules',required=True,type=Path)
    p.add_argument('--source',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);pack=read(a.rules);source=read(a.source)
    verify_source_pins(pack,source)
    result=export(pack,source);write(a.output/'linked_contracts.json',result)
    write(a.output/'source_pins.json',{'rule_pack_sha256':sha(a.rules),'source_sha256':sha(a.source),'actual_LLM_calls':0})
    print(json.dumps({'features':len(result['feature_contracts']),'rules':len(result['rule_recommendations']),
        'evidence_requests':len(result['evidence_requests']),'operational_rules_enabled':0,'actual_LLM_calls':0}))
if __name__=='__main__':main()
