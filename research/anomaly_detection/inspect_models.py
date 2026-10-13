"""Hash-check exact published models; pickle loading needs explicit --trusted."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for part in iter(lambda:handle.read(1024*1024),b''): value.update(part)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trusted',action='store_true',help='Explicitly trust repository model files as executable pickle input')
    parser.add_argument('--smoke',action='store_true',help='Check synthetic prediction shapes, not accuracy')
    args = parser.parse_args()
    if args.smoke and not args.trusted: parser.error('--smoke requires --trusted')
    inventory = json.loads((ROOT/'model_inventory.json').read_text(encoding='utf-8'))
    sys.path.insert(0,str(ROOT/'code'))
    for entry in inventory['models']:
        path = ROOT/entry['path']
        if not path.resolve().is_relative_to(ROOT) or sha256(path)!=entry['sha256']:
            raise ValueError('Model target/hash mismatch')
        print(f"SHA-256 PASS: {entry['path']} ({entry['bytes']:,} bytes)",flush=True)
        if not args.trusted: continue
        import joblib
        import numpy as np
        import pandas as pd
        from process_anomaly_pipeline.data import prepare
        import process_anomaly_pipeline.pipeline as local_pipeline
        assert Path(local_pipeline.__file__).resolve().is_relative_to(ROOT)
        model = joblib.load(path)
        demo = [{'transactionId':'DEMO-1','processId':'DEMO-P','dependProcessId':None,
                 'processHubId':'DEMO-H','adapterType':'DB.READER','channelId':'DEMO-C',
                 'status':'S','startTime':'2026-10-13T12:00:00.000','endTime':'2026-10-13T12:00:00.100',
                 'successCount':1,'errorCount':0,'totalCount':1,'retryCount':0,'responseCode':'0000'}]
        if isinstance(model,local_pipeline.ProcessAnomalyPipeline):
            model = local_pipeline.ProcessAnomalyPipeline.load(path,trusted=True)
            if args.smoke: assert len(model.predict(demo))==1
            print('LOAD PASS: existing Process IF',flush=True)
        elif isinstance(model,dict) and set(model)=={'row','window','baselines'}:
            if args.smoke:
                values = model['row'].score(prepare(pd.DataFrame(demo)))
                assert values[0].shape==(1,6) and np.isfinite(values[0]).all()
                from model_evaluation.data import window_features
                native = pd.DataFrame({'case_id':['DEMO'],'adapterType':['DB.READER'],'processId':['DEMO-P'],
                    'window_start':['2026-10-13T12:00:00'],'window_end':['2026-10-13T12:01:00'],
                    'execution_arrivals':[1],'history_median_arrivals':[1],'history_p95_arrivals':[3],
                    'duration_p50_ms':[100],'duration_p95_ms':[100]})
                values = model['window'].score(window_features(native,model['baselines']))
                assert values[0].shape==(1,6) and np.isfinite(values[0]).all()
            print(f"LOAD PASS: row + {model['window'].config.window_minutes}m window, six strategies each",flush=True)
        else: raise ValueError('Unexpected model artifact')
    if not args.trusted: print('No pickle loaded. Use --trusted only for files you trust.')


if __name__=='__main__': main()
