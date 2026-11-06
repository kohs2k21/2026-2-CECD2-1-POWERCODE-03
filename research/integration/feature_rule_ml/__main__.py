"""Run from the repository root: python -B -m research.integration.feature_rule_ml."""
import argparse
import json
import sys

from .spec_bridge import prepare, transform
from .ml_bridge import fit, inspect_published
from .runner import predict


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    s = p.add_subparsers(dest="command", required=True)
    q = s.add_parser("prepare", help="Verify/replay actual LLM outputs and measure shared rows")
    q.add_argument("--spec-run", required=True)
    q.add_argument("--suite", required=True)
    q.add_argument("--output", required=True)
    q = s.add_parser("transform", help="Apply frozen definitions to a new approved capture; no LLM")
    q.add_argument("--catalog", required=True)
    q.add_argument("--input-manifest", required=True)
    q.add_argument("--output", required=True)
    q = s.add_parser("fit", help="EXPLICIT new CPU ML fit on three independent reference packs")
    for arg in ("train", "calibration", "threshold", "group", "output"):
        q.add_argument("--" + arg, required=True)
    q.add_argument("--features", nargs="+", required=True, help="Explicit feature aliases")
    q.add_argument("--algorithm", choices=("if", "kmeans", "lof"), required=True)
    q.add_argument("--quantile", type=float, default=.99)
    q = s.add_parser("predict", help="Frozen HR/SR/ML + tri-state OR; NEVER fits or calls an LLM")
    q.add_argument("--prepared", required=True)
    q.add_argument("--output", required=True)
    q.add_argument("--model", action="append", default=[])
    q.add_argument("--trust-local-model", action="store_true")
    s.add_parser("inspect-ml", help="Check published v12 hashes and versions without loading pickle")
    a = p.parse_args(argv)
    try:
        if a.command == "prepare":
            r = prepare(a.spec_run, a.suite, a.output)
        elif a.command == "transform":
            r = transform(a.catalog, a.input_manifest, a.output)
        elif a.command == "fit":
            r = fit(a.train, a.calibration, a.threshold, a.group, a.features,
                    a.algorithm, a.output, a.quantile)
        elif a.command == "predict":
            r = predict(a.prepared, a.output, a.model, a.trust_local_model)
        else:
            r = inspect_published()
        if a.command == "inspect-ml":
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"command": a.command, "output": a.output, "complete": True}))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as e:
        print(json.dumps({"complete": False, "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
