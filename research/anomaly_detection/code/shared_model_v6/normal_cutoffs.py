"""Normal-derived candidate cutoffs only; no benchmark labels are read."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
from model_research_v13.data import write
from shared_model_v6.protocol import OUT

def run():
    dest=OUT/'attempt01';pkg=joblib.load(dest/'models.joblib');cal=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')['normal_calibration']
    models={**pkg['models'],'alternative_IF':pkg['alternative_IF']};q=(.985,.99,.9925,.995,.9975,.999,.9995)
    values={a:[{'q':k,'threshold':float(v)}for k,v in zip(q,np.quantile(m.score(cal),q,method='higher'))]for a,m in models.items()}
    write(dest/'normal_cutoffs.json',{'normal_rows':len(cal),'test_labels_read':False,'candidates':values})
    print('Normal cutoff candidates saved; no test labels read',flush=True)

if __name__=='__main__':run()
