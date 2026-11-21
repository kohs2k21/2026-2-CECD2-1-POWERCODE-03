"""Backend-oriented original Process CSV/JSON inference, no input-schema change."""
import argparse
import json
from pathlib import Path
import pandas as pd

from .bank import load_bank
from ..raw_windows import ProcessWindowService, CollectionCoverage


def records(path):
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8-sig"))
    return pd.read_csv(path, dtype="string", encoding="utf-8-sig", keep_default_na=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", type=Path, required=True, help="Trusted models_portable.joblib")
    p.add_argument("--processes", type=Path, required=True, help="Original Process CSV or JSON")
    p.add_argument("--reviewed-normal-processes", type=Path, required=True)
    p.add_argument("--normal-end", required=True, help="Exclusive end of fixed 672-hour normal reference")
    p.add_argument("--window-end", required=True)
    p.add_argument("--analysis-hours", type=int, choices=(1, 4, 8, 12, 24), required=True)
    p.add_argument("--coverage", type=Path, required=True,
                   help="Collector telemetry JSON: certified_intervals:[[start,end],...]")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists() or args.output.resolve() in (args.processes.resolve(), args.reviewed_normal_processes.resolve()):
        raise FileExistsError("Never overwrite input or an existing output")
    telemetry = json.loads(args.coverage.read_text(encoding="utf-8-sig"))
    if not telemetry.get("source") or "certified_intervals" not in telemetry:
        raise ValueError("Coverage needs an explicit source and certified_intervals; CSV extent is not certification")
    coverage = CollectionCoverage(tuple(map(tuple, telemetry["certified_intervals"])))
    service = ProcessWindowService(load_bank(args.model))
    references = service.prepare_reference(records(args.reviewed_normal_processes), end=args.normal_end, coverage=coverage)
    result = service.predict_processes(records(args.processes), references=references,
        window_end=args.window_end, analysis_hours=args.analysis_hours, coverage=coverage)
    # JSON dataframes serialize unavailable numeric values to null, not a
    # fabricated zero or nonstandard NaN token.
    payload = {k: json.loads(v.to_json(orient="records", date_format="iso")) if isinstance(v, pd.DataFrame) else v
               for k, v in result.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"{len(result['decisions'])} retrospective hourly decisions; selected {args.analysis_hours}h only")


if __name__ == "__main__": main()
