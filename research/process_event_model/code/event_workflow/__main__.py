"""CSV/JSON inference and keyed evaluation for original Process-row models."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from process_event_model.features import timestamps
from process_event_model.inference import load
from process_event_model.models import metrics

KEYS = ['case_id', 'adapterType', 'processId', 'window_start']


def read_frame(path):
    path = Path(path)
    if path.suffix.lower() in {'.json', '.jsonl', '.ndjson'}:
        text = path.read_text(encoding='utf-8-sig')
        value = json.loads(text) if path.suffix.lower() == '.json' else [json.loads(s) for s in text.splitlines() if s.strip()]
        return pd.DataFrame(value)
    return pd.read_csv(path, encoding='utf-8-sig')


def binary(values, name):
    if values.isna().any() or not values.isin([False, True, 0, 1]).all():
        raise ValueError(f'{name} must contain boolean/0/1 values, not strings or missing labels')
    return values.astype(bool)


def evaluate_rows(predictions, labels, decision='anomaly'):
    """One-to-one event IDs; never compare row metrics with minute metrics."""
    if 'event_row_id' not in labels or 'is_anomaly' not in labels:
        raise ValueError('Row labels require event_row_id and is_anomaly')
    p = predictions[['event_row_id', decision]].copy()
    y = labels[['event_row_id', 'is_anomaly']].copy()
    if p.empty or p.event_row_id.isna().any() or y.event_row_id.isna().any():
        raise ValueError('Nonempty predictions and non-null IDs required')
    p['event_row_id'], y['event_row_id'] = p.event_row_id.astype(str), y.event_row_id.astype(str)
    joined = p.merge(y, on='event_row_id', how='outer', validate='one_to_one', indicator=True)
    if not joined._merge.eq('both').all():
        raise ValueError('Every prediction needs exactly one label, with no extra labels')
    return {**metrics(binary(joined.is_anomaly, 'is_anomaly'), binary(joined[decision], decision)),
            'evaluation_unit': 'original Process execution', 'decision': decision, 'point_adjustment': False}


def minute_predictions(predictions, windows, monitor):
    """Project already causal execution features onto caller-supplied windows.

    Windows are evaluation queries, not fabricated Process records. An empty
    interval never opens an incident. Case IDs isolate independent episodes.
    """
    from process_event_model.minute_refinement import MinuteReplay
    if monitor.incidents is None:
        raise ValueError('Minute evaluation requires the frozen incident policy')
    if set(KEYS) - set(windows):
        raise ValueError(f'Minute windows require {KEYS}')
    f, w = predictions.copy(), windows[KEYS].copy()
    if f.empty or w.empty or w[KEYS].isna().any().any():
        raise ValueError('Nonempty Process history and non-null windows required')
    w['window_start'] = timestamps(w.window_start)
    if not w.window_start.eq(w.window_start.dt.floor('min')).all():
        raise ValueError('Minute windows must start on a minute boundary')
    for frame in (f, w):
        for name in KEYS[:3]:
            frame[name] = frame[name].astype(str)
        frame['_uid'] = [json.dumps(list(k), ensure_ascii=False) for k in frame[KEYS[:3]].itertuples(index=False, name=None)]
    if w.duplicated(KEYS).any():
        raise ValueError('Duplicate minute query')
    if set(w._uid) - set(f._uid):
        raise ValueError('Minute query has no Process history; input-stop onset is out of scope')
    # Ignore streams not queried; retain all history for the queried streams.
    f = f.loc[f._uid.isin(w._uid)].copy()
    w['_end'] = (w.window_start + pd.Timedelta(minutes=1)).astype('datetime64[ns, UTC]').astype('int64') / 1e9
    f['_time'] = f.availableTime.astype('datetime64[ns, UTC]').astype('int64') / 1e9
    families = monitor.detector.state(f)[0] if monitor.incidents.policy.traffic_model_family else None
    alarm = MinuteReplay(f, w).project(f.score.to_numpy(float), monitor.incidents.policy, families=families)
    result = w[KEYS].copy()
    result['anomaly'] = alarm
    return result


def evaluate_minutes(predictions, labels):
    if set(KEYS + ['is_anomaly']) - set(labels):
        raise ValueError('Minute labels require case_id, adapterType, processId, window_start, is_anomaly')
    p, y = predictions.copy(), labels[KEYS + ['is_anomaly']].copy()
    for frame in (p, y):
        frame['window_start'] = timestamps(frame.window_start)
        for col in KEYS[:3]:
            frame[col] = frame[col].astype(str)
    if p.empty or y[KEYS].isna().any().any():
        raise ValueError('Nonempty predictions and non-null keys required')
    joined = p.merge(y, on=KEYS, how='outer', validate='one_to_one', indicator=True)
    if not joined._merge.eq('both').all():
        raise ValueError('Every minute prediction needs exactly one label')
    return {**metrics(binary(joined.is_anomaly, 'is_anomaly'), binary(joined.anomaly, 'anomaly')),
            'evaluation_unit': 'final causal minute alert', 'point_adjustment': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('predict', 'evaluate'))
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--algorithm', choices=('if', 'kmeans', 'lof'), required=True)
    parser.add_argument('--processes', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--labels', type=Path)
    parser.add_argument('--unit', choices=('row', 'minute'), default='row')
    parser.add_argument('--decision', choices=('anomaly', 'confirmed_anomaly'), default='anomaly')
    parser.add_argument('--normal-only-baseline', action='store_true', help='No incident policy; only for separately refitted baseline')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--trust-model', action='store_true', required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('Output exists; choose a new path')
    if args.command == 'evaluate' and args.labels is None:
        parser.error('Evaluation requires --labels')
    if args.unit == 'minute' and (args.command != 'evaluate' or args.normal_only_baseline):
        parser.error('Minute unit requires evaluate with the frozen policy')
    if args.expected_sha256 and hashlib.sha256(args.model.read_bytes()).hexdigest() != args.expected_sha256:
        raise ValueError('Model hash mismatch')
    records = read_frame(args.processes)
    if records.empty:
        raise ValueError('No completed Process rows supplied')
    if args.command == 'evaluate' and args.unit == 'row' and 'event_row_id' not in records:
        raise ValueError('Row evaluation requires explicit stable event_row_id in input and labels')
    monitor = load(args.model, args.algorithm, with_incidents=not args.normal_only_baseline)
    result = monitor.predict(records)
    payload = {'evaluation_unit': 'original Process execution',
               'predictions': json.loads(result.to_json(orient='records', date_format='iso'))}
    if args.command == 'evaluate':
        labels = read_frame(args.labels)
        if args.unit == 'row':
            payload['metrics'] = evaluate_rows(result, labels, args.decision)
        else:
            projected = minute_predictions(result, labels, monitor)
            payload['metrics'] = evaluate_minutes(projected, labels)
            payload['minute_predictions'] = json.loads(projected.to_json(orient='records', date_format='iso'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'rows': len(result), 'output': str(args.output), 'metrics': payload.get('metrics')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
