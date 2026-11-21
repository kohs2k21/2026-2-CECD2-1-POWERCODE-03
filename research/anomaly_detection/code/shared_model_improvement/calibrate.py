"""IF observation-state calibration; still one detector, no Adapter routing.

Parent raw scores/model are preserved. Selection uses search only; final scoring
reuses frozen raw-score arrays and observable state from verified feature caches.
"""
import argparse
import copy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score
from shared_model_improvement.run import OUTPUT as PARENT, OLD, ALGORITHMS, MAIN_SCENARIOS, prepare, load_models, _stats
from model_research_v13.data import read, write
from model_evaluation.data import sha256

OUTPUT = ROOT/"reports/shared_model_improvement_final"


class EvidenceCalibrated:
    def __init__(self, base, mode, quantile, references, minimum=80):
        self.base, self.mode, self.quantile = base, mode, quantile
        self.algorithm, self.setting = base.algorithm, base.setting
        self.encoder, self.columns, self.estimator = base.encoder, base.columns, base.estimator
        self.fit_rows, self.fit_seconds = base.fit_rows, base.fit_seconds
        self.threshold, self.minimum = 0., minimum
        self.cutoffs = {key:float(np.quantile(values, quantile, method="higher")) for key,values in references.items()}

    @staticmethod
    def keys(frame, mode):
        availability = frame.traffic_assessed.to_numpy(int)+2*frame.latency_assessed.to_numpy(int)
        if mode == "availability": return availability.astype(str)
        if mode == "phase": return frame.phase.to_numpy(str)
        if mode == "phase_availability":
            return np.array([str(p)+"_"+str(a) for p,a in zip(frame.phase,availability)])
        return np.repeat("GLOBAL", len(frame))

    @classmethod
    def fit_reference(cls, base, normal, mode):
        values = base.score(normal)
        keys = cls.keys(normal, mode)
        references = {"GLOBAL": values}
        for key in np.unique(keys):
            subset = values[keys == key]
            references[key] = subset if len(subset) >= 80 else values
        return references

    def from_raw(self, frame, scores):
        keys = self.keys(frame, self.mode)
        cutoff = np.array([self.cutoffs.get(k,self.cutoffs["GLOBAL"]) for k in keys])
        return scores-cutoff

    def score(self, frame): return self.from_raw(frame, self.base.score(frame))
    def predict(self, frame):
        margin = self.score(frame)
        return margin, margin > 0

    def record(self):
        return {**self.base.record(), "threshold":0., "calibration_context":self.mode,
                "normal_quantile":self.quantile, "raw_score_cutoffs":self.cutoffs,
                "adapter_specific_thresholds":False, "score_definition":"raw IF score minus observation-state normal cutoff"}


def tune():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/"selection.json").exists(): raise FileExistsError("Final selection already frozen")
    selected, fitted = load_models(PARENT)
    data = prepare(); search = data["search"]
    base = fitted["models"]["if"]
    raw = base.score(search["frame"])
    baseline = selected["baseline"]["if"]
    best, key, chosen, records = None, None, None, []
    for mode in ("global", "availability", "phase", "phase_availability"):
        references = EvidenceCalibrated.fit_reference(base, data["normal_calibration"], mode)
        for q in (.90,.925,.95,.96,.97,.98,.99,.995,.999):
            model = EvidenceCalibrated(base,mode,q,references)
            scores = model.from_raw(search["frame"],raw)
            stats = _stats(search["y"],scores,0.,search["scenario"])
            eligible = (stats["normal_false_positive_rate"] <= max(baseline["normal_false_positive_rate"]+.005,.03)
                and stats["precision"] >= baseline["precision"]-.02)
            record = {"mode":mode,"normal_quantile":q,"eligible":eligible,**stats}
            records.append(record)
            score = (stats["f1"],stats["macro_f1"],-stats["normal_false_positive_rate"])
            if eligible and (key is None or score > key): best,key,chosen=model,score,record
    if best is None: raise RuntimeError("No valid calibration")
    fitted["models"]["if"] = best
    new_selection = copy.deepcopy(selected)
    new_selection["selected"]["if"].update({**chosen,"threshold":0.,"calibration":best.record()})
    new_selection.update({"parent_model_sha256":selected["model_sha256"],
        "parent_final_results_informed_availability_calibration":True,
        "calibration_selection_on_search_only":True,"adapter_specific_thresholds":False})
    joblib.dump(fitted,OUTPUT/"models.joblib",compress=3)
    new_selection["model_sha256"] = sha256(OUTPUT/"models.joblib")
    write(OUTPUT/"selection.json",new_selection);write(OUTPUT/"calibration_search.json",records)
    print("Selected IF calibration",chosen,flush=True)
    # Confirmation after freezing; never used for selecting modes/quantiles.
    confirmation = data["confirmation"]
    old = joblib.load(OLD/"shared_models.joblib")["models"]
    rows=[]
    for a in ALGORITHMS:
        for method,detector,t in (("baseline",old[a+"_baseline"],old[a+"_baseline"].thresholds["0.03"]),
                                  ("improved",fitted["models"][a],fitted["models"][a].threshold)):
            rows.append({"algorithm":a,"method":method,**_stats(confirmation["y"],detector.score(confirmation["frame"]),t,confirmation["scenario"])})
    write(OUTPUT/"confirmation.json",{"used_to_reselect":False,"metrics":rows})


def evaluate():
    selected,fitted = load_models(OUTPUT)
    parent = read(PARENT/"summary.json")
    digest=selected["benchmark_manifest_sha256"]
    if sha256(ROOT/"data/evaluation/manifest.json") != digest: raise ValueError("Benchmark changed")
    cache=ROOT/"reports/model_research_v18/feature_cache"/digest[:16]
    datasets,all_y,all_scores=[],[],[]
    model=fitted["models"]["if"]
    for dataset in parent["datasets"]:
        name=dataset["dataset"]
        source=np.load(PARENT/(name+"_scores.npz"))
        scores=source["scores"].copy(); y=source["y"]
        offset=0
        for record in read(cache/name/"index.json"):
            path=cache/name/record["file"]
            if sha256(path) != record["sha256"]: raise ValueError("Feature cache changed")
            frame=joblib.load(path)["frame"]
            n=len(frame)
            scores[offset:offset+n,0]=model.from_raw(frame,scores[offset:offset+n,0])
            offset+=n
        assert offset==len(y)
        np.savez_compressed(OUTPUT/(name+"_scores.npz"),y=y,scores=scores,adapter=source["adapter"],case_index=source["case_index"])
        records=[]
        for i,a in enumerate(ALGORITHMS):
            records.append({"method":a,**_stats(y,scores[:,i],fitted["models"][a].threshold),
                "average_precision":float(average_precision_score(y,scores[:,i])),
                "roc_auc":float(roc_auc_score(y,scores[:,i])),"threshold":fitted["models"][a].threshold})
        datasets.append({"dataset":name,"metrics":records});all_y.append(y);all_scores.append(scores)
        print(name,[(r["method"],round(r["f1"]*100,2)) for r in records],flush=True)
    y,scores=np.concatenate(all_y),np.concatenate(all_scores)
    summary=copy.deepcopy(parent);summary["datasets"]=datasets
    summary["pooled"]=[{"method":a,**_stats(y,scores[:,i],fitted["models"][a].threshold),
        "average_precision":float(average_precision_score(y,scores[:,i])),"roc_auc":float(roc_auc_score(y,scores[:,i]))}
        for i,a in enumerate(ALGORITHMS)]
    summary["main_five_pooled"]=[]
    for i,a in enumerate(ALGORITHMS):
        arrays=[np.load(OUTPUT/(s+"_scores.npz")) for s in MAIN_SCENARIOS]
        yy=np.concatenate([v["y"] for v in arrays]);ss=np.concatenate([v["scores"][:,i] for v in arrays])
        summary["main_five_pooled"].append({"method":a,**_stats(yy,ss,fitted["models"][a].threshold)})
    for i,record in enumerate(summary["comparisons"]):
        record["improved_f1"]=summary["pooled"][i]["f1"]
        record["target_met"]=record["improved_f1"] >= record["before_adapter_f1"]
    summary["protocol"].update({"model_sha256":selected["model_sha256"],"observation_state_calibration_if_only":True,
                                "adapter_specific_thresholds":False,"reused_frozen_parent_raw_scores":True})
    write(OUTPUT/"summary.json",summary)
    sample=joblib.load(PARENT/"analysis_samples.joblib")
    sample["if_score"]=model.from_raw(sample,sample.if_score.to_numpy())
    joblib.dump(sample,OUTPUT/"analysis_samples.joblib",compress=3)
    print(summary["comparisons"],flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--stage",choices=("tune","evaluate"),required=True)
    # Canonical pickle module, never __main__.
    from shared_model_improvement.calibrate import tune,evaluate
    {"tune":tune,"evaluate":evaluate}[p.parse_args().stage]()
