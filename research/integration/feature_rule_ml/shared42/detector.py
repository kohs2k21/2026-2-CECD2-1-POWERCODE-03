"""Same current raw features into frozen HR, numeric/observed SR and three MLs."""
from collections import Counter
from pathlib import Path
import csv
import gzip
import json
import time

import numpy as np

from ..common import InputFailure, new_dir, sha, write
from ..frozen_rule_library import apply_rule, combine_codes, named, state_counts
from ..previous_shared42 import score_lof
from .artifacts import DEFAULT, load_models, profile, seal
from .capture import Capture
from .features import materialize

ROLES = ('HR','observed_SR','numeric_SR')
ALGORITHMS = ('if','kmeans','lof')


def score_family(bundle, frame):
    ok = np.isfinite(frame[bundle['contract']['features']].to_numpy(float)).any(axis=1)
    scores = np.full(len(frame),np.nan)
    codes = np.full(len(frame),-1,np.int8)
    # Completely unavailable rows never get imputed into an evaluated healthy row.
    if ok.any():
        margin,flags,_ = bundle['model'].score(frame.loc[ok])
        scores[ok] = margin[:,2]
        codes[ok] = flags[:,2].astype(np.int8)
    return scores,codes


def intersections(family):
    counts = Counter()
    for i in range(len(family['HR'])):
        key = '+'.join(k for k,c in [('HR',family['HR']),('SR',family['numeric_SR']),('ML',family['ML'])] if c[i] == 1)
        counts[key or 'no_warning'] += 1
    return dict(counts)


def detect(manifest, output, release=DEFAULT, model_root=None, trusted=False, progress=None):
    started = time.perf_counter()
    if Path(output).exists():
        raise InputFailure('Use a new output directory; existing results are preserved')
    folder,p = profile(release)
    model_root = Path(model_root) if model_root else folder/'models'
    bundles, model_meta = load_models(model_root,p,trusted)
    c = Capture(manifest,p)
    features = materialize(c,p,progress)
    measured = time.perf_counter()
    out = new_dir(output)
    native, rule_stats, totals = {}, [], {}
    for short, fs in features.items():
        n = len(fs['frame'])
        states,scores = {},{}
        for algorithm in ALGORITHMS:
            obj = bundles[short+'_'+algorithm]
            scores[algorithm],states[algorithm] = score_lof(obj,fs['frame']) if algorithm == 'lof' else score_family(obj,fs['frame'])
        arrays = {r:[] for r in ROLES}
        fired, unavailable = [[] for _ in range(n)],[[] for _ in range(n)]
        for rule in p['definition']['rules']:
            if rule['table'] != p['definition']['schema']['tables'][short]['name']:
                continue
            j = fs['ids'].index(rule['feature_id'])
            if rule['role'] == 'HR' and not set(rule['evidence_ids']) <= c.confirmed_evidence:
                codes = np.where(fs['population'][:,j] | fs['selection_unknown'][:,j],-1,-2).astype(np.int8)
            else:
                codes = apply_rule(fs['exact'][rule['feature_id']],fs['population'][:,j],rule['dtype'],rule['condition'])
                codes[fs['selection_unknown'][:,j]] = -1
            arrays[rule['role']].append(codes)
            for i in np.flatnonzero(codes==1):
                fired[i].append(rule['id'])
            for i in np.flatnonzero(codes==-1):
                unavailable[i].append(rule['id'])
            rule_stats.append(dict(id=rule['id'],role=rule['role'],grain=short,states=state_counts(codes)))
        for role in ROLES:
            states[role] = combine_codes(arrays[role],n)
        states['SR'] = combine_codes([states['observed_SR'],states['numeric_SR']],n)
        states['ML'] = combine_codes([states[k] for k in ALGORITHMS],n)
        states['OR_numeric'] = combine_codes([states[k] for k in ('HR','numeric_SR','ML')],n)
        states['OR_all'] = combine_codes([states[k] for k in ('HR','SR','ML')],n)
        native[short] = states
        totals[short] = {k:state_counts(v) for k,v in states.items()}
        with gzip.open(out/(short+'_decisions.csv.gz'),'wt',newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['source_row_id','transaction_id','process_id','message_id',*states,'if_margin','kmeans_margin','lof_score','fired_rule_ids','unavailable_rule_ids','ML_unavailable_reason'])
            for i,row in fs['metadata'].iterrows():
                writer.writerow([row.row_id,row.transaction_id,row.process_id,row.message_id,*[named(v[i]) for v in states.values()],*[float(scores[k][i]) if np.isfinite(scores[k][i]) else '' for k in ALGORITHMS],json.dumps(fired[i]),json.dumps(unavailable[i]),'all_selected_features_unavailable' if states['ML'][i] == -1 else ''])
    tmeta = features['T']['metadata']
    # Missing T IDs remain individually addressable through their original row IDs.
    tkeys = [('transaction',tx) if tx else ('unlinked_source_row',str(row)) for tx,row in zip(tmeta.transaction_id,tmeta.row_id)]
    txpos = {tx:i for i,tx in enumerate(tkeys)}
    family,unlinked = {},Counter()
    active = {(s['grain'],s['role']) for s in rule_stats}
    for key in (*ROLES,*ALGORITHMS):
        all_codes = [[] for _ in tkeys]
        for short,methods in native.items():
            if key in ROLES and (short,key) not in active:
                continue
            for rowidx,(tx,code) in enumerate(zip(features[short]['metadata'].transaction_id,methods[key])):
                link = tkeys[rowidx] if short == 'T' else ('transaction',tx)
                if link not in txpos:
                    unlinked[short+':'+key] += 1
                    continue
                all_codes[txpos[link]].append(int(code))
        family[key] = np.array([1 if 1 in a else 0 if a and all(x==0 for x in a) else -1 for a in all_codes],np.int8)
    n = len(tkeys)
    family['SR'] = combine_codes([family['observed_SR'],family['numeric_SR']],n)
    family['ML'] = combine_codes([family[k] for k in ALGORITHMS],n)
    family['OR_numeric'] = combine_codes([family[k] for k in ('HR','numeric_SR','ML')],n)
    family['OR_all'] = combine_codes([family[k] for k in ('HR','SR','ML')],n)
    with gzip.open(out/'transaction_decisions.csv.gz','wt',newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['transaction_id','source_row_id',*family])
        for i,row in tmeta.iterrows():
            writer.writerow([row.transaction_id,row.row_id,*[named(v[i]) for v in family.values()]])
    summary = dict(transactions=n,native=totals,transaction={k:state_counts(v) for k,v in family.items()},numeric_overlap=intersections(family),unlinked_source_rows_by_method=dict(unlinked),rule_count=len(rule_stats),ML_fit_count=0,LLM_calls=0,raw_feature_calculation=True,independent_checks=sum(len(x['audits']) for x in features.values()),definition_sha=p['definition_sha'],models_sha=sha(model_root/'model_set.json'),capture_sha=c.manifest_sha,feature_seconds=measured-started,total_seconds=time.perf_counter()-started,post_completion_batch=True,real_time_latency_evaluated=False)
    write(out/'summary.json',summary)
    write(out/'rule_statistics.json',rule_stats)
    write(out/'feature_audit.json',{s:f['audits'] for s,f in features.items()})
    write(out/'execution.json',dict(source_pins=c.pins,definition_sha=p['definition_sha'],model_set=model_meta,score_direction=dict(if_margin='positive means warning',kmeans_margin='positive means warning',lof_score='larger is more anomalous; warning above saved threshold'),OR_policy='any warning; all evaluated nonwarning; otherwise unassessable',no_normal_certificate=True))
    # Portable exact NPZ values are useful for independent regression audits.
    for short,fs in features.items():
        np.savez_compressed(out/(short+'_features.npz'),values=fs['values'],population=fs['population'],selection_unknown=fs['selection_unknown'],feature_ids=np.asarray(fs['ids']))
    c.assert_unchanged()
    seal(out,'detection_run')
    return summary
