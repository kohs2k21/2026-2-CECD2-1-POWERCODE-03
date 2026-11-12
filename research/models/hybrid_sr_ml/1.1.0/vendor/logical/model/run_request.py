"""Machine-readable boundary for one local recommendation request.

Wraps the unchanged selected CLI. A failed/context-limited request is unavailable,
never a fabricated rule or nonwarning. No automatic retry, fit or input truncation.
Copy this file beside cli.py in a selected portable research bundle.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--base-path', required=True, type=Path)
    p.add_argument('--mode', choices=['recommend', 'discover', 'discover-roots'], default='recommend')
    p.add_argument('--maximum-rounds', type=int, default=4, choices=range(1, 13))
    p.add_argument('--root', action='append')
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    command = [sys.executable, '-B', str(ROOT / 'cli.py'), a.mode,
               '--input', str(a.input.resolve()), '--output', str(a.output.resolve() / 'generation'),
               '--base-path', str(a.base_path.resolve())]
    if a.mode == 'discover':
        command += ['--maximum-rounds', str(a.maximum_rounds)]
    if a.mode == 'discover-roots':
        for root in a.root or []:
            command += ['--root', root]
    failure, code = None, None
    try:
        with (a.output / 'stdout.log').open('x') as stdout, (a.output / 'stderr.log').open('x') as stderr:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        code = result.returncode
        if code:
            detail = (a.output / 'stderr.log').read_text()
            failure = ('context_budget_exceeded' if 'Input exceeds validated context' in detail else
                       'request_rejected_or_execution_failed')
    except Exception as exc:
        failure = type(exc).__name__ + ': ' + str(exc)
    ledgers = list((a.output / 'generation').rglob('model_calls.jsonl'))
    # These are reservations, not completed response counts. Keep the distinction.
    reserved = sum(len(path.read_text().splitlines()) for path in ledgers)
    record = dict(request_status='completed_for_review' if failure is None and code == 0 else 'unavailable',
                  exit_code=code, reason=failure, mode=a.mode, command=command,
                  input_sha256=hashlib.sha256(a.input.read_bytes()).hexdigest() if a.input.is_file() else None,
                  generation_directory=str(a.output.resolve() / 'generation'),
                  raw_attempts_and_partial_results_preserved=True,
                  model_call_ledger_reservations=reserved,
                  reservation_count_is_not_completed_model_responses=True,
                  automatic_retry=False, automatic_model_fallback=False,
                  automatic_rule_activation=False, model_or_threshold_fit=False,
                  failure_is_not_nonwarning=True, seconds=time.monotonic() - start)
    with (a.output / 'request_status.json').open('x') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({k: record[k] for k in ['request_status', 'reason', 'exit_code', 'seconds']}))
    if record['request_status'] != 'completed_for_review':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
