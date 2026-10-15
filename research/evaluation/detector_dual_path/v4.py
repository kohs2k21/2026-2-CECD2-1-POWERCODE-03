"""Frozen validation selection and single-pass sealed synthetic test.

Run as python -m research.evaluation.detector_dual_path.v4 --output <new-folder>.
Performance is a separate real-wall-clock command; this replay does not sleep.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd
import sklearn
from threadpoolctl import threadpool_limits

from .models import frame_digest, sha256_file
from .v4_core import (ROLES, FEATURES, SCENARIOS, build_role, digest, fit_models, group_metrics,
                      History, read_config, row_frame, score_block, truth_at, window_frame)

BASE = Path(__file__).resolve().parent


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def finite_limit(value):
    return float("inf") if value == "off" else float(value)


def support_table(seed, role, blocks, config):
    rows = []
    for day in range(7):
        for hour in range(24):
            cohort = [b for b in blocks if b.scenario == "normal" and
                (lambda s: (s.dayofweek, s.hour) == (day, hour))(
                    pd.Timestamp(b.start, unit="s", tz="UTC").tz_convert("Asia/Seoul"))]
            rows.append({"seed": seed, "role": role, "weekday": day, "hour": hour,
                "dates": len({pd.Timestamp(b.start, unit="s", tz="UTC").date() for b in cohort}),
                "blocks": len(cohort), "gaps": sum(max(0, len(b.arrivals) - 1) for b in cohort),
                "arrivals": sum(len(b.arrivals) for b in cohort),
                "eligible_seconds": len(cohort) * config["normal_seconds"]})
    if any(r["dates"] < 2 or r["gaps"] < config["minimum_gaps"] for r in rows):
        raise ValueError("Role calendar support incomplete")
    return rows


def references(blocks, history, config):
    rows = pd.concat([row_frame(b, config["ready_seconds"]) for b in blocks], ignore_index=True)
    windows = pd.concat([window_frame(b, history, 60, config["window_seconds"],
                                     config["ready_seconds"]) for b in blocks], ignore_index=True)
    if not rows.eligible.all() or not windows.eligible.all():
        raise ValueError("Normal references contain unsupported observations")
    return rows, windows


def replay(blocks, history, process, trends, stride, config):
    result = {}
    for block in blocks:
        result[block.block_id] = score_block(block, row_frame(block, config["ready_seconds"]),
            window_frame(block, history, stride, config["window_seconds"], config["ready_seconds"]),
            process, trends)
    return result


def candidate_grid(trend):
    # Values are declared before validation and never generated from test scores.
    return trend


def select(candidates, budget, a_rate):
    eligible = [c for c in candidates if a_rate <= budget and c["normal_alerts_per_hour"] <= budget]
    if not eligible:
        return {"eligible": False, "reason": "fixed_A_exceeds_budget" if a_rate > budget else "no_feasible_joint_threshold",
                "budget_per_hour": budget, "candidate_id": None}
    selected = min(eligible, key=lambda c: (-c["event_recall"], c["penalized_delay_seconds"],
                                          c["cost_units"], c["candidate_id"]))
    return {**selected, "eligible": True, "budget_per_hour": budget,
            "reason": "validation_only_frozen", "selection_role": "validation"}


def compact(metrics):
    return {k: v for k, v in metrics.items() if k not in ("events", "blocks")}


def compare_events(results):
    groups = {r["group"]: r for r in results}
    if "B" not in groups or "D" not in groups: return None
    b = {e["block_id"]: e for e in groups["B"]["events"] if not e["logical_contract_audit"]}
    d = {e["block_id"]: e for e in groups["D"]["events"] if not e["logical_contract_audit"]}
    return {"D_additional_over_B": [i for i in b if not b[i]["hit"] and d[i]["hit"]],
            "D_lost_from_B": [i for i in b if b[i]["hit"] and not d[i]["hit"]]}


def run(output, config_path=BASE / "v4_config.json"):
    config = read_config(config_path)
    output = Path(output).resolve()
    if output.exists(): raise FileExistsError("New run directory required; sealed tests cannot be rerun in place")
    output.mkdir(parents=True)
    started = time.perf_counter()
    source = (BASE / config["source_snapshot"]).resolve()
    if not source.is_dir(): raise FileNotFoundError("Fetch and verify fixed cb006179 source snapshot first")
    declaration = {"version": config["version"], "config": config, "config_sha256": digest(config),
        "source_sha": config["source_sha"], "sklearn_version": sklearn.__version__,
        "calendar_timezone": "Asia/Seoul", "stored_clock": "UTC Unix seconds",
        "clock_contract": "now observed minus first observed new execution; revisions never reset",
        "guard_sources": {"business_open": "independent synthetic schedule", "inflow_expected": "independent synthetic expected-flow schedule",
            "collector_visible": "independent synthetic collector heartbeat", "publication_fresh": "independent synthetic publication-state signal"},
        "guard_recovery_ready_seconds": 900, "window_interval": "[t-W,t)", "event_interval": "[s,e)",
        "event_hit": "s<t<e; exact onset antecedent excluded; count_contract logical audit separate",
        "window_truth": "q=overlap/W; q>0 & s<=t<e active, q>0 & t>=e recovery; raw q retained",
        "normal_exposure": "normal-only independent blocks; all groups same eligible timer intervals; exclude readiness and guards",
        "alarm_budget": "suppressed same-scope union of A and trend divided by same eligible normal exposure",
        "selection_order": ["whole-group_budget", "equal-scenario_event_recall", "miss-penalized_delay", "fixed_cost_units", "candidate_id"],
        "cost_units": "deterministic job/branch proxy used only as tied-validation comparator; wall-clock load measured separately",
        "fixed_PROCESS": "v3 global IF refitted balanced normal train/calibration/threshold; nextafter(max normal threshold score,+inf)",
        "new_trend": "cb006179 v5 RawModel IF128 and v8 ClusterDistance radius, refitted equal-info v4 features; not v12 composed predictor",
        "v12_original_refit": "blocked: development inputs, benchmark history/manifests and intermediate frozen artifacts not uploaded",
        "features": list(FEATURES), "normal_only_fit": True, "threshold_role": "threshold",
        "selection_role": "validation", "test_used_for_selection": False,
        "test_generation": "after seed-specific selection and model hashes frozen",
        "recovery_followup": "scenario blocks extend e+300+61s; eligible q0 with no raw trend/prior-stride realtime alert; right-censoring reported; q0 not necessarily completion-clean",
        "limitations": ["synthetic controlled effects, no customer generalization", "no operational threshold approval",
            "no ESB/API/DB/network E2E claim", "five seeds share generator, not five customer sites",
            "fixed 300s windows, 60s/5s sensitivity; no post-test window search"]}
    write_json(output / "declaration.json", declaration)
    all_support, final_summaries, exposures = [], [], []
    for seed in config["seeds"]:
        folder = output / f"seed_{seed}"; folder.mkdir()
        print(f"v4 seed={seed} independent references", flush=True)
        roles = {role: build_role(seed, role, config, include_scenarios=role == "validation") for role in ROLES[:4]}
        role_sources = {role: {b.source_id for b in blocks} for role, blocks in roles.items()}
        if any(role_sources[a] & role_sources[b] for i, a in enumerate(roles) for b in list(roles)[i+1:]):
            raise ValueError("Reference source leaked across roles")
        history = History.fit(roles["train"], config)
        row_refs, window_refs = {}, {}
        for role in ROLES[:3]:
            row_refs[role], window_refs[role] = references(roles[role], history, config)
        process, trends = fit_models(row_refs, window_refs, config, seed, BASE)
        metadata = {"seed": seed, "source_sha": config["source_sha"], "source_hashes": trends["if"].source_hashes,
            "rows": {n: {"rows": len(f), "sha256": frame_digest(f)} for n, f in row_refs.items()},
            "windows": {n: {"rows": len(f), "sha256": frame_digest(f)} for n, f in window_refs.items()},
            "row_features": list(process.encoder.columns), "trend_features": list(trends["if"].encoder.columns),
            "process_threshold": process.threshold, "trend_thresholds": {a: m.threshold for a, m in trends.items()},
            "history": history.support, "normal_training_role_dates": {
                r: {"first": min(b.start for b in bs), "last": max(b.end for b in bs)} for r, bs in roles.items()},
            "trend_estimator_settings": {"if": {"trees": 128, "samples": config["trend_samples"], "rotations": 1, "n_jobs": 1},
                "kmeans": {"k": config["trend_k"], "covariance": "radius", "n_init": 5, "batch_size": 2048}},
            "v12_difference": "no v12 cadence branch/historical-family/synthetic traffic prior; extra elapsed, 7-day calendar and shared guards; new v4 fitted detector"}
        joblib.dump({"process": process, "trends": trends, "history": history, "config": config}, folder / "models.joblib", compress=3)
        metadata["model_sha256"] = sha256_file(folder / "models.joblib")
        write_json(folder / "manifest.json", metadata)
        all_candidates, frozen = [], []
        for stride in config["strides"]:
            print(f"v4 seed={seed} validation stride={stride}", flush=True)
            cache = replay(roles["validation"], history, process, trends, stride, config)
            a = group_metrics(roles["validation"], cache, "A", "if", np.inf, np.inf, config["suppression_seconds"])
            for algorithm, model in trends.items():
                for group in ("A", "B", "C", "D"):
                    candidates = []
                    rule_options = config["rule_multipliers"] if group in ("B", "D") else ["off"]
                    ml_options = config["ml_threshold_offsets"] if group in ("C", "D") else ["off"]
                    for ri, rule in enumerate(rule_options):
                        for mi, offset in enumerate(ml_options):
                            ml_limit = "off" if offset == "off" else float(model.threshold + offset)
                            metrics = a if group == "A" else group_metrics(roles["validation"], cache, group, algorithm,
                                finite_limit(rule), finite_limit(ml_limit), config["suppression_seconds"])
                            item = {**compact(metrics), "group": group, "algorithm": algorithm,
                                "stride_seconds": stride, "rule_limit": rule, "ml_limit": ml_limit,
                                "cost_units": {"A": 0, "B": 1, "C": 2, "D": 3}[group],
                                "candidate_id": f"{seed}:{stride}:{algorithm}:{group}:r{ri}:m{mi}"}
                            candidates.append(item)
                    all_candidates.extend(candidates)
                    for budget in config["budgets_per_hour"]:
                        frozen.append({"seed": seed, "group": group, "algorithm": algorithm, "stride_seconds": stride,
                            **select(candidates, budget, a["normal_alerts_per_hour"])})
        write_json(folder / "validation.json", {"candidates": all_candidates, "selection": frozen})
        # Seal selection before any test block is constructed or scored.
        seal = {"selection": frozen, "selection_sha256": digest(frozen), "model_sha256": metadata["model_sha256"],
                "config_sha256": digest(config), "test_score_passes_per_stride": 1}
        write_json(folder / "selection_seal.json", seal)
        test = build_role(seed, "test", config, include_scenarios=True)
        for role, blocks in {**roles, "test": test}.items():
            all_support.extend(support_table(seed, role, blocks, config))
        test_results = []
        for stride in config["strides"]:
            print(f"v4 seed={seed} sealed test stride={stride}", flush=True)
            cache = replay(test, history, process, trends, stride, config)
            raw_windows = []
            for block in test:
                data = cache[block.block_id]
                frame = data["windows"].copy()
                frame["scenario"], frame["control"] = block.scenario, block.control
                frame["overlap_q"], frame["truth_phase"] = truth_at(block, frame.clock.to_numpy(), config["window_seconds"])
                for algorithm, scores in data["trend_scores"].items(): frame[f"{algorithm}_score"] = scores
                raw_windows.append(frame)
            pd.concat(raw_windows, ignore_index=True).to_csv(folder / f"test_windows_{stride}.csv", index=False)
            for recipe in (r for r in frozen if r["stride_seconds"] == stride):
                if not recipe["eligible"]:
                    test_results.append({**recipe, "test_status": "validation_ineligible_not_selected"})
                    continue
                metrics = group_metrics(test, cache, recipe["group"], recipe["algorithm"],
                    finite_limit(recipe["rule_limit"]), finite_limit(recipe["ml_limit"]), config["suppression_seconds"])
                within = metrics["normal_alerts_per_hour"] <= recipe["budget_per_hour"]
                item = {**recipe, **metrics, "test_status": "within_budget" if within else "test_budget_exceeded", "test_within_budget": within}
                test_results.append(item)
                final_summaries.append({"seed": seed, "stride_seconds": stride, "budget_per_hour": recipe["budget_per_hour"],
                                       "candidate_id": recipe["candidate_id"], **compact(metrics), "test_within_budget": within})
        if digest(frozen) != seal["selection_sha256"]: raise ValueError("Selection changed during sealed test")
        write_json(folder / "final_test.json", {"selection_sha256": seal["selection_sha256"], "results": test_results,
            "incremental": [{"algorithm": algorithm, "stride_seconds": stride, "budget_per_hour": budget,
                "paired_events": compare_events([r for r in test_results if r["algorithm"] == algorithm and
                    r["stride_seconds"] == stride and r["budget_per_hour"] == budget and "events" in r])}
                for algorithm in trends for stride in config["strides"] for budget in config["budgets_per_hour"]]})
        exposure = sum(config["normal_seconds"] for b in test if b.scenario == "normal") / 3600
        if exposure < 20: raise ValueError("Less than 20 independent normal hours/seed")
        exposures.append({"seed": seed, "normal_hours": exposure})
    pd.DataFrame(all_support).to_csv(output / "calendar_support.csv", index=False)
    pd.DataFrame(final_summaries).to_csv(output / "comparison.csv", index=False)
    variability = []
    summary_frame = pd.DataFrame(final_summaries)
    if len(summary_frame):
        for key, group in summary_frame.groupby(["stride_seconds", "algorithm", "group", "budget_per_hour"], sort=True):
            variability.append({"stride_seconds": int(key[0]), "algorithm": key[1], "group": key[2], "budget_per_hour": float(key[3]),
                "seeds": group.seed.astype(int).tolist(), "event_recall_range": [float(group.event_recall.min()), float(group.event_recall.max())],
                "event_recall_mean": float(group.event_recall.mean()), "normal_hours": float(group.normal_hours.sum()),
                "normal_alerts": int(group.normal_alerts.sum()), "test_budget_failures": int((~group.test_within_budget).sum())})
    total = sum(e["normal_hours"] for e in exposures)
    if total < 100: raise ValueError("Less than 100 effective independent normal hours")
    write_json(output / "summary.json", {"exposure": exposures, "independent_normal_hours": total,
        "variability": variability, "wall_seconds": time.perf_counter() - started,
        "confidence": "seed/event/day blocks are units; overlapping windows not independent; report seed ranges, not customer confidence",
        "zero_alert_poisson_95_upper_per_hour": 2.995732273553991 / total,
        "poisson_limit_scope": "descriptive under Poisson assumption only; temporal synthetic dependence and real customer rates unvalidated"})
    return {"output": str(output), "normal_hours": total, "seeds": len(config["seeds"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", default=str(BASE / "v4_config.json"))
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.output, arguments.config), ensure_ascii=False))
