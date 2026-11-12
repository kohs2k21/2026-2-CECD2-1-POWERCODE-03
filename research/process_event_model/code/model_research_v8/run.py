"""Fresh-development v8 selection frozen before unchanged benchmark evaluation."""
import argparse
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"code"))
import joblib
import numpy as np
import pandas as pd
from model_evaluation.metrics import Counts
from model_evaluation.data import sha256, verify_benchmark
from model_evaluation.evaluate import event_results
from model_evaluation.tune_windows import write_json
from model_improvement.data import BENCHMARK, SOURCE_CACHE, final_rows, control_grid
from model_improvement.refine import OUTPUT as V4, ALL_FEATURES as LEGACY_FEATURES, frozen as frozen_v4
from model_improvement.run import trusted_load
from model_research_v5.run_matched import frozen as frozen_v7
from model_research_v5.run import components,summarize,six_metrics,METRIC_NAMES
from model_research_v5.features import context_features
from model_research_v8.data import prepare,load,OUTPUT,CODE_FILES,fingerprints
from model_research_v8.models import ResearchSettings,ChannelFamily,Suite,METHODS
from model_research_v8.features import FEATURES,add_duration_features


def tune():
    data=load()
    if (OUTPUT/"selection.json").exists():raise FileExistsError("v8 selection is frozen")
    chosen,results={},[]
    for algorithm in ("if","kmeans"):
        configs=([ResearchSettings("if",samples=n) for n in (256,1024,4096)] if algorithm=="if" else
                 [ResearchSettings("kmeans",k=k,covariance=c) for k,c in ((6,"radius"),(12,"radius"),(6,"diagonal"),(12,"full"))])
        best=model=fallback=fallback_model=None
        for settings in configs:
            print(f"v8 fit {asdict(settings)}",flush=True)
            family=ChannelFamily(FEATURES,settings).fit(data["partitions"],data["source_support"])
            cached=components(family,data["bank"],"search")
            for beta in (.5,1.,2.):
                for aware in (False,True):
                    for q in (.975,.99,.995):
                        for weight in (.25,.5,.75):
                            for mode in ("mean","max"):
                                family.set_operating_point(q,weight,aware,beta,mode)
                                r={"algorithm":algorithm,"settings":asdict(settings),"quantile":q,"global_weight":weight,
                                   "phase_aware":aware,"latency_weight":beta,"combination":mode,**summarize(family,cached)}
                                # Maintain a low pooled FPR, avoid hiding disastrous sparse-case FPR.
                                r["within_budget"]=r["worst_strategy_pooled_fpr"]<=.02 and r["worst_scenario_strategy_fpr"]<=.05
                                results.append(r)
                                key=(r["objective"],-r["worst_strategy_pooled_fpr"],-settings.samples,-settings.k)
                                fkey=(-r["worst_strategy_pooled_fpr"],-r["worst_scenario_strategy_fpr"],r["objective"])
                                if fallback is None or fkey>fallback[0]:fallback,fallback_model=(fkey,r),copy.deepcopy(family)
                                if r["within_budget"] and (best is None or key>best[0]):best,model=(key,r),copy.deepcopy(family)
        if best is None:best,model=fallback,fallback_model
        chosen[algorithm]=model;write_json(OUTPUT/f"chosen_{algorithm}.json",best[1])
        print(f"v8 chosen {algorithm}: macro F1={best[1]['objective']:.2%}, pooled FPR={best[1]['worst_strategy_pooled_fpr']:.2%}, feasible={best[1]['within_budget']}",flush=True)
    suite=Suite(chosen);suite.reference=data["reference"];suite.baseline=data["baseline"];suite.legacy_baselines=data["legacy_baselines"]
    v4,_=frozen_v4();v7,_=frozen_v7()
    validation={name:{split:six_metrics(m,bank,split) for split in ("search","confirmation")}
                for name,m,bank in (("baseline_v4",v4,data["legacy_bank"]),("baseline_v7",v7,data["legacy_bank"]),("tuned_v8",suite,data["bank"]))}
    joblib.dump(suite,OUTPUT/"models_v8.joblib",compress=3)
    write_json(OUTPUT/"search_results.json",results);write_json(OUTPUT/"validation_results.json",validation)
    write_json(OUTPUT/"selection.json",{"model_sha256":sha256(OUTPUT/"models_v8.joblib"),"inputs":fingerprints(),
        "training_code_sha256":{n:sha256(Path(__file__).parent/n) for n in CODE_FILES},
        "families":{a:json.loads((OUTPUT/f"chosen_{a}.json").read_text(encoding="utf-8")) for a in chosen},
        "confirmation_used_for_selection":False,"final_used_for_selection":False,"hard_used_for_selection":False,
        "normal_only_model_fit":True,"labelled_validation_selection":True,
        "objective":"Equal-scenario F1 averaged over 3 strategies, EACH pooled normal FPR<=2%, EACH scenario/strategy FPR<=5%.",
        "limitation":"Adaptive research after prior inspected benchmark; fresh episodes share generator/reserved source pool.",
        "completed_utc":datetime.now(timezone.utc).isoformat()})


def frozen():
    r=json.loads((OUTPUT/"selection.json").read_text(encoding="utf-8"))
    if fingerprints()!=r["inputs"]:raise ValueError("Changed frozen v8 input")
    for n,digest in r["training_code_sha256"].items():
        if sha256(Path(__file__).parent/n)!=digest:raise ValueError(f"Changed frozen v8 code {n}")
    return trusted_load(OUTPUT/"models_v8.joblib",r["model_sha256"]),r


def frame_from_arrays(a):
    f=pd.DataFrame(a["features"],columns=LEGACY_FEATURES,dtype=float)
    f["case_id"],f["adapterType"],f["processId"]=a["case_index"],a["adapter"],a["process"]
    f["window_start"]=pd.to_datetime(a["window_end_seconds"]-60,unit="s")
    # v4 lacks native counts in its exported feature list: reconstruct ONLY from
    # past counts and the cached 1-minute logratio. No normal oracle is read.
    from model_evaluation.windowing import PriorHistory
    history=PriorHistory(BENCHMARK/"reference_history_windows.csv")
    p=history.lookup(f.window_start,np.ones(len(f),int))[:,0]
    f["native_execution_arrivals"]=np.rint(np.exp(f.arrival_logratio_1m)*(p+1)-1).clip(0)
    return context_features(f)


def evaluate():
    suite,selection=frozen();verify_benchmark(BENCHMARK,SOURCE_CACHE)
    previous=json.loads((V4/"summary.json").read_text(encoding="utf-8"))
    pooled,support,datasets=Counts(),Counts(),[]
    for prior in previous["datasets"]:
        name=prior["dataset"];dest=OUTPUT/name;dest.mkdir(exist_ok=True)
        if (dest/"metrics.json").exists():
            result=json.loads((dest/"metrics.json").read_text(encoding="utf-8"))
            assert result["model_sha256"]==selection["model_sha256"]
            for c,digest in result["arrays_sha256"].items():assert sha256(dest/f"{c}.npz")==digest
        else:
            folder=BENCHMARK/name
            cases=pd.read_csv(folder/"cases.csv",encoding="utf-8-sig")
            events=pd.read_csv(folder/"events.csv",encoding="utf-8-sig")
            labels=pd.read_csv(folder/"process_labels.csv",usecols=["transactionId","case_id"],encoding="utf-8-sig",dtype="string")
            raw=final_rows(folder,labels,cases,events)
            arrays,counts,supported={},Counts(),Counts()
            mapping=dict(zip(cases.case_id,np.arange(len(cases))))
            for cohort,digest in prior["arrays_sha256"].items():
                assert sha256(V4/name/f"{cohort}.npz")==digest
                with np.load(V4/name/f"{cohort}.npz",allow_pickle=False) as z:a={k:z[k] for k in z.files if k not in ("margin","prediction","covered")}
                f=frame_from_arrays(a)
                completions=raw[cohort].copy();completions["case_id"]=completions.case_id.map(mapping)
                if completions.case_id.isna().any():raise ValueError("Unmapped original case")
                f=add_duration_features(f,completions,suite.reference)
                margins,predictions,coverage,evidence=[],[],[],[]
                for start in range(0,len(f),50_000):
                    part=f.iloc[start:start+50_000];m,p,c=suite.score(part)
                    margins.append(m.astype(np.float32));predictions.append(p);coverage.append(c)
                    evidence.append(np.column_stack([suite.families[a].global_model.explain(part) for a in ("if","kmeans")]).astype(np.float32))
                a["margin"],a["prediction"],a["covered"]=np.vstack(margins),np.vstack(predictions),np.concatenate(coverage)
                a["features"]=f[list(FEATURES)].to_numpy(np.float32);a["channel_evidence"]=np.vstack(evidence)
                for col in ("traffic_assessed","latency_assessed","completion_count_1m","duration_reference_level","phase"):
                    a[col]=f[col].to_numpy(dtype=str if col=="phase" else None)
                counts.update(a["truth"],a["prediction"]);supported.update(a["truth"],a["prediction"],a["covered"])
                np.savez_compressed(dest/f"{cohort}.npz",**a);arrays[cohort]=a
            result={"dataset":name,"case_count":len(cases),"metrics":counts.records(),"supported_only_metrics":supported.records(),
                "events":event_results(arrays["observed"],arrays["normal"],events,cases),
                "coverage":float(np.concatenate([a["covered"] for a in arrays.values()]).mean()),
                "traffic_assessed_fraction":float(np.concatenate([a["traffic_assessed"] for a in arrays.values()]).mean()),
                "latency_assessed_fraction":float(np.concatenate([a["latency_assessed"] for a in arrays.values()]).mean()),
                "model_sha256":selection["model_sha256"],"arrays_sha256":{c:sha256(dest/f"{c}.npz") for c in arrays}}
            write_json(dest/"metrics.json",result)
        pooled.values+=Counts.from_records(result["metrics"]).values;support.values+=Counts.from_records(result["supported_only_metrics"]).values;datasets.append(result)
        print(f"v8 {name}: IF ensemble={result['metrics'][2]['f1']:.2%}, KMeans ensemble={result['metrics'][5]['f1']:.2%}",flush=True)
    write_json(OUTPUT/"summary.json",{"pooled":pooled.records(),"supported_only":support.records(),"datasets":datasets,
        "basic_macro":[{"method":m,**{k:float(np.mean([d["metrics"][i][k] for d in datasets if d["dataset"]!="mixed_hard"])) for k in METRIC_NAMES}} for i,m in enumerate(METHODS)],
        "thresholds":suite.threshold_records(),"support":suite.families["if"].support,"model_sha256":selection["model_sha256"],
        "protocol":{"original_labels_times_unchanged":True,"point_adjustment":False,"not_blind_original_test":True,
                    "normal_only_fit":True,"unassessed_rows_remain_in_metric_denominators":True,"process_only":True}})
    verify_benchmark(BENCHMARK,SOURCE_CACHE)


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--stage",choices=("prepare","tune","evaluate","all"),default="all");args=p.parse_args()
    if args.stage in ("prepare","all"):prepare()
    if args.stage in ("tune","all"):tune()
    if args.stage in ("evaluate","all"):evaluate()
