"""Repair our trusted local experiment's __main__ pickle binding, not its model.

Recomputes exact saved development confusion counts before replacing the local
artifact, preserving the original pickle for traceability. Not for remote input.
"""
import json
import shutil

import joblib
import numpy as np

from process_event_model.family_weights import FamilyConfidenceDetector
from process_event_model.data import ROOT,write_json,digest
from process_event_model.minute_refinement import MinuteReplay,EventPolicy,results


def run():
    folder=ROOT/'reports/process_event_model/family_event'
    path=folder/'event_models.joblib';old_sha=digest(path)
    package=joblib.load(path)
    selection=json.loads((folder/'selection.json').read_text(encoding='utf-8'))
    data=joblib.load(folder/'minute_development.joblib');replay=MinuteReplay(data['frame'],data['windows'])
    for a,model in package['models'].items():
        assert type(model).__module__=='process_event_model.family_weights'
        recomputed=results(data,replay.project(model.predict(data['frame'])[0],EventPolicy(**selection['models'][a]['policy'])))
        for task,m in recomputed.items():
            for k,v in m.items():assert np.isclose(v,selection['models'][a]['tasks'][task][k]),(a,task,k)
    backup=folder/'event_models_nonportable_before_repair.joblib'
    if not backup.exists():shutil.copy2(path,backup)
    joblib.dump(package,path,compress=3)
    write_json(folder/'portability_repair.json',dict(original_sha256=old_sha,
        portable_sha256=digest(path),development_metrics_exactly_reproduced=True,
        classes='process_event_model.family_weights.FamilyConfidenceDetector',
        mathematical_parameters_unchanged=True))
    print('Canonical pickle repaired; saved development metrics reproduced',flush=True)


if __name__=='__main__':run()
