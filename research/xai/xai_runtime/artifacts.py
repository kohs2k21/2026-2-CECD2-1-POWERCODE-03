from pathlib import Path
import csv
import gzip
import hashlib
import json

PROJECT = Path(__file__).resolve().parents[3]
XAI = Path(__file__).resolve().parents[1]
DESIGN = XAI / 'design/1.1.0'
RELEASE = PROJECT / 'data/model_releases/hybrid_sr_ml/1.1.0/hybrid_sr_ml'


class InputError(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise InputError(reason)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(), parse_constant=lambda x: (_ for _ in ()).throw(InputError('nonfinite_json')))


def write(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def rows(path):
    op = gzip.open if str(path).endswith('.gz') else open
    with op(path, 'rt', newline='', encoding='utf-8-sig') as f:
        rd = csv.DictReader(f)
        require(len(rd.fieldnames or []) == len(set(rd.fieldnames or [])), 'duplicate_csv_headers')
        for row in rd:
            require(None not in row and all(v is not None for v in row.values()), 'malformed_csv')
            yield row


def jsonlines(path):
    with gzip.open(path, 'rt') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def verify(folder, manifest='manifest.json'):
    folder = Path(folder).resolve()
    require(folder.is_relative_to(PROJECT), 'artifact_outside_project')
    m = read(folder / manifest)
    pins = {}
    for item in m['files']:
        name = item.get('path', item.get('file'))
        require(isinstance(name, str) and name not in pins, 'invalid_manifest_entry')
        path = (folder / name).resolve()
        require(path.is_relative_to(folder) and path.is_file(), 'escaped_or_missing_artifact')
        require(sha(path) == item['sha256'], 'changed_artifact:' + name)
        pins[name] = item['sha256']
    return pins


def seal(folder):
    folder = Path(folder)
    files = [dict(path=str(p.relative_to(folder)), sha256=sha(p))
             for p in sorted(folder.rglob('*')) if p.is_file()]
    write(folder / 'manifest.json', dict(contract='xai-stage12-results/1.0.0', files=files))
