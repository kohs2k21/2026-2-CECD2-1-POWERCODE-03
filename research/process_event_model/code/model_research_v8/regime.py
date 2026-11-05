"""v12: causal regime routing, preserving frozen source-disjoint branches.

Sparse streams (<3 PRIOR nonempty arrival minutes): frozen v8 conditional model.
Established streams: frozen v10 IF / frozen v4 KMeans. No label/scenario router,
no new threshold fitting, no prior final prediction cache used as model input.
"""
import argparse,copy,json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
import pandas as pd
from model_evaluation.data import sha256,verify_benchmark
from model_evaluation.windowing import PriorHistory
from model_evaluation.tune_windows import write_json,selected_references
from model_improvement.data import BENCHMARK,SOURCE_CACHE,SEARCH,validation_inputs
from model_improvement.features import build_features
from model_improvement.refine import with_legacy,frozen as frozen_v4
from model_improvement.run import trusted_load
from model_research_v5.features import context_features
from model_research_v5.run import six_metrics
from model_research_v8.features import add_duration_features
from model_research_v8.models import Suite
from model_research_v8.data import load as load_v8,fingerprints
from model_research_v8.run import frozen as frozen_v8
from model_research_v8.bridge import frozen as frozen_v10
from model_research_v8.compat import legacy_classes

OUTPUT=ROOT/'reports/model_research_v12';SEED=15_170_042


class RegimeFamily:
    def __init__(self,sparse,established,algorithm):
        self.sparse,self.established,self.algorithm=sparse,established,algorithm
        assert set(sparse.experts)==set(established.experts)
        self.experts=established.experts;self.support=established.support

    def score(self,frame):
        margin,pred,covered=self.established.score(frame)
        mask=~frame.traffic_assessed.to_numpy(bool)
        if mask.any():
            sm,sp,sc=self.sparse.score(frame.loc[mask])
            np.testing.assert_array_equal(sc,covered[mask])
            margin[mask],pred[mask]=sm,sp
        np.testing.assert_array_equal(pred,margin>0)
        return margin,pred,covered

    def records(self):
        return [{**r,'regime':'sparse <3 prior nonempty minutes','algorithm':self.algorithm} for r in self.sparse.records()]+[
                {**r,'regime':'established >=3 prior nonempty minutes','algorithm':self.algorithm} for r in self.established.records()]


def freeze():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/'selection.json').exists():return frozen()
    v8,s8=frozen_v8()
    with legacy_classes():v10,s10=frozen_v10()
    v4,s4=frozen_v4()
    suite=Suite({'if':RegimeFamily(copy.deepcopy(v8.families['if']),copy.deepcopy(v10.families['if']),'if'),
                 'kmeans':RegimeFamily(copy.deepcopy(v8.families['kmeans']),copy.deepcopy(v4.families['kmeans']),'kmeans')})
    for a in ('reference','baseline','legacy_baselines'):setattr(suite,a,getattr(v8,a))
    joblib.dump(suite,OUTPUT/'models_v12.joblib',compress=3)
    write_json(OUTPUT/'selection.json',{'inputs':fingerprints(),'code_sha256':sha256(Path(__file__)),'model_sha256':sha256(OUTPUT/'models_v12.joblib'),
        'branch_model_sha256':{'v8':s8['model_sha256'],'v10':s10['model_sha256'],'v4':s4['model_sha256']},
        'routing':'3 prior nonempty arrival minutes; uses observed past, not scenario/severity/episode length.',
        'new_hyperparameter_search':False,'confirmation_used_for_selection':False,'final_used_for_selection':False,
        'frozen_before_new_validation':True,'normal_source_reference_template_holdout_preserved':True,
        'not_blind_original_benchmark':True,'fresh_confirmation_seed':SEED,'completed_utc':datetime.now(timezone.utc).isoformat()})
    return suite


def frozen():
    r=json.loads((OUTPUT/'selection.json').read_text(encoding='utf-8'))
    if r['inputs']!=fingerprints() or r['code_sha256']!=sha256(Path(__file__)):raise ValueError('Changed v12 code/input')
    return trusted_load(OUTPUT/'models_v12.joblib',r['model_sha256']),r


def confirm():
    suite,_=frozen();verify_benchmark(BENCHMARK,SOURCE_CACHE);_,bundle=verify_benchmark(BENCHMARK,SOURCE_CACHE)
    prior=load_v8();_,_,state,_=selected_references(SEARCH,BENCHMARK,SOURCE_CACHE)
    native=validation_inputs(bundle,state,count=240,seed=SEED);history=PriorHistory(BENCHMARK/'reference_history_windows.csv')
    bank,legacy={},{}
    for name,d in native.items():
        bank[name],legacy[name]={},{}
        for cohort in ('observed','normal'):
            f=context_features(with_legacy(build_features(d[cohort],d[cohort+'_rows'],history,prior['baseline']),prior['legacy_baselines'],history))
            # This entire bank is confirmation-only, not an opportunity to tune.
            f['validation_split']='confirmation'
            legacy[name][cohort]=f;bank[name][cohort]=add_duration_features(f,d[cohort+'_rows'],prior['reference'])
    v4,_=frozen_v4()
    from model_research_v5.run_matched import frozen as frozen_v7
    v7,_=frozen_v7()
    validation={name:{'confirmation':six_metrics(m,b,'confirmation')}
                for name,m,b in (('baseline_v4',v4,legacy),('baseline_v7',v7,legacy),('regime_v12',suite,bank))}
    write_json(OUTPUT/'validation_results.json',validation)
    print([(r['method'],round(r['f1']*100,2),round(r['normal_false_positive_rate']*100,2)) for r in validation['regime_v12']['confirmation']['pooled']],flush=True)


def evaluate():
    suite,selection=frozen()
    import model_research_v8.bridge as evaluator
    previous_output,previous_frozen=evaluator.OUTPUT,evaluator.frozen
    try:evaluator.OUTPUT,evaluator.frozen=OUTPUT,lambda:(suite,selection);evaluator.evaluate()
    finally:evaluator.OUTPUT,evaluator.frozen=previous_output,previous_frozen
    summary=json.loads((OUTPUT/'summary.json').read_text(encoding='utf-8'))
    summary['protocol'].update({'normal_source_reference_template_holdout_preserved':True,'no_new_tree_or_threshold_fit':True,
                               'fixed_before_fresh_confirmation':True,'routing_uses_observed_past_only':True})
    write_json(OUTPUT/'summary.json',summary)


if __name__=='__main__':
    # Export canonical module classes, never __main__ pickle classes.
    from model_research_v8.regime import freeze,confirm,evaluate
    p=argparse.ArgumentParser();p.add_argument('--stage',choices=('freeze','confirm','evaluate','all'),default='all');a=p.parse_args()
    if a.stage in ('freeze','all'):freeze()
    if a.stage in ('confirm','all'):confirm()
    if a.stage in ('evaluate','all'):evaluate()
