"""Independently recompute full-benchmark metrics and exercise saved monitors."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
import pandas as pd
import nbformat
from PIL import Image
from adapter_feature_ablation.features import ADAPTER
from adapter_feature_ablation.run import counts
from model_evaluation.metrics import metric_record
from model_evaluation.data import sha256,verify_benchmark,ReplayCatalog,history_lookup
from model_research_v13.data import read,write
from model_improvement.data import rich_rows
from evaluation_benchmark.generate import Case
from shared_model_improvement.run import ALGORITHMS
from shared_model_target.run_current import prepare
from shared_model_target.evaluate_refined import assert_same_detector

OUT=ROOT/'reports/shared_model_target_lof'
PARENT=ROOT/'reports/shared_model_target_current'
OLD=ROOT/'reports/shared_model_improvement_refined'


def smoke():
    source=read(ROOT/'reports/evaluation_v2_work/inputs.json')
    case=Case(ReplayCatalog(source['templates']),'input_stop',0,987654322,history_lookup(ROOT/'data/evaluation'))
    case.baseline(12)
    native=pd.DataFrame(case.export()['windows.csv']);native=native.loc[native.scope.eq('TARGET')].reset_index(drop=True)
    rows=rich_rows(case,case.normal);records=[]
    for entry in read(OUT/'handoff/manifest.json')['models']:
        path=OUT/'handoff'/entry['file'];assert sha256(path)==entry['sha256']
        model=joblib.load(path);result=model.predict(native,rows)
        assert len(result)==len(native)and np.isfinite(result.score).all()
        np.testing.assert_array_equal(result.anomaly,result.score>result.threshold)
        # Score prefixes through the raw-input serving path: future logs may not
        # alter predictions that were already made for earlier complete minutes.
        shorter=model.predict(native.iloc[:8].copy(),rows)
        np.testing.assert_allclose(result.score.iloc[:8],shorter.score,rtol=1e-10,atol=1e-10)
        unative=native.copy();unative['adapterType']='UNSEEN_ADAPTER'
        urows=rows.copy();urows['adapterType']='UNSEEN_ADAPTER'
        unknown=model.predict(unative,urows)
        assert np.isfinite(unknown.score).all()and unknown.adapter_reference_level.eq(2).all()
        records.append({'algorithm':entry['algorithm'],'raw_Process_prediction':True,'prefix_causal':True,
            'unknown_adapter_finite_global_reference':True,'saved_model_sha256_verified':True})
    return records


def audit():
    selected=read(OUT/'selection.json');summary=read(OUT/'summary.json')
    assert sha256(OUT/'models.joblib')==selected['model_sha256']
    pkg=joblib.load(OUT/'models.joblib');oldpkg=joblib.load(PARENT/'models.joblib')
    for a in ('if','kmeans'):assert_same_detector(pkg['models'][a],oldpkg['models'][a])
    manifest,bundle=verify_benchmark(ROOT/'data/evaluation',ROOT/'reports/evaluation_v2_work/inputs.json')
    assert sha256(ROOT/'data/evaluation/manifest.json')==selected['benchmark_manifest_sha256']
    oldaudit=read(OLD/'audit.json')
    assert sha256(OLD/'models.joblib')==oldaudit['model_sha256']
    assert sha256(OLD/'summary.json')==oldaudit['summary_sha256']
    totals=np.zeros((3,4),np.int64);decisions=0
    for dataset in summary['datasets']:
        name=dataset['dataset'];p=np.load(OUT/(name+'_scores.npz'));parent=np.load(PARENT/(name+'_scores.npz'));old=np.load(OLD/(name+'_scores.npz'))
        np.testing.assert_array_equal(p['y'],parent['y']);np.testing.assert_array_equal(p['y'],old['y'])
        np.testing.assert_array_equal(p['scores'][:,:2],parent['scores'][:,:2])
        np.testing.assert_array_equal(p['case_index'],parent['case_index']);np.testing.assert_array_equal(p['adapter'],parent['adapter'])
        decisions+=len(p['y']);assert p['scores'].shape==(len(p['y']),3)and np.isfinite(p['scores']).all()
        for i,a in enumerate(ALGORITHMS):
            vector=counts(p['y'],p['scores'][:,i]>pkg['models'][a].threshold);totals[i]+=vector
            for key,value in metric_record(*vector).items():assert np.isclose(value,dataset['metrics'][i][key]),(name,a,key)
    assert decisions==2167766
    development=prepare();sample=development['train'].iloc[:500].copy();uses=[];requirements=[]
    sets=[set(development['train'].case_id),set(development['normal_calibration'].case_id),
          set(development['search']['frame'].case_id),set(development['confirmation']['frame'].case_id)]
    assert all(not sets[i]&sets[j]for i in range(4)for j in range(i))
    targets={'if':.4,'kmeans':.5,'lof':.5}
    for i,a in enumerate(ALGORITHMS):
        model=pkg['models'][a];metrics=metric_record(*totals[i])
        assert metrics['f1']>=targets[a]and metrics['normal_false_positive_rate']<.1
        for key,value in metrics.items():assert np.isclose(value,summary['pooled'][i][key]),(a,key)
        assert len(model.columns)==26 and len(set(model.columns)&set(ADAPTER))==9
        assert not set(model.columns)&{'case_id','processId','adapterType','window_is_anomaly','scenario','scenario_types','event_ids'}
        assert model.record()['estimator_count']==1 and not hasattr(model,'experts')
        assert all(model.base.weights[j]>.0 for j,c in enumerate(model.columns)if c in ADAPTER)
        changed=sample.copy();changed['adapter_count_p95_z']+=20
        diff=float(np.max(np.abs(model.score(changed)-model.score(sample))));assert diff>1e-8
        uses.append({'algorithm':a,'active_adapter_features':9,'score_change_for_changed_adapter_feature':diff})
        requirements.append({'algorithm':a,'f1':metrics['f1'],'f1_target':targets[a],
            'normal_false_positive_rate':metrics['normal_false_positive_rate'],'passed':True})
    fresh=read(OUT/'fresh_confirmation_v2.json');assert fresh['seed']==41200042 and fresh['used_to_reselect']is False
    info=read(OUT/'fresh_confirmation_v2_inputs.json');assert info['truth_aligned_by_case_and_window']
    assert fresh['model_sha256']==selected['model_sha256']
    assert sha256(OUT/'fresh_confirmation_v2_inputs.joblib')==info['sha256']
    nb=nbformat.read(ROOT/'code/shared_model_target/result.ipynb',as_version=4)
    cells=[c for c in nb.cells if c.cell_type=='code']
    assert all(c.execution_count is not None for c in cells)
    assert not [o for c in cells for o in c.outputs if o.output_type=='error']
    plots=[]
    for path in sorted((OUT/'figures').glob('*.png')):
        with Image.open(path)as image:image.verify()
        plots.append({'file':path.name,'sha256':sha256(path)})
    assert len(plots)==7
    serving=smoke()
    result={'passed':True,'requirements':requirements,'all_decisions':decisions,
      'normal':int(totals[0,0]+totals[0,1]),'anomaly':int(totals[0,2]+totals[0,3]),
      'all_original_benchmark_csv_hashes_verified':True,'original_models_and_results_preserved':True,
      'truth_and_case_alignment_equal':True,'metrics_independently_recomputed':True,
      'IF_and_kmeans_unchanged':True,'single_estimator_each':True,'adapter_experts':False,
      'development_case_sets_disjoint':True,'adapter_numeric_sensitivity':uses,
      'fresh_seed':fresh['seed'],'fresh_confirmation_used_for_reselection':False,
      'fresh_truth_key_alignment_verified':True,'notebook_executed_code_cells':len(cells),'notebook_errors':0,
      'figures':plots,'serving':serving,'stateful_worker_implemented':False,
      'model_sha256':selected['model_sha256'],'summary_sha256':sha256(OUT/'summary.json'),
      'source_sha256':{p.name:sha256(p)for p in(ROOT/'code/shared_model_target').glob('*.py')}}
    write(OUT/'audit.json',result);print(result,flush=True)


if __name__=='__main__':audit()
