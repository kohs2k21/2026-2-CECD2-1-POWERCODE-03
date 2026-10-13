"""Portable research handoff: v12 cadence-routed IF and KMeans candidates.

No operating backend/model is overwritten. Continuous per-stream minute grids,
completed Process logs, and a prior history baseline are required upstream.
"""
import copy,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
import pandas as pd
from model_evaluation.data import sha256
from model_evaluation.windowing import PriorHistory
from model_improvement.features import build_features
from model_improvement.refine import with_legacy,frozen as frozen_v4
from model_research_v5.features import context_features
from model_research_v8.features import add_duration_features
from model_research_v8.bridge import frozen as frozen_v10
from model_research_v8.compat import legacy_classes


class ProcessResearchMonitor:
    def __init__(self,family,reference,baseline,legacy_baselines,history,algorithm):
        self.family,self.reference,self.baseline,self.legacy_baselines,self.history,self.algorithm=family,reference,baseline,legacy_baselines,history,algorithm

    def features(self,native,completions):
        # Input is Process-derived; no Transaction table or anomaly labels.
        # Caller must supply a contiguous grid including empty minutes and all
        # available prefix context. Each case_id is one monitoring stream.
        unique=native.groupby('case_id')[['adapterType','processId']].nunique()
        if unique.gt(1).any().any():raise ValueError('One Adapter+Process per monitoring stream required')
        frame=context_features(with_legacy(build_features(native,completions,self.history,self.baseline),self.legacy_baselines,self.history))
        return add_duration_features(frame,completions,self.reference)

    def score_features(self,frame):return self.family.score(frame)

    def predict(self,native,completions):
        frame=self.features(native,completions);margin,pred,covered=self.family.score(frame)
        result=frame[['case_id','adapterType','processId','window_start']].copy()
        for i,name in enumerate(('global','adapter','ensemble')):
            result[name+'_margin']=margin[:,i];result[name+'_anomaly']=pred[:,i]
        result['expert_available']=covered
        result['traffic_assessed']=frame.traffic_assessed
        result['latency_assessed']=frame.latency_assessed
        return result


def load(path,expected_sha256):
    path=Path(path)
    if sha256(path)!=expected_sha256:raise ValueError('Model hash does not match trusted manifest')
    return joblib.load(path)


def export():
    from model_research_v8.regime import frozen
    suite,selection=frozen()
    folder=ROOT/'reports/model_research_v12/handoff';folder.mkdir(exist_ok=True)
    history=PriorHistory(ROOT/'data/evaluation/reference_history_windows.csv')
    results=[]
    for algorithm,family in suite.families.items():
        family=copy.deepcopy(family)
        if algorithm=='if':
            # Not needed at inference; do not ship synthetic normal reference
            # DataFrames/case IDs as part of the backend model artifact.
            for attr in ('calibration','threshold','normal'):
                if hasattr(family.established,attr):delattr(family.established,attr)
        monitor=ProcessResearchMonitor(family,suite.reference,suite.baseline,suite.legacy_baselines,history,algorithm)
        path=folder/(algorithm+'_v12_candidate.joblib')
        joblib.dump(monitor,path,compress=3)
        results.append({'algorithm':algorithm,'file':path.name,'bytes':path.stat().st_size,'sha256':sha256(path),
                        'status':'Research candidate, not production deployment',
                        'synthetic_traffic_history':True,'reference_calibration_dataframe_removed':algorithm=='if'})
    import importlib.metadata as metadata
    record={'models':results,'dependencies':{p:metadata.version(p) for p in ('numpy','pandas','scikit-learn','scipy','joblib')},
            'requires_module':'model_research_v8.inference','operating_model_unchanged':True,
            'input':'Process-derived minute grid and completed rows; no Transaction table',
            'caution':'Synthetic prior traffic history must be replaced/validated against real operating history before production.'}
    (folder/'manifest.json').write_text(json.dumps(record,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(record,indent=2,ensure_ascii=False))


if __name__=='__main__':
    # Canonical module namespace, not __main__, in exported pickle classes.
    from model_research_v8.inference import export
    export()
