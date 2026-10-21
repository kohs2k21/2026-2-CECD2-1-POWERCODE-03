"""Import the repository research code this prototype reuses, without copying it and
without pulling in scikit-learn.

Two repository packages are needed:

* ``process_anomaly_pipeline`` (research/anomaly_detection/code): ``config``, ``data``,
  ``rules``, ``quantiles``. Its real ``__init__`` imports ``pipeline.py`` (Isolation Forest,
  scikit-learn), which this prototype never uses. A package *shim* is registered in
  ``sys.modules`` with ``__path__`` pointing at the original directory, so the submodules
  are loaded from the untouched repository files while the sklearn import is bypassed.
* ``research.evaluation.process_synthetic_lab`` (``config``, ``data``, ``scenarios``,
  ``evaluate``): imported as-is. Its ``__init__`` imports ``research.evaluation.trainer_lab``
  (path setup only) and ``data.py`` imports the trainer_lab customer-CSV loader *functions*;
  nothing is called, no customer file is read.

Call :func:`setup` before importing either package. Idempotent.
"""
from __future__ import annotations

from pathlib import Path
import sys
import types

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]                                   # <repo>/research/evaluation/process_sr_prototype
PIPELINE_DIR = REPO_ROOT / 'research' / 'anomaly_detection' / 'code' / 'process_anomaly_pipeline'
LAB_DIR = REPO_ROOT / 'research' / 'evaluation' / 'process_synthetic_lab'

PIPELINE_PACKAGE = 'process_anomaly_pipeline'


def setup():
    for path in (REPO_ROOT, PIPELINE_DIR.parent):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    if not PIPELINE_DIR.is_dir() or not LAB_DIR.is_dir():
        raise ImportError(f'research code not found under {REPO_ROOT}')
    if PIPELINE_PACKAGE not in sys.modules:
        shim = types.ModuleType(PIPELINE_PACKAGE, (
            'Shim package for process_anomaly_pipeline: submodules are loaded from the original '
            'repository directory; the original __init__ (sklearn IsolationForest pipeline) is bypassed.'))
        shim.__path__ = [str(PIPELINE_DIR)]
        shim.__package__ = PIPELINE_PACKAGE
        shim.__shim_origin__ = str(PIPELINE_DIR / '__init__.py')
        sys.modules[PIPELINE_PACKAGE] = shim
    return {'pipelineDir': PIPELINE_DIR.relative_to(REPO_ROOT).as_posix(),
            'labDir': LAB_DIR.relative_to(REPO_ROOT).as_posix(),
            'shims': [f'{PIPELINE_PACKAGE}.__init__ (sklearn pipeline import bypassed; submodules loaded from the original files)'],
            'copies': []}
