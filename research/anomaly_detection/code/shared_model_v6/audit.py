"""Independent full metrics + old smoke tests + new causal/feature tests."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import unittest,io
import numpy as np
from sklearn.metrics import accuracy_score,precision_score,recall_score,f1_score,confusion_matrix
from model_research_v13.data import read,write
from model_evaluation.data import sha256,verify_benchmark
from shared_model_v6.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze
from shared_model_v5.audit import run as serving_audit

def run():
    protocol=freeze();dest=OUT/'release';summary=read(dest/'summary.json');selection=read(dest/'selection.json')
    assert sha256(dest/'models.joblib')==selection['model_sha256']
    assert sha256(ROOT/'data/evaluation/manifest.json')==protocol['benchmark_manifest_sha256']
    assert sha256(BASE/'models.joblib')==protocol['baseline_models_sha256']
    assert sha256(BASE/'summary.json')==protocol['baseline_summary_sha256']
    assert sha256(ROOT/'data/adbench_1000/dataset.npz')==protocol['ADBench_npz_sha256']
    for t in TASKS:assert read(ROOT/'data/evaluation'/t/'manifest.json')==protocol['unchanged_task_file_manifests'][t]
    verify_benchmark(ROOT/'data/evaluation',ROOT/'reports/evaluation_v2_work/inputs.json')
    import joblib
    pkg=joblib.load(dest/'models.joblib');ys=[];predictions=[]
    for i,t in enumerate(TASKS):
        z=np.load(dest/(t+'_scores.npz'));old=np.load(BASE/(t+'_scores.npz'))
        for key in ('y','case_index','adapter'):np.testing.assert_array_equal(z[key],old[key])
        pred=np.column_stack([z['scores'][:,j]>pkg['models'][a].threshold for j,a in enumerate(ALGORITHMS)])
        ys.append(z['y']);predictions.append(pred)
        for j,a in enumerate(ALGORITHMS):
            tn,fp,fn,tp=confusion_matrix(z['y'],pred[:,j],labels=[False,True]).ravel()
            assert fp/(tn+fp)<=.05;assert tp>0
            r=summary['datasets'][i]['metrics'][j]
            assert np.isclose(r['f1'],f1_score(z['y'],pred[:,j],zero_division=0))
    y=np.concatenate(ys);p=np.concatenate(predictions);old=read(BASE/'summary.json')
    for j,a in enumerate(ALGORITHMS):
        r=summary['pooled'][j]
        for key,fn in [('accuracy',accuracy_score),('precision',precision_score),('recall',recall_score),('f1',f1_score)]:
            assert np.isclose(fn(y,p[:,j]),r[key]),(a,key)
        assert r['recall']>=.6
        for key in ('accuracy','precision','f1'):assert r[key]>=old['pooled'][j][key]-1e-12,(a,key)
    assert summary['all_targets_met']
    serving_audit(dest)
    stream=io.StringIO();tests=unittest.defaultTestLoader.loadTestsFromName('shared_model_v6.test_v6');result=unittest.TextTestRunner(stream=stream).run(tests)
    print(stream.getvalue(),flush=True);assert result.wasSuccessful()
    audit=read(dest/'audit.json');audit.update({'unit_tests_run':audit['unit_tests_run']+result.testsRun,
        'Recall_60pct_scope':'each model pooled, NOT every task','pooled_accuracy_precision_F1_nonregression_verified':True,
        'all_task_FPR_at_most_5pct_verified':True,'all_task_F1_nonregression':{a:summary['acceptance'][a]['all_task_F1_nonregression']for a in ALGORITHMS},
        'independent_sklearn_metrics_recomputation':True,'normal_geometry_KDTree_query_counters_not_learned_parameters':True,
        'unchanged_active_benchmark_and_ADBench':True,'no_data_label_severity_change':True,'external_blind_test':False})
    write(dest/'audit.json',audit)
    write(OUT/'latest.json',{'current_release':'reports/shared_model_v6/release','notebook':'code/shared_model_v6/result.ipynb',
        'all_pooled_targets_met':True,'all_task_recall_60pct_claimed':False,'ADBench_unchanged':True})
    print('V6 AUDIT PASS',audit['unit_tests_run'],'tests; independent metrics and all pooled targets',flush=True)

if __name__=='__main__':run()
