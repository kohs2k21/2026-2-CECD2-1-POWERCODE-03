"""Canonical finite JSON, source pins, and append-only bounded call accounting."""
from pathlib import Path
from decimal import Decimal
import json, hashlib, time, os, fcntl

def read(p):
    return json.loads(Path(p).read_text())

def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda : f.read(8 << 20), b''):
            h.update(b)
    return h.hexdigest()

def json_default(x):
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, Path):
        return str(x)
    raise TypeError(type(x).__name__)

def digest(x):
    return hashlib.sha256(json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=json_default, allow_nan=False).encode()).hexdigest()

def write(p, x):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(x, ensure_ascii=False, indent=2, default=json_default, allow_nan=False) + '\n')

def pin(p):
    return {'name': Path(p).name, 'sha256': sha(p)}

def new_output(p):
    p = Path(p).resolve()
    p.mkdir(parents=True, exist_ok=False)
    (p / 'model_calls.jsonl').write_text('')
    return p

class ModelFailure(RuntimeError):
    pass

class InputFailure(ValueError):
    pass

class Budget:

    def __init__(self, out, cap=6, shared_plan=None, phase='development_portable'):
        self.out = Path(out)
        self.cap = cap
        self.shared = Path(shared_plan).resolve() if shared_plan else None
        self.phase = phase
        if not 0 <= cap <= 160:
            raise ValueError('Bounded call cap required')

    def reserve(self, kind, call):
        if kind not in ['feature', 'rule', 'planner']:
            raise ValueError(kind)
        path = self.out / 'model_calls.jsonl'
        path.parent.mkdir(exist_ok=True, parents=True)
        with path.open('a+') as h:
            fcntl.flock(h, fcntl.LOCK_EX)
            h.seek(0)
            old = [json.loads(x) for x in h if x.strip()]
            if len(old) >= self.cap:
                raise ModelFailure('Local Model response budget exhausted')
            receipt = {'sequence': len(old) + 1, 'kind': kind, 'call': call, 'phase': self.phase, 'at': time.time(), 'failed_or_interrupted_calls_consume_budget': True}
            if any((x['call'] == call and x['kind'] == kind for x in old)):
                raise ModelFailure('Identical attempt identifier cannot be retried')
            if self.shared:
                plan = read(self.shared)
                run = self.shared.parent
                if (run / 'logs/budget_closed.json').exists():
                    raise ModelFailure('Shared experiment closed')
                if sha(plan['parent_ledger']['path']) != plan['parent_ledger']['sha256']:
                    raise ModelFailure('Parent ledger changed')
                shared = run / 'logs/generation_ledger.jsonl'
                with shared.open('a+') as s:
                    fcntl.flock(s, fcntl.LOCK_EX)
                    s.seek(0)
                    entries = [json.loads(x) for x in s if x.strip()]
                    if len(entries) >= plan['total_response_cap'] or sum((x['kind'] == kind for x in entries)) >= plan['caps'][kind]:
                        raise ModelFailure('Shared Model budget exhausted')
                    if not self.phase.startswith('frozen_final') and sum((x['kind'] == kind and (not x.get('group', x.get('phase', '')).startswith(('frozen_final', kind + '_frozen_final'))) for x in entries)) >= plan['development_caps'][kind]:
                        raise ModelFailure('Final evaluation responses reserved')
                    sr = {**receipt, 'sequence': len(entries) + 1, 'input_id': call, 'group': self.phase, 'input_source': 'portable_official_entry', 'local_sequence': receipt['sequence']}
                    s.write(json.dumps(sr) + '\n')
                    s.flush()
                    os.fsync(s.fileno())
                    receipt['shared_sequence'] = sr['sequence']
            h.write(json.dumps(receipt) + '\n')
            h.flush()
            os.fsync(h.fileno())
        return receipt
