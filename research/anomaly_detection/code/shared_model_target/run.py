"""New causal feature/geometry search. All selection is development only."""
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
from shared_model_target.features import augment
from shared_model_target.calibration import NormalReference,CalibratedDetector
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from model_research_v15.features import augment as temporal_features
from sklearn.metrics import average_precision_score,roc_auc_score

OUTPUT=ROOT/"reports/shared_model_target"
PARENT=ROOT/"reports/shared_model_improvement_refined"


def prepare():
    OUTPUT.mkdir(exist_ok=True);path=OUTPUT/"development.joblib"
    if path.exists():
        assert sha256(path)==read(OUTPUT/"preparation.json")["sha256"]
        return joblib.load(path)
    source=load_development();selected,parent=load_models(PARENT);reference=parent["adapter_reference"]
    data={"train":augment(reference.transform(source["partitions"]["train"])),
          "normal_calibration":augment(reference.transform(source["normal_reference"])),
          "adapter_reference":reference,"feature_context":parent["feature_context"]}
    for split in ("search","confirmation"):
        frames,truth,scenario=[],[],[]
        for name,cohorts in source[split].items():
            for cohort,f in cohorts.items():
                frames.append(augment(reference.transform(f)))
                truth.append(f.window_is_anomaly.to_numpy(bool) if cohort=="observed" else np.zeros(len(f),bool))
                scenario.extend([name]*len(f))
        data[split]={"frame":pd.concat(frames,ignore_index=True),"y":np.concatenate(truth),"scenario":np.asarray(scenario)}
    ids=[set(data["train"].case_id),set(data["normal_calibration"].case_id),set(data["search"]["frame"].case_id),set(data["confirmation"]["frame"].case_id)]
    assert all(not ids[i]&ids[j] for i in range(4) for j in range(i))
    joblib.dump(data,path,compress=3)
    write(OUTPUT/"preparation.json",{"sha256":sha256(path),"parent_model_sha256":selected["model_sha256"],
         "normal_fit_only":True,"paired_cohorts_augmented_separately":True,"case_sets_disjoint":True,
         "benchmark_manifest_sha256":selected["benchmark_manifest_sha256"]})
    return data


def candidates(algorithm):
    for view in ("context","balanced"):
        for transform in ("quantile","hybrid"):
            if algorithm=="if":
                for samples,whitening,rotation in ((128,False,False),(256,False,False),(256,True,False),(256,True,True)):
                    yield Setting(algorithm,view=view,transform=transform,samples=samples,whitening=whitening,rotation=rotation)
            elif algorithm=="kmeans":
                for k,geometry,whitening in ((8,"radius",False),(16,"radius",False),(8,"diagonal",False),(16,"diagonal",True)):
                    yield Setting(algorithm,view=view,transform=transform,clusters=k,geometry=geometry,whitening=whitening)
            else:
                for neighbors,cap,whitening in ((35,4096,False),(75,4096,False),(150,4096,False),(75,8192,True)):
                    yield Setting(algorithm,view=view,transform=transform,neighbors=neighbors,reference_cap=cap,whitening=whitening)


def tune():
    if (OUTPUT/"selection.json").exists():raise FileExistsError("Frozen")
    data=prepare();search=data["search"];normal=data["normal_calibration"]
    models,chosen,records={},{},[]
    for a in ALGORITHMS:
        best,key=None,None
        for number,setting in enumerate(candidates(a)):
            base=TargetDetector(setting).fit(data["train"])
            nscore,sscore=base.score(normal),base.score(search["frame"])
            for mode in ("global","adapter","adapter_availability"):
                for kind in ("cdf","robust"):
                    reference=NormalReference.fit(normal,nscore,mode,1.,kind)
                    ns,ss=reference.apply(normal,nscore),reference.apply(search["frame"],sscore)
                    for q in (.90,.925,.95,.96,.97,.98,.99,.995):
                        threshold=float(np.quantile(ns,q,method="higher"));stats=_stats(search["y"],ss,threshold,search["scenario"])
                        eligible=stats["normal_false_positive_rate"]<.075
                        row={"algorithm":a,"candidate":number,"settings":asdict(setting),"mode":mode,"kind":kind,"q":q,"threshold":threshold,"eligible":eligible,**stats}
                        records.append(row)
                        rank=(stats["f1"],stats["macro_f1"],-stats["normal_false_positive_rate"])
                        if eligible and (key is None or rank>key):best,key=(base,reference,threshold,row),rank
            write(OUTPUT/"search_results.json",records)
            print(a,number,setting.view,setting.transform,"best",round(key[0]*100,3) if key else None,flush=True)
        if best is None:raise RuntimeError("No valid candidate")
        base,ref,threshold,row=best;models[a]=CalibratedDetector(base,ref,threshold);chosen[a]=row
        joblib.dump(models[a],OUTPUT/(a+"_selected.joblib"),compress=3)
        write(OUTPUT/"search_progress.json",{"selected":chosen,"algorithm_finished":a})
    package={"models":models,"adapter_reference":data["adapter_reference"],"feature_context":data["feature_context"]}
    joblib.dump(package,OUTPUT/"models.joblib",compress=3)
    write(OUTPUT/"selection.json",{"model_sha256":sha256(OUTPUT/"models.joblib"),"selected":chosen,
         "benchmark_manifest_sha256":read(OUTPUT/"preparation.json")["benchmark_manifest_sha256"],
         "selection_on_search_only":True,"confirmation_used_for_selection":False,"parent_test_informed_design":True,
         "benchmark_modified":False,"single_estimator_per_algorithm":True,"adapter_features":9,"adapter_experts":False,
         "targets":{"if":.40,"kmeans":.50,"lof":.50},"false_positive_target":.10})
    rows=[];v=data["confirmation"]
    for a in ALGORITHMS:rows.append({"method":a,**_stats(v["y"],models[a].score(v["frame"]),models[a].threshold,v["scenario"])})
    write(OUTPUT/"confirmation.json",{"used_for_selection":False,"metrics":rows})


def evaluate(folder=None):
    global OUTPUT
    if folder is not None:OUTPUT=Path(folder).resolve()
    selection=read(OUTPUT/"selection.json")
    assert sha256(OUTPUT/"models.joblib")==selection["model_sha256"]
    package=joblib.load(OUTPUT/"models.joblib")
    digest=selection["benchmark_manifest_sha256"]
    assert sha256(ROOT/"data/evaluation/manifest.json")==digest
    original=ROOT/"reports/model_research_v18/feature_cache"/digest[:16]
    new_cache=OUTPUT/"feature_cache"/digest[:16];new_cache.mkdir(parents=True,exist_ok=True)
    from shared_model_target.current_features import CurrentAdapterReference
    current_mode=isinstance(package["adapter_reference"],CurrentAdapterReference)
    feature_hash=sha256(ROOT/"code/shared_model_target/features.py")
    if current_mode:feature_hash+="_"+sha256(ROOT/"code/shared_model_target/current_features.py")
    expected={"benchmark_sha256":digest,"feature_source_sha256":feature_hash,
              "reference_model_sha256":read(OUTPUT/"preparation.json")["parent_model_sha256"]}
    if (new_cache/"protocol.json").exists():assert read(new_cache/"protocol.json")==expected
    else:write(new_cache/"protocol.json",expected)
    all_y,all_s,datasets,samples=[],[],[],[]
    for name in read(ROOT/"data/evaluation/manifest.json")["datasets"]:
        cache_dir=new_cache/name;cache_dir.mkdir(exist_ok=True)
        entries=[];ys,ss,indices,adapters=[],[],[],[]
        for i,entry in enumerate(read(original/name/"index.json")):
            source=original/name/entry["file"];cached=cache_dir/entry["file"]
            assert sha256(source)==entry["sha256"]
            if cached.exists():
                index_path=cached.with_suffix(".json")
                assert index_path.exists() and sha256(cached)==read(index_path)["sha256"]
                payload=joblib.load(cached)
            else:
                old=joblib.load(source)
                frame=package["adapter_reference"].transform(temporal_features(old["frame"]))
                if not current_mode:frame=augment(frame)
                payload={"frame":frame,"metadata":old["metadata"]}
                joblib.dump(payload,cached,compress=3)
                write(cached.with_suffix(".json"),{"sha256":sha256(cached),"source_sha256":entry["sha256"]})
            frame,meta=payload["frame"],payload["metadata"]
            scores=np.column_stack([package["models"][a].score(frame) for a in ALGORITHMS])
            y=np.asarray(meta["truth"],bool)
            ys.append(y);ss.append(scores);indices.append(meta["case_index"]);adapters.append(frame.adapterType.to_numpy(str))
            ids=np.random.default_rng(4242+i).choice(len(frame),min(len(frame),100),replace=False)
            sample=frame.iloc[ids].copy();sample["actual_anomaly"],sample["dataset"]=y[ids],name
            for j,a in enumerate(ALGORITHMS):sample[a+"_score"]=scores[ids,j]
            samples.append(sample)
            entries.append({"file":cached.name,"sha256":sha256(cached),"cohort":entry["cohort"],"decisions":len(frame)})
        write(cache_dir/"index.json",entries)
        y,scores=np.concatenate(ys),np.concatenate(ss)
        np.savez_compressed(OUTPUT/(name+"_scores.npz"),y=y,scores=scores,case_index=np.concatenate(indices),adapter=np.concatenate(adapters))
        rows=[{"method":a,**_stats(y,scores[:,i],package["models"][a].threshold),
               "average_precision":float(average_precision_score(y,scores[:,i])),"roc_auc":float(roc_auc_score(y,scores[:,i]))}
              for i,a in enumerate(ALGORITHMS)]
        datasets.append({"dataset":name,"metrics":rows});all_y.append(y);all_s.append(scores)
        write(OUTPUT/"evaluation_progress.json",{"finished":[d["dataset"] for d in datasets],"metrics":rows})
        print(name,[(r["method"],round(r["f1"]*100,2),round(r["normal_false_positive_rate"]*100,2)) for r in rows],flush=True)
    y,scores=np.concatenate(all_y),np.concatenate(all_s)
    rows=[{"method":a,**_stats(y,scores[:,i],package["models"][a].threshold),
           "average_precision":float(average_precision_score(y,scores[:,i])),"roc_auc":float(roc_auc_score(y,scores[:,i]))}
          for i,a in enumerate(ALGORITHMS)]
    targets={"if":.4,"kmeans":.5,"lof":.5}
    summary={"pooled":rows,"datasets":datasets,"targets_met":{r["method"]:r["f1"]>=targets[r["method"]] and r["normal_false_positive_rate"]<.1 for r in rows},
             "protocol":{"decisions":len(y),"normal":int((~y).sum()),"anomaly":int(y.sum()),"benchmark_modified":False,
                          "point_adjustment":False,"single_estimator_per_algorithm":True,"adapter_experts":False,"adapter_features":9,
                          "not_external_blind_test":True,"feature_source_sha256":feature_hash}}
    write(OUTPUT/"summary.json",summary);joblib.dump(pd.concat(samples,ignore_index=True),OUTPUT/"analysis_samples.joblib",compress=3)
    print(rows,flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--stage",choices=("prepare","tune","evaluate"),required=True)
    p.add_argument("--output",type=Path);args=p.parse_args()
    if args.stage=="evaluate":evaluate(args.output)
    else:{"prepare":prepare,"tune":tune}[args.stage]()
