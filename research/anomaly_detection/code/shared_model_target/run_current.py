"""Current/recovery-aware version; both old development folds used transparently."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/"code"))
import joblib
import numpy as np
import pandas as pd
from dataclasses import asdict
from adapter_feature_ablation.run import load_development
from shared_model_improvement.run import load_models,_stats,ALGORITHMS
from shared_model_target.models import Setting,TargetDetector
from shared_model_target.current_features import CurrentAdapterReference
from shared_model_target.calibration import NormalReference,CalibratedDetector
from model_research_v13.data import read,write
from model_evaluation.data import sha256

OUTPUT=ROOT/"reports/shared_model_target_current"
PARENT=ROOT/"reports/shared_model_improvement_refined"


def prepare():
    OUTPUT.mkdir(exist_ok=True);path=OUTPUT/"development.joblib"
    if path.exists():assert sha256(path)==read(OUTPUT/"preparation.json")["sha256"];return joblib.load(path)
    source=load_development();parent,package=load_models(PARENT)
    ref=CurrentAdapterReference.fit(source["partitions"]["train"])
    data={"train":ref.transform(source["partitions"]["train"]),"normal_calibration":ref.transform(source["normal_reference"]),
          "adapter_reference":ref,"feature_context":package["feature_context"]}
    for split in ("search","confirmation"):
        frames,ys,scenarios=[],[],[]
        for name,cohorts in source[split].items():
            for cohort,frame in cohorts.items():
                frames.append(ref.transform(frame));ys.append(frame.window_is_anomaly.to_numpy(bool) if cohort=="observed" else np.zeros(len(frame),bool))
                scenarios.extend([name]*len(frame))
        data[split]={"frame":pd.concat(frames,ignore_index=True),"y":np.concatenate(ys),"scenario":np.asarray(scenarios)}
    joblib.dump(data,path,compress=3)
    write(OUTPUT/"preparation.json",{"sha256":sha256(path),"parent_model_sha256":parent["model_sha256"],
        "benchmark_manifest_sha256":parent["benchmark_manifest_sha256"],"normal_reference_fit_train_only":True,
        "old_confirmation_reclassified_as_second_development_fold":True})
    return data


def candidates(a):
    for transform in ("quantile","asinh","hybrid"):
        if a=="if":
            for samples,white,rotation in ((128,False,False),(256,False,False),(256,True,False),(256,True,True)):
                yield Setting(a,view="current",transform=transform,samples=samples,whitening=white,rotation=rotation)
        elif a=="kmeans":
            for k,geometry,white in ((8,"radius",False),(16,"radius",False),(8,"diagonal",False),(16,"diagonal",True)):
                yield Setting(a,view="current",transform=transform,clusters=k,geometry=geometry,whitening=white)
        else:
            for k,cap,white in ((35,4096,False),(75,4096,False),(150,4096,False),(75,8192,True)):
                yield Setting(a,view="current",transform=transform,neighbors=k,reference_cap=cap,whitening=white)


def tune():
    if (OUTPUT/"selection.json").exists():raise FileExistsError("Frozen")
    data=prepare();normal=data["normal_calibration"]
    models,chosen,records={},{},[]
    for a in ALGORITHMS:
        best,key=None,None
        for number,setting in enumerate(candidates(a)):
            base=TargetDetector(setting).fit(data["train"])
            ns=base.score(normal);raw={n:base.score(data[n]["frame"])for n in ("search","confirmation")}
            for mode in ("global","adapter","availability","adapter_availability"):
                for kind in ("cdf","robust"):
                    ref=NormalReference.fit(normal,ns,mode,.5 if mode!="global" else 1.,kind)
                    cal=ref.apply(normal,ns);scores={n:ref.apply(data[n]["frame"],raw[n])for n in raw}
                    for q in (.90,.925,.95,.96,.97,.98,.99,.995):
                        threshold=float(np.quantile(cal,q,method="higher"))
                        folds={n:_stats(data[n]["y"],scores[n],threshold,data[n]["scenario"])for n in raw}
                        eligible=max(r["normal_false_positive_rate"]for r in folds.values())<.08
                        all_y=np.concatenate([data[n]["y"]for n in raw]);all_scores=np.concatenate(list(scores.values()))
                        pooled=_stats(all_y,all_scores,threshold)
                        row={"algorithm":a,"candidate":number,"settings":asdict(setting),"mode":mode,"kind":kind,"q":q,"threshold":threshold,
                             "eligible":eligible,"folds":folds,**pooled}
                        records.append(row)
                        rank=(pooled["f1"],min(r["f1"]for r in folds.values()),-pooled["normal_false_positive_rate"])
                        if eligible and (key is None or rank>key):best,key=(base,ref,threshold,row),rank
            write(OUTPUT/"search_results.json",records)
            print(a,number,setting.transform,"best",round(key[0]*100,3)if key else None,flush=True)
        if best is None:raise RuntimeError("No candidate meeting BOTH development-fold FP caps")
        base,ref,threshold,row=best;models[a]=CalibratedDetector(base,ref,threshold);chosen[a]=row
        joblib.dump(models[a],OUTPUT/(a+"_selected.joblib"),compress=3)
        write(OUTPUT/"search_progress.json",{"selected":chosen})
    joblib.dump({"models":models,"adapter_reference":data["adapter_reference"],"feature_context":data["feature_context"]},OUTPUT/"models.joblib",compress=3)
    write(OUTPUT/"selection.json",{"selected":chosen,"model_sha256":sha256(OUTPUT/"models.joblib"),
        "benchmark_manifest_sha256":read(OUTPUT/"preparation.json")["benchmark_manifest_sha256"],
        "two_existing_development_folds_used_for_selection":True,"fresh_confirmation_required_after_selection":True,
        "normal_only_estimator_and_reference_fit":True,"single_estimator_per_algorithm":True,"adapter_experts":False,
        "adapter_features":9,"prior_test_informed_architecture":True,"benchmark_modified":False})


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--stage",choices=("prepare","tune"),required=True)
    {"prepare":prepare,"tune":tune}[p.parse_args().stage]()
