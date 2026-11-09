"""Separate normal reference, estimator and score-calibration cases.

We do not claim a chronological or unseen-source-template split. The cases
are disjoint, and none is a fixed-benchmark normal control or injected fault.
"""
import hashlib
import joblib
from shared_model_v7.protocol import ROOT,OUT,freeze,atomic_dump
from model_research_v13.data import read,write
from model_evaluation.data import sha256


def prepare():
    freeze();path=OUT/'independent_normal_cases.joblib';meta=OUT/'independent_normal_cases.json'
    if path.exists():
        receipt=read(meta);assert sha256(path)==receipt['sha256'];return joblib.load(path)
    source=ROOT/'reports/shared_model_v4_rank/development.joblib';dev=joblib.load(source);normal=dev['normal_calibration']
    isfit=normal.case_id.astype(str).map(lambda key:int(hashlib.sha256(key.encode()).hexdigest()[:8],16)%2==0)
    train=normal.loc[isfit].reset_index(drop=True);cal=normal.loc[~isfit].reset_index(drop=True)
    reference=joblib.load(OUT/'neighbourhood_1.joblib')['reference'];refcases=set(reference.tables[-1][1][()][1])
    assert not refcases&set(normal.case_id);assert not set(train.case_id)&set(cal.case_id)
    payload={**dev,'train':train,'normal_calibration':cal};atomic_dump(payload,path)
    write(meta,{'sha256':sha256(path),'source_sha256':sha256(source),'normal_reference_sha256':sha256(OUT/'neighbourhood_1.joblib'),
        'fit_minutes':len(train),'fit_cases':train.case_id.nunique(),'calibration_minutes':len(cal),'calibration_cases':cal.case_id.nunique(),
        'reference_fit_case_intersection':0,'reference_calibration_case_intersection':0,'fit_calibration_case_intersection':0,
        'benchmark_rows_or_labels_fit':False,'chronological_source_holdout_claimed':False,'unseen_source_template_holdout_claimed':False,
        'split':'entire normal cases by stable SHA256 case-id parity'})
    return payload
