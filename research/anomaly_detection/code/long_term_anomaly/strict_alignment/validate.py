"""Independent metric, input-preservation and full backend replay audit."""
from unittest.mock import patch
import argparse
import numpy as np
from ..common import ROOT,REPORT,read_json,write_json,sha256
from ..benchmark import load_split
from ..experiment import metadata_for_split
from ..validate import count_metrics
from ..strict_intervals.run import frame_for
from .run import OUT,OLD,initialize,code_hash
from .model import load_bank,score_frame,HORIZONS


def main():
    initialize();checks={'all_previous_source_data_models_and_reports_preserved':True}
    for s in read_json(REPORT/'source_audit.json')['sources']:assert sha256(ROOT/s['source'])==s['sha256']
    checks['all_three_original_period_files_unchanged']=True
    c,r,seals=(read_json(OUT/n) for n in ('calibration.json','results.json','prediction_seal.json'))
    assert c['code_sha256']==r['code_sha256']==seals['code_sha256']==code_hash()
    assert c['model_sha256']==r['model_sha256']==seals['model_sha256']==sha256(OUT/'models.joblib')
    assert c['selection_sha256']==sha256(OLD/'quantile_warp_search.json')
    assert c['protocol_sha256']==sha256(OUT/'protocol.json')
    assert r['calibration_sha256']==sha256(OUT/'calibration.json')
    checks['model_source_selection_protocol_and_prediction_seals_match']=True
    assert not c['test_used_for_current_selection'] and not r['new_independent_blind_evaluation']
    checks['single_development_selected_candidate_and_reused_test_disclosed']=True
    bank=load_bank(OUT/'models.joblib');d=load_split('test',True);meta=metadata_for_split('test',d['case_id'])
    indices=[int(np.flatnonzero(meta.scenario.eq(t)&meta.injected)[0]) for t in sorted(meta.scenario.unique())]
    flags={};counts=set();replays=[]
    for h in HORIZONS:
        p=OUT/f'scores_{h}h.npz';assert sha256(p)==seals['scores_sha256'][str(h)]
        with np.load(p) as z:score,t=z['score'],float(z['threshold'])
        assert t==bank.threshold(h)
        np.testing.assert_array_equal(np.isfinite(score),d['collection_observed'][:,672:])
        summary=r['results'][str(h)]
        groups={'pooled':(np.ones(len(meta),bool),summary['pooled'])}
        groups.update({task:(meta.scenario.eq(task).to_numpy(),v) for task,v in summary['scenarios'].items()})
        for task,(keep,metrics) in groups.items():
            own=count_metrics(d['y'][keep].ravel(),score[keep].ravel(),t)
            for key,value in own.items():assert np.isclose(value,metrics[key],rtol=0,atol=1e-12),(h,task,key)
        med={k:float(np.median([v[k] for v in summary['scenarios'].values()])) for k in ('accuracy','precision','recall','f1')}
        for k,v in med.items():assert np.isclose(v,summary['median'][k],rtol=0,atol=1e-12)
        flags[str(h)]=(min(med.values())>=.65 and summary['maximum_scenario_fpr']<=.06
                       and summary['pooled']['false_positive_rate']<=.06)
        assert flags[str(h)]==summary['target_met'];counts.add(summary['pooled']['assessed_hours'])
        if h!=1:
            with np.load(OLD/f'scores_{h}h.npz') as z:np.testing.assert_array_equal(score,z['score'])
        for i in indices:
            frame=frame_for(d['X'][i],d['collection_observed'][i],d['latency_available'][i],d['start_ns'][i],d['case_id'][i])
            with patch('builtins.open',side_effect=AssertionError('Backend inference file I/O')):
                out=score_frame(bank,frame,h,True)
            np.testing.assert_allclose(out.score,score[i],rtol=0,atol=1e-8,equal_nan=True)
            replays.append(f'{h}h:{d["case_id"][i]}')
    assert len(counts)==1 and all(flags.values())==r['goal_met_all_lengths']
    checks['all_original_observed_hours_and_all_six_scenarios_used']=True
    checks['all_35_confusion_matrices_and_all_metric_medians_recomputed']=True
    checks['other_four_horizon_scores_exactly_unchanged']=True
    checks['thirty_complete_backend_api_replays_without_file_io_match']=True
    assert not any(bank.record()[k] for k in ('monitoring_state_carried','outside_batch_monitoring_used','normal_reference_auto_updated'))
    checks['no_other_monitoring_batch_or_automatic_reference_updates']=True
    write_json(OUT/'validation.json',dict(integrity_passed=all(checks.values()),checks=checks,
        goal_met_all_lengths=all(flags.values()),goal_per_length=flags,api_replays=replays,
        original_observed_hours_per_length=next(iter(counts)),model_bytes=(OUT/'models.joblib').stat().st_size))
    print('Alignment integrity audit',checks,flush=True);print('Full goal flags',flags,flush=True)


if __name__=='__main__':argparse.ArgumentParser(description=__doc__).parse_args();main()
