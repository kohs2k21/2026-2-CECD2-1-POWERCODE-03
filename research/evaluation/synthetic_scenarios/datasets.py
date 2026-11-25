import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

GUTENTAG_COMMIT = "0bf06e601d57f1f1e4e2d52cfbfa27871dd6a021"
TSB_COMMIT = "313f0fdeba14292b9db4e1aa94c74a983a25de31"


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def source_version(path, expected):
    actual = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    if actual != expected:
        raise ValueError(f"upstream revision mismatch: {actual}")
    status=subprocess.check_output(['git','-C',str(path),'status','--porcelain','--untracked-files=all'],text=True).splitlines()
    dirty=[line for line in status if not (line.startswith('?? ') and '__pycache__/' in line and line.endswith('.pyc'))]
    if dirty:
        raise ValueError('modified upstream source checkout')
    return actual


def guten_case(seed, offset, length, config):
    from gutenTAG import GutenTAG, TrainingType
    n = config["gutentag_length"]
    start = int(np.random.default_rng(seed).uniform(.55, .8) * n)
    cfg = {"timeseries": [{"name": "single-mean", "length": n, "semi-supervised": True,
        "base-oscillations": [{"kind": "sine", "frequency": 40.0, "amplitude": 1.0, "variance": .03}],
        "anomalies": [{"exact-position": start, "length": length, "channel": 0,
            "kinds": [{"kind": "mean", "offset": offset}]}]}]}
    generator = GutenTAG(seed=seed)
    generator.load_config_dict(cfg)
    data = generator.generate(return_timeseries=True)
    reference = next(d.timeseries for d in data if d.training_type == TrainingType.TRAIN_NO_ANOMALIES)
    test = next(d.timeseries for d in data if d.training_type == TrainingType.TEST)
    values = test["value-0"].to_numpy(dtype=float)
    labels = test["is_anomaly"].to_numpy(dtype=int)
    expected = np.zeros(n, dtype=int); expected[start:start+length] = 1
    if not np.array_equal(labels, expected):
        raise ValueError("unexpected native GutenTAG anomaly labels")
    # Official AnomalyMean adds a fixed offset, with no creeping: recover its exact paired base.
    paired = values.copy(); paired[labels == 1] -= offset
    ref = reference["value-0"].to_numpy(dtype=float)
    cut = int(.75 * len(ref))
    return ref[:cut], ref[cut:], paired, values, labels, cfg


def process_reference(path):
    path = Path(path)
    before = sha(path)
    frame = pd.read_csv(path, na_values=["\\N"])
    required = ["TRANSACTION_ID", "PROCESS_HUB_ID", "PROCESS_ID", "STATUS", "START_TIME", "END_TIME",
                "SUCCESS_COUNT", "ERROR_COUNT", "TOTAL_COUNT"]
    if any(k not in frame for k in required):
        raise ValueError("PROCESS schema incomplete")
    frame["_row"] = np.arange(len(frame))
    frame["_start"] = pd.to_datetime(frame.START_TIME, errors="coerce")
    frame["_end"] = pd.to_datetime(frame.END_TIME, errors="coerce")
    frame["duration_ms"] = (frame._end - frame._start).dt.total_seconds() * 1000
    numeric = frame[["SUCCESS_COUNT", "ERROR_COUNT", "TOTAL_COUNT"]].apply(pd.to_numeric, errors="coerce")
    valid = (frame.STATUS.eq("S") & frame._start.notna() & frame._end.notna()
        & frame.duration_ms.ge(0) & np.isfinite(frame.duration_ms)
        & numeric.notna().all(axis=1) & numeric.ge(0).all(axis=1)
        & numeric.TOTAL_COUNT.gt(0) & (numeric.SUCCESS_COUNT + numeric.ERROR_COUNT).eq(numeric.TOTAL_COUNT)
        & frame[["TRANSACTION_ID", "PROCESS_HUB_ID", "PROCESS_ID"]].notna().all(axis=1))
    candidates = frame.loc[valid].copy()
    sizes = candidates.groupby(["PROCESS_HUB_ID", "PROCESS_ID"], sort=True).size()
    if sizes.empty:
        raise ValueError("no complete process candidates")
    selected = sizes.idxmax()  # Selection uses count, never test anomaly outcomes.
    group = candidates.loc[candidates.PROCESS_HUB_ID.eq(selected[0]) & candidates.PROCESS_ID.eq(selected[1])]
    group = group.sort_values(["_end", "_row"], kind="stable")
    if len(group) < 2000:
        raise ValueError("too few rows in one process")
    # Split contiguous transaction intervals, so even interleaved revisions cannot cross splits.
    bounds = group.groupby("TRANSACTION_ID", sort=False)._end.agg(["min", "max"])
    ordered = bounds.sort_values("min", kind="stable")
    blocks = []
    for tx, row in ordered.iterrows():
        if blocks and row["min"] <= blocks[-1][0]:
            blocks[-1][0] = max(blocks[-1][0], row["max"]); blocks[-1][1].append(tx)
        else:
            blocks.append([row["max"], [tx]])
    if len(blocks) < 20:
        raise ValueError("insufficient disjoint transaction blocks")
    first, second = int(.5 * len(blocks)), int(.7 * len(blocks))
    sets = [set(tx for b in part for tx in b[1]) for part in (blocks[:first], blocks[first:second], blocks[second:])]
    assert all(not sets[i] & sets[j] for i in range(3) for j in range(i+1, 3))
    arrays = [group.loc[group.TRANSACTION_ID.isin(s), "duration_ms"].to_numpy(dtype=float) for s in sets]
    if min(map(len, arrays)) < 500:
        raise ValueError("small chronological split")
    detail = {"source_sha256": before, "source_filename": path.name, "source_rows": len(frame),
        "eligible_rows": int(valid.sum()), "excluded_rows": int((~valid).sum()),
        "selected_rows": len(group), "selected_group_sha256": hashlib.sha256(json.dumps([str(k) for k in selected]).encode()).hexdigest(),
        "selected_group_count_rank": 1, "transaction_blocks": len(blocks),
        "split_rows": dict(zip(["train", "calibration", "test"], map(len, arrays))),
        "split_transactions": dict(zip(["train", "calibration", "test"], map(len, sets))),
        "quantiles_ms": dict(zip(["min", "p50", "p90", "p99", "max"], map(float, np.quantile(group.duration_ms, [0, .5, .9, .99, 1])))),
        "labels": "unknown; S is a candidate filter, not a verified normal label",
        "time_basis": "raw completion-time ordering; source timezone unverified",
        "source_preserved": sha(path) == before}
    assert detail["source_preserved"]
    return *arrays, detail


def tsb_case(base, seed, length, parameter, upstream, kind="add"):
    if str(upstream) not in sys.path:
        sys.path.insert(0, str(upstream))
    from TSB_UAD.transformer.transformer import change_segment_add_scale, flip_segment
    labels = np.zeros(len(base), dtype=int)
    # select_region truncates contamination*n and period count. Ensure exactly one region.
    contamination = (length + .25) / len(base)
    if kind == "add":
        changed, labels = change_segment_add_scale(base, labels, contamination, length,
            seed=seed, para=parameter, method="add")
    else:
        changed, labels = flip_segment(base, labels, contamination, length, seed=seed)
    mask = labels.astype(bool)
    positions = np.flatnonzero(mask)
    if len(positions) != length or not np.all(np.diff(positions) == 1):
        raise ValueError("TSB-UAD did not inject exactly one interval")
    if not np.array_equal(changed[~mask], base[~mask]):
        raise ValueError("TSB-UAD changed values outside injection")
    return changed, labels.astype(int)
