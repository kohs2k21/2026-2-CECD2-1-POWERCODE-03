"""Explicitly export preserved definitions/weights; never fits or copies source CSVs."""
from pathlib import Path
import copy

import joblib

from ..common import digest, new_dir, read, sha, write, ML_CODE
from ..frozen_rule_library import compile_library
from .artifacts import environment, runtime_path, runtime_fingerprints, seal


def export(legacy, lof, columns, output, llm_manifest=None):
    legacy, lof = Path(legacy), Path(lof)
    records = read(legacy/'inputs/regression65.json')
    source_rules = read(legacy/'inputs/prior_validated_rules.json')
    lib = compile_library(source_rules,records)
    features = [dict(id=r['id'],feature=r['input']['feature'],origin='AI_definition' if i<58 else 'reused_code_indicator') for i,r in enumerate(records)]
    bysha = {r['feature']['sha']:r['id'] for r in features}
    roles = dict(hr='HR',observed='observed_SR',baseline='numeric_SR')
    rules = [dict(id=r['key'],feature_id=bysha[r['feature']['sha']],feature_sha=r['feature']['sha'],table=r['feature']['grain']['table'],dtype=r['feature']['dtype'],role=roles[r['decision']['request']],condition=r['decision']['condition'],evidence_ids=r['decision']['evidence'],reason=r['decision']['reason'],baseline={k:v for k,v in r['baseline'].items() if k not in ('reference_manifest','id','sha256')}) for r in source_rules]
    info = read(columns)
    tables = {}
    for short,name in [('T','EC_TRANSACTION_MONITORING'),('P','EC_PROCESS_MONITORING'),('M','EC_MESSAGE_MONITORING')]:
        cols = [copy.deepcopy(c) for c in info[name].values() if not c.get('excluded')]
        cols.append(dict(name='__SOURCE_ROW_ID',logical_type='string',nullable=False,excluded=False,unit=dict(name=None,status='unknown'),missing_tokens=['']))
        if short in 'PM':
            cols.append(dict(name='__RETRY_LEXICAL',logical_type='string',nullable=False,excluded=False,unit=dict(name=None,status='unknown'),missing_tokens=['']))
        tables[short] = dict(name=name,columns=cols)
    relations = [dict(id=rid,from_table=tables[child]['name'],from_columns=keys,to_table=tables[parent]['name'],to_columns=keys,no_reference_values=[]) for rid,child,parent,keys in [('physical_P_to_T','P','T',['TRANSACTION_ID']),('physical_M_to_T','M','T',['TRANSACTION_ID']),('physical_M_to_P_retry','M','P',['TRANSACTION_ID','PROCESS_ID','__RETRY_LEXICAL'])]]
    contracts = {}
    for old in read(legacy/'models/input_contracts.json'):
        c = {k:copy.deepcopy(v) for k,v in old.items() if k not in ('source_pins','sha256')}
        c['sha256'] = digest(c)
        contracts[next(s for s,t in tables.items() if t['name']==c['table'])] = c
    definition = dict(schema=dict(tables=tables,relationships=relations),features=features,rules=rules,ml_contracts=contracts)
    out = new_dir(output)
    public_profile = dict(contract='shared42-frozen-specification1.0',definition=definition,definition_sha=digest(definition),roles=lib['roles'],unique_feature_conditions=lib['unique_feature_conditions'],origin=dict(features='2026-10-15 Feature epoch2 specification',rules='2026-10-16 Rule epoch2 generation',IF_KMeans='2026-10-18 saved models',LOF='2026-11-06 explicit additional training'),reference_policy='Frozen offline research comparison; capture explicitly authorizes reuse. Not a certified normal/SLA baseline or online expiry policy.',raw_customer_rows_included=False)
    if llm_manifest:
        original = read(llm_manifest)
        public_profile['design_provenance'] = {name:dict(adapter_sha256=sha(Path(path)/'adapter_model.safetensors'),config_sha256=sha(Path(path)/'adapter_config.json'),new_generation_in_this_delivery=False,adapter_used_for_offline_specification_not_detection=True) for name,path in [('Feature',original['Feature_adapter_fixed']),('Rule',original['Rule_adapter_selected']['adapter'])]}
    write(out/'profile.json',public_profile)
    runtime_path()
    model_dir = new_dir(out/'models')
    models, provenance = {}, []
    for short in 'TPM':
        for algorithm in ('if','kmeans','lof'):
            source = lof/(short+'_lof/model.joblib') if algorithm == 'lof' else legacy/'models'/(short+'_'+algorithm+'.joblib')
            obj = joblib.load(source)
            obj['contract'] = contracts[short]
            obj['feature_contract_sha'] = contracts[short]['sha256']
            # Model objects are unchanged; only local archive metadata is removed.
            obj.pop('manifest',None)
            file = short+'_'+algorithm+'.joblib'
            joblib.dump(obj,model_dir/file,compress=3)
            models[short+'_'+algorithm] = dict(file=file,sha256=sha(model_dir/file),format='novelty_lof' if algorithm=='lof' else 'legacy_family')
            provenance.append(dict(model=short+'_'+algorithm,original_file_sha=sha(source),export_file_sha=sha(model_dir/file),weights_refit=False,metadata_change='relative portable contract; source paths removed'))
    write(model_dir/'model_set.json',dict(contract='shared42-model-set1.0',definition_sha=digest(definition),environment=environment(),runtime_fingerprints=runtime_fingerprints(),models=models,origin='previous_validated_models_portable_export',weights_refit=False,provenance=provenance))
    seal(model_dir,'model_set')
    seal(out,'frozen_release')
    return out


if __name__ == '__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('legacy','lof','columns','output'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--llm-manifest')
    a=p.parse_args()
    print(export(a.legacy,a.lof,a.columns,a.output,a.llm_manifest))
