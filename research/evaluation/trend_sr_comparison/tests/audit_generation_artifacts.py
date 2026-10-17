"""Independent read-only base/patch reconstruction and artifact ledger audit."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def read_arrays(path, columns):
    with np.load(path, allow_pickle=False) as saved:
        frame = pd.DataFrame(index=pd.to_datetime(saved["endpoint_ns"], unit="ns", utc=True))
        frame.index.name = "endpoint"
        for column in columns:
            values = saved[column]
            frame[column] = pd.to_datetime(values, unit="ns", utc=True) if column in ("bin_start", "available_at") else values
        positions = saved["row_positions"].copy() if "row_positions" in saved else None
    return frame, positions


def audit(root):
    root = Path(root).resolve()
    code_root = Path(__file__).resolve().parents[1]
    hashes, records = {}, []
    def digest(path):
        path = path.resolve()
        if path not in hashes:
            hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        return hashes[path]
    for path in sorted(root.rglob("definition.json")):
        definition = json.loads(path.read_text(encoding="utf-8"))
        if "normal_source_file" not in definition:
            continue
        directory = path.parent
        manifest_hashes = json.loads((directory / "hashes.json").read_text(encoding="utf-8"))
        for relative, expected in manifest_hashes.items():
            target = (directory / relative).resolve()
            assert target.is_relative_to(root), (directory, relative)
            assert digest(target) == expected, str(target)
        for relative, expected in definition["generator_code_sha256"].items():
            assert digest(code_root / relative) == expected, relative
        columns = definition["source_columns"]
        assert not set(columns) & {"scenario_id", "event_id", "normal_confirmed", "expected_count", "injected_parameters"}
        frame, _ = read_arrays(directory / definition["normal_source_file"], columns)
        patch, positions = read_arrays(directory / "source-patch.npz", columns)
        assert positions is not None and len(positions) == len(patch)
        assert len(np.unique(positions)) == len(positions)
        assert np.all((positions >= 0) & (positions < len(frame)))
        pd.testing.assert_index_equal(frame.index[positions], patch.index)
        for column in columns:
            frame.iloc[positions, frame.columns.get_loc(column)] = patch[column].to_numpy()
        source_hash = hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes()).hexdigest()
        assert source_hash == definition["source_hash"], str(directory)
        assert len(frame) == definition["source_rows"] == 123844
        truth = pd.read_csv(directory / "truth-events.csv")
        assert len(truth) == definition["generated_events"]
        assert truth["event_id"].nunique() == len(truth)
        if len(truth):
            assert set(truth["snapshot_hash"]) == {source_hash}
            assert (pd.to_datetime(truth["end"], utc=True) > pd.to_datetime(truth["start"], utc=True)).all()
        check = json.loads((directory / "generation-check.json").read_text(encoding="utf-8"))
        assert check["generation_valid"] is True
        assert definition["seed"] == 9501 and definition["split"] == "selection"
        assert not definition["final_authorized"]
        records.append({"directory": str(directory.relative_to(root)), "source_hash": source_hash,
                        "rows": len(frame), "events": len(truth), "hash_entries": len(manifest_hashes)})
    assert len(records) == 83, len(records)
    coverage = pd.read_csv(root / "coverage18.csv")
    assert len(coverage) == 18
    compatibility = json.loads((root / "compatibility153.json").read_text(encoding="utf-8"))
    assert len(compatibility) == 153
    return {"sources_restored": len(records), "unique_files_hashed": len(hashes),
            "coverage_subtypes": len(coverage), "compatibility_pairs": len(compatibility),
            "records": records, "final_source_opened": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.root)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "records"}))
