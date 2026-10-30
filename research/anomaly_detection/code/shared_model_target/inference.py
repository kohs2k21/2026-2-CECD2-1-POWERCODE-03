"""One shared estimator + normal reference statistics per algorithm."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/"code"))
import joblib
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from model_research_v15.inference import ProcessResearchMonitor as Processor
from shared_model_target.run_current import prepare


class Monitor:
    def __init__(self,detector,reference,context):
        self.detector,self.reference=detector,reference
        self.processor=Processor(None,context,detector.algorithm)
    def features(self,native,completed):return self.reference.transform(self.processor.features(native,completed))
    def predict_features(self,frame):
        scores,alarms=self.detector.predict(frame)
        result=frame[["case_id","adapterType","processId","window_start","adapter_reference_level","adapter_reference_support"]].copy()
        result["score"],result["threshold"],result["anomaly"]=scores,self.detector.threshold,alarms
        return result
    def predict(self,native,completed):return self.predict_features(self.features(native,completed))


def export(folder):
    folder=Path(folder).resolve();selection=read(folder/"selection.json")
    assert sha256(folder/"models.joblib")==selection["model_sha256"]
    package=joblib.load(folder/"models.joblib");output=folder/"handoff";output.mkdir(exist_ok=True)
    sample=prepare()["train"].iloc[:180];records=[]
    for a,detector in package["models"].items():
        monitor=Monitor(detector,package["adapter_reference"],package["feature_context"])
        path=output/(a+"_shared_current.joblib");joblib.dump(monitor,path,compress=3)
        loaded=joblib.load(path)
        np.testing.assert_array_equal(monitor.predict_features(sample).score,loaded.predict_features(sample).score)
        records.append({"algorithm":a,"file":path.name,"bytes":path.stat().st_size,"sha256":sha256(path),**detector.record(),
                        "save_load_score_identical":True})
    write(output/"manifest.json",{"models":records,"requires_module":"shared_model_target.inference",
        "runtime_reads_benchmark":False,"production_deployed":False,"single_estimator_per_algorithm":True,"adapter_experts":False,
        "input":"Process-derived chronological contiguous minute grid + completed Process rows; past context retained",
        "stateful_worker_implemented":False,"formal_false_alarm_guarantee":False,
        "adapter_reference_duration_window":"current one-minute P95; count context is past 5m mean totalCount"})
    print("Three target monitors exported",flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True)
    from shared_model_target.inference import export
    export(p.parse_args().output)
