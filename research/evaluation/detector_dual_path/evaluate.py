"""Synthetic PROCESS/TIME_WINDOW comparisons with explicit evaluation units.

Candidate choice uses validation only. Final test runs once after the selected
configuration is frozen. An analytical hit union is not an alert/severity policy.
"""
from dataclasses import asdict
import csv
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from .models import fit_pair, frame_digest, sha256_file
from .performance import benchmark_pair


def confusion(records):
    counts = dict(tn=0, fp=0, fn=0, tp=0)
    statuses = {}
    for record in records:
        status = record.get("status", "complete")
        statuses[status] = statuses.get(status, 0) + 1
        if record.get("prediction") is None:
            continue
        truth, prediction = bool(record["truth"]), bool(record["prediction"])
        counts[("tp" if prediction else "fn") if truth else ("fp" if prediction else "tn")] += 1
    tp, fp, tn, fn = (counts[name] for name in ("tp", "fp", "tn", "fn"))
    judged = tp + fp + tn + fn
    return {**counts, "opportunities": len(records), "judged": judged,
            "unavailable": len(records) - judged, "statuses": statuses,
            "coverage": judged / len(records) if records else None,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            "fpr": fp / (tn + fp) if tn + fp else None}


def event_summary(episodes, row_records, window_records, mode, suppression_seconds=60,
                  normal_monitor_offset_seconds=900):
    if mode not in ("realtime", "trend", "dual"):
        raise ValueError("Unknown path mode")
    rows = [record for record in row_records if record.get("prediction") is True]
    windows = [record for record in window_records if record.get("prediction") is True]
    records = (rows if mode == "realtime" else windows if mode == "trend" else rows + windows)
    all_records = (row_records if mode == "realtime" else window_records if mode == "trend" else row_records + window_records)
    all_by_case, rows_by_case, windows_by_case = {}, {}, {}
    for items, target in ((all_records, all_by_case), (rows, rows_by_case), (windows, windows_by_case)):
        for item in items:
            target.setdefault(item["case_id"], []).append(item)
    by_case = {}
    for record in records:
        by_case.setdefault(record["case_id"], []).append(record)
    row_hits, trend_hits, records_by_event, event_records = set(), set(), [], []
    control_seconds, raw_warnings, suppressed_warnings = 0., 0, 0
    for episode in episodes:
        hits = sorted(by_case.get(episode.case_id, []), key=lambda item: item["clock"])
        if episode.control:
            monitor_start = episode.start + normal_monitor_offset_seconds
            control_seconds += max(0., episode.end - monitor_start)
            hits = [record for record in hits if monitor_start <= record["clock"] < episode.end]
            raw_warnings += len(hits)
            last = -float("inf")
            for record in hits:
                if record["clock"] - last >= suppression_seconds:
                    suppressed_warnings += 1
                    last = record["clock"]
            judged = any(record.get("prediction") is not None and monitor_start <= record["clock"] < episode.end
                         for record in all_by_case.get(episode.case_id, []))
            event_records.append({"truth": False, "prediction": bool(hits) if judged else None,
                                  "status": "complete" if judged else "unavailable"})
            continue
        active_rows = [r for r in rows_by_case.get(episode.case_id, []) if episode.anomaly_start <= r["clock"] < episode.anomaly_end]
        active_windows = [r for r in windows_by_case.get(episode.case_id, []) if episode.anomaly_start <= r["clock"] < episode.anomaly_end]
        if active_rows:
            row_hits.add(episode.case_id)
        if active_windows:
            trend_hits.add(episode.case_id)
        active = [r for r in hits if episode.anomaly_start <= r["clock"] < episode.anomaly_end]
        judged = any(episode.anomaly_start <= record["clock"] < episode.anomaly_end
                     and record.get("prediction") is not None for record in all_by_case.get(episode.case_id, []))
        first = active[0]["clock"] if active else None
        records_by_event.append({"case_id": episode.case_id, "scenario": episode.scenario,
                                 "hit": bool(active), "first_delay_seconds": first - episode.anomaly_start if first else None,
                                 "row_supported": episode.scenario != "input_stop"})
        event_records.append({"truth": True, "prediction": bool(active) if judged else None,
                              "status": "complete" if judged else "unsupported" if mode == "realtime" and episode.scenario == "input_stop" else "unavailable"})
    hours = control_seconds / 3600
    delays = [record["first_delay_seconds"] for record in records_by_event if record["first_delay_seconds"] is not None]
    return {"mode": mode, "confusion": confusion(event_records), "event_count": len(records_by_event),
            "normal_monitor_hours": hours, "normal_raw_warnings": raw_warnings,
            "normal_suppressed_warnings": suppressed_warnings,
            "normal_raw_warnings_per_hour": raw_warnings / hours if hours else None,
            "normal_suppressed_warnings_per_hour": suppressed_warnings / hours if hours else None,
            "suppression_seconds": suppression_seconds, "realtime_hit": len(row_hits),
            "normal_monitor_offset_seconds": normal_monitor_offset_seconds,
            "trend_hit": len(trend_hits), "both_hit": len(row_hits & trend_hits),
            "trend_additional_hit": len(trend_hits - row_hits),
            "all_event_detection_rate": sum(record["hit"] for record in records_by_event) / len(records_by_event) if records_by_event else None,
            "first_delay_p50_seconds": float(np.median(delays)) if delays else None,
            "first_delay_p95_seconds": float(np.quantile(delays, .95)) if delays else None,
            "events": records_by_event,
            "limitations": ["analytical union only; operational OR/severity/notification policy not selected",
                            "event opportunity covers the injected active interval; late recovery alarms not event hits",
                            "input_stop has no PROCESS row support; event denominator still includes it",
                            "normal time includes paired controls after the common 900-second warmup only"]}


def assert_independent_sources(episodes):
    by_role = {}
    for episode in episodes:
        by_role.setdefault(episode.split, set()).add(episode.source_template_id)
    required = {"train", "calibration", "threshold", "validation", "test"}
    if set(by_role) != required:
        raise ValueError("Five reference/selection/test roles required")
    for first in required:
        for second in required - {first}:
            if by_role[first] & by_role[second]:
                raise ValueError("Source template leaked across split roles")
    return {name: sorted(values) for name, values in by_role.items()}


def _clock_grid(episode, stride):
    return np.arange(episode.start + stride, episode.end + .001, stride)


def _episode_features(episode, lookback, stride, history, baselines, *, include_warmup=False):
    from .replay import ReplayState
    from process_anomaly_pipeline.data import prepare
    state = ReplayState(episode.start, source=episode.source, scope=episode.case_id)
    events = sorted(episode.events, key=lambda event: (event.observed_at, event.revision))
    position, row_raw, row_meta, window_frames, window_meta = 0, [], [], [], []
    for clock in _clock_grid(episode, stride):
        while position < len(events) and events[position].observed_at <= clock:
            event = events[position]
            position += 1
            if not state.ingest(event):
                continue
            raw = state.process_record(event)
            completed = raw.get("endTime") is not None
            row_raw.append(raw)
            row_meta.append({"case_id": episode.case_id, "truth": episode.row_label(event),
                             "clock": event.observed_at, "event_at": event.event_at,
                             "status": "complete" if completed else "incomplete_process",
                             "prediction": None, "margin": None})
        frame = state.window_frame(float(clock), lookback, history, baselines)
        status = frame.iloc[0].window_state
        available = status in ("ready", "no_arrivals", "no_completions") or include_warmup
        window_frames.append(frame)
        window_meta.append({"case_id": episode.case_id, "truth": not episode.control and episode.anomaly_start <= clock < episode.anomaly_end,
                            "clock": float(clock), "status": "complete" if available else status,
                            "prediction": None, "margin": None,
                            "window_start": float(clock) - lookback, "window_end": float(clock)})
    rows = prepare(row_raw)
    windows = pd.concat(window_frames, ignore_index=True) if window_frames else pd.DataFrame()
    return rows, row_meta, windows, window_meta


def _baselines(train_rows):
    return {"process": train_rows.groupby(["adapterType", "processId"], observed=True).duration_ms.median().to_dict(),
            "adapter": train_rows.groupby("adapterType", observed=True).duration_ms.median().to_dict(),
            "global": float(train_rows.duration_ms.median())}


def _references(episodes, history, lookback, stride):
    from process_anomaly_pipeline.data import prepare
    from .replay import ReplayState
    row_partitions = {}
    roles = ("train", "calibration", "threshold")
    for role in roles:
        frames = []
        for episode in episodes:
            if episode.split != role or not episode.control:
                continue
            state = ReplayState(episode.start, source=episode.source, scope=episode.case_id)
            raw = []
            for event in episode.events:
                if state.ingest(event) and event.fields.get("endTime") is not None:
                    raw.append({**state.process_record(event), "source_template_id": episode.source_template_id})
            frames.append(prepare(raw))
        row_partitions[role] = pd.concat(frames, ignore_index=True)
    baselines = _baselines(row_partitions["train"])
    window_partitions = {}
    for role in roles:
        frames = []
        for episode in episodes:
            if episode.split != role or not episode.control:
                continue
            _, _, windows, _ = _episode_features(episode, lookback, stride, history, baselines, include_warmup=True)
            windows = windows.loc[windows.window_state.ne("warmup")].copy()
            if windows.empty:
                raise ValueError("No full-window normal reference; extend warmup before fitting")
            windows["source_template_id"] = episode.source_template_id
            frames.append(windows)
        window_partitions[role] = pd.concat(frames, ignore_index=True)
    return row_partitions, window_partitions, baselines


def _score_episodes(episodes, pair, history, baselines, stride):
    row_frames, window_frames, row_meta, window_meta = [], [], [], []
    for episode in episodes:
        rows, rm, windows, wm = _episode_features(episode, pair.lookback_seconds, stride, history, baselines)
        row_frames.append(rows)
        window_frames.append(windows)
        row_meta.extend(rm)
        window_meta.extend(wm)
    rows = pd.concat(row_frames, ignore_index=True)
    windows = pd.concat(window_frames, ignore_index=True)
    with threadpool_limits(limits=1):
        for frame, records, scorer in ((rows, row_meta, pair.score_rows), (windows, window_meta, pair.score_window)):
            indices = [index for index, record in enumerate(records) if record["status"] == "complete"]
            if indices:
                margins = scorer(frame.iloc[indices])
                for index, margin in zip(indices, margins):
                    records[index].update(margin=float(margin), prediction=bool(margin > 0))
    units = {"process_revision": confusion(row_meta), "time_window": confusion(window_meta)}
    results = {mode: event_summary(episodes, row_meta, window_meta, mode) for mode in ("realtime", "trend", "dual")}
    return {"data_role": episodes[0].split, "algorithm": pair.algorithm, "lookback_seconds": pair.lookback_seconds, "stride_seconds": stride,
            "units": units, "paths": results, "feature_hashes": {"row": frame_digest(rows), "window": frame_digest(windows)},
            "quality_clock": "virtual_observed_time_not_wall_clock_latency"}, rows, windows


def pareto_candidates(candidates):
    def objectives(candidate):
        dual = candidate["paths"]["dual"]
        delay = dual["first_delay_p95_seconds"]
        return (dual["trend_additional_hit"], -dual["normal_suppressed_warnings_per_hour"],
                -delay if delay is not None else -float("inf"))
    frontier = []
    for candidate in candidates:
        scores = objectives(candidate)
        if not any(all(a >= b for a, b in zip(objectives(other), scores)) and any(a > b for a, b in zip(objectives(other), scores)) for other in candidates):
            frontier.append(candidate)
    return frontier


def choose_candidate(candidates):
    frontier = pareto_candidates(candidates)
    if not frontier:
        raise ValueError("No validation candidates")
    # The objectives and tie-breaks are frozen before any final-test computation.
    def key(candidate):
        dual = candidate["paths"]["dual"]
        return (-dual["trend_additional_hit"], dual["normal_suppressed_warnings_per_hour"],
                dual["first_delay_p95_seconds"] if dual["first_delay_p95_seconds"] is not None else float("inf"),
                candidate.get("compute_p95_ms", float("inf")), candidate["lookback_seconds"],
                -candidate["stride_seconds"], candidate["algorithm"])
    return min(frontier, key=key), frontier


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _compute_probe(pair, rows, windows):
    # Validation-only local compute tie-break, not customer latency acceptance.
    samples = []
    with threadpool_limits(limits=1):
        pair.score_rows(rows.iloc[:1])
        pair.score_window(windows.iloc[:1])
        for _ in range(3):
            for index in range(min(12, len(windows))):
                start = time.perf_counter()
                pair.score_window(windows.iloc[[index]])
                samples.append((time.perf_counter() - start) * 1000)
    return {"compute_p95_ms": float(np.quantile(samples, .95)),
            "compute_probe": {"role": "validation_only", "measurement": "local_single_window_model_compute",
                              "warmup_each_path": 1, "repeats": 3, "samples": len(samples)}}


def _candidate_csv(path, candidates):
    fields = ("data_role", "algorithm", "lookback_seconds", "stride_seconds", "mode", "event_recall", "event_f1",
              "trend_additional_hit", "normal_raw_warnings_per_hour", "normal_suppressed_warnings_per_hour",
              "first_delay_p95_seconds", "row_coverage", "window_coverage")
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for candidate in candidates:
            for mode, values in candidate["paths"].items():
                writer.writerow({"data_role": candidate["data_role"], "algorithm": candidate["algorithm"], "lookback_seconds": candidate["lookback_seconds"],
                                 "stride_seconds": candidate["stride_seconds"], "mode": mode,
                                 "event_recall": values["confusion"]["recall"], "event_f1": values["confusion"]["f1"],
                                 **{field: values[field] for field in ("trend_additional_hit", "normal_raw_warnings_per_hour", "normal_suppressed_warnings_per_hour", "first_delay_p95_seconds")},
                                 "row_coverage": candidate["units"]["process_revision"]["coverage"],
                                 "window_coverage": candidate["units"]["time_window"]["coverage"]})


def run_experiment(config, output_path):
    from .replay import FrozenHistory
    from .scenarios import build_suite, normal_history_observations
    output = Path(output_path)
    output.mkdir(parents=True, exist_ok=True)
    owned_names = ("validation.json", "final_test.json", "performance.json", "comparison.csv", "selection.json")
    if any((output / name).exists() for name in owned_names):
        raise FileExistsError("Use a fresh run directory; existing results are immutable")
    seed = int(config.get("seed", 45))
    cases = int(config.get("cases_per_scenario", 20))
    episodes = build_suite(seed, cases)
    sources = assert_independent_sources(episodes)
    observations = normal_history_observations(seed)
    history = FrozenHistory.fit_observations(observations, max(event.observed_at for event in observations) + 1)
    validation = [episode for episode in episodes if episode.split == "validation"]
    test = [episode for episode in episodes if episode.split == "test"]
    model_config = {"seed": seed, "n_estimators": int(config.get("n_estimators", 32)),
                    "max_samples": int(config.get("max_samples", 256)), "k": int(config.get("k", 6)),
                    "quantile": float(config.get("quantile", .99)), "kmeans_batch_size": 256,
                    "kmeans_n_init": 3, "kmeans_max_iter": 50}
    pairs, baselines_by_spec, candidates = {}, {}, []
    def evaluate_candidate(algorithm, lookback, stride):
        cache_key = (algorithm, lookback, stride)
        if cache_key not in pairs:
            refs, windows, baselines = _references(episodes, history, lookback, stride)
            pairs[cache_key] = fit_pair(algorithm, refs, windows, lookback, model_config)
            baselines_by_spec[cache_key] = baselines
        result, rows, windows = _score_episodes(validation, pairs[cache_key], history, baselines_by_spec[cache_key], stride)
        result.update(_compute_probe(pairs[cache_key], rows, windows))
        candidates.append(result)
        return result
    selected_by_algorithm = {}
    for algorithm in ("if", "kmeans"):
        first = [evaluate_candidate(algorithm, 300, int(stride)) for stride in config.get("candidate_strides", [60, 15, 5])]
        first_choice, _ = choose_candidate(first)
        stride = first_choice["stride_seconds"]
        second = [first_choice]
        for lookback in config.get("candidate_windows", [60, 300, 900]):
            if int(lookback) != 300:
                second.append(evaluate_candidate(algorithm, int(lookback), stride))
        choice, frontier = choose_candidate(second)
        selected_by_algorithm[algorithm] = {"algorithm": algorithm, "lookback_seconds": choice["lookback_seconds"],
                                            "stride_seconds": choice["stride_seconds"],
                                            "pareto": [{"lookback_seconds": item["lookback_seconds"],
                                                        "stride_seconds": item["stride_seconds"]} for item in frontier]}
    frozen_selection = {"data_role": "validation_only", "staged_search_not_global_optimum": True,
                        "objective_order": ["additional_event_hits", "normal_warnings_per_hour", "first_delay_p95"],
                        "tie_break": ["available_measured_compute", "shorter_lookback", "slower_stride"],
                        "thresholds_pre_fitted_from_independent_threshold_role": True,
                        "selected": selected_by_algorithm}
    _write_json(output / "selection.json", frozen_selection)
    final, performance, artifacts = [], {}, []
    import joblib
    for algorithm, choice in selected_by_algorithm.items():
        key = (algorithm, choice["lookback_seconds"], choice["stride_seconds"])
        pair, baselines = pairs[key], baselines_by_spec[key]
        result, rows, windows = _score_episodes(test, pair, history, baselines, choice["stride_seconds"])
        final.append(result)
        # The same frozen pair is reused in both contention modes.
        row_ready = rows.loc[rows.endTime.notna()].head(int(config.get("performance_jobs", 120)))
        window_ready = windows.loc[windows.window_state.isin(("ready", "no_arrivals", "no_completions"))].head(int(config.get("performance_jobs", 120)))
        performance[algorithm] = benchmark_pair(pair, [row_ready.iloc[[index]] for index in range(len(row_ready))],
                                                [window_ready.iloc[[index]] for index in range(len(window_ready))],
                                                repeats=int(config.get("performance_repeats", 3)),
                                                warmup=int(config["warmup"]),
                                                input_rate_hz=float(config["input_rate_hz"]))
        artifact = output / f"synthetic-{algorithm}-pair.joblib"
        if artifact.exists():
            raise FileExistsError("Do not overwrite fitted pair")
        joblib.dump(pair, artifact)
        artifacts.append({"path": artifact.name, "sha256": sha256_file(artifact), "metadata": pair.metadata()})
    protocol = {"label": "synthetic_mechanical_pilot", "customer_accuracy_claim": False,
                "timestamp_resolution": "second-scale synthetic generation; observations include fractional delays",
                "seed": seed, "cases_per_scenario_per_split": cases, "source_template_roles": sources,
                "source_template_hash": hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
                "history_cutoff": history.fitted_until, "history_source": "dedicated_pre_train_normal_synthetic_history",
                "model_config": model_config, "artifacts": artifacts,
                "final_test_evaluations_per_algorithm": 1,
                "limitations": ["synthetic generator and labelled interventions; no customer domain generalization claim",
                                "five independent source-template roles; paired normal/injected templates remain in one role",
                                "cold-start windows unavailable; full-window normal references used for fitting",
                                "algorithm comparison uses common 300-second/60-second validation baseline; selected recipes may differ",
                                "no queue/network/PostgreSQL/server API or deployed detector implementation",
                                "test outcomes never used to retune thresholds or candidate choice",
                                "no quantitative operational acceptance budget approved; recommendation conditional"]}
    _write_json(output / "validation.json", {"protocol": protocol, "candidates": candidates})
    _write_json(output / "final_test.json", {"protocol": protocol, "selection": frozen_selection, "results": final})
    _write_json(output / "performance.json", performance)
    _candidate_csv(output / "comparison.csv", candidates + final)
    return {"protocol": protocol, "selection": frozen_selection, "validation_candidates": len(candidates),
            "final_test": final, "performance": performance,
            "files": list(owned_names) + [item["path"] for item in artifacts]}
