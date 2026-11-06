"""Check four published hashes; optionally load trusted models and smoke test."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "code"))


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verified_entries():
    inventory = json.loads((ROOT / "latest_inventory.json").read_text(encoding="utf-8"))
    for entry in inventory["models"]:
        path = (ROOT / entry["path"]).resolve()
        if not path.is_relative_to(ROOT) or sha256(path) != entry["sha256"]:
            raise ValueError(f"Model path/hash mismatch: {entry['path']}")
    return inventory["models"]


def long_demo(service):
    """Invented Process rows + explicit fixture coverage, not evaluation data."""
    import numpy as np
    import pandas as pd
    from long_term_anomaly.raw_windows import CollectionCoverage
    starts = pd.date_range("2026-01-01", periods=8064 + 288, freq="5min").as_unit("ns")
    durations = 100 + 15 * np.sin(np.arange(len(starts)) / 8)
    raw = pd.DataFrame({"transactionId": [f"DEMO-{i}" for i in range(len(starts))],
        "processId": "DEMO.PROCESS", "processHubId": "DEMO.HUB", "adapterType": "DEMO.ADAPTER",
        "retryCount": 0, "status": "S", "startTime": starts,
        "endTime": starts + pd.to_timedelta(durations, unit="ms"),
        "totalCount": 1, "successCount": 1, "errorCount": 0})
    normal_end = starts[8064]
    end = normal_end + pd.Timedelta(hours=24)
    coverage = CollectionCoverage(((starts[0], end),))
    refs = service.prepare_reference(raw.iloc[:8064], end=normal_end, coverage=coverage)
    for h in (1, 4, 8, 12, 24):
        result = service.predict_processes(raw.iloc[8064:], references=refs, window_end=end, analysis_hours=h, coverage=coverage)
        decisions = result["decisions"]
        assert len(decisions) == h and decisions.assessable.all()
        assert np.isfinite(decisions.score).all()
    return {"horizons": [1, 4, 8, 12, 24], "raw_Process_API": True}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trusted", action="store_true")
    p.add_argument("--smoke", action="store_true")
    args = p.parse_args(argv)
    if args.smoke and not args.trusted:
        p.error("--smoke requires --trusted; pickle can execute code")
    for e in verified_entries():
        print(f"HASH PASS {e['algorithm']} {e['bytes']:,} bytes", flush=True)
        if not args.trusted:
            continue
        import joblib
        import numpy as np
        from research_workflow.short import inputs
        from research_workflow.test_short import demo
        model = joblib.load(ROOT / e["path"])
        if args.smoke:
            if e["algorithm"] == "long_term":
                from long_term_anomaly.raw_windows import ProcessWindowService
                long_demo(ProcessWindowService(model))
            else:
                for adapter in ("DB.READER", "DEMO.UNSEEN"):
                    n, c = inputs(*demo(minutes=12, adapter=adapter))
                    result = model.predict(n, c)
                    assert len(result) == len(n) and np.isfinite(result.score).all()
                    np.testing.assert_array_equal(result.anomaly, result.score > result.threshold)
        print(f"LOAD{'/SMOKE' if args.smoke else ''} PASS {e['algorithm']}", flush=True)
    print("No accuracy claim: this checks integrity/API operation, not labelled benchmark performance.")


if __name__ == "__main__":
    main()
