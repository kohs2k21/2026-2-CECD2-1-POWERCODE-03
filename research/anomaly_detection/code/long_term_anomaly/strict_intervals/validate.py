"""Independent metric and goal audit; a failed goal is NOT a failed integrity check."""
from unittest.mock import patch
import argparse
import numpy as np
import pandas as pd
from ..common import ROOT,REPORT,DATA,read_json,write_json,sha256
from ..benchmark import load_split
from ..experiment import metadata_for_split
from ..validate import count_metrics
from .model import load_bank,score_frame,HORIZONS
from .run import OUT,code_hash,frame_for


def validate():
    checks={}
    for name,expected in read_json(OUT/'preservation.json')['files'].items():
        assert sha256(ROOT/name)==expected,name
    checks['all_original_models_notebooks_reports_and_benchmark_preserved']=True
    for item in read_json(REPORT/'source_audit.json')['sources']:
        assert sha256(ROOT/item['source'])==item['sha256']
    checks['all_three_raw_periods_unchanged']=True
    c,r,seal=(read_json(OUT/name) for name in ('calibration.json','results.json','prediction_seal.json'))
    assert code_hash()==c['code_sha256']==r['code_sha256']==seal['code_sha256']
    assert sha256(OUT/'models.joblib')==c['model_sha256']==r['model_sha256']==seal['model_sha256']
    assert sha256(OUT/'development_selection.json')==c['selection_sha256']
    assert sha256(OUT/'calibration.json')==r['calibration_sha256']
    checks['model_code_selection_and_predictions_hashes_match']=True
    assert c['test_used_for_selection'] is False and r['independent_new_blind_evaluation'] is False
    checks['no_independent_blind_test_claim']=True
    d=load_split('test',True);meta=metadata_for_split('test',d['case_id'])
    bank=load_bank(OUT/'models.joblib');counts=set();goals={};replays=[]
    case_indices=[int(np.flatnonzero(meta.scenario.eq(task)&meta.injected)[0]) for task in sorted(meta.scenario.unique())]
    for h in HORIZONS:
        p=OUT/f'scores_{h}h.npz';assert sha256(p)==seal['scores_sha256'][str(h)]
        with np.load(p) as f:s,threshold=f['score'],float(f['threshold'])
        assert threshold==bank.settings[h]['threshold']
        assert np.array_equal(np.isfinite(s),d['collection_observed'][:,672:])
        result=r['results'][str(h)]
        groups={'pooled':(np.ones(len(meta),bool),result['pooled'])}
        groups.update({task:(meta.scenario.eq(task).to_numpy(),v) for task,v in result['scenarios'].items()})
        for task,(keep,value) in groups.items():
            independent=count_metrics(d['y'][keep].ravel(),s[keep].ravel(),threshold)
            for key,number in independent.items():assert np.isclose(number,value[key],rtol=0,atol=1e-12),(h,task,key)
        med={k:float(np.median([v[k] for v in result['scenarios'].values()])) for k in ('accuracy','precision','recall','f1')}
        for k,v in med.items():assert np.isclose(v,result['median'][k],rtol=0,atol=1e-12)
        achieved=min(med.values())>=.65 and result['maximum_scenario_fpr']<=.06 and result['pooled']['false_positive_rate']<=.06
        assert achieved==result['target_met'];goals[str(h)]=achieved
        counts.add(result['pooled']['assessed_hours'])
        for i in case_indices:
            frame=frame_for(d['X'][i],d['collection_observed'][i],d['latency_available'][i],d['start_ns'][i],d['case_id'][i])
            with patch('builtins.open',side_effect=AssertionError('Inference used file I/O')):
                out=score_frame(bank,frame,h,True)
            np.testing.assert_allclose(out.score,s[i],rtol=0,atol=1e-10,equal_nan=True)
            replays.append(f'{h}h:{d["case_id"][i]}')
    assert len(counts)==1
    checks['same_all_original_observed_hours_for_every_length']=True
    checks['all_30_scenarios_and_five_pooled_metrics_recomputed_independently']=True
    checks['all_medians_and_65_percent_goal_flags_independently_verified']=True
    checks['thirty_full_dataframe_API_replays_match_frozen_array_predictions']=True
    assert all(not bank.record()[k] for k in ('monitoring_state_carried','outside_batch_monitoring_used','normal_reference_auto_updated'))
    checks['no_monitoring_state_in_model_contract']=True
    assert all(goals.values())==r['goal_met_all_lengths']
    write_json(OUT/'validation.json',{'integrity_passed':all(checks.values()),'checks':checks,
        'goal_met_all_lengths':all(goals.values()),'goal_per_length':goals,'api_replays':replays,
        'assessed_hours_per_length':next(iter(counts)),'model_bytes':(OUT/'models.joblib').stat().st_size})
    print('Integrity checks',checks,flush=True);print('Goal audit, NOT automatically a success',goals,flush=True)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    validate()
