"""Audit benchmark reference support; never modify a dataset or fitted model.

Mathematical coverage requires IID/exchangeable draws. Source-workflow IDs
prevent row duplication from being counted as independent evidence, but do
not establish independence, stationarity or production representativeness.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
from scipy.stats import binom

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
from evaluation_benchmark.generate import Catalog, make_history


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def tail_seen_probability(n, quantile=.95):
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError("n must be a positive integer")
    if not 0 < quantile < 1:
        raise ValueError("quantile must be inside (0, 1)")
    return 1 - quantile ** n


def minimum_tail_sample(quantile=.95, confidence=.95):
    if not 0 < quantile < 1 or not 0 < confidence < 1:
        raise ValueError("quantile and confidence must be inside (0, 1)")
    return math.ceil(math.log1p(-confidence) / math.log(quantile))


def quantile_interval_ranks(n, quantile=.95, confidence=.95):
    """Equal-tailed binomial/order-statistic CI; missing endpoint = unbounded.

For continuous IID observations, K=#(X < population quantile) is binomial.
An interval [X_(lower), X_(upper)] covers when lower <= K < upper.
This does NOT mean an empirical P95 is precise or distribution-free IID
assumptions are valid for the archived, filtered production prototypes.
"""
    tail_seen_probability(n, quantile)
    if not 0 < confidence < 1:
        raise ValueError("confidence must be inside (0, 1)")
    alpha = (1 - confidence) / 2
    lower = int(binom.ppf(alpha, n, quantile))
    upper = int(binom.ppf(1 - alpha, n, quantile)) + 1
    return {"lower_rank": lower if lower >= 1 else None,
            "upper_rank": upper if upper <= n else None,
            "finite_both_ends": lower >= 1 and upper <= n}


def source_groups(bundle):
    groups = defaultdict(dict)
    adapter_rows, adapter_workflows = defaultdict(int), defaultdict(set)
    for template in bundle["templates"]:
        workflow = (template["source_period"], template["source_transactionId"])
        for row in template["rows"]:
            adapter = row["adapterType"]
            key = (adapter, row["processId"], row["totalCount"])
            if workflow in groups[key]:
                raise ValueError("Repeated Process in the same source workflow")
            groups[key][workflow] = float(row["duration_ms"])
            adapter_rows[adapter] += 1
            adapter_workflows[adapter].add(workflow)
    return groups, adapter_rows, adapter_workflows


def sensitivity(groups, repeats=200, seed=20261023):
    """Repeated subsampling, not new anomaly generation or model selection.

Use exact Adapter/Process/count groups with >=200 source workflows and a
positive reference P95. Random 70/30 workflow split; held-out workflows do
not enter resamples. Errors are relative to the *fit-pool* empirical P95,
not a claimed known population quantile. Group-level results are retained.
"""
    sizes = (10, 30, 59, 72, 100)
    per_group = []
    for key, samples in sorted(groups.items()):
        values = np.array([v for _, v in sorted(samples.items())], dtype=float)
        if len(values) < 200:
            continue
        key_seed = int.from_bytes(hashlib.sha256(repr((seed, key)).encode()).digest()[:8], "big")
        rng = np.random.default_rng(key_seed)
        values = values[rng.permutation(len(values))]
        boundary = int(.7 * len(values))
        fit, holdout = values[:boundary], values[boundary:]
        reference = float(np.quantile(fit, .95, method="linear"))
        if reference <= 0:
            continue
        for n in sizes:
            estimates = np.array([np.quantile(rng.choice(fit, n, replace=False), .95,
                                             method="linear") for _ in range(repeats)])
            error = np.abs(estimates - reference) / reference * 100
            exceed = (holdout[:, None] > estimates[None, :]).mean(axis=0) * 100
            per_group.append({"adapter": key[0], "process": key[1], "totalCount": key[2],
                              "source_workflows": len(values), "sample_size": n,
                              "fit_pool_P95_ms": reference,
                              "median_relative_error_percent": float(np.median(error)),
                              "P95_relative_error_percent": float(np.quantile(error, .95)),
                              "holdout_exceedance_median_percent": float(np.median(exceed)),
                              "holdout_exceedance_P95_percent": float(np.quantile(exceed, .95))})
    aggregate = []
    for n in sizes:
        rows = [r for r in per_group if r["sample_size"] == n]
        if rows:
            aggregate.append({"sample_size": n, "groups": len(rows), "repeats_per_group": repeats,
                "median_group_median_error_percent": float(np.median([r["median_relative_error_percent"] for r in rows])),
                "median_group_P95_error_percent": float(np.median([r["P95_relative_error_percent"] for r in rows])),
                "median_group_holdout_exceedance_percent": float(np.median([r["holdout_exceedance_median_percent"] for r in rows]))})
    return {"aggregate": aggregate, "per_group": per_group,
            "normal_archive_is_filtered_and_not_frequency_representative": True,
            "IID_or_production_coverage_established": False,
            "not_a_blind_model_performance_experiment": True}


def audit(repeats=200):
    archive = ROOT / "reports/evaluation_v2_work/inputs.json"
    protected = [archive, ROOT / "data/evaluation/manifest.json",
                 ROOT / "code/evaluation_benchmark/generate.py",
                 *[ROOT / "reports/shared_model_v7/release/handoff" / (a + "_shared.joblib")
                   for a in ("if", "kmeans", "lof")]]
    before = {str(p.relative_to(ROOT)): digest(p) for p in protected}
    bundle = json.loads(archive.read_text(encoding="utf-8"))
    catalog = Catalog(bundle)
    groups, rows, workflows = source_groups(bundle)
    history_rows, history = make_history(20261010)
    census = [{"adapter": a, "prototype_rows": rows[a],
               "source_workflows": len(workflows[a]),
               "current_delay_eligible_keys": len(catalog.delay_by_adapter.get(a, [])),
               "below_30_even_after_adapter_pooling": rows[a] < 30}
              for a in sorted(rows)]
    result = {"seed": 20261023,
        "references": {"Wilks_1941": "https://doi.org/10.1214/aoms/1177731788",
                       "Hyndman_Fan_1996": "https://robjhyndman.com/publications/quantiles/"},
        "old_30_rule_scientifically_validated": False,
        "tail_seen_formula_under_continuous_IID": "1 - 0.95**n",
        "minimum_for_95pct_probability_of_one_upper_tail_observation": minimum_tail_sample(),
        "not_a_minimum_for_precise_P95_estimation": True,
        "theory": [{"n": n, "tail_seen_probability": tail_seen_probability(n),
                    "expected_upper_tail_observations": .05 * n,
                    "equal_tailed_95pct_quantile_CI": quantile_interval_ranks(n)}
                   for n in (2, 3, 6, 7, 9, 30, 59, 72, 100)],
        "adapter_support": census,
        "delay_supported_adapters": sorted(catalog.delay_by_adapter),
        "traffic_supported_adapters": catalog.adapter_names,
        "synthetic_history": {"minute_rows": len(history_rows),
            "weekday_hour_strata": len(history),
            "minimum_minutes_per_stratum": min(map(len, history.values())),
            "maximum_minutes_per_stratum": max(map(len, history.values())),
            "Adapter_specific_original_arrival_distribution_estimated": False,
            "minimum_source_workflows_to_clone": 1,
            "cloning_increases_independent_evidence": False,
            "origin": "14-day synthetic bounded Poisson, weekday/hour conditions",
            "real_idle_collection_failure_and_outage_distinguished": False},
        "sensitivity": sensitivity(groups, repeats=repeats),
        "proposed_not_activated": {
            "support_unit": "distinct source workflows, not cloned rows",
            "59_is_only_a_one_sided_tail_observation_screen": True,
            "require_quantile_uncertainty_and_hard_rule_headroom_checks": True,
            "traffic_requires_real_collection_coverage_and_activity_schedule_validation": True,
            "new_empirical_policy_or_benchmark_not_activated": True},
        "input_hashes": before}
    after = {str(p.relative_to(ROOT)): digest(p) for p in protected}
    if before != after:
        raise RuntimeError("Protected input changed during audit")
    result["protected_input_hashes_unchanged"] = True
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=200)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/benchmark_support_evidence_20261023/summary.json")
    args = parser.parse_args()
    target = args.output.resolve()
    if not target.is_relative_to((ROOT / "reports").resolve()) or target.exists():
        raise ValueError("Use a new JSON output under reports/; never overwrite")
    if args.repeats < 1:
        raise ValueError("Positive repeat count required")
    result = audit(args.repeats)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"output": str(target), "sensitivity": result["sensitivity"]["aggregate"],
                      "protected_inputs_unchanged": True}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
