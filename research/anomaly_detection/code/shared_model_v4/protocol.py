"""Explicit immutable task targets; no relaxed FP margins."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
from model_research_v13.data import read,write
from model_evaluation.data import sha256

def freeze():
    out=ROOT/'reports/shared_model_v4';out.mkdir(exist_ok=True)
    log=read(ROOT/'reports/shared_model_v3_release/summary.json')['pooled']
    ad=read(ROOT/'reports/shared_model_v2_release/adbench_results.json')['results']
    ad=[r for r in ad if r['version']=='improved']
    protocol={'target_F1':.60,'algorithms':['if','kmeans','lof'],
        'log_false_positive_caps':{r['method']:r['fp']for r in log},
        'adbench_false_positive_caps':{r['method']:r['fp']for r in ad},
        'log_baseline':log,'adbench_baseline':ad,
        'benchmark_manifest_sha256':sha256(ROOT/'data/evaluation/manifest.json'),
        'ADBench_npz_sha256':sha256(ROOT/'data/adbench_1000/dataset.npz'),
        'training_or_fitting_on_fixed_test_rows':False,'benchmark_or_labels_modified':False,
        'drop_hard_or_difficult_minutes':False,'point_adjustment':False,
        'previously_viewed_benchmarks_adaptive_research_not_blind':True,
        'ADBench_protocol_pending_user_choice':'default: same-service-model transfer; separate retraining only if user selects it'}
    path=out/'protocol.json'
    if path.exists():assert read(path)==protocol
    else:write(path,protocol)
    print('Frozen FP budgets',protocol['log_false_positive_caps'],protocol['adbench_false_positive_caps'])

if __name__=='__main__':freeze()
