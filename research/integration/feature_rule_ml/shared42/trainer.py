"""Explicit CPU retraining of all nine ML models; never mutates Rule/LLM weights."""
from pathlib import Path
import time

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from ..common import InputFailure, new_dir, sha, write, read
from ..previous_shared42 import fit_lof, score_lof
from .artifacts import DEFAULT, environment, profile, runtime_path, runtime_fingerprints, seal
from .capture import Capture
from .features import materialize


def train(train_manifest, calibration_manifest, threshold_manifest, output, release=DEFAULT):
    before = time.perf_counter()
    if Path(output).exists():
        raise InputFailure('Use a new output directory; existing models are preserved')
    _, p = profile(release)
    captures = [Capture(x, p) for x in (train_manifest, calibration_manifest, threshold_manifest)]
    if [x.manifest.get('split') for x in captures] != ['train','calibration','threshold']:
        raise InputFailure('Trainer requires explicit train/calibration/threshold roles; test is forbidden')
    if any(not row['TRANSACTION_ID'] or row['TRANSACTION_ID'] not in c.family_ids for c in captures for rows in c.rows.values() for row in rows):
        raise InputFailure('Trainer references need linkable transaction families; review orphan/missing IDs first')
    for a, b in ((0,1),(0,2),(1,2)):
        if captures[a].family_ids & captures[b].family_ids:
            raise InputFailure('Transaction family leakage across Trainer references')
        if captures[a].manifest['period']['end_exclusive'] > captures[b].manifest['period']['start_inclusive']:
            raise InputFailure('Trainer reference periods overlap or are not chronological')
    feature_sets = [materialize(c, p) for c in captures]
    root = new_dir(output)
    out = new_dir(root / 'models')
    runtime_path()
    from gh_ml_core import Family, Settings
    models, audits = {}, []
    for short in 'TPM':
        contract = p['definition']['ml_contracts'][short]
        parts = {name: f[short]['frame'] for name, f in zip(('train','calibration','threshold'), feature_sets)}
        coverage = {}
        for name, frame in list(parts.items()):
            ok = np.isfinite(frame[contract['features']].to_numpy(float)).any(axis=1)
            parts[name] = frame.loc[ok].copy()
            coverage[name] = dict(all_rows=len(frame), usable_rows=int(ok.sum()), excluded_all_unavailable=int((~ok).sum()))
            if ok.sum() < 8:
                raise InputFailure('At least eight usable rows in every grain/reference; not a statistical adequacy certificate')
        support = {a: {name: int(f.loc[f.adapterType.eq(a),'transaction_id'].nunique()) for name,f in parts.items()} for a in set().union(*(set(f.adapterType) for f in parts.values()))}
        if short == 'T':
            support = {}
        for algorithm in ('if','kmeans'):
            started = time.perf_counter()
            obj = Family(contract['columns'], Settings(algorithm=algorithm,samples=512,rotations=1,k=6,shape_distance=False,seed=42))
            with threadpool_limits(limits=2):
                obj.fit(parts, support)
                obj.set_operating_point(.99,.5,False)
            bundle = dict(model=obj, contract=contract, feature_contract_sha=contract['sha256'])
            file = short + '_' + algorithm + '.joblib'
            joblib.dump(bundle, out / file, compress=3)
            restored = joblib.load(out / file)
            for a,b in zip(obj.score(parts['threshold']), restored['model'].score(parts['threshold'])):
                np.testing.assert_array_equal(a,b)
            models[short+'_'+algorithm] = dict(file=file, sha256=sha(out/file), format='legacy_family')
            audits.append(dict(grain=short,algorithm=algorithm,coverage=coverage,fit_seconds=time.perf_counter()-started,saved_reloaded_exact=True))
        # Existing novelty LOF and its separate normalization/threshold recipe.
        bundle, meta = fit_lof(*(parts[n] for n in ('train','calibration','threshold')), contract, root / ('training_' + short + '_lof'))
        restored = joblib.load(root / ('training_' + short + '_lof') / 'model.joblib')
        detail_path = root / ('training_' + short + '_lof') / 'model.json'
        detail = read(detail_path)
        detail['calibration_date'] = captures[1].manifest['period']
        detail['threshold_date'] = captures[2].manifest['period']
        write(detail_path,detail)
        for a,b in zip(score_lof(bundle,parts['threshold']),score_lof(restored,parts['threshold'])):
            np.testing.assert_array_equal(a,b)
        file = short + '_lof.joblib'
        joblib.dump(bundle,out/file,compress=3)
        models[short+'_lof'] = dict(file=file,sha256=sha(out/file),format='novelty_lof')
        # Dates come from this invocation, not the legacy LOF recipe's historical labels.
        audits.append(dict(grain=short,algorithm='lof',coverage=coverage,fit_seconds=meta['fit_seconds'],fit_vectors=meta['fit_vectors'],effective_neighbors=meta['effective_neighbors'],saved_reloaded_exact=True))
    write(out/'model_set.json',dict(contract='shared42-model-set1.0',definition_sha=p['definition_sha'],environment=environment(),runtime_fingerprints=runtime_fingerprints(),models=models,origin='explicit_shared42_trainer',references=[dict(manifest_sha=c.manifest_sha,period=c.manifest['period'],split=c.manifest['split']) for c in captures],rules_changed=False,LLM_fit_count=0,test_used_in_fit=False,unlabeled_reference_not_certified_normal=True))
    seal(out,'model_set')
    for c in captures:
        c.assert_unchanged()
    summary = dict(new_ML_fit_count=9,LLM_fit_count=0,rules_changed=False,models_directory='models',audits=audits,total_seconds=time.perf_counter()-before,source_pins=[c.pins for c in captures],features_independently_verified=195)
    write(root/'training.json',summary)
    seal(root,'training_run')
    return summary
