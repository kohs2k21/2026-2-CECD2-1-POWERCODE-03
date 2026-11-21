"""Fresh replay cases after freeze, not used for model/threshold selection."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/"code"))
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import read,write,trusted_cache
from model_evaluation.data import sha256
from model_improvement.data import validation_inputs
from model_research_v15.inference import ProcessResearchMonitor as Processor
from shared_model_improvement.run import _stats,ALGORITHMS


def aligned_truth(frame,source):
    keys=pd.MultiIndex.from_frame(frame[["case_id","window_start"]])
    oracle=source.set_index(["case_id","window_start"]).window_is_anomaly
    if oracle.index.duplicated().any():raise ValueError("Ambiguous truth keys")
    aligned=oracle.reindex(keys)
    if aligned.isna().any():raise ValueError("Unmapped confirmation window")
    return aligned.to_numpy(bool)


def confirm(folder,seed=41_100_042):
    folder=Path(folder).resolve();selected=read(folder/"selection.json")
    assert sha256(folder/"models.joblib")==selected["model_sha256"]
    package=joblib.load(folder/"models.joblib")
    path=folder/"fresh_confirmation_v2_inputs.joblib"
    if path.exists():
        info=read(folder/"fresh_confirmation_v2_inputs.json");assert sha256(path)==info["sha256"]
        assert info['seed']==seed
        bank=joblib.load(path)
    else:
        bundle=read(ROOT/"reports/evaluation_v2_work/inputs.json")
        state_path=ROOT/"reports/window_search_v2/training_inputs.joblib"
        state=trusted_cache(state_path,read(state_path.parent/"selection.json")["training_inputs_sha256"])
        native=validation_inputs(bundle,state,count=120,seed=seed)
        processor=Processor(None,package["feature_context"],"if");bank={}
        for name,inputs in native.items():
            frames,truth=[],[]
            for cohort in ("observed","normal"):
                source=inputs[cohort]
                clean=source.drop(columns=["window_is_anomaly","scenario_types","event_ids","validation_split"],errors="ignore")
                frame=processor.features(clean,inputs[cohort+"_rows"])
                frames.append(package["adapter_reference"].transform(frame))
                truth.append(aligned_truth(frame,source)if cohort=="observed"else np.zeros(len(frame),bool))
            bank[name]={"frame":pd.concat(frames,ignore_index=True),"y":np.concatenate(truth)}
        joblib.dump(bank,path,compress=3)
        write(folder/"fresh_confirmation_v2_inputs.json",{"sha256":sha256(path),"seed":seed,"cases_per_scenario":120,
            "paired_normal_controls":True,"generated_after_model_freeze":True,"same_source_generator_not_external_blind":True,
            "generator":"original validation generator; no mild-v2 CSV difficulty adjustment",
            "truth_aligned_by_case_and_window":True,"obsolete_unaligned_files_preserved_not_used":True})
    records=[];all_y=[];all_s=[]
    for name,values in bank.items():
        scores=np.column_stack([package["models"][a].score(values["frame"])for a in ALGORITHMS])
        records.append({"dataset":name,"metrics":[{"method":a,**_stats(values["y"],scores[:,i],package["models"][a].threshold)}for i,a in enumerate(ALGORITHMS)]})
        all_y.append(values["y"]);all_s.append(scores)
    y,scores=np.concatenate(all_y),np.concatenate(all_s)
    pooled=[{"method":a,**_stats(y,scores[:,i],package["models"][a].threshold)}for i,a in enumerate(ALGORITHMS)]
    write(folder/"fresh_confirmation_v2.json",{"used_to_reselect":False,"seed":seed,"datasets":records,"pooled":pooled,
        "model_sha256":selected["model_sha256"]})
    print("Fresh frozen confirmation",pooled,flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);p.add_argument('--seed',type=int,default=41_100_042)
    args=p.parse_args();confirm(args.output,args.seed)
