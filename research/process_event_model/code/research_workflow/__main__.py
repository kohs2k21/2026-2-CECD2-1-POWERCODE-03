"""CLI for published-model inference/evaluation and separate new-data refits."""
import argparse
import json
from pathlib import Path
import joblib
from .short import inputs, read_frame, load_trusted, train, evaluate


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("predict", "evaluate"):
        a = sub.add_parser(command)
        a.add_argument("--model", type=Path, required=True)
        a.add_argument("--expected-sha256")
        a.add_argument("--trust-model", action="store_true", required=True)
        a.add_argument("--native", type=Path, required=True)
        a.add_argument("--completions", type=Path, required=True)
        a.add_argument("--output", type=Path, required=True)
        if command == "evaluate":
            a.add_argument("--labels", type=Path, required=True)
    a = sub.add_parser("train")
    a.add_argument("--algorithm", choices=("if", "kmeans", "lof"), required=True)
    a.add_argument("--normal-native", type=Path, required=True)
    a.add_argument("--normal-completions", type=Path, required=True)
    a.add_argument("--calibration-native", type=Path, required=True)
    a.add_argument("--calibration-completions", type=Path, required=True)
    a.add_argument("--reviewed-normal", action="store_true", required=True)
    a.add_argument("--settings", type=Path, required=True, help="Published core setting JSON; no hidden research cache")
    a.add_argument("--quantile", type=float, default=.99)
    a.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    if args.output.exists():
        p.error("Output exists; choose a new file (published models are never overwritten)")
    if args.command == "train":
        model = train(read_frame(args.normal_native), read_frame(args.normal_completions),
                      read_frame(args.calibration_native), read_frame(args.calibration_completions),
                      algorithm=args.algorithm, setting=json.loads(args.settings.read_text(encoding="utf-8-sig")), quantile=args.quantile)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, args.output, compress=3)
        print(json.dumps(model.provenance, ensure_ascii=False, indent=2))
    else:
        native, completions = inputs(read_frame(args.native), read_frame(args.completions))
        result = load_trusted(args.model, args.expected_sha256).predict(native, completions)
        payload = json.loads(result.to_json(orient="records", date_format="iso"))
        if args.command == "evaluate":
            payload = {"metrics": evaluate(result, read_frame(args.labels)), "predictions": payload}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        print(f"{len(result)} minute decisions; {args.command} -> {args.output}")


if __name__ == "__main__":
    main()
