"""No benchmark fitting. New estimator/calibration normal cases are disjoint."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,hashlib
import joblib
from shared_model_v7 import search_features as search
from shared_model_v7.expanded import prepare
from shared_model_v7 import independent_models as models
from shared_model_v7.independent_models import Setting,Detector
from model_research_v13.data import write


def settings(a):
    for weight in(.25,.5,1.,2.):
        for mode in('replace','max'):yield Setting(a,view='independent',joint_weight=weight,mode=mode,samples=128,neighbors=600)
    for weight in(.5,1.):
        yield Setting(a,view='radial',joint_weight=weight,mode='replace',samples=64,neighbors=600)
        yield Setting(a,view='independent',joint_weight=weight,mode='append',samples=128,neighbors=800,white=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    dev=prepare();normal=dev['normal_calibration']
    isfit=normal.case_id.astype(str).map(lambda x:int(hashlib.sha256(x.encode()).hexdigest()[:8],16)%2==0)
    source_cases=set(dev['train'].case_id);fit=normal.loc[isfit].reset_index(drop=True);cal=normal.loc[~isfit].reset_index(drop=True)
    assert not source_cases&set(normal.case_id);assert not set(fit.case_id)&set(cal.case_id)
    models.REFERENCE=joblib.load(search.OUT/'neighbourhood_1.joblib')['reference']
    dest=search.OUT/'independent_search';dest.mkdir(exist_ok=True)
    write(dest/'normal_split.json',{'reference_minutes':len(dev['train']),'estimator_fit_minutes':len(fit),'calibration_minutes':len(cal),
        'reference_estimator_case_intersection':0,'estimator_calibration_case_intersection':0,
        'normal_only':True,'source_split_not_chronological':True,'benchmark_rows_fit':False,'split':'stable sha256 normal case_id parity'})
    dev={**dev,'train':fit,'normal_calibration':cal};search.Detector=Detector;search.settings=settings
    search.run(args.algorithms,draws=1000,output=dest,development=dev)
