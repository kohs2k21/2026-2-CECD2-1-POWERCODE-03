"""Run data-free synthetic-fixture suites with this bundle's import paths."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
env = os.environ.copy()
env['PYTHONPATH'] = os.pathsep.join(str(ROOT/p) for p in ('code','code/analysis','code/evaluation_benchmark'))
env['MPLBACKEND'] = 'Agg'
env['PYTHONUTF8'] = '1'
for folder in ('code/analysis','code/process_anomaly_pipeline/tests','code/evaluation_benchmark','code/model_evaluation','code/benchmark_difficulty'):
    print(f'Testing {folder}',flush=True)
    command = [sys.executable,'-m','unittest','discover','-s',str(ROOT/folder),'-p','test*.py']
    if folder=='code/process_anomaly_pipeline/tests':
        command += ['-t',str(ROOT/'code')]
    result = subprocess.run(command,cwd=ROOT,env=env)
    if result.returncode: sys.exit(result.returncode)
result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT),
                         '-p', 'test_latest_models.py'], cwd=ROOT, env=env)
if result.returncode: sys.exit(result.returncode)
print('All synthetic-fixture suites passed; benchmark full evaluation not rerun.',flush=True)
