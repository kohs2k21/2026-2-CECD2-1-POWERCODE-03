"""Second row-preserving experiment, old attempts never overwritten."""
import argparse
import copy
import json
import time

import joblib
import numpy as np
import pandas as pd

from .data import ROOT, TASKS, original, sample_ids, write_json, benchmark, digest
from .row_context import ExactReference, RowContext, ROW_FEATURES
from .models import Detector, Settings, metrics

OUT=ROOT/"reports/process_event_model/row_context_v2"


def actual_session_days(raw):
    dates=raw.availableTime.dt.tz_convert('Asia/Seoul').dt.normalize()
    chosen=set()
    for split in ('train','calibration','threshold','test'):
        part=raw.loc[raw.normal_fit_eligible & raw.split.eq(split)]
        days=sorted(dates.loc[part.index].unique())
        if not days:raise ValueError(f'No actual normal source dates for {split}')
        chosen.update(days[::max(1,len(days)//6)])
        for _,group in part.groupby('adapterType',observed=True):
            chosen.add(dates.loc[group.index[0]])
    return {str(d) for d in chosen}


def prepare():
    OUT.mkdir(exist_ok=True,parents=True)
    path=OUT/"original_features.joblib"
    if path.exists():return joblib.load(path)
    raw=original(OUT.parent)
    ref_path=OUT/"exact_reference.joblib"
    if ref_path.exists():ref=joblib.load(ref_path)
    else:
        eligible=raw.normal_fit_eligible & raw.split.eq("train")
        print("Exact reference: fitting on",int(eligible.sum()),"actual original normal rows",flush=True)
        ref=ExactReference.fit(raw.loc[eligible]);joblib.dump(ref,ref_path,compress=3)
    # Directly selected from actual observed dates; no earlier experiment or
    # synthetic normal-window cache is required for this preparation.
    days=actual_session_days(raw)
    dates=raw.availableTime.dt.tz_convert("Asia/Seoul").dt.normalize().astype(str)
    context=raw.loc[dates.isin(days)].copy()
    context["case_id"]=context.source_period.astype(str)+":"+dates.loc[context.index]
    print("Attaching exact/context features to",len(context),"actual rows",flush=True)
    features=RowContext(ref).transform(context)
    assert len(features)==len(context)
    parts={}
    for name,cap in (("train",60000),("calibration",15000),("threshold",15000),("test",15000)):
        candidates=features.loc[features.normal_fit_eligible & features.split.eq(name)]
        parts[name]=candidates.loc[sample_ids(candidates,cap)].copy()
        assert not parts[name].benchmark_source.any()
    package=dict(reference=ref,partitions=parts)
    joblib.dump(package,path,compress=3)
    write_json(OUT/"provenance.json",dict(original_only_fit=True,synthetic_fit_rows=0,
        source_context_rows=len(context),rows_preserved=True,reference_actual_rows=int(eligible.sum()) if 'eligible' in locals() else None,
        original_times_preserved=True,no_empty_rows_generated=True,
        partitions={n:len(f) for n,f in parts.items()},features=list(ROW_FEATURES),
        feature_code_sha256=digest(ROOT/"code/process_event_model/row_context.py")))
    return package


def development(ref,limit):
    path=OUT/f"development_{limit}.joblib"
    if path.exists():return joblib.load(path)
    bank={}
    for task in TASKS:
        if task=="input_stop":continue
        pair,cases=benchmark(task,limit)
        targets=cases.set_index("case_id")
        frames=[]
        started=time.perf_counter()
        for cohort,raw in pair.items():
            y=raw.label.to_numpy(bool)
            f=RowContext(ref).transform(raw.drop(columns=["label","row_is_anomaly","collective_context_is_anomaly"]))
            f["label"]=y
            f["target"]=f.adapterType.eq(f.case_id.map(targets.target_adapter)) & f.processId.eq(f.case_id.map(targets.target_process_id))
            f["cohort"]=cohort
            frames.append(f)
        bank[task]=pd.concat(frames,ignore_index=True)
        print("V2 development",task,"rows",len(bank[task]),"seconds",round(time.perf_counter()-started,1),flush=True)
        joblib.dump(bank[task],OUT/f"{task}_development.joblib",compress=3)
    joblib.dump(bank,path,compress=3)
    return bank


def run(limit=400):
    p=prepare();normal=p["partitions"]["train"];cal=p["partitions"]["calibration"]
    bank=development(p["reference"],limit)
    # Primary benchmark matches LEGACY TARGET scope, retaining original rows.
    # Whole-workflow traffic changes are not labelled on non-target roles.
    frames={t:f.loc[f.target].copy() for t,f in bank.items()}
    selected={};history=[]
    for algorithm in ("if","kmeans","lof"):
        best=None
        sizes=(64,256,1024) if algorithm=="if" else (8,24,64) if algorithm=="kmeans" else (20,60,150)
        for view in ("row_compact","row_point","row_context"):
            for size in sizes:
                kwargs=dict(samples=size) if algorithm=="if" else dict(clusters=size) if algorithm=="kmeans" else dict(neighbors=size)
                setting=Settings(algorithm,view,**kwargs,whiten=algorithm=="kmeans")
                model=Detector(setting).fit(normal)
                score={t:model.raw_score(f) for t,f in frames.items()}
                cal_score=model.raw_score(cal)
                clean=np.concatenate([s[~frames[t].label.to_numpy(bool)] for t,s in score.items()])
                for q in (.94,.95,.96,.97,.98,.99,.995):
                    threshold=float(max(np.quantile(clean,q),np.quantile(cal_score,.94)))
                    rows={t:metrics(frames[t].label,s>threshold) for t,s in score.items()}
                    macro=float(np.mean([m["f1"] for m in rows.values()]))
                    maximum=max(m["false_positive_rate"] for m in rows.values())
                    value=macro if maximum<=.06 else -maximum
                    row=dict(settings=vars(setting),threshold=threshold,quantile=q,macro_f1=macro,maximum_fpr=maximum,
                             tasks=rows,original_normal_calibration_alert_rate=float(np.mean(cal_score>threshold)))
                    history.append(row)
                    if best is None or value>best[0]:
                        model.threshold=threshold
                        best=(value,copy.deepcopy(model),row)
                print("V2",algorithm,view,size,"best",round(best[2]["macro_f1"],4),flush=True)
        selected[algorithm]=best[1]
        joblib.dump(dict(reference=p["reference"],model=best[1]),OUT/f"{algorithm}_candidate.joblib",compress=3)
        write_json(OUT/f"{algorithm}_development.json",best[2])
    joblib.dump(dict(reference=p["reference"],models=selected,processor="row_context_v2"),OUT/"event_models.joblib",compress=3)
    write_json(OUT/"development_search.json",dict(candidates=history,development_cases_per_task=limit,
        primary_scope="same target Adapter+Process as legacy, original execution rows",
        no_benchmark_changes=True,normal_original_only_estimator_fit=True,external_blind_test=False))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--cases",type=int,default=400)
    parser.add_argument("--prepare-only",action="store_true")
    args=parser.parse_args()
    if args.prepare_only:prepare()
    else:run(args.cases)
