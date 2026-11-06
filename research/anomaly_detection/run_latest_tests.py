"""Data-free publication suite. Legacy data-bound replay tests are separate."""
from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parent
MODULES = [
    "test_published_latest", "research_workflow.test_short",
    "shared_model_v7.test_v7", "shared_model_v7.test_feature_importance",
    "evaluation_benchmark.test_support_evidence",
]


def main():
    sys.path[:0] = [str(ROOT), str(ROOT / "code"), str(ROOT / "code/evaluation_benchmark")]
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    (ROOT / "publication_checks_issue80.json").write_text(json.dumps({
        "suite": "run_latest_tests.py", "tests_run": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
        "passed": result.wasSuccessful(), "fixtures": "invented, not original/benchmark data",
        "covers": ["four model hashes/load", "three short API roundtrips and future exclusion",
                   "raw Process long API at five horizons", "new normal-data refits for three algorithms",
                   "feature causality, support arithmetic, and metric key matching"],
        "full_labelled_benchmark_replay_this_publication": False,
        "server_integration_or_production_deployment": False,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
