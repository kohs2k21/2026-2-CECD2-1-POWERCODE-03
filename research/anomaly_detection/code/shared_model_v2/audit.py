"""Requirement audit of frozen models, all saved predictions and ADBench data."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,ast,importlib.metadata
import joblib
import numpy as np
import pandas as pd
import nbformat
from PIL import Image
from unittest.mock import patch
from adapter_feature_ablation.features import ADAPTER
from adapter_feature_ablation.run import counts
from model_evaluation.metrics import metric_record
from model_evaluation.data import sha256,verify_benchmark,ReplayCatalog,history_lookup
from model_research_v13.data import read,write
from model_improvement.data import rich_rows
from evaluation_benchmark.generate import Case
from shared_model_improvement.run import ALGORITHMS
from shared_model_v2.run import prepare
from shared_model_target.evaluate_refined import assert_same_detector

OLD=ROOT/'reports/shared_model_target_lof'
DATA=ROOT/'data/adbench_1000'


def audit(folder):
    folder=Path(folder).resolve();summary=read(folder/'summary.json');selected=read(folder/'selection.json')
    assert sha256(folder/'models.joblib')==selected['model_sha256']
    pkg=joblib.load(folder/'models.joblib');oldpkg=joblib.load(OLD/'models.joblib');previous=read(OLD/'summary.json')
    oldaudit=read(OLD/'audit.json')
    assert sha256(OLD/'models.joblib')==oldaudit['model_sha256']and sha256(OLD/'summary.json')==oldaudit['summary_sha256']
    verify_benchmark(ROOT/'data/evaluation',ROOT/'reports/evaluation_v2_work/inputs.json')
    assert_same_detector(pkg['models']['if'],oldpkg['models']['if'])
    totals=np.zeros((3,4),np.int64);n=0
    for d in summary['datasets']:
        p=np.load(folder/(d['dataset']+'_scores.npz'));old=np.load(OLD/(d['dataset']+'_scores.npz'))
        np.testing.assert_array_equal(p['y'],old['y']);np.testing.assert_array_equal(p['case_index'],old['case_index'])
        np.testing.assert_array_equal(p['adapter'],old['adapter']);np.testing.assert_array_equal(p['scores'][:,0],old['scores'][:,0])
        assert p['scores'].shape==(len(p['y']),3)and np.isfinite(p['scores']).all();n+=len(p['y'])
        for j,a in enumerate(ALGORITHMS):
            vector=counts(p['y'],p['scores'][:,j]>pkg['models'][a].threshold);totals[j]+=vector
            for k,v in metric_record(*vector).items():assert np.isclose(v,d['metrics'][j][k]),(d['dataset'],a,k)
    assert n==2167766
    requirements=[];sensitivity=[];normal=prepare()['train'].iloc[:500].copy()
    for j,a in enumerate(ALGORITHMS):
        model=pkg['models'][a];r=metric_record(*totals[j]);prior=previous['pooled'][j]
        assert r['f1']>=.55,(a,'F1 below 55%',r['f1'])
        assert r['normal_false_positive_rate']<=prior['normal_false_positive_rate']+1e-12,(a,'FPR increased')
        for k,v in r.items():assert np.isclose(v,summary['pooled'][j][k]),(a,k)
        assert len(set(model.columns)&set(ADAPTER))==9
        assert not set(model.columns)&{'case_id','processId','adapterType','window_is_anomaly','scenario','scenario_types','event_ids'}
        assert model.record()['estimator_count']==1 and not hasattr(model,'experts')
        assert model.estimator is model.base.estimator
        if hasattr(model.reference,'base'):assert model.reference.base.estimator is model.estimator
        assert all(model.base.weights[i]>0 for i,c in enumerate(model.columns)if c in ADAPTER)
        original=model.score(normal);changed=normal.copy();changed['adapter_count_p95_z']+=20
        difference=float(np.max(np.abs(original-model.score(changed))));assert difference>1e-8
        check=normal.copy();check['window_is_anomaly']=True;check['scenario_types']='oracle'
        np.testing.assert_array_equal(original,model.score(check))
        np.testing.assert_allclose(model.score(normal.iloc[:150]),original[:150],rtol=1e-10,atol=1e-10)
        requirements.append({'algorithm':a,'f1':r['f1'],'F1_target_met':True,'normal_FPR':r['normal_false_positive_rate'],
           'previous_normal_FPR':prior['normal_false_positive_rate'],
           'normal_FPR_delta_percentage_points':(r['normal_false_positive_rate']-prior['normal_false_positive_rate'])*100,
           'strictly_no_increase':r['normal_false_positive_rate']<=prior['normal_false_positive_rate'],
           'same_level_guard_passed':True})
        sensitivity.append({'algorithm':a,'Adapter_features':9,'score_change_when_Adapter_feature_changes':difference})
    # The exact CSV deliverables are the same rows/numbers used by evaluation.
    dm=read(DATA/'manifest.json');z=np.load(DATA/'dataset.npz');assert sha256(DATA/'dataset.npz')==dm['npz_sha256']
    f=pd.read_csv(DATA/'features.csv',encoding='utf-8-sig',float_precision='round_trip');labels=pd.read_csv(DATA/'labels.csv',encoding='utf-8-sig')
    np.testing.assert_array_equal(f.sample_id,labels.sample_id);np.testing.assert_array_equal(f.sample_id,z['sample_id'])
    np.testing.assert_array_equal(f[z['columns']].to_numpy(float),z['X']);np.testing.assert_array_equal(labels.is_anomaly.to_numpy(bool),z['y'])
    assert len(f)==1000 and int(z['y'].sum())==200 and f.sample_id.is_unique
    for mode,g in labels.groupby('generation_mode'):assert len(g)==250 and g.is_anomaly.sum()==50
    path=Path(importlib.metadata.distribution('adbench').locate_file('adbench/datasets/data_generator.py'))
    assert sha256(path)==dm['provenance']['source_sha256']
    tree=ast.parse(path.read_text(encoding='utf-8'));cls=next(x for x in tree.body if isinstance(x,ast.ClassDef)and x.name=='DataGenerator')
    leaf=next(x for x in cls.body if isinstance(x,ast.FunctionDef)and x.name=='generate_realistic_synthetic')
    digest=__import__('hashlib').sha256(ast.dump(leaf,include_attributes=False).encode()).hexdigest()
    assert digest==dm['provenance']['method_ast_sha256']
    ad=read(folder/'adbench_results.json');az=np.load(folder/'adbench_scores.npz');assert ad['model_sha256']==selected['model_sha256']
    assert ad['used_for_tuning']is False and ad['frozen_models_and_thresholds']is True
    np.testing.assert_array_equal(az['sample_id'],z['sample_id']);np.testing.assert_array_equal(az['y'],z['y'])
    for r in ad['results']:
        s=az[r['version']+'_'+r['method']];vector=counts(z['y'],s>r['threshold'])
        for k,v in metric_record(*vector).items():assert np.isclose(v,r[k]),('ADBench',r['version'],r['method'],k)
    # Actual save/load and raw Process serving path, including unknown Adapter.
    source=read(ROOT/'reports/evaluation_v2_work/inputs.json');case=Case(ReplayCatalog(source['templates']),'input_stop',0,987654322,history_lookup(ROOT/'data/evaluation'))
    case.baseline(12);native=pd.DataFrame(case.export()['windows.csv']);native=native.loc[native.scope.eq('TARGET')].reset_index(drop=True);rows=rich_rows(case,case.normal)
    serving=[]
    import builtins,io
    real_open=builtins.open
    def guarded_open(file,*args,**kwargs):
        name=str(file).replace('\\','/')
        if any(x in name for x in('data/','reports/','development.joblib','training_inputs.joblib')):
            raise AssertionError('Runtime attempted to read research data: '+name)
        return real_open(file,*args,**kwargs)
    for entry in read(folder/'handoff/manifest.json')['models']:
        path=folder/'handoff'/entry['file'];assert sha256(path)==entry['sha256'];model=joblib.load(path)
        with patch('builtins.open',guarded_open),patch('io.open',guarded_open):
            predicted=model.predict(native,rows);prefix=model.predict(native.iloc[:8].copy(),rows)
            unative=native.copy();unative['adapterType']='UNSEEN_ADAPTER';urows=rows.copy();urows['adapterType']='UNSEEN_ADAPTER';unknown=model.predict(unative,urows)
        assert len(predicted)==len(native)and np.isfinite(predicted.score).all()
        np.testing.assert_array_equal(predicted.anomaly,predicted.score>predicted.threshold)
        np.testing.assert_allclose(predicted.score.iloc[:8],prefix.score,rtol=1e-10,atol=1e-10)
        assert np.isfinite(unknown.score).all()and unknown.adapter_reference_level.eq(2).all()
        serving.append({'algorithm':entry['algorithm'],'saved_artifact_hash':True,'raw_Process_to_prediction':True,'prefix_causal':True,'unknown_Adapter_fallback':True,'no_research_data_reads_during_prediction':True})
    fresh=read(folder/'fresh_confirmation_v2.json');assert fresh['model_sha256']==selected['model_sha256']and fresh['used_to_reselect']is False
    assert fresh['seed']==42400042
    fi=read(folder/'fresh_confirmation_v2_inputs.json');assert fi['truth_aligned_by_case_and_window']
    assert sha256(folder/'fresh_confirmation_v2_inputs.joblib')==fi['sha256']
    notebook=nbformat.read(ROOT/'code/shared_model_v2/result.ipynb',as_version=4);cells=[c for c in notebook.cells if c.cell_type=='code']
    assert all(c.execution_count is not None for c in cells)and not[o for c in cells for o in c.outputs if o.output_type=='error']
    figures=[]
    for path in sorted((folder/'figures').glob('*.png')):
        with Image.open(path)as im:im.verify()
        figures.append(path.name)
    assert len(figures)==7
    result={'passed':True,'requirements':requirements,'all_decisions':n,'metrics_recomputed':True,
       'original_benchmark_csv_hashes_verified':True,'original_models_and_summary_preserved':True,
       'truth_and_case_alignment_unchanged':True,'IF_model_and_scores_exactly_unchanged':True,
       'Adapter_feature_sensitivity':sensitivity,'ADBench_samples':1000,'ADBench_normal':800,'ADBench_anomaly':200,
       'ADBench_official_leaf_hash_verified':True,'ADBench_CSV_and_NPZ_exact_roundtrip':True,
       'ADBench_metrics_independently_recomputed':True,'fresh_seed':fresh['seed'],
       'notebook_executed_code_cells':len(cells),'notebook_errors':0,'figures':figures,'serving':serving,
       'final_strict_FPR_margin_absolute':0.,'external_blind_test':False,'stateful_worker_implemented':False,
       'model_sha256':selected['model_sha256'],'summary_sha256':sha256(folder/'summary.json'),
       'source_sha256':{p.name:sha256(p)for p in(ROOT/'code/shared_model_v2').glob('*.py')}}
    write(folder/'audit.json',result);print(result,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);audit(p.parse_args().output)
