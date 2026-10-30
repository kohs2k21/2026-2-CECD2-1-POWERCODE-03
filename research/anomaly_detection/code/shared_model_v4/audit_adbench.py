"""Recompute the tabular result and independently audit exact split overlap."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import importlib.metadata
import joblib
import numpy as np
import pandas as pd
import nbformat
from shared_model_v4.adbench_training import OUT as SOURCE,COLUMNS,FLAGS,fingerprint
from shared_model_v4.finalize_adbench import OUT
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import ALGORITHMS,_stats

def run():
    preparation=read(SOURCE/'preparation.json');assert sha256(SOURCE/'development.joblib')==preparation['development_sha256']
    z=np.load(ROOT/'data/adbench_1000/dataset.npz');assert sha256(ROOT/'data/adbench_1000/dataset.npz')==preparation['fixed_npz_sha256']
    d=joblib.load(SOURCE/'development.joblib');sets={k:{fingerprint(row)for row in f[list(COLUMNS)].to_numpy(float)}for k,f in d.items()};test={fingerprint(row)for row in z['X']}
    assert not any(s&test for s in sets.values());keys=list(sets)
    assert not any(sets[a]&sets[b]for i,a in enumerate(keys)for b in keys[i+1:])
    assert not d['train'].label.any()and not d['calibration'].label.any()
    f=pd.DataFrame(z['X'],columns=z['columns'])
    for c in FLAGS:f[c]=f[c].astype(bool)
    f['adapterType']='ADBENCH_GLOBAL_CONTEXT';f['case_id']=z['sample_id'];saved=np.load(OUT/'scores.npz');selection=read(OUT/'selection.json');results=read(OUT/'results.json');records=[]
    np.testing.assert_array_equal(saved['y'],z['y']);np.testing.assert_array_equal(saved['sample_id'],z['sample_id'])
    for a,r in zip(ALGORITHMS,results['results']):
        path=OUT/(a+'_adbench_specific.joblib');assert sha256(path)==selection['selected'][a]['model_sha256'];m=joblib.load(path);s=m.score(f)
        np.testing.assert_array_equal(s,saved[a]);assert not set(m.columns)&{'label','generation_mode','case_id','scenario_bits'}
        for k,v in _stats(z['y'],s,m.threshold).items():assert np.isclose(v,r[k])
        assert r['F1_target_met']and r['FP_cap_met']
        records.append({'method':a,'file':path.name,'bytes':path.stat().st_size,'sha256':sha256(path),'threshold':m.threshold,
            'actual_estimator_parameters':m.estimator.get_params(),'save_load_reproducible':True})
    nb=nbformat.read(ROOT/'code/shared_model_v4/result.ipynb',as_version=4);code=[c for c in nb.cells if c.cell_type=='code']
    assert all(c.execution_count is not None for c in code);assert not[o for c in code for o in c.outputs if o.output_type=='error']
    write(OUT/'audit.json',{'passed':True,'goals_met':{r['method']:r['F1_target_met']and r['FP_cap_met']for r in results['results']},
        'exact_test_vector_overlap_with_fit_calibration_development':0,'exact_pairwise_training_split_overlap':0,
        'normal_only_fit':True,'fixed_test_unchanged':True,'metric_recomputed':True,'models':records,
        'ADBench_specific_protocol_not_service_transfer':True,'shared_generator_population_not_external_blind':True,
        'executed_notebook_code_cells':len(code),'versions':{n:importlib.metadata.version(n)for n in ('numpy','pandas','scipy','scikit-learn','joblib')},
        'requires_code_tree':True,'safe_trusted_joblib_only':True})
    print('ADBench + notebook audit passed',[(r['method'],r['bytes'])for r in records],flush=True)

if __name__=='__main__':run()
