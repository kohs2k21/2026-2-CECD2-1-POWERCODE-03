"""IF-only compact/balanced views; keep all nine retained Adapter features."""
import argparse
import copy
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/"code"))
import joblib
import numpy as np
from dataclasses import asdict
from shared_model_improvement.models import Setting,StableDetector
from shared_model_improvement.run import OUTPUT as PARENT,OLD,ALGORITHMS,MAIN_SCENARIOS,prepare,load_models,_stats,QUANTILES
from model_research_v13.data import read,write
from model_research_v15.features import augment
from model_evaluation.data import sha256
from sklearn.metrics import average_precision_score,roc_auc_score

OUTPUT=ROOT/"reports/shared_model_improvement_refined"


def tune():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/"selection.json").exists():raise FileExistsError("Refined selection frozen")
    selected,fitted=load_models(PARENT);data=prepare();search=data["search"]
    best=copy.deepcopy(fitted["models"]["if"]);chosen=copy.deepcopy(selected["selected"]["if"])
    best_key=(chosen["f1"],chosen["macro_f1"],-chosen["normal_false_positive_rate"])
    baseline=selected["baseline"]["if"];records=[]
    for view in ("full","balanced","compact"):
        for transform in ("hybrid","clip","quantile"):
            for samples in (64,128,256):
                for maximum in (1.,.6):
                    setting=Setting("if",view=view,transform=transform,samples=samples,max_features=maximum)
                    model=StableDetector(setting).fit(data["train"])
                    normal=model.score(data["normal_calibration"]);scores=model.score(search["frame"])
                    for q in QUANTILES:
                        cutoff=float(np.quantile(normal,q,method="higher"))
                        stats=_stats(search["y"],scores,cutoff,search["scenario"])
                        eligible=(stats["normal_false_positive_rate"]<=max(baseline["normal_false_positive_rate"]+.005,.03)
                                  and stats["precision"]>=baseline["precision"]-.02)
                        record={"settings":asdict(setting),"normal_quantile":q,"threshold":cutoff,"eligible":eligible,**stats}
                        records.append(record)
                        key=(stats["f1"],stats["macro_f1"],-stats["normal_false_positive_rate"])
                        if eligible and key>best_key:
                            best,best_key,chosen=model,key,record;best.threshold=cutoff
                    write(OUTPUT/"search_results.json",records)
                    print(view,transform,samples,maximum,"best",round(best_key[0]*100,3),flush=True)
    fitted["models"]["if"]=best
    selected=copy.deepcopy(selected);selected["selected"]["if"]=chosen
    selected.update({"parent_model_sha256":sha256(PARENT/"models.joblib"),
        "prior_failed_IF_test_informed_view_design":True,"refinement_selection_on_search_only":True,
        "adapter_features_removed_in_refinement":0,"base_redundancy_views_compared":True})
    joblib.dump(fitted,OUTPUT/"models.joblib",compress=3);selected["model_sha256"]=sha256(OUTPUT/"models.joblib")
    write(OUTPUT/"selection.json",selected)
    print("Frozen refined IF",chosen,flush=True)
    confirmation=data["confirmation"]
    rows=[];old=joblib.load(OLD/"shared_models.joblib")["models"]
    for a in ALGORITHMS:
        for method,model,t in (("baseline",old[a+"_baseline"],old[a+"_baseline"].thresholds["0.03"]),
                             ("improved",fitted["models"][a],fitted["models"][a].threshold)):
            rows.append({"algorithm":a,"method":method,**_stats(confirmation["y"],model.score(confirmation["frame"]),t,confirmation["scenario"])})
    write(OUTPUT/"confirmation.json",{"used_to_reselect":False,"metrics":rows})


def evaluate():
    selected,fitted=load_models(OUTPUT);parent=read(PARENT/"summary.json")
    digest=selected["benchmark_manifest_sha256"]
    assert sha256(ROOT/"data/evaluation/manifest.json")==digest
    cache=ROOT/"reports/model_research_v18/feature_cache"/digest[:16]
    datasets,all_y,all_s,samples=[],[],[],[]
    for dataset in parent["datasets"]:
        name=dataset["dataset"];original=np.load(PARENT/(name+"_scores.npz"))
        y,scores=original["y"],original["scores"].copy();offset=0
        for index,record in enumerate(read(cache/name/"index.json")):
            path=cache/name/record["file"]
            if sha256(path)!=record["sha256"]:raise ValueError("Changed feature cache")
            payload=joblib.load(path);frame=fitted["adapter_reference"].transform(augment(payload["frame"]))
            n=len(frame);scores[offset:offset+n,0]=fitted["models"]["if"].score(frame)
            ids=np.random.default_rng(4200+index).choice(n,min(n,100),replace=False)
            sample=frame.iloc[ids].copy();sample["actual_anomaly"],sample["dataset"]=y[offset:offset+n][ids],name
            for i,a in enumerate(ALGORITHMS):sample[a+"_score"]=scores[offset:offset+n,i][ids]
            samples.append(sample);offset+=n
        assert offset==len(y)
        np.savez_compressed(OUTPUT/(name+"_scores.npz"),y=y,scores=scores,adapter=original["adapter"],case_index=original["case_index"])
        records=[{"method":a,**_stats(y,scores[:,i],fitted["models"][a].threshold),
                  "average_precision":float(average_precision_score(y,scores[:,i])),"roc_auc":float(roc_auc_score(y,scores[:,i])),
                  "threshold":fitted["models"][a].threshold} for i,a in enumerate(ALGORITHMS)]
        datasets.append({"dataset":name,"metrics":records});all_y.append(y);all_s.append(scores)
        write(OUTPUT/"evaluation_progress.json",{"finished":[d["dataset"] for d in datasets],"metrics":records})
        print(name,[(r["method"],round(r["f1"]*100,2)) for r in records],flush=True)
    y,scores=np.concatenate(all_y),np.concatenate(all_s)
    summary=copy.deepcopy(parent);summary["datasets"]=datasets
    summary["pooled"]=[{"method":a,**_stats(y,scores[:,i],fitted["models"][a].threshold),
        "average_precision":float(average_precision_score(y,scores[:,i])),"roc_auc":float(roc_auc_score(y,scores[:,i]))} for i,a in enumerate(ALGORITHMS)]
    summary["main_five_pooled"]=[]
    for i,a in enumerate(ALGORITHMS):
        arrays=[np.load(OUTPUT/(s+"_scores.npz")) for s in MAIN_SCENARIOS]
        yy=np.concatenate([v["y"] for v in arrays]);ss=np.concatenate([v["scores"][:,i] for v in arrays])
        summary["main_five_pooled"].append({"method":a,**_stats(yy,ss,fitted["models"][a].threshold)})
    for i,r in enumerate(summary["comparisons"]):
        r["improved_f1"]=summary["pooled"][i]["f1"];r["target_met"]=r["improved_f1"]>=r["before_adapter_f1"]
    summary["protocol"].update({"model_sha256":selected["model_sha256"],"IF_redundant_base_features_refined":True,
        "prior_test_results_informed_architecture":True,"adapter_feature_count_unchanged":True})
    write(OUTPUT/"summary.json",summary)
    joblib.dump(__import__('pandas').concat(samples,ignore_index=True),OUTPUT/"analysis_samples.joblib",compress=3)
    print(summary["comparisons"],flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--stage",choices=("tune","evaluate"),required=True)
    {"tune":tune,"evaluate":evaluate}[p.parse_args().stage]()
