"""Check frozen artifacts and exercise both model families without datasets."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'code'))


def main():
    # Refuse hidden research inputs even if the original workspace is present.
    # Library metadata inside the virtualenv is not research input.
    def guard(event,args):
        if event!='open' or not isinstance(args[0],(str,bytes)):
            return
        path=Path(args[0]).resolve()
        if path.suffix.lower() not in {'.csv','.npz','.joblib','.pkl','.pickle'}:
            return
        if not path.is_relative_to(ROOT) and not path.is_relative_to(Path(sys.prefix)):
            raise RuntimeError('Hidden original/benchmark/cache input is forbidden')
    sys.addaudithook(guard)
    import joblib
    import numpy as np
    import pandas as pd
    from process_event_model.inference import load
    from event_workflow.__main__ import read_frame
    from research_workflow.short import inputs
    manifest=json.loads((ROOT/'model_manifest.json').read_text(encoding='utf-8'))
    results={}
    for entry in manifest['models']:
        path=ROOT/entry['destination']
        assert path.stat().st_size==entry['bytes'],entry['destination']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256'],entry['destination']
    actual={p.relative_to(ROOT).as_posix() for p in (ROOT/'models').rglob('*.joblib')}
    assert actual=={r['destination'] for r in manifest['models']}
    original=read_frame(ROOT/'examples/completed_process.json')
    native,completed=inputs(read_frame(ROOT/'examples/minute_grid.json'),read_frame(ROOT/'examples/minute_completions.json'))
    for algorithm in ('if','kmeans','lof'):
        model=ROOT/'models/latest_process_rows/event_models.joblib'
        result=load(model,algorithm,with_incidents=True).predict(original)
        assert len(result)==len(original)
        assert result.transactionId.tolist()==original.transactionId.tolist()
        assert np.isfinite(result.score).all()
        assert result.reference_level.eq(4).all()
        old=joblib.load(ROOT/'models/previous_minute'/(algorithm+'_shared.joblib'))
        previous=old.predict(native,completed)
        assert len(previous)==len(native) and np.isfinite(previous.score).all()
        results[algorithm]={'latest_rows':len(result),'previous_minutes':len(previous),'unseen_adapter_passed':True}
    print(json.dumps({'passed':True,'model_files':4,'algorithm_models':6,'reads_original_or_benchmark_data':False,'results':results},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
