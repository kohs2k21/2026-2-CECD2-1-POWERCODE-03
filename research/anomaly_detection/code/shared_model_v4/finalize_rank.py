"""Before full test, apply both fold-relative and absolute operational FPR caps.

The absolute rate comes from the previous release, not new model test scores.
No fixed full-test candidate results are read by this selector.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
from model_research_v13.data import read,write
from shared_model_v4.run_rank import OUT,prepare
from shared_model_target.calibration import CalibratedDetector,NormalReference
from shared_model_improvement.run import ALGORITHMS
from shared_model_v4.evaluate_log import freeze

def run():
    if(OUT/'models.joblib').exists():raise FileExistsError('Frozen bundle must not be reselected')
    protocol=read(ROOT/'reports/shared_model_v4/protocol.json');baseline={r['method']:r for r in protocol['log_baseline']};d=prepare();cal=d['normal_calibration']
    for a in ALGORITHMS:
        assert(OUT/(a+'_selected.joblib')).exists(),'Finish original search before finalizing'
        records=read(OUT/(a+'_search.json'));cap=baseline[a]['normal_false_positive_rate']
        eligible=[r for r in records if r['eligible']and r['normal_false_positive_rate']<=cap]
        record=max(eligible,key=lambda r:(r['f1'],-r['normal_false_positive_rate']))
        base=joblib.load(OUT/(a+'_candidate_'+str(record['candidate'])+'.joblib'));raw=base.score(cal)
        ref=NormalReference.fit(cal,raw,record['mode'],record['alpha'],record['kind']);model=CalibratedDetector(base,ref,record['threshold'])
        joblib.dump(model,OUT/(a+'_final.joblib'),compress=3)
        write(OUT/(a+'_final_selection.json'),{**record,'additional_absolute_development_FPR_cap':cap,'full_new_test_seen_before_this_choice':False})
        print('Before-full-test final',a,record['candidate'],record['f1'],record['normal_false_positive_rate'],flush=True)
    # Do not overwrite original search winners. The release bundle explicitly
    # uses final selections after the stricter pre-test absolute rate screen.
    old=joblib.load(ROOT/'reports/shared_model_v3_release/models.joblib');models={a:joblib.load(OUT/(a+'_final.joblib'))for a in ALGORITHMS}
    from model_evaluation.data import sha256
    joblib.dump({**old,'models':models},OUT/'models.joblib',compress=3)
    write(OUT/'selection.json',{'model_sha256':sha256(OUT/'models.joblib'),'benchmark_manifest_sha256':protocol['benchmark_manifest_sha256'],
        'selected_on':'separate development only; old relative fold caps AND old absolute pooled FPR cap',
        'models':{a:models[a].record()for a in ALGORITHMS},'protocol':protocol,
        'new_test_rows_fit':0,'thresholds_frozen_before_full_test':True})

if __name__=='__main__':run()
