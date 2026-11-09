"""The unchanged-parent/model-only and active/difficulty-only experiments."""
import argparse
from pathlib import Path
import sys
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"code"))
import joblib
import model_research_v15.evaluate as runner
import model_research_v14.evaluate as evaluator
from model_evaluation.data import sha256
from model_research_v15.data import prepare
from model_research_v17.features import augment
from model_research_v17.run import OUTPUT


def configure(source, output):
    evaluator.BENCHMARK, evaluator.OUTPUT, evaluator.prepare = source, output, prepare
    def load(path):
        payload = joblib.load(path)
        if isinstance(payload, dict) and "frame" in payload and "metadata" in payload:
            payload["frame"] = augment(payload["frame"])
        return payload
    evaluator.joblib = SimpleNamespace(load=load, dump=joblib.dump)
    digest = sha256(source/"manifest.json")
    if digest == runner.PARENT_SHA:
        evaluator.cache_folder = lambda: ROOT/"reports/model_research_v14/feature_cache"/digest[:16]
    else: evaluator.cache_folder = lambda: OUTPUT/"feature_cache"/digest[:16]


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--stage", choices=("cache", "parent", "adjusted"), required=True)
    stage = p.parse_args().stage
    runner.OUTPUT, runner.configure = OUTPUT, configure
    if stage == "cache": configure(runner.ACTIVE, OUTPUT); evaluator.build_cache()
    else: runner.evaluate(stage)
