"""Verify published v12 hashes; optional data-free, explicitly trusted smoke."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def demo_inputs(adapter="DB.READER"):
    """Small synthetic stream, including an empty minute; no benchmark reads."""
    import numpy as np
    import pandas as pd
    from model_evaluation.data import unix_seconds
    starts = pd.date_range("2026-10-14T12:00:00", periods=8, freq="min")
    arrivals = np.array([1, 1, 1, 1, 1, 0, 1, 1])
    native = pd.DataFrame({"case_id": "DEMO", "adapterType": adapter,
                           "processId": "DEMO-P", "window_start": starts,
                           "window_end": starts + pd.Timedelta(minutes=1),
                           "execution_arrivals": arrivals})
    ends = starts[arrivals > 0] + pd.Timedelta(seconds=1)
    completions = pd.DataFrame({"case_id": "DEMO", "adapterType": adapter,
                               "processId": "DEMO-P", "totalCount": 1,
                               "duration": 100.0, "end_seconds": unix_seconds(ends)})
    return native, completions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trusted", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.smoke and not args.trusted:
        parser.error("--smoke requires --trusted; do not load unknown pickle files")
    inventory = json.loads((ROOT / "model_inventory.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT / "code"))
    for entry in [*inventory["models"], *inventory["data_caches"]]:
        path = ROOT / entry["path"]
        if not path.resolve().is_relative_to(ROOT) or sha256(path) != entry["sha256"]:
            raise ValueError(f"Hash/path mismatch: {entry['path']}")
        print(f"SHA-256 PASS: {entry['path']} ({entry['bytes']:,} bytes)", flush=True)
    if not args.trusted:
        print("No pickle loaded. --trusted --smoke checks inference, not accuracy.")
        return
    import numpy as np
    from model_research_v8.inference import load, ProcessResearchMonitor
    for entry in inventory["models"]:
        model = load(ROOT / entry["path"], entry["sha256"])
        if not isinstance(model, ProcessResearchMonitor):
            raise ValueError("Unexpected portable model type")
        if args.smoke:
            for adapter in ("DB.READER", "DEMO.NEW.ADAPTER"):
                native, completions = demo_inputs(adapter)
                result = model.predict(native, completions)
                assert len(result) == len(native)
                assert np.isfinite(result[[n + "_margin" for n in ("global", "adapter", "ensemble")]]).all().all()
                for name in ("global", "adapter", "ensemble"):
                    np.testing.assert_array_equal(result[name + "_anomaly"], result[name + "_margin"] > 0)
                if adapter == "DEMO.NEW.ADAPTER":
                    assert not result.expert_available.any()
                    for name in ("adapter", "ensemble"):
                        np.testing.assert_array_equal(result[name + "_margin"], result.global_margin)
                assert not result.traffic_assessed.iloc[:3].any()
                assert result.traffic_assessed.iloc[3:].all()
        print(f"LOAD PASS: v12 {model.algorithm}, three routes, hash checked", flush=True)
    print("SMOKE PASS: no benchmark CSV, source archive or training cache read by prediction." if args.smoke else "Trusted models loaded.")


if __name__ == "__main__":
    main()
