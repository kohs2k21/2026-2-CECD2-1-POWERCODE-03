"""Apply independent scenario oracles to saved development sources, not regeneration."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from research.evaluation.trend_sr_comparison.tests.audit_generation_artifacts import read_arrays
from research.evaluation.trend_sr_comparison.tests.test_generated_data import GeneratedSourceContracts


def load(directory):
    manifest = json.loads((directory / "definition.json").read_text(encoding="utf-8"))
    frame, _ = read_arrays(directory / manifest["normal_source_file"], manifest["source_columns"])
    patch, positions = read_arrays(directory / "source-patch.npz", manifest["source_columns"])
    for column in frame.columns:
        frame.iloc[positions, frame.columns.get_loc(column)] = patch[column].to_numpy()
    events = pd.read_csv(directory / "truth-events.csv")
    for column in ("start", "end"):
        events[column] = pd.to_datetime(events[column], utc=True)
    return SimpleNamespace(frame=frame, events=events, manifest=manifest)


def audit(root):
    methods = ["test_all_eighteen_transforms_have_declared_physical_effects",
               "test_pulse_lengths_gaps_and_single_event_lengths_are_exact",
               "test_intervention_cannot_modify_normal_gaps_or_other_source_rows",
               "test_zero_count_has_unknown_duration_without_becoming_source_failure",
               "test_duration_variance_case_preserves_mean_and_increases_variability",
               "test_cases_have_full_day_washout_and_business_activity"]
    records = []
    for seed in (3101,3137,3173,3209,3247):
        directory = root / str(seed) / "selection"
        test = GeneratedSourceContracts()
        test.normal, test.main, test.error = [load(directory / suite) for suite in ("normal","main","error")]
        assert set(test.main.events.source_seed) == {seed}
        assert set(test.error.events.source_seed) == {seed}
        for name in methods:
            getattr(test, name)()
            records.append({"seed":seed,"oracle":name,"passed":True})
    return {"checks":len(records),"passed":all(r["passed"] for r in records),"records":records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    result = audit(args.root)
    args.output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "records"}))
