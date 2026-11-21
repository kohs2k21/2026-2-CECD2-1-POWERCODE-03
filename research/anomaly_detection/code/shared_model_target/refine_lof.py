"""LOF-only larger-neighborhood/reference search; retain successful IF/K-means."""
import argparse
import copy
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/"code"))
import joblib
import numpy as np
from dataclasses import asdict
from shared_model_target.run_current import prepare
from shared_model_target.models import Setting,TargetDetector
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_improvement.run import _stats
from model_research_v13.data import read,write
from model_evaluation.data import sha256

PARENT=ROOT/"reports/shared_model_target_current"
OUTPUT=ROOT/"reports/shared_model_target_lof"


def candidates():
    for cap in (4096,8192,16384):
        for neighbors in (150,300,500):
            yield Setting("lof",view="current",transform="hybrid",neighbors=neighbors,reference_cap=cap)
    for neighbors in (150,300):
        for weight in (.1,.5):
            yield Setting("lof",view="current",transform="hybrid",neighbors=neighbors,reference_cap=8192,adapter_weight=weight)
    yield Setting("lof",view="current",transform="hybrid",neighbors=300,reference_cap=4096,metric="manhattan")


def tune():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/"selection.json").exists():raise FileExistsError("Frozen refinement")
    parent=read(PARENT/"selection.json");assert sha256(PARENT/"models.joblib")==parent["model_sha256"]
    fitted=joblib.load(PARENT/"models.joblib");data=prepare();normal=data["normal_calibration"]
    best=copy.deepcopy(fitted["models"]["lof"]);record=copy.deepcopy(parent["selected"]["lof"])
    best_key=(record["f1"],min(f["f1"]for f in record["folds"].values()),-record["normal_false_positive_rate"])
    rows=[]
    for i,setting in enumerate(candidates()):
        base=TargetDetector(setting).fit(data["train"])
        ns=base.score(normal);raw={n:base.score(data[n]["frame"])for n in ("search","confirmation")}
        for mode in ("global","availability"):
            for kind in ("cdf","robust"):
                for alpha in ((1.,)if mode=="global"else(.5,1.)):
                    ref=NormalReference.fit(normal,ns,mode,alpha,kind)
                    normal_scores=ref.apply(normal,ns);scores={n:ref.apply(data[n]["frame"],raw[n])for n in raw}
                    for q in (.90,.925,.95,.96,.97,.98,.99,.995):
                        threshold=float(np.quantile(normal_scores,q,method="higher"))
                        folds={n:_stats(data[n]["y"],scores[n],threshold,data[n]["scenario"])for n in raw}
                        y=np.concatenate([data[n]["y"]for n in raw]);s=np.concatenate(list(scores.values()))
                        pooled=_stats(y,s,threshold)
                        eligible=max(v["normal_false_positive_rate"]for v in folds.values())<.08
                        r={"settings":asdict(setting),"candidate":i,"mode":mode,"kind":kind,"alpha":alpha,"q":q,
                           "threshold":threshold,"folds":folds,"eligible":eligible,**pooled}
                        rows.append(r)
                        key=(pooled["f1"],min(v["f1"]for v in folds.values()),-pooled["normal_false_positive_rate"])
                        if eligible and key>best_key:best,best_key,record=CalibratedDetector(base,ref,threshold),key,r
        write(OUTPUT/"search_results.json",rows)
        joblib.dump(best,OUTPUT/"best_so_far.joblib",compress=3)
        write(OUTPUT/"progress.json",{"candidate_completed":i,"best":record})
        print(i,"k",setting.neighbors,"cap",setting.reference_cap,"weight",setting.adapter_weight,"best F1",round(best_key[0]*100,3),flush=True)
    fitted["models"]["lof"]=best
    selected=copy.deepcopy(parent);selected["selected"]["lof"]=record
    joblib.dump(fitted,OUTPUT/"models.joblib",compress=3)
    selected.update({"model_sha256":sha256(OUTPUT/"models.joblib"),"parent_model_sha256":parent["model_sha256"],
                     "only_lof_refined":True,"IF_and_kmeans_unchanged":True,"prior_final_results_informed_neighbor_search":True})
    write(OUTPUT/"selection.json",selected)
    # Reuse the verified same feature tables; no final labels used in selection.
    write(OUTPUT/"preparation.json",{**read(PARENT/"preparation.json"),"reference_unchanged_from":str(PARENT.relative_to(ROOT))})
    print("LOF refinement frozen",record,flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--stage",choices=("tune",),required=True);p.parse_args();tune()
