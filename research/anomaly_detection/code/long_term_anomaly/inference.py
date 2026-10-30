"""Validated offline CSV/DataFrame API for a future batch worker.

Raw Process rows must be aggregated first. A collection-health signal must be
supplied; absence of a Process row alone cannot prove input interruption.
"""
import argparse
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from .benchmark import CHANNELS


def boolean(series, name):
    lookup = {"true": True, "false": False, "1": True, "0": False}
    s = series.astype(str).str.lower().str.strip()
    if not s.isin(lookup).all():
        raise ValueError(f"{name}: use explicit true/false or 1/0")
    return s.map(lookup).to_numpy(bool)


def score_frame(model, frame):
    """Score each independent Adapter+Process batch encoded by case_id.

    All rows must be strictly hourly. No categorical column is encoded into the
    score. The caller is responsible for supplying an approved normal reference
    context; STATUS=S alone cannot provide that approval.
    """
    required = {"case_id", "timestamp", *CHANNELS, "collection_observed", "latency_available"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    outputs = []
    for case_id, part in frame.groupby("case_id", sort=False, dropna=False):
        if pd.isna(case_id):
            raise ValueError("case_id cannot be null")
        times = pd.to_datetime(part.timestamp, errors="raise", format="mixed")
        if times.isna().any() or not times.diff().dropna().eq(pd.Timedelta(hours=1)).all():
            raise ValueError(f"{case_id}: timestamps must be ordered, unique and exactly one hour apart")
        x = part[list(CHANNELS)].apply(pd.to_numeric, errors="raise").to_numpy(float)
        observed = boolean(part.collection_observed, "collection_observed")
        latency = boolean(part.latency_available, "latency_available")
        if (latency & ~observed).any():
            raise ValueError("Latency available at an unobserved collection hour")
        if not np.isfinite(x[observed, 0]).all() or not np.isfinite(x[latency, 1:]).all():
            raise ValueError("Observed channels contain nonfinite values")
        if (x[np.isfinite(x)] < 0).any():
            raise ValueError("log1p aggregate channels cannot be negative")
        if (observed & (x[:, 0] == 0) & latency).any():
            raise ValueError("No requests: latency must be unavailable, not zero-duration")
        r = model.predict(x, observed, latency)
        c = model.setting.context_hours
        result = pd.DataFrame({"case_id": str(case_id), "timestamp": times.iloc[c:].to_numpy(),
                               "score": r["score"], "threshold": model.threshold,
                               "is_anomaly": r["is_anomaly"], "assessable": r["assessable"],
                               "reason": r["reason"], "retrospective": True})
        outputs.append(result)
    if not outputs:
        raise ValueError("No cases")
    return pd.concat(outputs, ignore_index=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--method", default=None)
    args = parser.parse_args()
    if args.output.resolve() == args.input.resolve():
        raise ValueError("Never overwrite the input")
    # Only load trusted, locally produced joblib files: pickle is executable code.
    package = joblib.load(args.model)
    method = args.method or package["primary"]
    predictions = score_frame(package["models"][method], pd.read_csv(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError(args.output)
    predictions.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"{method}: {len(predictions)} monitor hours; {int(predictions.is_anomaly.sum())} alerts")


if __name__ == "__main__":
    main()
