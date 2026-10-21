"""Base cohort + injected scenarios built only from Git research code (no customer data).

- Base PROCESS rows: `fixture()` copied verbatim from
  research/evaluation/process_synthetic_lab/tests/test_core.py (origin/dev 872f540). It is an
  invented, deterministic cohort (durations 100..118 ms, counts 10..16, adapters A/B, status S).
- Injection + truth labels: the repository's research/evaluation/process_synthetic_lab/scenarios.py
  (P01 explicit failure [fixture-only semantics], P02 single delay, P03 consecutive delay,
  P04 count-conservation violation), imported in place via research_imports (no copy).
- Reference rows: lab reference_mask (research rules) AND status == S.
"""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_imports  # noqa: E402

RESEARCH_IMPORTS = research_imports.setup()

from process_anomaly_pipeline.data import prepare  # noqa: E402
from research.evaluation.process_synthetic_lab.config import ExperimentConfig  # noqa: E402
from research.evaluation.process_synthetic_lab.data import reference_mask, row_ids  # noqa: E402
from research.evaluation.process_synthetic_lab.scenarios import adapter_baselines, generate  # noqa: E402

FIXTURE_SOURCE = 'research/evaluation/process_synthetic_lab/tests/test_core.py::fixture (origin/dev 872f540), verbatim'
P01_EVIDENCE = 'PROTOTYPE FIXTURE ONLY: invented rows; not customer failure semantics (lab README keeps P01 on hold)'


def fixture(n=120, paired=False):
    """Verbatim copy of the lab test fixture (invented identities)."""
    records = []
    for i in range(n):
        start = pd.Timestamp("2025-01-01") + pd.Timedelta(minutes=i)
        records.append(dict(source_period="invented", transactionId=f"tx-{i // 2 if paired else i}",
                            processId=f"step-{i}", processHubId="hub", retryCount=0,
                            dependProcessId=None, adapterType="A" if i % 2 == 0 else "B", channelId="channel",
                            status="S", startTime=start, endTime=start + pd.Timedelta(milliseconds=100 + i % 19),
                            totalCount=10 + i % 7, successCount=10 + i % 7, errorCount=0, responseCode=None))
    frame = prepare(pd.DataFrame(records))
    frame["parentStartTime"] = frame.startTime - pd.Timedelta(minutes=1)
    frame["parentEndTime"] = frame.endTime + pd.Timedelta(minutes=5)
    frame["parentInterfaceId"] = "interface"
    frame["row_id"] = row_ids(frame)
    return frame


def chronological_split(frame, train_fraction=0.7, validation_fraction=0.15):
    """Start-time order split 70/15/15. The fixture has one PROCESS per transaction, so no
    transaction can cross a boundary; the lab's transaction-interval purge is therefore a no-op
    here and is not re-implemented (its loader needs the customer CSV path)."""
    order = frame.sort_values(['startTime', 'row_id'], kind='stable').index
    n = len(order)
    cut_train = int(n * train_fraction)
    cut_val = int(n * (train_fraction + validation_fraction))
    roles = pd.Series('test', index=frame.index, dtype=object)
    roles.loc[order[:cut_train]] = 'train'
    roles.loc[order[cut_train:cut_val]] = 'threshold'
    parts = {role: frame.loc[roles.eq(role)].reset_index(drop=True) for role in ('train', 'threshold', 'test')}
    assert parts['train'].startTime.max() < parts['threshold'].startTime.min() < parts['test'].startTime.min()
    return parts


def train_reference(train):
    """train-success-only: status == S AND research reference rules (quality/consistency)."""
    mask = reference_mask(train) & train.status.eq('S').fillna(False)
    return train.loc[mask].reset_index(drop=True), {
        'trainRows': int(len(train)), 'referenceRows': int(mask.sum()),
        'statusCounts': {str(k): int(v) for k, v in train.status.astype(str).value_counts().items()}}


def to_sr_frame(frame):
    """Lab camelCase frame -> Trainer raw snake_case columns consumed by the SR methods."""
    return pd.DataFrame({
        'row_id': frame.row_id.astype(object),
        'adapter_type': frame.adapterType.astype(object),
        'status': frame.status.astype(object),
        'total_count': frame.totalCount.astype(float),
        'success_count': frame.successCount.astype(float),
        'error_count': frame.errorCount.astype(float),
        'retry_count': frame.retryCount.astype(float),
        'duration_ms': frame.duration_ms.astype(float),
        'interface_id': frame.parentInterfaceId.astype(object),
    })


def experiment_config(target_rows=10, seeds=(11, 23, 47)):
    return ExperimentConfig(target_rows=target_rows, seeds=tuple(seeds),
                            scenarios=('P01', 'P02', 'P03', 'P04'),
                            failure_status='F', failure_semantics_evidence=P01_EVIDENCE,
                            count_conservation_contract=True,
                            min_train_rows=20, min_threshold_rows=10, min_adapter_rows=5)


def inject(test, baselines, config, *, scenario, severity, seed):
    """Returns (mutated frame, truth frame[row_id,label,scenario,...], metadata)."""
    result = generate(test, baselines, config, scenario=scenario, severity=severity, seed=seed)
    return result.frame, result.truth, result.metadata


__all__ = ['fixture', 'chronological_split', 'train_reference', 'to_sr_frame', 'experiment_config',
           'inject', 'adapter_baselines', 'reference_mask', 'FIXTURE_SOURCE', 'P01_EVIDENCE', 'RESEARCH_IMPORTS', 'np']
