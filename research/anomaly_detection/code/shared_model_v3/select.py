"""Freeze a joint-F1/Recall candidate before full benchmark scoring."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import __main__
import joblib
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_improvement.run import ALGORITHMS,_stats
from shared_model_v3.sensitive_detector import Detector as SensitiveDetector
from shared_model_v3.features import SequenceReference
from shared_model_v3.adaptive import AdaptiveReference
from shared_model_v3.sensitive import SensitiveReference

BASE=ROOT/'reports/shared_model_v2_release'

def load(path):
    # First sensitive prototype ran as __main__; canonicalize on new export.
    old=getattr(__main__,'Detector',None);__main__.Detector=SensitiveDetector
    try:m=joblib.load(path)
    finally:
        if old is None:delattr(__main__,'Detector')
        else:__main__.Detector=old
    if type(m).__module__=='__main__':m.__class__=SensitiveDetector
    return m

def select(out,sources):
    out=Path(out).resolve();sources=[Path(s).resolve()for s in sources];out.mkdir(exist_ok=True)
    if(out/'selection.json').exists():raise FileExistsError('Already frozen')
    pkg=joblib.load(BASE/'models.joblib');d=joblib.load(ROOT/'reports/shared_model_v3/development.joblib')
    folds=('search','confirmation');y=np.concatenate([d[k]['y']for k in folds]);chosen={}
    for a in ALGORITHMS:
        old=pkg['models'][a];ss=np.concatenate([old.score(d[k]['frame'])for k in folds]);bm=_stats(y,ss,old.threshold)
        before={k:_stats(d[k]['y'],old.score(d[k]['frame']),old.threshold)for k in folds}
        best=None;rank=(0.,bm['f1'],bm['recall']);record={'from_parent':True,**bm,'threshold':old.threshold}
        for source in sources:
            rows=read(source/(a+'_search.json'))
            for r in rows:
                if r['settings'].get('flow',False):continue
                eligible=all(r['folds'][k]['normal_false_positive_rate']<=before[k]['normal_false_positive_rate']for k in folds)
                key=(min(r['f1']-bm['f1'],r['recall']-bm['recall']),r['f1'],r['recall'])
                if eligible and key>rank:
                    rank=key;best=(source,r);record={**r,'source':str(source.relative_to(ROOT)),
                        'development_F1_gain_pp':(r['f1']-bm['f1'])*100,'development_recall_gain_pp':(r['recall']-bm['recall'])*100}
        if best:
            source,r=best;base=load(source/(a+'_candidate_'+str(r['candidate'])+'.joblib'))
            data=joblib.load(source/'development.joblib');normal=data['normal_calibration']
            ref=NormalReference.fit(normal,base.score(normal),r['mode'],r['alpha'],r['kind'])
            model=CalibratedDetector(base,ref,r['threshold'])
            pooled=_stats(y,np.concatenate([model.score(data[k]['frame'])for k in folds]),model.threshold)
            assert pooled=={k:r[k]for k in pooled}
            pkg['models'][a]=model
        chosen[a]=record
    pkg['adapter_reference']=SensitiveReference(AdaptiveReference(SequenceReference(pkg['adapter_reference'])))
    joblib.dump(pkg,out/'models.joblib',compress=3)
    write(out/'selection.json',{'selected':chosen,'model_sha256':sha256(out/'models.joblib'),
        'parent_sha256':sha256(BASE/'models.joblib'),'benchmark_manifest_sha256':sha256(ROOT/'data/evaluation/manifest.json'),
        'normal_only_estimator_fit':True,'development_labels_for_joint_F1_recall_selection':True,
        'final_benchmark_labels_read_during_selection':False,'previously_viewed_benchmark_not_external_blind':True,
        'adapter_experts':False,'single_estimator_each':True,'benchmark_modified':False,
        'flow_features_excluded_due_to_historical_control_counter_alignment':True})
    print('FROZEN',[(a,r.get('source','parent'),r['f1'],r['recall'])for a,r in chosen.items()],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--sources',nargs='+',type=Path,required=True)
    a=p.parse_args();select(a.output,a.sources)
