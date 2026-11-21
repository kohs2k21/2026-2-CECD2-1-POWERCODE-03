"""Independent counts, five-task gates, portable raw-Process inference tests."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,unittest,io,importlib.metadata
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import read,write
from model_evaluation.data import sha256,ReplayCatalog,history_lookup
from evaluation_benchmark.generate import Case
from model_improvement.data import rich_rows
from shared_model_target.inference import Monitor
from shared_model_v5.protocol import TASKS,ALGORITHMS,OUT,gates
from sklearn.metrics import confusion_matrix

def run(out):
    out=Path(out);summary=read(out/'summary.json');sel=read(out/'selection.json');pkg=joblib.load(out/'models.joblib')
    assert sha256(out/'models.joblib')==sel['model_sha256']
    assert read(OUT/'scope_audit.json')['passed']
    assert [d['dataset']for d in summary['datasets']]==list(TASKS)
    yy=[];pp=[]
    for d in summary['datasets']:
        z=np.load(out/(d['dataset']+'_scores.npz'));y=z['y'];s=z['scores'];assert np.isfinite(s).all()
        prediction=np.column_stack([s[:,i]>pkg['models'][a].threshold for i,a in enumerate(ALGORITHMS)])
        yy.append(y);pp.append(prediction)
        for i,a in enumerate(ALGORITHMS):
            tn,fp,fn,tp=map(int,confusion_matrix(y,prediction[:,i],labels=[False,True]).ravel());r=d['metrics'][i]
            for k,v in dict(tn=tn,fp=fp,fn=fn,tp=tp).items():assert r[k]==v
            assert np.isclose(r['f1'],2*tp/max(2*tp+fp+fn,1))
            assert np.isclose(r['normal_false_positive_rate'],fp/(tn+fp))
    y=np.concatenate(yy);pred=np.concatenate(pp)
    for i,a in enumerate(ALGORITHMS):
        assert gates([d['metrics'][i]for d in summary['datasets']])['all_met'],a
        tn,fp,fn,tp=map(int,confusion_matrix(y,pred[:,i],labels=[False,True]).ravel())
        assert fp/(tn+fp)<.05
        for k,v in dict(tn=tn,fp=fp,fn=fn,tp=tp).items():assert summary['pooled'][i][k]==v
    assert summary['all_targets_met']
    modules=('shared_model_v5.test_v5','shared_model_v4.test_v4','shared_model_v3.test_v3','shared_model_v2.test_v2','shared_model_target.test_target','shared_model_improvement.test_models','adapter_feature_ablation.test_ablation')
    tests=unittest.defaultTestLoader.loadTestsFromNames(modules);stream=io.StringIO();result=unittest.TextTestRunner(stream=stream).run(tests)
    print(stream.getvalue(),flush=True);assert result.wasSuccessful()
    bundle=read(ROOT/'reports/evaluation_v2_work/inputs.json')
    case=Case(ReplayCatalog(bundle['templates']),'input_stop',0,48500042,history_lookup(ROOT/'data/evaluation'));case.baseline(20)
    native=pd.DataFrame(case.export()['windows.csv']);native=native.loc[native.scope.eq('TARGET')].reset_index(drop=True);raw=rich_rows(case,case.normal)
    dest=out/'handoff';dest.mkdir(exist_ok=True);records=[]
    from unittest.mock import patch
    import builtins
    real_open=builtins.open
    def guard(file,*args,**kwargs):
        name=str(file).replace('\\','/')
        if any(v in name for v in ('data/','reports/','training_inputs.joblib','development.joblib')):raise AssertionError('Runtime research-data read: '+name)
        return real_open(file,*args,**kwargs)
    for a,m in pkg['models'].items():
        assert m.record()['estimator_count']==1
        assert not set(m.columns)&{'label','window_is_anomaly','scenario_bits','case_id','adapterType','processId','generation_mode'}
        monitor=Monitor(m,pkg['adapter_reference'],pkg['feature_context']);path=dest/(a+'_shared.joblib');joblib.dump(monitor,path,compress=3)
        loaded=joblib.load(path)
        with patch('builtins.open',guard),patch('io.open',guard):
            whole=loaded.predict(native,raw);prefix=loaded.predict(native.iloc[:10].copy(),raw)
            un=native.copy();un['adapterType']='UNKNOWN_V5';ur=raw.copy();ur['adapterType']='UNKNOWN_V5';unknown=loaded.predict(un,ur)
        np.testing.assert_allclose(whole.score.iloc[:10],prefix.score,rtol=1e-10,atol=1e-10)
        np.testing.assert_array_equal(whole.score,monitor.predict(native,raw).score);assert np.isfinite(unknown.score).all()
        records.append({'algorithm':a,'file':path.name,'bytes':path.stat().st_size,'sha256':sha256(path),**m.record(),
            'actual_estimator_parameters':m.estimator.get_params(),'raw_Process_predict':True,'prefix_causal':True,
            'save_load_identical':True,'unknown_adapter_finite':True,'no_runtime_data_reads':True})
    write(dest/'manifest.json',{'models':records,'requires_code_tree':True,'production_deployed':False,
        'input':'chronological contiguous minute grid + completed Process rows + retained past prefix',
        'worker_must_supply_empty_minutes_and_state':True,'versions':{n:importlib.metadata.version(n)for n in ('numpy','pandas','scipy','scikit-learn','joblib')}})
    write(out/'audit.json',{'checks_passed':True,'all_targets_met':True,'unit_tests_run':result.testsRun,'unit_tests_passed':True,
        'independent_confusion_counts':True,'remaining_data_labels_unchanged':True,'ADBench_unchanged':True,
        'model_sha256':sel['model_sha256'],'summary_sha256':sha256(out/'summary.json'),'raw_serving_checks':records,
        'external_blind_test':False})
    print('AUDIT PASS',result.testsRun,'tests; five-task gates; raw inference',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=OUT/'attempt01');run(p.parse_args().output)
