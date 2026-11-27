"""Run with python -B -m research.models.benchmark_esb_rule_b COMMAND."""
import argparse
import json
from pathlib import Path

from .bridge import convert_suggestion
from .contract import PACKAGE, validate_prediction
from .inference import BRuleModel, verify_package


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify")
    commands.add_parser("replay")
    generate = commands.add_parser("recommend")
    generate.add_argument("--input", required=True)
    generate.add_argument("--base-path", required=True)
    generate.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "verify":
        m = verify_package()
        print(json.dumps({"passed": True, "files": len(m["files"]), "model": m["model"]}, ensure_ascii=False))
    elif args.command == "replay":
        verify_package()
        examples = json.loads((PACKAGE / "examples/public_pfs_saved_responses.json").read_text())
        rows = []
        for item in examples["items"]:
            errors = validate_prediction(item["input"], item["prediction"])
            draft = None if errors else convert_suggestion(item["input"], item["prediction"])
            rows.append({"id": item["id"], "errors": errors, "baseline_draft": draft is not None})
        print(json.dumps({"mode": "CPU_saved_response_replay", "new_LLM_calls": 0, "items": rows}, ensure_ascii=False))
        if any(row["errors"] for row in rows):
            raise SystemExit(1)
    else:
        inp = json.loads(Path(args.input).read_text())
        inp = inp.get("input", inp)
        result = BRuleModel(args.base_path).recommend(inp, args.output)
        if result["status"] == "validated":
            draft = convert_suggestion(inp, result["prediction"])
            with (Path(args.output) / "baseline_draft.json").open("x") as stream:
                json.dump(draft, stream, ensure_ascii=False, indent=2, allow_nan=False)
        print(json.dumps({"status": result["status"], "attempts": result["attempts"], "errors": result["errors"]}))
        if result["status"] != "validated":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
