"""Isolated Python 3.12 consumer of PR #98; never imports the legacy ML tree."""
import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys


ALGORITHMS = ("if", "kmeans", "lof")
REQUIRED = ("transactionId", "processId", "adapterType", "startTime", "endTime", "totalCount")
OPTIONAL = ("availableTime", "successCount", "errorCount", "retryCount", "status",
            "dependProcessId", "processHubId", "channelId", "responseCode", "source_period")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verified_package(package, lock):
    """Check code, weights, policy and exact environment before deserialization."""
    package = Path(package).resolve()
    if sys.version_info[:2] != (3, 12):
        raise ValueError("PR98 requires its approved Python 3.12 runtime")
    for name, expected in lock["pins"].items():
        if sha(package / name) != expected:
            raise ValueError("PR98 package pin changed: " + name)
    inventory = json.loads((package / "source_inventory.json").read_text())
    for item in inventory["files"]:
        path = (package / item["path"]).resolve()
        if not path.is_relative_to(package) or sha(path) != item["sha256"]:
            raise ValueError("PR98 source/model changed: " + item["path"])
    requirements = {}
    for line in (package / "requirements.txt").read_text().splitlines():
        name, version = line.strip().split("==")
        requirements[name] = version
    actual = {name: importlib.metadata.version(name) for name in requirements}
    if actual != requirements:
        raise ValueError("PR98 dependency versions differ: " + repr(actual))
    return actual


def execute(request):
    package = Path(request["package"]).resolve()
    dependencies = verified_package(package, request["source_lock"])
    # This script runs in a fresh interpreter, with only this ML code root added.
    sys.path.insert(0, str(package / "code"))
    import numpy as np
    import pandas as pd
    from threadpoolctl import threadpool_limits
    from process_event_model.features import normalize, timestamps
    from process_event_model.inference import load

    rows = request["records"]
    if len({r["event_row_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate event_row_id")
    clean, rejected, bad_transactions = [], {}, set()
    # Derive availability from the COMPLETE supplied transaction before quarantining.
    transaction_end = {}
    def transaction_key(row):
        return str(row.get("source_period", "runtime")), str(row.get("transactionId"))
    for row in rows:
        try:
            tx = transaction_key(row)
            end = timestamps(pd.Series([row["endTime"]])).iloc[0]
            if pd.notna(end):
                transaction_end[tx] = max(transaction_end.get(tx, end), end)
        except (KeyError, TypeError, ValueError):
            pass
    for row in rows:
        rid = row["event_row_id"]
        tx = row.get("transactionId")
        try:
            if any(row.get(k) is None or str(row.get(k)).strip() == "" for k in REQUIRED):
                raise ValueError("missing_required_process_field")
            count = float(row["totalCount"])
            if not math.isfinite(count) or count < 0 or not count.is_integer():
                raise ValueError("invalid_total_count")
            selected = {k: row[k] for k in (*REQUIRED, *OPTIONAL) if k in row}
            selected["event_row_id"] = rid
            if "availableTime" not in selected:
                selected["availableTime"] = transaction_end[transaction_key(row)].isoformat()
            normalized = normalize([selected])
            clean.append(normalized.iloc[0].to_dict())
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            rejected[rid] = str(exc)
            if tx is not None:
                bad_transactions.add(transaction_key(row))
    arrivals = {}
    for row in clean:
        arrivals.setdefault(transaction_key(row), set()).add(row["availableTime"])
    inconsistent = {key for key, clocks in arrivals.items() if len(clocks) != 1}
    bad_transactions.update(inconsistent)
    eligible = []
    for row in clean:
        if transaction_key(row) in bad_transactions:
            rejected[row["event_row_id"]] = ("inconsistent_transaction_available_time" if transaction_key(row) in inconsistent
                                               else "incomplete_transaction_payload: invalid peer Process")
        else:
            eligible.append(row)
    methods, feature_rows, thresholds = {}, {}, {}
    if eligible:
        frame = pd.DataFrame(eligible)
        # Match EventMonitor.predict's observed-date session policy, not label/case IDs.
        frame["case_id"] = (frame.source_period.astype(str) + ":" +
                            frame.availableTime.dt.tz_convert("Asia/Seoul").dt.normalize().astype(str))
        monitors = {a: load(package / "models/latest_process_rows/event_models.joblib", a,
                            with_incidents=True) for a in ALGORITHMS}
        with threadpool_limits(limits=1):
            features = monitors["if"].processor.transform(frame)
            required_columns = sorted(set().union(*(m.detector.columns for m in monitors.values())))
            numeric = [c for c in features if c not in (*REQUIRED, *OPTIONAL, "event_row_id", "case_id")
                       and pd.api.types.is_numeric_dtype(features[c])]
            for c in required_columns:
                if not np.isfinite(features[c].to_numpy(dtype=float)).all():
                    raise ValueError("Nonfinite required frozen ML feature: " + c)
            shared = ["duration_ms", "totalCount", *numeric]
            shared = list(dict.fromkeys(shared))
            for row in features.to_dict("records"):
                feature_rows[row["event_row_id"]] = {
                    "availableTime": row["availableTime"].isoformat(),
                    "values": {c: float(row[c]) if np.isfinite(row[c]) else None for c in shared}}
            for algorithm, monitor in monitors.items():
                result = monitor.predict_features(features.copy())
                thresholds[algorithm] = float(monitor.detector.threshold)
                records = json.loads(result.to_json(orient="records", date_format="iso", double_precision=15))
                methods[algorithm] = {}
                for row in records:
                    score = row["score"]
                    valid = score is not None and math.isfinite(score)
                    alarm = row[request["decision"]] if valid else None
                    methods[algorithm][row["event_row_id"]] = {
                        "state": "unassessable" if not valid else "warning" if alarm else "nonwarning",
                        "score": score if valid else None, "threshold": row["threshold"],
                        "anomaly": row["anomaly"], "confirmed_anomaly": row["confirmed_anomaly"],
                        "incident_active": row["incident_active"], "incident_action": row["incident_action"],
                        "reason": None if valid else "nonfinite_model_score"}
    out = []
    for row in rows:
        rid = row["event_row_id"]
        peer = {a: methods.get(a, {}).get(rid, {"state": "unassessable", "score": None,
                 "threshold": thresholds.get(a), "reason": rejected.get(rid, "not_scored")}) for a in ALGORITHMS}
        states = [v["state"] for v in peer.values()]
        state = "warning" if "warning" in states else "nonwarning" if set(states) == {"nonwarning"} else "unassessable"
        out.append({"event_row_id": rid, "ML": {"state": state, "methods": peer},
                    "ml_features": feature_rows.get(rid), "input_error": rejected.get(rid)})
    return {"contract": "process-event-bridge1.0", "rows": out, "decision": request["decision"],
            "score_direction": "larger is more anomalous; alarm copied from frozen detector",
            "dependencies": dependencies, "python": sys.version.split()[0],
            "feature_input": "PR98 frozen processor output; no arbitrary LLM feature substitution",
            "availability": "explicit availableTime; otherwise max(endTime) of supplied transaction payload",
            "state_policy": "fresh monitors per offline run; complete timestamp batches; observed-date sessions",
            "new_fit": False, "new_llm_calls": 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--request", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    try:
        result = execute(json.loads(args.request.read_text()))
    except Exception as exc:
        result = {"error": type(exc).__name__ + ": " + str(exc), "rows": [], "new_fit": False}
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        return 2
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
