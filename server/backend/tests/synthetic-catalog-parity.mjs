import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { SYNTHETIC_PROFILES, staticSpecHash } from '../dist/routes/syntheticEvaluationContract.js';

// Run in a worker-enabled development/CI environment; backend runtime needs no Python.
const worker = fileURLToPath(new URL('../../worker/', import.meta.url));
const result = spawnSync(process.env.PYTHON ?? 'python', ['-c',
  'import json; from trainer.synthetic_profiles import PROFILES,digest; print(json.dumps({"profiles":PROFILES,"hashes":{k:digest(v) for k,v in PROFILES.items()}},sort_keys=True,ensure_ascii=False))'],
{ cwd: worker, encoding: 'utf8' });
assert.equal(result.status, 0, result.error?.message ?? result.stderr);
const python = JSON.parse(result.stdout);
assert.deepEqual(python.profiles, SYNTHETIC_PROFILES);
assert.deepEqual(python.hashes, Object.fromEntries(Object.entries(SYNTHETIC_PROFILES).map(([key, value]) => [key, staticSpecHash(value)])));
console.log('synthetic static catalogs and canonical SHA-256 match across TypeScript/Python');
