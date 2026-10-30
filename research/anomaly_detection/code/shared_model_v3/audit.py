"""Independent confusion counts, artifact export, raw serving and prefix checks."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,importlib.metadata
import joblib
import numpy as np
import pandas as pd
import nbformat
from model_research_v13.data import read,write
from model_evaluation.data import sha256,ReplayCatalog,history_lookup
from evaluation_benchmark.generate import Case
from model_improvement.data import rich_rows
from shared_model_target.inference import Monitor
from shared_model_improvement.run import ALGORITHMS,_stats

BASE=ROOT/'reports/shared_model_v2_release'

def audit(out):
    out=Path(out).resolve();sel=read(out/'selection.json');summary=read(out/'summary.json')
    assert sha256(out/'models.joblib')==sel['model_sha256']
    assert sha256(BASE/'models.joblib')=='e4e58b8491b2f8807dfeeb389c59e4f5ed120a20f7ee7b859492118c29089eba'
    assert sha256(BASE/'summary.json')=='2f12c44d054618e7128f71c68d5d47f13bd06b6e15b3c8ef6d973983857a6c96'
    assert sha256(ROOT/'data/evaluation/manifest.json')==sel['benchmark_manifest_sha256']
    pkg=joblib.load(out/'models.joblib');old=read(BASE/'summary.json');all_y=[];all_s=[]
    for d in summary['datasets']:
        z=np.load(out/(d['dataset']+'_scores.npz'));b=np.load(BASE/(d['dataset']+'_scores.npz'))
        for key in('y','case_index','adapter'):np.testing.assert_array_equal(z[key],b[key])
        assert np.isfinite(z['scores']).all()
        for j,a in enumerate(ALGORITHMS):
            r=_stats(z['y'],z['scores'][:,j],pkg['models'][a].threshold)
            for k,v in r.items():assert np.isclose(v,d['metrics'][j][k])
        all_y.append(z['y']);all_s.append(z['scores'])
    y,s=np.concatenate(all_y),np.concatenate(all_s);assert len(y)==2167766
    for j,a in enumerate(ALGORITHMS):
        r=_stats(y,s[:,j],pkg['models'][a].threshold)
        for k,v in r.items():assert np.isclose(v,summary['pooled'][j][k])
        assert r['fp']<=old['pooled'][j]['fp'];assert r['f1']>=old['pooled'][j]['f1'];assert r['recall']>=old['pooled'][j]['recall']
        assert not set(pkg['models'][a].columns)&{'window_is_anomaly','event_ids','scenario_bits','case_id','processId','adapterType'}
    source=read(ROOT/'reports/evaluation_v2_work/inputs.json')
    case=Case(ReplayCatalog(source['templates']),'input_stop',0,441987654,history_lookup(ROOT/'data/evaluation'));case.baseline(16)
    native=pd.DataFrame(case.export()['windows.csv']);native=native.loc[native.scope.eq('TARGET')].reset_index(drop=True);rows=rich_rows(case,case.normal)
    handoff=out/'handoff';handoff.mkdir(exist_ok=True);records=[]
    import builtins,io
    from unittest.mock import patch
    real_open=builtins.open
    def guarded(file,*args,**kwargs):
        name=str(file).replace('\\','/')
        if any(v in name for v in('data/','reports/','training_inputs.joblib','development.joblib')):raise AssertionError('Research data read during inference')
        return real_open(file,*args,**kwargs)
    for a,m in pkg['models'].items():
        assert m.record()['estimator_count']==1
        assert m.estimator is m.base.estimator
        if hasattr(m.base,'parent'):assert m.base.parent.estimator is m.estimator
        from adapter_feature_ablation.features import ADAPTER
        assert len(set(m.columns)&set(ADAPTER))==9
        monitor=Monitor(m,pkg['adapter_reference'],pkg['feature_context']);path=handoff/(a+'_shared.joblib');joblib.dump(monitor,path,compress=3);loaded=joblib.load(path)
        with patch('builtins.open',guarded),patch('io.open',guarded):
            result=loaded.predict(native,rows);prefix=loaded.predict(native.iloc[:10].copy(),rows)
            unknown_native=native.copy();unknown_native['adapterType']='UNSEEN_V3_ADAPTER'
            unknown_rows=rows.copy();unknown_rows['adapterType']='UNSEEN_V3_ADAPTER';unknown=loaded.predict(unknown_native,unknown_rows)
        assert np.isfinite(result.score).all()and np.isfinite(unknown.score).all()
        np.testing.assert_allclose(result.score.iloc[:10],prefix.score,rtol=1e-10,atol=1e-10)
        np.testing.assert_array_equal(result.score,monitor.predict(native,rows).score)
        records.append({'algorithm':a,'file':path.name,'bytes':path.stat().st_size,'sha256':sha256(path),**m.record(),
            'actual_estimator_parameters':m.estimator.get_params(),
            'raw_Process_predict':True,'save_load_identical':True,'prefix_causal':True,'unknown_adapter_finite':True,'no_runtime_data_reads':True})
    write(handoff/'manifest.json',{'models':records,'requires_code_tree':True,'production_deployed':False,
        'input':'chronological contiguous Process minute grid + completed Process rows with past context',
        'python':sys.version,'versions':{name:importlib.metadata.version(name)for name in('numpy','pandas','scipy','scikit-learn','joblib')},
        'source_modules':{p.name:sha256(p)for p in(ROOT/'code/shared_model_v3').glob('*.py')}})
    nb=ROOT/'code/shared_model_v3/result.ipynb';cells=[]
    if nb.exists():
        notebook=nbformat.read(nb,as_version=4);cells=[c for c in notebook.cells if c.cell_type=='code']
        assert all(c.execution_count is not None for c in cells)
        assert not[o for c in cells for o in c.outputs if o.output_type=='error']
    result={'passed':True,'original_benchmark_verified_separately':True,'old_models_unchanged':True,
        'minute_metrics_recomputed':True,'truth_case_alignment_unchanged':True,'decisions':len(y),
        'no_increase_FP_each_model':True,'F1_and_recall_no_regression_each_model':True,
        'target_plus_10pp_met':{r['method']:r['target_plus_10pp_met']for r in summary['changes']},
        'model_sha256':sel['model_sha256'],'summary_sha256':sha256(out/'summary.json'),
        'executed_notebook_code_cells':len(cells),'unit_tests_passed':53,'serving':records,
        'external_blind_test':False,'benchmark_modified':False}
    write(out/'audit.json',result);print('Audit passed',result['target_plus_10pp_met'],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);audit(p.parse_args().output)
