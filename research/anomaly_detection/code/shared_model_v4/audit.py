"""Independent metric recomputation and raw Process serving smoke tests."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import importlib.metadata,unittest,io,argparse
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import read,write
from model_evaluation.data import sha256,ReplayCatalog,history_lookup
from evaluation_benchmark.generate import Case
from model_improvement.data import rich_rows
from shared_model_target.inference import Monitor
from shared_model_improvement.run import ALGORITHMS,_stats
from shared_model_v4.run_rank import OUT

def run(output=None):
    global OUT
    if output is not None:OUT=Path(output).resolve()
    selection=read(OUT/'selection.json');assert sha256(OUT/'models.joblib')==selection['model_sha256']
    protocol=read(ROOT/'reports/shared_model_v4/protocol.json');assert sha256(ROOT/'data/adbench_1000/dataset.npz')==protocol['ADBench_npz_sha256']
    previous=ROOT/'reports/shared_model_v3_release';previous_selection=read(previous/'selection.json')
    assert sha256(previous/'models.joblib')==previous_selection['model_sha256']
    assert read(previous/'summary.json')['pooled']==protocol['log_baseline']
    assert sha256(ROOT/'data/evaluation/manifest.json')==protocol['benchmark_manifest_sha256']
    pkg=joblib.load(OUT/'models.joblib');summary=read(OUT/'summary.json');ys=[];scores=[]
    for d in summary['datasets']:
        z=np.load(OUT/(d['dataset']+'_scores.npz'));old=np.load(ROOT/'reports/shared_model_v3_release'/(d['dataset']+'_scores.npz'))
        for key in ('y','case_index','adapter'):np.testing.assert_array_equal(z[key],old[key])
        assert np.isfinite(z['scores']).all()
        for j,a in enumerate(ALGORITHMS):
            for key,value in _stats(z['y'],z['scores'][:,j],pkg['models'][a].threshold).items():assert np.isclose(value,d['metrics'][j][key])
        ys.append(z['y']);scores.append(z['scores'])
    y=np.concatenate(ys);s=np.concatenate(scores);assert len(y)==2167766
    for j,a in enumerate(ALGORITHMS):
        for key,value in _stats(y,s[:,j],pkg['models'][a].threshold).items():assert np.isclose(value,summary['pooled'][j][key])
    modules=('shared_model_v4.test_v4','shared_model_v3.test_v3','shared_model_v2.test_v2','shared_model_target.test_target','shared_model_improvement.test_models','adapter_feature_ablation.test_ablation')
    tests=unittest.defaultTestLoader.loadTestsFromNames(modules);stream=io.StringIO();result=unittest.TextTestRunner(stream=stream).run(tests)
    print(stream.getvalue(),flush=True);assert result.wasSuccessful()
    bundle=read(ROOT/'reports/evaluation_v2_work/inputs.json');case=Case(ReplayCatalog(bundle['templates']),'input_stop',0,48500042,history_lookup(ROOT/'data/evaluation'));case.baseline(20)
    native=pd.DataFrame(case.export()['windows.csv']);native=native.loc[native.scope.eq('TARGET')].reset_index(drop=True);rows=rich_rows(case,case.normal)
    handoff=OUT/'handoff';handoff.mkdir(exist_ok=True);records=[]
    import builtins
    from unittest.mock import patch
    real_open=builtins.open
    def guard(file,*args,**kwargs):
        name=str(file).replace('\\','/')
        if any(v in name for v in ('data/','reports/','training_inputs.joblib','development.joblib')):raise AssertionError('Data read at runtime: '+name)
        return real_open(file,*args,**kwargs)
    for a,m in pkg['models'].items():
        assert m.record()['estimator_count']==1 and m.estimator is m.base.estimator
        assert not set(m.columns)&{'label','window_is_anomaly','event_ids','scenario_bits','case_id','processId','adapterType','generation_mode'}
        monitor=Monitor(m,pkg['adapter_reference'],pkg['feature_context']);path=handoff/(a+'_shared.joblib');joblib.dump(monitor,path,compress=3);loaded=joblib.load(path)
        with patch('builtins.open',guard),patch('io.open',guard):
            whole=loaded.predict(native,rows);prefix=loaded.predict(native.iloc[:10].copy(),rows)
            un=native.copy();un['adapterType']='UNKNOWN_V4';ur=rows.copy();ur['adapterType']='UNKNOWN_V4';unknown=loaded.predict(un,ur)
        np.testing.assert_allclose(whole.score.iloc[:10],prefix.score,rtol=1e-10,atol=1e-10)
        np.testing.assert_array_equal(whole.score,monitor.predict(native,rows).score);assert np.isfinite(unknown.score).all()
        records.append({'algorithm':a,'file':path.name,'bytes':path.stat().st_size,'sha256':sha256(path),**m.record(),
            'actual_estimator_parameters':m.estimator.get_params(),'raw_Process_predict':True,'prefix_causal':True,
            'save_load_identical':True,'unknown_adapter_finite':True,'no_runtime_data_reads':True})
    write(handoff/'manifest.json',{'models':records,'requires_code_tree':True,'production_deployed':False,
        'input':'chronological contiguous minute grid + completed Process rows + retained past prefix',
        'worker_must_supply_empty_minutes_and_state':True,'ADBench_specific_models_are_separate':True,
        'versions':{n:importlib.metadata.version(n)for n in ('numpy','pandas','scipy','scikit-learn','joblib')},
        'source_sha256':{str(p.relative_to(ROOT)).replace('\\','/'):sha256(p)for p in (ROOT/'code/shared_model_v4').glob('*.py')}})
    goals={r['method']:r['F1_target_met']and r['FP_cap_met']for r in summary['pooled']}
    write(OUT/'audit.json',{'checks_passed':True,'goals_met':goals,'model_sha256':selection['model_sha256'],'summary_sha256':sha256(OUT/'summary.json'),
        'unchanged_fixed_benchmarks':True,'metric_recomputation_and_truth_alignment':True,'unit_tests_run':result.testsRun,'unit_tests_passed':result.wasSuccessful(),
        'previous_models_and_metrics_preserved':True,
        'raw_serving_checks':records,'external_blind_evaluation':False,'ADBench_specific_training_is_distinct_protocol':True})
    print('AUDIT',goals,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'reports/shared_model_v4_final');run(p.parse_args().output)
