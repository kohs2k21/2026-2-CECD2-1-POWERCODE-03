"""v11: representative known-normal calibration; frozen v10 trees/prototypes.

Normal controls from earlier synthetic development experiments become TRAINING
calibration data. They are NOT an independent confirmation set anymore.
Tree/center training remains source-template disjoint from reserved templates,
but threshold calibration shares source templates with the original benchmark.
Fresh validation CASES are independent; SOURCE templates/generator are shared.
All such overlap is explicit, not a blind/external/production performance claim.
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
from model_research_v5.run_matched import frozen as frozen_v7
from model_research_v8.features import add_duration_features
from model_research_v8.models import Suite
from model_research_v8.data import load as load_v8,fingerprints
from model_research_v8.refine import load as load_v9
from model_research_v8.bridge import frozen as frozen_v10,cache,summarize,OUTPUT as V10
from model_research_v8.compat import legacy_classes

OUTPUT=ROOT/'reports/model_research_v11';SEED=14_170_042


def prepare():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/'preparation.json').exists():return load()
    _,bundle=verify_benchmark(BENCHMARK,SOURCE_CACHE);v8,v9=load_v8(),load_v9()
    _,_,state,_=selected_references(SEARCH,BENCHMARK,SOURCE_CACHE)
    native=validation_inputs(bundle,state,count=360,seed=SEED);history=PriorHistory(BENCHMARK/'reference_history_windows.csv')
    bank,legacy={},{}
    forbidden=set().union(*(set(d['observed'].case_id) for p in (v8,v9) for d in p['bank'].values()))
    for name,d in native.items():
        bank[name],legacy[name]={},{}
        for cohort in ('observed','normal'):
            f=context_features(with_legacy(build_features(d[cohort],d[cohort+'_rows'],history,v8['baseline']),v8['legacy_baselines'],history))
            if set(f.case_id)&forbidden:raise ValueError('v11 calibration/validation case overlap')
            legacy[name][cohort]=f;bank[name][cohort]=add_duration_features(f,d[cohort+'_rows'],v8['reference'])
    calibration=pd.concat([d['normal'] for d in v8['bank'].values()],ignore_index=True)
    threshold=pd.concat([d['normal'] for d in v9['bank'].values()],ignore_index=True)
    assert not set(calibration.case_id)&set(threshold.case_id)
    payload={'bank':bank,'legacy_bank':legacy,'calibration':calibration,'threshold':threshold,
             **{a:v8[a] for a in ('reference','baseline','legacy_baselines')}}
    joblib.dump(payload,OUTPUT/'development_inputs.joblib',compress=3)
    write_json(OUTPUT/'preparation.json',{'inputs':fingerprints(),'code_sha256':sha256(Path(__file__)),'data_sha256':sha256(OUTPUT/'development_inputs.joblib'),
        'fresh_seed':SEED,'search_cases':1080,'confirmation_cases':1080,'normal_calibration_windows':len(calibration),'normal_threshold_windows':len(threshold),
        'normal_calibration_cases':int(calibration.case_id.nunique()),'normal_threshold_cases':int(threshold.case_id.nunique()),
        'known_normal_reference':'Earlier v8/v9 synthetic normal controls, now training calibration inputs, not held-out validation.',
        'tree_center_fit_source_holdout_unchanged':True,'threshold_calibration_source_template_overlap_with_test':True,
        'normal_controls_not_original_final_normal_csv':True,'not_blind_original_test':True})
    return payload


def load():
    r=json.loads((OUTPUT/'preparation.json').read_text(encoding='utf-8'));load_v8();load_v9()
    if r['inputs']!=fingerprints() or r['code_sha256']!=sha256(Path(__file__)):raise ValueError('Changed v11 code/input')
    return trusted_load(OUTPUT/'development_inputs.joblib',r['data_sha256'])


def adapted(family,data):
    family=copy.deepcopy(family)
    family.calibration,family.threshold=data['calibration'],data['threshold']
    family.normal={n:family.components(data[n])[0] for n in ('calibration','threshold')}
    median,p95=np.quantile(family.normal['calibration'][:,:3],[.5,.95],axis=0)
    family.base_spread=np.maximum(p95-median,.1)
    family.set_operating_point(.99,.995,.5,1.,'max')
    return family


def tune():
    data=load()
    if (OUTPUT/'selection.json').exists():raise FileExistsError('v11 frozen')
    with legacy_classes():previous,previous_selection=frozen_v10()
    chosen,results={},[]
    for algorithm,original in previous.families.items():
        family=adapted(original,data);cached=cache(family,data['bank'],'search');best=model=fallback=fallback_model=None
        for ql in (.95,.975,.99):
            for qj in (.975,.99,.995):
                for weight in (.25,.5,.75):
                    for gamma in (.5,1.,2.):
                        for mode in ('mean','max'):
                            family.set_operating_point(ql,qj,weight,gamma,mode)
                            r={'algorithm':algorithm,'latency_features':list(family.columns),'latency_quantile':ql,'joint_quantile':qj,
                               'global_weight':weight,'latency_weight':gamma,'combination':mode,**summarize(family,cached)}
                            r['within_budget']=r['worst_strategy_pooled_fpr']<=.02 and r['worst_scenario_strategy_fpr']<=.05
                            # User now prioritizes pooled Accuracy/Precision/Recall/F1.
                            # Still disclose macro and worst-scenario FPR, not just this objective.
                            r['selection_objective']=float(np.mean([m['f1'] for m in r['pooled']]))
                            results.append(r);key=(r['selection_objective'],r['objective'],-r['worst_strategy_pooled_fpr'])
                            fk=(-r['worst_strategy_pooled_fpr'],-r['worst_scenario_strategy_fpr'],r['selection_objective'])
                            if fallback is None or fk>fallback[0]:fallback,fallback_model=(fk,r),copy.deepcopy(family)
                            if r['within_budget'] and (best is None or key>best[0]):best,model=(key,r),copy.deepcopy(family)
        if best is None:best,model=fallback,fallback_model
        chosen[algorithm]=model;write_json(OUTPUT/f'chosen_{algorithm}.json',best[1])
        print(f"v11 chosen {algorithm}: pooled F1={best[1]['selection_objective']:.2%}, pooled FPR={best[1]['worst_strategy_pooled_fpr']:.2%}, feasible={best[1]['within_budget']}",flush=True)
    suite=Suite(chosen)
    for a in ('reference','baseline','legacy_baselines'):setattr(suite,a,data[a])
    v4,_=frozen_v4();v7,_=frozen_v7()
    validation={name:{split:six_metrics(m,bank,split) for split in ('search','confirmation')}
                for name,m,bank in (('baseline_v4',v4,data['legacy_bank']),('baseline_v7',v7,data['legacy_bank']),
                                    ('baseline_v10',previous,data['bank']),('recalibrated_v11',suite,data['bank']))}
    joblib.dump(suite,OUTPUT/'models_v11.joblib',compress=3);write_json(OUTPUT/'search_results.json',results);write_json(OUTPUT/'validation_results.json',validation)
    write_json(OUTPUT/'selection.json',{'inputs':fingerprints(),'code_sha256':sha256(Path(__file__)),'model_sha256':sha256(OUTPUT/'models_v11.joblib'),
        'previous_model_sha256':previous_selection['model_sha256'],'families':{a:json.loads((OUTPUT/f'chosen_{a}.json').read_text(encoding='utf-8')) for a in chosen},
        'tree_center_not_refit':True,'normal_only_calibration':True,'labelled_selection':True,'confirmation_used_for_selection':False,
        'original_final_used_for_selection':False,'hard_used_for_selection':False,'threshold_calibration_source_overlap_with_original_test':True,
        'objective':'Pooled F1 averaged across three routes, pooled normal FPR<=2%, every scenario/route<=5%.',
        'not_blind_external_or_production_evaluation':True,'completed_utc':datetime.now(timezone.utc).isoformat()})


def frozen():
    r=json.loads((OUTPUT/'selection.json').read_text(encoding='utf-8'))
    with legacy_classes():frozen_v10()
    if r['inputs']!=fingerprints() or r['code_sha256']!=sha256(Path(__file__)):raise ValueError('Changed frozen v11 code/input')
    return trusted_load(OUTPUT/'models_v11.joblib',r['model_sha256']),r


def evaluate():
    # Same raw-feature-only evaluation as v10, with output/model loader supplied
    # explicitly. The frozen v10 module source is NOT edited or overwritten.
    import model_research_v8.bridge as evaluator
    suite,selection=frozen()
    original_output,original_frozen=evaluator.OUTPUT,evaluator.frozen
    try:
        evaluator.OUTPUT,evaluator.frozen=OUTPUT,lambda:(suite,selection)
        evaluator.evaluate()
    finally:evaluator.OUTPUT,evaluator.frozen=original_output,original_frozen
    summary=json.loads((OUTPUT/'summary.json').read_text(encoding='utf-8'))
    summary['protocol'].update({'threshold_calibration_source_template_overlap_with_test':True,
                               'normal_controls_used_as_training_calibration':True,
                               'tree_center_not_refit':True,'fresh_validation_seed':SEED})
    write_json(OUTPUT/'summary.json',summary)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',choices=('prepare','tune','evaluate','all'),default='all');args=p.parse_args()
    if args.stage in ('prepare','all'):prepare()
    if args.stage in ('tune','all'):tune()
    if args.stage in ('evaluate','all'):evaluate()
