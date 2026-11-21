"""Export each shared detector with the actual Process feature pipeline."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,importlib.metadata,platform
import joblib
import numpy as np
from model_evaluation.data import sha256
from model_research_v13.data import read,write
from shared_model_target.inference import Monitor
from shared_model_v2.run import prepare


def export(folder):
    folder=Path(folder).resolve();selection=read(folder/'selection.json')
    assert sha256(folder/'models.joblib')==selection['model_sha256']
    package=joblib.load(folder/'models.joblib');sample=prepare()['train'].iloc[:200].copy()
    dest=folder/'handoff';dest.mkdir(exist_ok=True);records=[]
    for a,detector in package['models'].items():
        model=Monitor(detector,package['adapter_reference'],package['feature_context'])
        path=dest/(a+'_shared.joblib');joblib.dump(model,path,compress=3)
        restored=joblib.load(path)
        np.testing.assert_array_equal(model.predict_features(sample).score,restored.predict_features(sample).score)
        records.append({'algorithm':a,'file':path.name,'bytes':path.stat().st_size,'sha256':sha256(path),
            **detector.record(),'roundtrip_score_identical':True})
    write(dest/'manifest.json',{'models':records,'single_estimator_each':True,'adapter_experts':False,
       'model_source_sha256':selection['model_sha256'],'requires_modules':['shared_model_v2','shared_model_target',
          'shared_model_improvement','adapter_feature_ablation','model_research_v15','model_research_v13','model_research_v8'],
       'input':'chronological contiguous minute grid + completed Process rows, past context retained',
       'runtime_reads_benchmark':False,'stateful_worker_implemented':False,'production_deployed':False,
       'environment':{'python':platform.python_version(),**{p:importlib.metadata.version(p)for p in('numpy','pandas','scipy','scikit-learn','joblib')}}})
    print('Exported three single shared monitors',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);export(p.parse_args().output)
