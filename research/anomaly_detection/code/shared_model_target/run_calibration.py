"""Freeze on search only, reuse unchanged parent raw scores, evaluate all seven datasets."""
import argparse
import copy
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/"code"))
import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score,roc_auc_score
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_improvement.run import prepare,load_models,_stats,ALGORITHMS,MAIN_SCENARIOS
from model_evaluation.data import sha256
from model_research_v13.data import read,write

PARENT=ROOT/"reports/shared_model_improvement_refined"
OUTPUT=ROOT/"reports/shared_model_target_calibration"
TARGET={"if":.40,"kmeans":.50,"lof":.50}


def tune():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/"selection.json").exists():raise FileExistsError("Frozen; retain selection")
    parent,fitted=load_models(PARENT);data=prepare();records=[];chosen={};models={}
    search=data["search"];normal=data["normal_calibration"]
    for a in ALGORITHMS:
        base=fitted["models"][a];nscore=base.score(normal);sscore=base.score(search["frame"])
        best,key=None,None
        for mode in ("global","availability","adapter","adapter_phase","adapter_availability"):
            for kind in ("cdf","robust"):
                for alpha in ((1.,) if mode=="global" else (.5,1.)):
                    ref=NormalReference.fit(normal,nscore,mode,alpha,kind)
                    nc=ref.apply(normal,nscore);sc=ref.apply(search["frame"],sscore)
                    for q in (.85,.90,.925,.95,.96,.97,.98,.99,.995):
                        threshold=float(np.quantile(nc,q,method="higher"))
                        stats=_stats(search["y"],sc,threshold,search["scenario"])
                        eligible=stats["normal_false_positive_rate"]<.08
                        row={"algorithm":a,"mode":mode,"kind":kind,"alpha":alpha,"q":q,"threshold":threshold,"eligible":eligible,**stats}
                        records.append(row)
                        rank=(stats["f1"],stats["macro_f1"],-stats["normal_false_positive_rate"])
                        if eligible and (key is None or rank>key):best,key=(ref,threshold,row),rank
        if best is None:raise RuntimeError("No valid development point")
        ref,threshold,row=best;models[a]=CalibratedDetector(base,ref,threshold);chosen[a]=row
        write(OUTPUT/"search_results.json",records)
        print(a,row,flush=True)
    package={**fitted,"models":models}
    joblib.dump(package,OUTPUT/"models.joblib",compress=3)
    write(OUTPUT/"selection.json",{"selected":chosen,"model_sha256":sha256(OUTPUT/"models.joblib"),
        "parent_model_sha256":parent["model_sha256"],"benchmark_manifest_sha256":parent["benchmark_manifest_sha256"],
        "targets":TARGET,"false_positive_target":.10,"development_false_positive_cap":.08,
        "selection_on_search_only":True,"parent_test_results_informed_design":True,"benchmark_modified":False,
        "single_estimator_per_algorithm":True,"adapter_experts":False,"normal_only_calibration":True})
    rows=[]
    for a in ALGORITHMS:
        v=data["confirmation"];scores=models[a].score(v["frame"])
        rows.append({"method":a,**_stats(v["y"],scores,models[a].threshold,v["scenario"])})
    write(OUTPUT/"confirmation.json",{"used_for_selection":False,"metrics":rows})


def evaluate():
    selected=read(OUTPUT/"selection.json")
    assert sha256(OUTPUT/"models.joblib")==selected["model_sha256"]
    fitted=joblib.load(OUTPUT/"models.joblib");parent=read(PARENT/"summary.json")
    digest=selected["benchmark_manifest_sha256"]
    assert sha256(ROOT/"data/evaluation/manifest.json")==digest
    cache=ROOT/"reports/model_research_v18/feature_cache"/digest[:16]
    all_y,all_s,datasets=[],[],[]
    for d in parent["datasets"]:
        name=d["dataset"];old=np.load(PARENT/(name+"_scores.npz"));scores=old["scores"].copy();offset=0
        for record in read(cache/name/"index.json"):
            path=cache/name/record["file"]
            if sha256(path)!=record["sha256"]:raise ValueError("Changed cache")
            frame=joblib.load(path)["frame"];n=len(frame)
            for i,a in enumerate(ALGORITHMS):scores[offset:offset+n,i]=fitted["models"][a].reference.apply(frame,scores[offset:offset+n,i])
            offset+=n
        y=old["y"];assert offset==len(y)
        np.savez_compressed(OUTPUT/(name+"_scores.npz"),y=y,scores=scores,case_index=old["case_index"],adapter=old["adapter"])
        rows=[{"method":a,**_stats(y,scores[:,i],fitted["models"][a].threshold),
               "average_precision":float(average_precision_score(y,scores[:,i])),"roc_auc":float(roc_auc_score(y,scores[:,i]))}
              for i,a in enumerate(ALGORITHMS)]
        datasets.append({"dataset":name,"metrics":rows});all_y.append(y);all_s.append(scores)
        print(name,[(r["method"],round(r["f1"]*100,2),round(r["normal_false_positive_rate"]*100,2)) for r in rows],flush=True)
    y,scores=np.concatenate(all_y),np.concatenate(all_s)
    rows=[{"method":a,**_stats(y,scores[:,i],fitted["models"][a].threshold),
           "average_precision":float(average_precision_score(y,scores[:,i])),"roc_auc":float(roc_auc_score(y,scores[:,i]))}for i,a in enumerate(ALGORITHMS)]
    summary={"pooled":rows,"datasets":datasets,"targets_met":{r["method"]:r["f1"]>=TARGET[r["method"]] and r["normal_false_positive_rate"]<.10 for r in rows},
             "protocol":{"decisions":len(y),"single_estimator_per_algorithm":True,"adapter_experts":False,"benchmark_modified":False,
                          "point_adjustment":False,"adapter_features_preserved":9,"not_external_blind_test":True}}
    write(OUTPUT/"summary.json",summary);print(rows,flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--stage",choices=("tune","evaluate"),required=True)
    {"tune":tune,"evaluate":evaluate}[p.parse_args().stage]()
