"""Use already audited historical normal-source coverage, not test templates."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import pandas as pd
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v7.protocol import OUT,BASE,freeze,atomic_dump
from shared_model_v3.features import augment
from shared_model_v4.rank_context import Context,RANK

def prepare():
    freeze();dest=OUT/'expanded_normal.joblib'
    if dest.exists():
        assert sha256(dest)==read(OUT/'expanded_normal.json')['sha256'];return joblib.load(dest)
    path=ROOT/'reports/shared_model_v4_stratified/normal.joblib';receipt=read(path.parent/'preparation.json')
    assert sha256(path)==receipt['sha256']
    rows=receipt['partitions']['train']['source_template_ids'];state=joblib.load(ROOT/'reports/window_search_v2/training_inputs.joblib')
    assert not set(rows)&set(state['reserved_template_ids'])
    dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');normal=joblib.load(path);f=augment(normal['train'])
    f=pd.concat([f,Context(dev['rank_history'],6).transform(f)],axis=1)
    # The original normal reference uses held-out source templates/normal cases.
    assert not set(f.case_id)&set(dev['normal_calibration'].case_id)
    payload={**dev,'train':f};atomic_dump(payload,dest)
    write(OUT/'expanded_normal.json',{'sha256':sha256(dest),'source_sha256':sha256(path),'original_normal_train_minutes':len(dev['train']),
        'expanded_normal_train_minutes':len(f),'expanded_Adapter_Process_groups':f.groupby(['adapterType','processId']).ngroups,
        'reserved_template_intersection':0,'calibration_case_intersection':0,'test_control_or_anomaly_rows_fit':False,
        'source_provenance':receipt,'featurizer_reused':'frozen numerical definitions, source-normal replay; added causal sequence/rank',
        'not_more_independent_real_logs':True,'not_chronological_source_holdout':True})
    print('EXPANDED',len(f),'groups',f.groupby(['adapterType','processId']).ngroups,flush=True)
    return payload

if __name__=='__main__':prepare()
