"""Offline train/predict commands. Inference never refits a model or threshold."""
import argparse
import json
import logging
from pathlib import Path
import pandas as pd
from .config import PipelineConfig
from .data import load_workspace
from .pipeline import ProcessAnomalyPipeline


def read_records(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8-sig"))
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    return pd.read_csv(path, dtype="string", encoding="utf-8-sig", keep_default_na=False,
                       na_values=["", "NULL", "\\N", "NaN", "nan"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Process rules + Adapter/global Isolation Forest")
    sub = parser.add_subparsers(dest="command", required=True)
    train = sub.add_parser("train")
    train.add_argument("--data-dir", type=Path, required=True)
    train.add_argument("--config", type=Path)
    train.add_argument("--model", type=Path, required=True)
    train.add_argument("--with-transaction-context", action="store_true")
    train.add_argument("--complete-context", action="store_true", help="Explicitly assert complete Process collection for relation rules")
    train.add_argument("--overwrite", action="store_true")
    predict = sub.add_parser("predict")
    predict.add_argument("--model", type=Path, required=True)
    predict.add_argument("--input", type=Path, required=True, help="Process JSON/JSONL/CSV records")
    predict.add_argument("--transactions", type=Path, help="Optional Transaction metadata, not model input")
    predict.add_argument("--complete-context", action="store_true")
    predict.add_argument("--trust-model", action="store_true", help="Only use for your own trusted joblib artifact")
    predict.add_argument("--output", type=Path, required=True, help="JSONL explanations or CSV flags/scores")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "train":
        if args.complete_context and not args.with_transaction_context:
            parser.error("--complete-context requires --with-transaction-context")
        if not args.overwrite and (args.model.exists() or args.model.with_suffix(".manifest.json").exists()):
            parser.error("Output model exists; choose a new version or explicitly --overwrite")
        config = PipelineConfig.from_dict(json.loads(args.config.read_text(encoding="utf-8")) if args.config else None)
        p, t, audit = load_workspace(args.data_dir, with_transactions=args.with_transaction_context)
        logging.info("Loaded %s Process rows; Transaction context=%s", len(p), 0 if t is None else len(t))
        model = ProcessAnomalyPipeline(config).fit(p, transactions=t, complete_context=args.complete_context)
        model.training_summary["sources"] = audit.to_dict("records")
        model.save(args.model, overwrite=args.overwrite)
        print(model.model_table().drop(columns="sample_adapter_counts").to_string(index=False))
        print(f"Saved {args.model}. No inference results exported.")
    else:
        if args.complete_context and args.transactions is None:
            parser.error("--complete-context requires --transactions")
        if args.output.exists():
            parser.error("Output exists; choose a new output filename")
        model = ProcessAnomalyPipeline.load(args.model, trusted=args.trust_model)
        p = read_records(args.input)
        t = read_records(args.transactions) if args.transactions else None
        from .data import prepare
        p = prepare(p)
        results = model.predict(p, transactions=t, complete_context=args.complete_context)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.suffix.lower() == ".csv":
            results.to_csv(args.output, index=False, encoding="utf-8-sig")
        else:
            # Explain existing full-batch results; never recompute relation rules on a slice.
            with args.output.open("x", encoding="utf-8") as handle:
                for start in range(0, len(p), 500):
                    sl = slice(start, start + 500)
                    for record in model.explain(p.iloc[sl], result=results.iloc[sl]):
                        handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        print(f"Scored {len(results)} Process rows; candidates={int(results.anomaly_candidate.sum())}; output={args.output}")


if __name__ == "__main__":
    main()
