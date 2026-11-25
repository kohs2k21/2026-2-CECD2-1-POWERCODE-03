import argparse
import contextlib
import importlib.metadata
import io
import json
import platform
import subprocess
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score
from threadpoolctl import threadpool_limits

from .datasets import GUTENTAG_COMMIT, TSB_COMMIT, guten_case, process_reference, sha, source_version, tsb_case
from .models import fit_detectors


def metrics(model, base, changed, labels):
    started = time.perf_counter()
    scores = model.score(changed)
    seconds = time.perf_counter() - started
    predicted = scores > model.threshold
    paired = model.predict(base)
    mask = labels.astype(bool)
    tp = int((predicted & mask).sum()); fp = int((predicted & ~mask).sum())
    fn = int((~predicted & mask).sum()); tn = int((~predicted & ~mask).sum())
    precision = tp / max(1, tp+fp); recall = tp / max(1, tp+fn)
    start = int(np.flatnonzero(mask)[0]); hits = np.flatnonzero(predicted & mask)
    # The interval is never expanded to turn one hit into a full detected interval.
    return {"tp":tp,"fp_against_injection_mask":fp,"fn":fn,"tn_against_injection_mask":tn,
        "precision_against_injection_mask":precision,"injected_recall":recall,
        "f1_against_injection_mask":2*precision*recall/max(1e-12, precision+recall),
        "average_precision":float(average_precision_score(labels, scores)),
        "roc_auc":float(roc_auc_score(labels, scores)),
        "paired_background_alarm_rate":float(paired.mean()),
        "outside_injection_alarm_rate":float(predicted[~mask].mean()),
        "paired_interval_alarm_rate":float(paired[mask].mean()),
        "additional_injected_coverage":float((predicted & ~paired)[mask].mean()),
        "event_hit":bool(hits.size),"paired_event_hit":bool(paired[mask].any()),
        "first_hit_delay_samples":int(hits[0]-start) if hits.size else None,
        "test_rows":len(base),"injected_rows":int(mask.sum()),"score_seconds":seconds,
        "threshold":model.threshold,"fit_seconds":model.fit_seconds,
        "outside_values_preserved":bool(np.array_equal(base[~mask], changed[~mask]))}


def microbenchmark(model, values):
    result = []
    for batch in [1,32,256]:
        model.score(values[:batch])
        timings=[]
        for _ in range(100):
            started=time.perf_counter();model.score(values[:batch]);timings.append(time.perf_counter()-started)
        result.append({"batch":batch,"calls":len(timings),"rows":batch*len(timings),
            "throughput_rows_per_second":batch*len(timings)/sum(timings),
            "call_p50_ms":float(np.quantile(timings,.5)*1000),
            "call_p95_ms":float(np.quantile(timings,.95)*1000)})
    return result


def execute(args):
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    config=json.loads(Path(args.config).read_text())
    (out/'config.json').write_text(json.dumps(config,indent=2))
    versions={"gutentag":source_version(args.gutentag_source,GUTENTAG_COMMIT),
              "tsb_uad":source_version(args.tsb_source,TSB_COMMIT)}
    manifest={"status":"running","config_sha256":sha(out/'config.json'),"upstream":versions,
        "repository_head":subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        "environment":{"python":platform.python_version(),"platform":platform.platform(),
            "packages":{p:importlib.metadata.version(p) for p in ['numpy','pandas','scipy','scikit-learn','timeeval-GutenTAG','psutil','matplotlib']}},
        "load_scope":"local IF inference and bounded memory queue; no production DB/API/GPU/SSE",
        "started_unix":time.time(),"source_hashes":{name:sha(Path(__file__).with_name(name)) for name in ["run.py","datasets.py","models.py","load.py"]}}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2))
    start=time.perf_counter()
    train,cal,real_base,detail=process_reference(args.input)
    manifest['real_csv']=detail
    results=[];micro=[];timing=[]
    for family in ['gutentag','tsb_csv']:
        for seed in config['seeds']:
            if family=='gutentag':
                started=time.perf_counter()
                with contextlib.redirect_stderr(io.StringIO()):
                    fit,calibration,_,_,_,_=guten_case(seed,config['gutentag_offsets'][0],config['interval_lengths'][0],config)
                timing.append({'family':family,'seed':seed,'stage':'generate_reference','seconds':time.perf_counter()-started})
            else:fit,calibration=train,cal
            models=fit_detectors(fit,calibration,seed,config)
            for length in config['interval_lengths']:
                magnitudes=config['gutentag_offsets'] if family=='gutentag' else config['tsb_add_ms']
                for magnitude in magnitudes:
                    started=time.perf_counter()
                    if family=='gutentag':
                        with contextlib.redirect_stderr(io.StringIO()):
                            candidate_train,candidate_cal,base,changed,labels,cfg=guten_case(seed,magnitude,length,config)
                        if not np.array_equal(candidate_train,fit) or not np.array_equal(candidate_cal,calibration):
                            raise ValueError('generation changed train/calibration across scenarios')
                    else:
                        base=real_base
                        changed,labels=tsb_case(base,seed,length,magnitude,args.tsb_source)
                    generation=time.perf_counter()-started
                    for model in models:
                        row={"family":family,"kind":"mean_shift" if family=='gutentag' else 'latency_add',
                            "seed":seed,"length":length,"magnitude":magnitude,"detector":model.kind,
                            "phase":"primary","threshold_quantile":config["calibration_quantile"],
                            "label_scope":"synthetic_ground_truth" if family=='gutentag' else "injection_only_original_truth_unknown",
                            "generation_seconds":generation,**metrics(model,base,changed,labels)}
                        assert row['outside_values_preserved']
                        results.append(row)
                    if seed==config['seeds'][0] and length==200 and magnitude==(2 if family=='gutentag' else 6000):
                        np.savez_compressed(out/(family+'-representative.npz'),train=fit,calibration=calibration,
                            base=base,changed=changed,labels=labels)
                        joblib.dump(models[0],out/(family+'-iforest.joblib'))
                        for model in models:
                            for entry in microbenchmark(model,changed):micro.append({'family':family,'detector':model.kind,**entry})
                        if family=='gutentag':(out/'gutentag-example-config.json').write_text(json.dumps(cfg,indent=2))
            if family=='tsb_csv':
                changed,labels=tsb_case(real_base,seed,config['tsb_flip_length'],0,args.tsb_source,kind='flip')
                for model in models:
                    results.append({'family':family,'kind':'flip_order_only','seed':seed,'length':config['tsb_flip_length'],
                        'magnitude':0,'detector':model.kind,'phase':'primary','threshold_quantile':config['calibration_quantile'],'label_scope':'injection_only_original_truth_unknown',
                        'generation_seconds':None,**metrics(model,real_base,changed,labels)})
            if family=='tsb_csv':
                for quantile in config.get('exploratory_quantiles',[]):
                    for model in models:
                        model.threshold=float(np.quantile(model.score(calibration),quantile))
                    for length in config['interval_lengths']:
                        for magnitude in config['tsb_add_ms']:
                            changed,labels=tsb_case(real_base,seed,length,magnitude,args.tsb_source)
                            for model in models:
                                results.append({'family':family,'kind':'latency_add','seed':seed,'length':length,
                                    'magnitude':magnitude,'detector':model.kind,'phase':'exploratory',
                                    'threshold_quantile':quantile,'label_scope':'injection_only_original_truth_unknown',
                                    'generation_seconds':None,**metrics(model,real_base,changed,labels)})
                    changed,labels=tsb_case(real_base,seed,config['tsb_flip_length'],0,args.tsb_source,kind='flip')
                    for model in models:
                        results.append({'family':family,'kind':'flip_order_only','seed':seed,'length':config['tsb_flip_length'],
                            'magnitude':0,'detector':model.kind,'phase':'exploratory','threshold_quantile':quantile,
                            'label_scope':'injection_only_original_truth_unknown','generation_seconds':None,**metrics(model,real_base,changed,labels)})
            pd.DataFrame(results).to_csv(out/'quality.csv',index=False)
            print(json.dumps({'phase':'quality','family':family,'seed':seed,'completed_cases':len(results)}),flush=True)
    pd.DataFrame(micro).to_csv(out/'microbenchmark.csv',index=False)
    (out/'generation-timing.json').write_text(json.dumps(timing,indent=2))
    manifest.update(status='quality_complete',quality_seconds=time.perf_counter()-start,finished_unix=time.time(),
        result_rows=len(results),artifact_hashes={p.name:sha(p) for p in out.glob('*.joblib')})
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--config',type=Path,default=Path(__file__).with_name('config.json'))
    parser.add_argument('--gutentag-source',required=True,type=Path)
    parser.add_argument('--tsb-source',required=True,type=Path)
    with threadpool_limits(limits=1):execute(parser.parse_args())
