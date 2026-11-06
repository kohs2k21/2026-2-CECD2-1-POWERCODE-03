"""Relative, sealed artifacts. Verification precedes every trusted pickle load."""
from pathlib import Path
import platform
import sys
import importlib.metadata

from ..common import InputFailure, RESEARCH, dependencies, digest, read, sha, write

DEFAULT = Path(__file__).resolve().parent / 'release'
CONTRACT = 'shared42-trainer-detector1.0'


def environment():
    return dict(python=platform.python_version(), packages={**dependencies(), 'threadpoolctl': importlib.metadata.version('threadpoolctl')})


def runtime_fingerprints():
    files = ('integration/feature_rule_ml/shared42/compat/gh_ml_core.py',
             'integration/feature_rule_ml/shared42/compat/shared_normalizer.py',
             'anomaly_detection/code/model_research_v5/models.py',
             'anomaly_detection/code/process_anomaly_pipeline/features.py',
             'models/feature_rule_spec/specmodel/engine.py',
             'models/feature_rule_spec/specmodel/primitives.py',
             'models/feature_rule_spec/specmodel/rule_logic.py')
    return {f:sha(RESEARCH/f) for f in files}


def seal(folder, kind):
    folder = Path(folder).resolve()
    manifest = dict(contract=CONTRACT, kind=kind, files=[
        dict(file=p.relative_to(folder).as_posix(), sha256=sha(p))
        for p in sorted(folder.rglob('*')) if p.is_file() and p.name != 'manifest.json'])
    write(folder / 'manifest.json', manifest)
    return manifest


def verify(folder, kind, required=()):
    folder = Path(folder).resolve()
    m = read(folder / 'manifest.json')
    if m.get('contract') != CONTRACT or m.get('kind') != kind:
        raise InputFailure('Wrong shared42 artifact contract/kind')
    files = m['files']
    names = {f['file'] for f in files}
    if len(names) != len(files) or not set(required) <= names:
        raise InputFailure('Missing or duplicate pinned artifact')
    for item in files:
        p = (folder / item['file']).resolve()
        if not p.is_relative_to(folder) or sha(p) != item['sha256']:
            raise InputFailure('Changed/escaped artifact: ' + item['file'])
    return folder


def profile(folder=DEFAULT):
    folder = verify(folder, 'frozen_release', ['profile.json', 'models/model_set.json'])
    p = read(folder / 'profile.json')
    if digest(p['definition']) != p['definition_sha']:
        raise InputFailure('Frozen definition fingerprint mismatch')
    return folder, p


def runtime_path():
    # These compatibility classes are byte-identical to the original saved model.
    compat = Path(__file__).resolve().parent / 'compat'
    if str(compat) not in sys.path:
        sys.path.insert(0, str(compat))


def load_models(folder, p, trusted=False):
    if not trusted:
        raise InputFailure('Explicit --trust-local-model is required for trusted joblib files')
    folder = verify(folder, 'model_set', ['model_set.json'])
    meta = read(folder / 'model_set.json')
    if meta['environment'] != environment() or meta['definition_sha'] != p['definition_sha']:
        raise InputFailure('Model Python/dependency or feature definition contract differs')
    if meta.get('runtime_fingerprints') != runtime_fingerprints():
        raise InputFailure('ML/calculation runtime code changed; explicit compatibility review required')
    if set(meta['models']) != {g + '_' + a for g in 'TPM' for a in ('if', 'kmeans', 'lof')}:
        raise InputFailure('All nine model entries are required; no automatic fit fallback')
    runtime_path()
    import joblib
    bundles = {}
    for key, item in meta['models'].items():
        path = (folder / item['file']).resolve()
        if not path.is_relative_to(folder) or sha(path) != item['sha256']:
            raise InputFailure('Model hash mismatch: ' + key)
        obj = joblib.load(path)
        contract = p['definition']['ml_contracts'][key[0]]
        if obj['feature_contract_sha'] != contract['sha256'] or obj['contract'] != contract:
            raise InputFailure('Saved model input differs: ' + key)
        expected = key.split('_')[1]
        actual = obj['raw'].settings.algorithm if expected == 'lof' else obj['model'].settings.algorithm
        if actual != expected:
            raise InputFailure('Saved algorithm differs: ' + key)
        bundles[key] = obj
    return bundles, meta
