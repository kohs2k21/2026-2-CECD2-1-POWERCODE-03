"""Public CSV -> immutable hybrid detector checks; no fit, GPU, or LLM calls."""
from pathlib import Path
import argparse
import json
import os
import re
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    research = Path(__file__).resolve().parents[2]
    model = research / 'models/hybrid_sr_ml/1.1.0'
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(model / 'vendor/numeric'),
               OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2')
    records = []

    def command(name, command, expected=0):
        began = time.perf_counter()
        with (out / (name + '.stdout.log')).open('x') as stdout, (out / (name + '.stderr.log')).open('x') as stderr:
            result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        record = dict(name=name, exit_code=result.returncode, expected_exit_code=expected,
                      passed=result.returncode == expected, seconds=time.perf_counter()-began)
        records.append(record)
        if not record['passed']:
            raise RuntimeError('Public check failed: ' + name + '; see output logs')

    py = [sys.executable, '-B']
    cli = py + [str(model / 'cli.py')]
    command('inspect', cli + ['inspect'])
    command('cpu_controls', py + [str(model / 'verify.py'), '--output', str(out / 'cpu_controls')])
    command('ratio_controls', py + ['-m', 'unittest', 'discover', '-s', str(model / 'tests'), '-p', 'test_ratio_fallback.py', '-v'])
    public = out / 'public_source'
    constructor = 'from research.integration.feature_rule_ml.shared42.example import make_capture; make_capture(' + repr(str(public)) + ', "test", "2026-07-04", count=32)'
    command('make_public_CSV', py + ['-c', constructor])
    capture = out / 'capture'
    command('capture', cli + ['capture-esb', '--transactions', str(public / 'T.csv'), '--processes', str(public / 'P.csv'),
                             '--messages', str(public / 'M.csv'), '--start', '2026-07-04', '--end', '2026-07-05',
                             '--split', 'test', '--data-class', 'controlled', '--complete-family-capture',
                             '--held-lineage-excluded', '--accept-frozen-reference', '--output', str(capture)])
    command('detect_fallback', cli + ['detect-esb', '--capture', str(capture / 'capture.json'), '--output', str(out / 'fallback')])
    command('detect_strict', cli + ['detect-esb', '--capture', str(capture / 'capture.json'), '--ratio-policy', 'strict', '--output', str(out / 'strict')])
    rejected = out / 'rejected_TEST_training'
    command('reject_TEST_training', cli + ['train-esb', '--train', str(capture / 'capture.json'), '--calibration', str(capture / 'capture.json'),
                                         '--threshold', str(capture / 'capture.json'), '--output', str(rejected)], expected=2)
    if rejected.exists():
        raise AssertionError('Rejected TEST training unexpectedly created models')
    summary = json.loads((out / 'fallback/summary.json').read_text())
    assert summary['entities'] == 32 and not summary['component_errors']
    assert summary['fit_calls'] == summary['LLM_calls'] == 0
    import pandas as pd
    fallback = pd.read_csv(out / 'fallback/decisions.csv.gz', keep_default_na=False).set_index('entity_id')
    strict = pd.read_csv(out / 'strict/decisions.csv.gz', keep_default_na=False).set_index('entity_id')
    assert set(fallback.index) == set(strict.index)
    strict = strict.loc[fallback.index]
    assert (fallback[['if', 'kmeans', 'lof', 'ML', 'logical_SR']].to_numpy() == strict[['if', 'kmeans', 'lof', 'ML', 'logical_SR']].to_numpy()).all()
    assert (fallback.strict_numeric_SR == strict.numeric_SR).all()
    assert (fallback.strict_combined == strict.combined).all()
    assert fallback.fallback_evaluation_count.astype(int).sum() > 0
    cpu = json.loads((out / 'cpu_controls/test_results.json').read_text())
    assert cpu['failed'] == cpu['not_run'] == 0
    ratio_count = re.search(r'Ran (\d+) tests? in', (out / 'ratio_controls.stderr.log').read_text())
    assert ratio_count is not None
    result = dict(commands=records, passed=len(records), failed=0, public_transactions=32,
                  default_components=summary['components'], strict_result_preserved=True,
                  ML_and_logical_unchanged=True, actual_alternative_evaluations=int(fallback.fallback_evaluation_count.astype(int).sum()),
                  model_fit_calls=0, LLM_calls=0, GPU_calls=0, customer_source_used=False,
                  TEST_training_rejected=True, cpu_summary=cpu,
                  ratio_CPU_tests=int(ratio_count.group(1)),
                  scope='Public synthetic functional checks; not incident accuracy or service deployment')
    with (out / 'verification.json').open('x') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
