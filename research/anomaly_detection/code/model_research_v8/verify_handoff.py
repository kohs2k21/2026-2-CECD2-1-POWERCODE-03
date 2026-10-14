"""Fresh-process portable loading and exact saved-prediction reproduction."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import numpy as np
import pandas as pd
from model_research_v8.inference import load
from model_research_v8.features import FEATURES
from model_improvement.refine import ALL_FEATURES as OLD_FEATURES
from model_evaluation.tune_windows import write_json


def main():
    folder=ROOT/'reports/model_research_v12/handoff'
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'));records=[]
    for record in manifest['models']:
        monitor=load(folder/record['file'],record['sha256']);n=0
        for name in ('individual_delay','input_stop','mixed_hard'):
            for cohort in ('observed','normal'):
                with np.load(ROOT/'reports/model_research_v8'/name/f'{cohort}.npz',allow_pickle=False) as v8,np.load(ROOT/'reports/model_improvement_v4'/name/f'{cohort}.npz',allow_pickle=False) as v4:
                    ids=np.linspace(0,len(v8['truth'])-1,100,dtype=int)
                    frame=pd.DataFrame(v4['features'][ids],columns=OLD_FEATURES,dtype=float)
                    for i,col in enumerate(FEATURES):frame[col]=v8['features'][ids,i]
                    frame['adapterType'],frame['phase']=v8['adapter'][ids],v8['phase'][ids]
                    for col in ('traffic_assessed','latency_assessed'):frame[col]=v8[col][ids]
                    margins,pred,covered=monitor.score_features(frame)
                    version='model_research_v12'
                    with np.load(ROOT/'reports'/version/name/f'{cohort}.npz',allow_pickle=False) as expected:
                        start=0 if record['algorithm']=='if' else 3
                        np.testing.assert_array_equal(pred,expected['prediction'][ids,start:start+3])
                        np.testing.assert_allclose(margins,expected['margin'][ids,start:start+3],rtol=2e-6,atol=2e-6)
                        np.testing.assert_array_equal(covered,expected['covered'][ids])
                    n+=len(ids)
        records.append({'algorithm':record['algorithm'],'decisions':n,'saved_predictions_equal':True,'portable_class':monitor.__class__.__module__,
                        'calibration_dataframes_absent':not hasattr(monitor.family,'calibration')})
    write_json(folder/'verification.json',{'results':records,'loaded_without_legacy_alias':True});print(records)


if __name__=='__main__':main()
