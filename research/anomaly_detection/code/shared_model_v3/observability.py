"""Read-only paired-development audit; no results feed model selection."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import write
from shared_model_target.current_features import CURRENT
from adapter_feature_ablation.features import ADAPTER
from shared_model_v3.features import SEQUENCE
from shared_model_v3.sensitive import SMALL

def run():
    d=joblib.load(ROOT/'reports/shared_model_v3_sensitive/development.joblib');records=[]
    p=joblib.load(ROOT/'reports/shared_model_v2_release/models.joblib')
    for split in ('search','confirmation'):
        v=d[split];f=v['frame'].copy();f['_truth']=v['y'];f['_scenario']=v['scenario']
        keys=['case_id','window_start'];first=~f.duplicated(keys);last=~f.duplicated(keys,keep='last')
        x=f.loc[first].set_index(keys);n=f.loc[last].set_index(keys).reindex(x.index)
        cols=[*CURRENT,*(c for c in ADAPTER if c!='adapter_workload_z'),*SEQUENCE,*SMALL]
        same=np.isclose(x[cols].to_numpy(float),n[cols].to_numpy(float),atol=1e-12,rtol=1e-12,equal_nan=True).all(axis=1)
        for scenario in np.unique(x._scenario):
            mask=x._scenario.eq(scenario).to_numpy()&x._truth.to_numpy(bool)
            row={'split':split,'scenario':scenario,'positive_minutes':int(mask.sum()),
                'feature_identical_to_normal_pair':int((mask&same).sum()),
                'identical_fraction':float(same[mask].mean()),
                'no_current_completion_fraction':float((~x.loc[mask,'latency_assessed']).mean())}
            records.append(row)
    write(ROOT/'reports/shared_model_v3/observability.json',{'development_only':True,
        'not_absolute_Bayes_error_bound':True,'interpretation':'Identical in these numeric features; raw events may contain other information.',
        'records':records})
    print(pd.DataFrame(records).to_string(index=False),flush=True)

if __name__=='__main__':run()
