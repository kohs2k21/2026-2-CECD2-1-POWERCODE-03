"""Final same-data and difficulty-only comparisons for the real routed ensemble."""
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"code"))
import model_research_v15.evaluate as runner
import model_research_v14.evaluate as evaluator
from model_research_v18.run import OUTPUT
from model_research_v13.data import read, write
from model_evaluation.data import sha256


def seal_indices():
    """Refresh index metadata after protocol additions; predictions unchanged."""
    for stage in ("parent", "adjusted", "difficulty_only"):
        path = OUTPUT/f"{stage}_evaluation_index.json"
        index = read(path)
        index["summary_sha256"] = sha256(ROOT/index["current"] / "summary.json")
        write(path, index)
    write(OUTPUT/"evaluation_index.json", read(OUTPUT/"adjusted_evaluation_index.json"))


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--stage", choices=("cache", "parent", "adjusted", "seal"), required=True)
    p.add_argument("--benchmark", type=Path)
    args = p.parse_args(); stage = args.stage
    if args.benchmark is not None:
        if stage != "cache": raise ValueError("Explicit staging path is only supported for read-only cache preparation")
        runner.ACTIVE = args.benchmark.resolve()
    runner.OUTPUT = OUTPUT
    if stage == "cache": runner.configure(runner.ACTIVE, OUTPUT); evaluator.build_cache()
    elif stage == "seal": seal_indices()
    else:
        runner.evaluate(stage)
        if stage == "adjusted": seal_indices()
