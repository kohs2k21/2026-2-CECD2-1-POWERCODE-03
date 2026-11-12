"""Explicit previous Feature/Rule release + 42 features + frozen IF/KMeans + new LOF.

Uses the SHA-pinned feature store of the SAME archived original TEST capture.
This command is a repeatable offline replay, not a raw-log streaming service.
Only --train-lof permits fitting. Existing IF/KMeans, rules and LLMs never fit.
"""
from pathlib import Path
from collections import Counter, defaultdict
from decimal import Decimal
import csv
import gzip
import importlib.metadata
import json
import sys
import time

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from .common import InputFailure, ML_CODE, digest, new_dir, pin, read, seal, sha, write
from .frozen_rule_library import compile_library, apply_rule, combine_codes, named, state_counts
sys.path.insert(0, str(ML_CODE))
from model_research_v5.models import Encoder, RawModel, Settings
from process_anomaly_pipeline.features import ScoreNormalizer

SHORT = {'EC_TRANSACTION_MONITORING': 'T', 'EC_PROCESS_MONITORING': 'P', 'EC_MESSAGE_MONITORING': 'M'}


def prepare_frame(values, populations, ids, metadata, contract):
    if list(ids) != list(dict.fromkeys(ids)) or values.shape != populations.shape or values.shape[1] != len(ids) or len(metadata) != len(values):
        raise InputFailure('Feature matrix/row dimensions or feature IDs differ')
    if np.isinf(values).any() or any(f not in ids for f in contract['features']):
        raise InputFailure('Invalid values or selected feature absent')
    frame = pd.DataFrame(values, columns=ids)
    for c in metadata.columns: frame[c] = metadata[c].to_numpy()
    for i, f in enumerate(ids): frame['population__' + f] = populations[:, i].astype(bool)
    for f in contract['features']:
        if frame.loc[~frame['population__' + f], f].notna().any():
            raise InputFailure('Out-of-population value cannot be numerical')
    for f in contract['missing_indicators']: frame['missing__' + f] = (frame[f].isna() & frame['population__' + f]).astype(float)
    for f in contract['population_indicators']: frame['in_population__' + f] = frame['population__' + f].astype(float)
    frame['phase'] = 'steady'
    return frame


def fit_lof(train, calibration, threshold, contract, output):
    """Original input representation; Github novelty LOF with bounded PR98 fit policy."""
    before = time.perf_counter()
    columns = contract['columns']
    valid = np.isfinite(train[contract['features']].to_numpy(dtype=float)).any(axis=1)
    encoder = Encoder.fit(train.loc[valid], columns)
    encoded = encoder.transform(train.loc[valid])
    unique = np.unique(np.round(encoded, 6), axis=0)
    distinct = len(unique)
    if distinct < 2: raise InputFailure('LOF needs at least two distinct training vectors; no fake rows')
    if distinct > 8192:
        unique = unique[np.random.default_rng(42).choice(distinct, 8192, replace=False)]
    settings = Settings(algorithm='lof', neighbors=50, seed=42)
    with threadpool_limits(limits=2):
        raw = RawModel(settings).fit(unique, encoder.transform(calibration))
        cal_valid = np.isfinite(calibration[contract['features']].to_numpy(float)).any(axis=1)
        cut_valid = np.isfinite(threshold[contract['features']].to_numpy(float)).any(axis=1)
        cal = raw.score(encoder.transform(calibration.loc[cal_valid]))
        cut = raw.score(encoder.transform(threshold.loc[cut_valid]))
    if not len(cal) or not len(cut): raise InputFailure('Missing separate calibration/threshold reference')
    normalizer = ScoreNormalizer.fit(cal)
    cutoff = float(np.quantile(normalizer.transform(cut), .99))
    bundle = dict(encoder=encoder, raw=raw, normalizer=normalizer, threshold=cutoff,
                  contract=contract, feature_contract_sha=contract['sha256'])
    out = new_dir(output)
    joblib.dump(bundle, out / 'model.joblib', compress=3)
    metadata = dict(algorithm='lof', grain=contract['table'], features=len(contract['features']),
        vector_dimensions=len(columns), original_train_rows=len(train), usable_train_rows=int(valid.sum()),
        distinct_encoded_train_vectors=distinct, fit_vectors=len(unique), effective_neighbors=raw.models[0].n_neighbors_,
        policy='TRAIN-only Encoder; PR98 round6/unique/cap8192; Github RawModel novelty=True; no synthetic fit rows',
        calibration_rows=int(cal_valid.sum()), threshold_rows=int(cut_valid.sum()),
        calibration_date='2026-08-20', threshold_date='2026-08-21', threshold_quantile=.99,
        score_direction='larger normalized score is more anomalous; warning iff > threshold',
        normalized_threshold=cutoff, fit_seconds=time.perf_counter() - before,
        test_used_in_fit=False, model=pin(out / 'model.joblib'), settings=vars(settings),
        input_contract_sha=contract['sha256'], source_code=[pin(ML_CODE / 'model_research_v5/models.py'), pin(ML_CODE / 'process_anomaly_pipeline/features.py')])
    write(out / 'model.json', metadata)
    return bundle, metadata


def score_lof(bundle, frame):
    assessed = np.isfinite(frame[bundle['contract']['features']].to_numpy(float)).any(axis=1)
    score = np.full(len(frame), np.nan)
    positions = np.flatnonzero(assessed)
    with threadpool_limits(limits=2):
        for start in range(0, len(positions), 8192):
            pos = positions[start:start + 8192]
            score[pos] = bundle['normalizer'].transform(bundle['raw'].score(bundle['encoder'].transform(frame.iloc[pos])))
    if not np.isfinite(score[assessed]).all(): raise InputFailure('Nonfinite LOF score')
    return score, np.where(assessed, (score > bundle['threshold']).astype(np.int8), -1)


def run_previous(release, llm_manifest, output, train_lof=False, trusted=False, lof_models=None):
    if not trusted or bool(train_lof) == bool(lof_models):
        raise InputFailure('Explicit trust and exactly one of LOF training or frozen model root required')
    start = time.perf_counter(); out = new_dir(output); release = Path(release).resolve()
    archive = {x['path']: x['sha256'] for x in read(release / 'FINAL_ARTIFACT_MANIFEST.json')['files']}
    checked = {}
    def verify(file):
        file = Path(file).resolve()
        if str(file) not in archive or sha(file) != archive[str(file)]: raise InputFailure('Archived artifact changed: ' + str(file))
        checked[str(file)] = archive[str(file)]
        return file
    def source(item):
        if sha(item['path']) != item['sha256']: raise InputFailure('Preserved source changed: ' + item['path'])
        checked[item['path']] = item['sha256']
    environment = read(verify(release / 'audit/environment.json'))
    for name in ('numpy', 'pandas', 'scipy', 'scikit-learn', 'joblib', 'threadpoolctl'):
        if importlib.metadata.version(name) != environment['packages'][name]: raise InputFailure('Saved ML dependency version differs: ' + name)
    for file in ('implementation/gh_ml_core.py', 'implementation/shared_normalizer.py', 'implementation/inference.py'):
        verify(release / file)
    # Only the original trusted class module is needed to deserialize previous models.
    sys.path.insert(0, str(release / 'implementation'))
    import inference as old_inference
    contracts = read(verify(release / 'models/input_contracts.json'))
    if sum(len(c['features']) for c in contracts) != 42: raise InputFailure('Expected preserved 42-definition ML contract')
    rules = read(verify(release / 'inputs/prior_validated_rules.json'))
    records = read(verify(release / 'inputs/regression65.json'))
    library = compile_library(rules, records)
    application_policy = read(verify(release / 'inputs/rule_application_contract.json'))
    source(application_policy['application_source_manifest']); source(application_policy['original_rules'])
    original_llm = read(llm_manifest)
    feature_adapter = Path(original_llm['Feature_adapter_fixed'])
    rule_adapter = Path(original_llm['Rule_adapter_selected']['adapter'])
    llm_pins = [pin(Path(llm_manifest))]
    for adapter in (feature_adapter, rule_adapter):
        llm_pins += [pin(adapter / name) for name in ('adapter_model.safetensors', 'adapter_config.json')]
    for item in original_llm['Rule_adapter_selected']['files']: source(item)
    # Read-only feature caches have already undergone 195 exact independent full-vector checks.
    feature_audit = read(verify(release / 'audit/feature_measurement.json'))
    if not feature_audit['passed'] or not feature_audit['195_full_vector_independent_checks'] or any(feature_audit['transaction_family_overlap'].values()):
        raise InputFailure('Feature cache lacks original full-scope source/measurement admission')
    for split in ('train', 'validation', 'test'):
        mf = read(verify(release / 'inputs' / (split + '_full_manifest.json')))
        if mf['children_truncated'] or mf['body_values_copied']: raise InputFailure('Capture is incomplete or contains Body')
        # Resolve from the original project path of the pinned cohort manifest.
        for item in mf['source_files']:
            candidates = [ancestor / item['path'] for ancestor in Path(application_policy['application_source_manifest']['path']).parents]
            actual = next((p for p in candidates if p.is_file()), None)
            if actual is None or sha(actual) != item['sha256']: raise InputFailure('Raw split source SHA differs')
            checked[str(actual)] = item['sha256']
    write(out / 'selected_profile.json', dict(contract='previous-Feature-Rule-shared42-LOF1.0',
        Feature_adapter=str(feature_adapter), Rule_adapter=str(rule_adapter), LLM_pins=llm_pins,
        LLM_generation_invoked=False, rule_release=pin(release / 'inputs/prior_validated_rules.json'),
        rules=library['roles'], total_rules=len(library['rules']), ML_definition_count=42,
        ML_columns_by_grain={c['table']:len(c['columns']) for c in contracts},
        IF_KMeans='unchanged previous weights', LOF='new LOF-only training' if train_lof else 'frozen previous42 LOF replay, no fit',
        fixed_input_replay=True, prior_Process6_models_used=False, application_policy=application_policy))
    def load_frame(split, short, contract):
        file = verify(release / 'features' / (split + '_full_' + short + '.npz'))
        with np.load(file, allow_pickle=False) as z: vals, pop, ids = z['values'].copy(), z['population'].copy(), z['feature_ids'].tolist()
        rows = pd.read_csv(verify(release / 'features' / (split + '_full_' + short + '_rows.csv.gz')), dtype=str, keep_default_na=False)
        if rows.row_id.duplicated().any(): raise InputFailure('Repeated original row key')
        return prepare_frame(vals, pop, ids, rows, contract), rows, vals, pop, ids
    native = {}; rule_statistics = []; model_meta = []; fired_tables = {}; row_frames = {}; timing = {}
    byid = {r['id']:r['input']['feature'] for r in records}
    for contract in contracts:
        short = SHORT[contract['table']]
        train, _, _, _, _ = load_frame('train', short, contract)
        validation, _, _, _, _ = load_frame('validation', short, contract)
        test, rows, values, population, ids = load_frame('test', short, contract)
        row_frames[short] = rows
        calibration = validation.loc[validation.family_date.eq('2026-08-20')]
        threshold = validation.loc[validation.family_date.eq('2026-08-21')]
        if not len(calibration) or not len(threshold): raise InputFailure('Original calibration/threshold split missing')
        timing[short] = {}; states = {}; scores = {}; rule_arrays = defaultdict(list); fired = [[] for _ in range(len(test))]
        before = time.perf_counter()
        for alg in ('if','kmeans'):
            model_file = verify(release / 'models' / (short + '_' + alg + '.joblib'))
            bundle = old_inference.load(model_file, archive[str(model_file)])
            if digest({k:v for k,v in contract.items() if k!='sha256'}) != contract['sha256']: raise InputFailure('Input contract digest differs')
            result = old_inference.predict_feature_frame(bundle, test, contract['sha256'])
            score = result['ensemble_margin'].to_numpy(float)
            states[alg] = np.where(result['assessed'], result['ensemble_anomaly'].to_numpy(np.int8), -1)
            scores[alg] = score
            previous = pd.read_csv(verify(release / 'predictions' / ('test_' + short + '_' + alg + '.csv.gz')))
            if not np.array_equal(previous[alg+'_ensemble_anomaly'].to_numpy(bool), result['ensemble_anomaly'].to_numpy(bool)) or not np.allclose(previous[alg+'_ensemble_margin'], score, equal_nan=True, atol=1e-12, rtol=1e-12):
                raise InputFailure('Original IF/KMeans frozen predictions changed')
        timing[short]['IF_KMeans_inference_seconds'] = time.perf_counter() - before
        if train_lof:
            lof, info = fit_lof(train, calibration, threshold, contract, out / 'models' / (short + '_lof'))
        else:
            folder = Path(lof_models) / (short + '_lof')
            info = read(folder / 'model.json')
            source(info['model'])
            for code in info['source_code']: source(code)
            if info['input_contract_sha'] != contract['sha256']: raise InputFailure('Frozen LOF feature contract differs')
            lof = joblib.load(folder / 'model.joblib')
            if lof['feature_contract_sha'] != contract['sha256']: raise InputFailure('LOF serialized input contract differs')
        model_meta.append(info)
        before = time.perf_counter(); score, states['lof'] = score_lof(lof, test); scores['lof'] = score
        timing[short]['LOF_inference_seconds'] = time.perf_counter() - before
        loaded = joblib.load(info['model']['path'])
        again, again_states = score_lof(loaded, test)
        if not np.array_equal(score, again, equal_nan=True) or not np.array_equal(states['lof'],again_states): raise InputFailure('LOF saved/reloaded scores differ')
        before = time.perf_counter()
        for r in library['rules']:
            record_id = r['id'].rsplit(':',1)[0]
            if record_id not in ids: continue
            f = byid[record_id]; j = ids.index(record_id)
            vector = [None if not np.isfinite(v) else bool(v) if f['dtype']=='boolean' else Decimal(str(v)) for v in values[:,j]]
            codes = apply_rule(vector, population[:,j], f['dtype'], r['original']['decision']['condition'])
            rule_arrays[r['role']].append(codes)
            for i in np.flatnonzero(codes==1):fired[i].append(r['id'])
            rule_statistics.append(dict(id=r['id'], role=r['role'], grain=short, condition=r['original']['decision']['condition'], states=state_counts(codes)))
        timing[short]['rule_evaluation_seconds'] = time.perf_counter() - before
        for role in ('HR','observed_SR','numeric_SR'): states[role] = combine_codes(rule_arrays[role], len(test))
        states['SR'] = combine_codes([states['observed_SR'],states['numeric_SR']],len(test))
        states['ML'] = combine_codes([states[a] for a in ('if','kmeans','lof')],len(test))
        states['OR_all'] = combine_codes([states[k] for k in ('HR','SR','ML')],len(test))
        states['OR_numeric'] = combine_codes([states[k] for k in ('HR','numeric_SR','ML')],len(test))
        native[short] = states; fired_tables[short] = fired
        with gzip.open(out / (short + '_decisions.csv.gz'),'wt',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['source_row_id','transaction_id',*states,'if_margin','kmeans_margin','lof_score','fired_rule_ids'])
            for i,row in rows.iterrows():writer.writerow([row.row_id,row.transaction_id,*[named(v[i]) for v in states.values()],*[float(scores[a][i]) if np.isfinite(scores[a][i]) else '' for a in ('if','kmeans','lof')],json.dumps(fired[i])])
        print(json.dumps({'grain_completed':short,'LOF_fit_seconds':info['fit_seconds'],'LOF_fit_vectors':info['fit_vectors'],'summary':{k:state_counts(v) for k,v in states.items()}}),flush=True)
    # Verify all rule activations against the archived exact Decimal evaluator.
    prior_stats = {r['rule_key']:r for r in read(verify(release/'reports/comparison.json'))['rule_statistics']}
    for s in rule_statistics:
        if s['states'].get('warning',0) != prior_stats[s['id']]['flagged']: raise InputFailure('Rule activation differs from original exact Decimal run: '+s['id'])
    trows=row_frames['T']; tx_order=trows.transaction_id.tolist(); txpos={tx:i for i,tx in enumerate(tx_order)}
    family={}
    for key in ('HR','observed_SR','numeric_SR','if','kmeans','lof'):
        allcodes=[[] for _ in tx_order]
        for short,methods in native.items():
            if key=='numeric_SR' and not any(s['grain']==short and s['role']=='numeric_SR' for s in rule_statistics):continue
            for tx,code in zip(row_frames[short].transaction_id,methods[key]): allcodes[txpos[tx]].append(int(code))
        family[key]=np.array([1 if 1 in c else 0 if c and all(x==0 for x in c) else -1 for c in allcodes],np.int8)
    n=len(tx_order)
    family['SR']=combine_codes([family[k] for k in ('observed_SR','numeric_SR')],n)
    family['ML']=combine_codes([family[a] for a in ('if','kmeans','lof')],n)
    family['OR_all']=combine_codes([family[k] for k in ('HR','SR','ML')],n)
    family['OR_numeric']=combine_codes([family[k] for k in ('HR','numeric_SR','ML')],n)
    with gzip.open(out/'transaction_decisions.csv.gz','wt',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['transaction_id',*family])
        for i,tx in enumerate(tx_order):writer.writerow([tx,*[named(v[i]) for v in family.values()]])
    for file,expected in {**checked,**{x['path']:x['sha256'] for x in llm_pins}}.items():
        if sha(file)!=expected:raise InputFailure('Existing artifact changed during run')
    summary=dict(native={g:{k:state_counts(v) for k,v in m.items()} for g,m in native.items()},
        transaction={k:state_counts(v) for k,v in family.items()},rule_roles=library['roles'],
        rule_count=len(rule_statistics),ML_features=42,LOF_models_trained=3 if train_lof else 0,LOF_models_used=3,IF_KMeans_retrained=False,
        frozen_feature_cache_reused=True,raw_feature_recalculation_in_this_run=False,
        Rule_LLM_called=False,Feature_LLM_called=False,selected_previous_LLM_weights=llm_pins,
        existing_artifacts_preserved=True,independent_fault_labels=False)
    write(out/'summary.json',summary);write(out/'rule_statistics.json',rule_statistics)
    write(out/'LOF_training.json',model_meta);write(out/'timing.json',dict(grains=timing,total_seconds=time.perf_counter()-start,includes_new_LOF_training=bool(train_lof)))
    write(out/'source_verification.json',dict(checked=checked,original_models_preserved=True,IF_KMeans_predictions_reproduced=True,all110_rule_counts_reproduced=True,LOF_saved_reloaded_exact=True))
    seal(out,'previous_shared42_with_LOF')
    return summary
