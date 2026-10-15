"""One pinned group per process, real cadence, independent RT1/trend1 queues.

An external coordinator launches A/B/C/D with a shared UTC barrier. No sleeping
timer occupies an inference slot. CPU affinity is not an exclusive reservation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import threading
import time

import joblib
import numpy as np
import pandas as pd
import psutil
from threadpoolctl import threadpool_info, threadpool_limits

from .models import detector_margin, sha256_file
from .performance import ResourceSampler
from .v4 import BASE, finite_limit, write_json
from .v4_core import Block, digest, window_frame
from process_anomaly_pipeline.data import prepare


def trace(seed, rate, warmup, measurement):
    """Shared deterministic mixed trace; readiness prefix is purely normal."""
    rng = np.random.default_rng(seed + 81732)
    start = pd.Timestamp("2026-05-18T12:10:00+09:00").timestamp()
    seconds = 900 + warmup + measurement
    offsets = np.arange(int(seconds * rate), dtype=float) / rate
    durations = rng.lognormal(np.log(.25), .25, len(offsets))
    counts = rng.integers(1, 9, len(offsets)).astype(float)
    mixed = offsets >= 900
    ids = np.arange(len(offsets))
    durations[mixed & (ids % 20 == 0)] = np.inf
    durations[mixed & (ids % 20 == 1)] += 8
    counts[mixed & (ids % 20 == 2)] = 1000
    # Generate the original condition-rate arrays before selecting warmup rows:
    # RNG consumption, readiness and measurement values remain unchanged.
    keep = (offsets < 900) | (offsets >= 900 + warmup) | (ids % (rate // 10) == 0)
    offsets, durations, counts = offsets[keep], durations[keep], counts[keep]
    arrivals = start + offsets
    # This trace consists of available PROCESS snapshots at receipt: completed
    # records already include duration; unfinished records never expose an end.
    completions = np.where(np.isfinite(durations), arrivals, np.inf)
    return Block("load", "load", "test", "load_mix", True, start, start + seconds,
                 arrivals, durations, counts, completions, np.zeros(len(offsets), bool))


def trace_hash(block, ready_only=False):
    mask = block.arrivals < block.start + 900 if ready_only else np.ones(len(block.arrivals), bool)
    serial = np.column_stack([block.arrivals[mask], np.nan_to_num(block.durations[mask], posinf=-1), block.counts[mask]])
    import hashlib
    return hashlib.sha256(serial.astype("<f8").tobytes()).hexdigest()


def process_input(block, index):
    at, duration, count = block.arrivals[index], block.durations[index], block.counts[index]
    complete = np.isfinite(duration)
    start = at - duration if complete else at
    return {"transactionId": f"load-{index}", "processId": "SYNTHETIC_P00", "processHubId": "SYNTHETIC_HUB",
        "adapterType": "HTTP", "retryCount": 0, "totalCount": float(count),
        "successCount": float(count) if complete else None, "errorCount": 0 if complete else None,
        "startTime": pd.Timestamp(start, unit="s", tz="UTC").isoformat(),
        "endTime": pd.Timestamp(at, unit="s", tz="UTC").isoformat() if complete else None,
        "status": "S" if complete else "P"}


class PathQueue:
    def __init__(self, name, limit, process):
        self.name, self.process = name, process
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"v4-{name}")
        self.limit = threading.BoundedSemaphore(limit)
        self.lock = threading.Lock()
        self.records, self.futures = [], []
        self.pending, self.peak_pending = 0, 0

    def not_submitted(self, scheduled, job_id, source_clock):
        with self.lock:
            self.records.append({"job_id": job_id, "path": self.name, "scheduled": scheduled,
                "submitted": None, "source_clock": source_clock,
                "status": "not_submitted_measurement_end"})

    def submit(self, scheduled, job, job_id, source_clock, input_deadline):
        submitted = time.perf_counter()
        if submitted >= input_deadline:
            self.not_submitted(scheduled, job_id, source_clock)
            return
        record = {"job_id": job_id, "path": self.name, "scheduled": scheduled,
            "submitted": submitted, "source_clock": source_clock,
            "producer_lateness_seconds": max(0., submitted - scheduled)}
        with self.lock:
            self.records.append(record)
        if not self.limit.acquire(blocking=False):
            record["status"] = "drop_queue_overflow"
            return
        with self.lock:
            self.pending += 1; self.peak_pending = max(self.peak_pending, self.pending)
        def execute():
            record["started"] = time.perf_counter()
            status = "complete"
            try:
                record["worker_affinity"] = self.process.cpu_affinity()
                record["prediction"] = bool(job())
            except Exception as exc:
                status = "error"
                record["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                record["finished"] = time.perf_counter()
                record["queue_wait_seconds"] = record["started"] - record["submitted"]
                record["compute_seconds"] = record["finished"] - record["started"]
                record["local_latency_seconds"] = record["finished"] - record["scheduled"]
                with self.lock:
                    if "status" not in record: record["status"] = status
                    self.pending -= 1
                self.limit.release()
        self.futures.append(self.executor.submit(execute))

    def close(self, deadline):
        _, pending = wait(self.futures, timeout=max(0., deadline - time.perf_counter()))
        for future in pending: future.cancel()
        with self.lock:
            for record in self.records:
                if "status" not in record:
                    record["status"] = "timeout_running" if "started" in record else "pending_at_drain_deadline"
        self.executor.shutdown(wait=False, cancel_futures=True)
        return len(pending)


def path_summary(queue, scheduled_jobs, measurement):
    records = queue.records
    complete = [r for r in records if r["status"] == "complete"]
    failures = sum(r["status"] != "complete" for r in records)
    latency = [r["local_latency_seconds"] for r in complete]
    def quantile(name, probability):
        values = [r[name] for r in complete]
        return float(np.quantile(values, probability)) if values else None
    not_submitted = sum(r["status"] == "not_submitted_measurement_end" for r in records)
    return {"scheduled_jobs": scheduled_jobs, "planned_records": len(records),
        "submitted_records": len(records) - not_submitted, "not_submitted_measurement_end": not_submitted,
        "complete": len(complete),
        "failed_or_unprocessed": failures, "drops": sum(r["status"] == "drop_queue_overflow" for r in records),
        "pending_or_timeout": sum(r["status"] in ("timeout_running", "pending_at_drain_deadline") for r in records),
        "errors": sum(r["status"] == "error" for r in records),
        "throughput_per_second": len(complete) / measurement, "peak_pending": queue.peak_pending,
        "completion_latency_p95_seconds": quantile("local_latency_seconds", .95),
        "completion_latency_p99_seconds": quantile("local_latency_seconds", .99) if queue.name == "realtime" else None,
        "completion_latency_max_seconds": max(latency, default=None),
        "producer_lateness_p95_seconds": quantile("producer_lateness_seconds", .95),
        "queue_wait_p95_seconds": quantile("queue_wait_seconds", .95),
        "compute_p95_seconds": quantile("compute_seconds", .95),
        "complete_only_over_3s_fraction": sum(x > 3 for x in latency) / len(complete) if complete else None,
        "all_scheduled_missed_3s_or_failed_fraction": (failures + sum(x > 3 for x in latency)) / scheduled_jobs if scheduled_jobs else None,
        "trend_p99_withheld_small_sample": queue.name == "trend"}


def phase(bundle, recipe, block, *, rate, stride, source_offset, duration, drain, anchor, measurement=False):
    process = psutil.Process()
    realtime = PathQueue("realtime", 1000, process)
    trend = PathQueue("trend", 1000, process)
    models, history = bundle["trends"], bundle["history"]
    group, algorithm = recipe["group"], recipe["algorithm"]
    source_begin = block.start + source_offset
    row_indices = np.flatnonzero((block.arrivals >= source_begin) & (block.arrivals < source_begin + duration))
    if len(row_indices) != int(duration * rate):
        raise ValueError("Trace phase rows do not match the predeclared rate and duration")
    row_due = np.arange(len(row_indices), dtype=float) / rate
    trend_due = np.arange(0., duration, stride) if group != "A" else np.empty(0)
    timeline = sorted([(float(t), "realtime", int(i)) for t, i in zip(row_due, row_indices)] +
                      [(float(t), "trend", i) for i, t in enumerate(trend_due)], key=lambda item: (item[0], item[1]))
    with ResourceSampler(interval=.1) as resources:
        input_deadline = anchor + duration
        for relative, path, index in timeline:
            scheduled = anchor + relative
            delay = scheduled - time.perf_counter()
            if delay > 0: time.sleep(delay)  # Producer/timer, outside both executors.
            source_clock = block.start + source_offset + relative
            queue = realtime if path == "realtime" else trend
            if time.perf_counter() >= input_deadline:
                queue.not_submitted(scheduled, index, source_clock)
                continue
            if path == "realtime":
                # Input receipt occurred in the producer. Features/prepare/score
                # happen inside the timed job rather than precomputed frames.
                raw = process_input(block, index)
                def row_job(raw=raw):
                    frame = prepare([raw])
                    logical = raw["successCount"] is not None and raw["successCount"] + raw["errorCount"] != raw["totalCount"]
                    return logical or detector_margin(bundle["process"], frame)[0] > 0
                realtime.submit(scheduled, row_job, index, source_clock, input_deadline)
            else:
                # Snapshot at scheduled timer time, not worker-start time; the
                # causal feature function excludes all later planned arrivals.
                def trend_job(clock=source_clock):
                    frame = window_frame(block, history, stride, clocks=[clock])
                    if not frame.eligible.iloc[0]: return False
                    result = group in ("B", "D") and frame.rule_score.iloc[0] > finite_limit(recipe["rule_limit"])
                    if group in ("C", "D"):
                        result |= models[algorithm].score(frame)[0] > finite_limit(recipe["ml_limit"])
                    return result
                trend.submit(scheduled, trend_job, index, source_clock, input_deadline)
        # Hold the requested interval even if last due was just before its end.
        delay = anchor + duration - time.perf_counter()
        if delay > 0: time.sleep(delay)
        deadline = anchor + duration + drain
        pending_rt = realtime.close(deadline)
        pending_trend = trend.close(deadline)
    return {"measurement": measurement, "duration_seconds": duration, "rate": rate, "stride_seconds": stride,
        "fixed_input_end_perf_counter": anchor + duration,
        "fixed_drain_end_perf_counter": anchor + duration + drain,
        "cpu_seconds_including_drain": resources.cpu_seconds, "wall_seconds_including_drain": resources.wall_seconds,
        "peak_rss_bytes_including_drain": resources.peak_rss_bytes, "host_cpu_count": psutil.cpu_count(),
        "realtime": path_summary(realtime, len(row_due), duration), "trend": path_summary(trend, len(trend_due), duration),
        "records": realtime.records + trend.records, "pending_after_drain": pending_rt + pending_trend}


def wait_utc(epoch):
    while True:
        remaining = epoch - time.time()
        if remaining <= 0: return
        time.sleep(min(remaining, .2))


def warmup_complete(warm):
    return not warm["pending_after_drain"] and all(
        warm[path]["complete"] == warm[path]["scheduled_jobs"] and
        not warm[path]["failed_or_unprocessed"]
        for path in ("realtime", "trend"))


def resource_readback(bundle):
    pools = [{"user_api": x["user_api"], "internal_api": x["internal_api"],
              "num_threads": x["num_threads"]} for x in threadpool_info()]
    estimators = {"process": bundle["process"].model}
    for algorithm, model in bundle["trends"].items():
        raw = model.raw_model
        if hasattr(raw, "models"):
            estimators.update({f"trend_{algorithm}_{i}": estimator for i, estimator in enumerate(raw.models)})
        elif hasattr(raw, "model"):
            estimators[f"trend_{algorithm}"] = raw.model
    jobs = {name: getattr(estimator, "n_jobs", "N/A") for name, estimator in estimators.items()}
    return {"threadpools": pools, "estimator_n_jobs": jobs,
            "validated": bool(pools) and all(x["num_threads"] == 1 for x in pools) and
                         all(value == 1 or value == "N/A" for value in jobs.values())}


def measurement_release(path, trace_sha256, ready_sha256, timeout):
    """Coordinator publishes this once all four warmups have fully completed."""
    path = Path(path)
    deadline = time.monotonic() + timeout
    while not path.is_file():
        if time.monotonic() >= deadline:
            raise RuntimeError("Common measurement release missing after all-group readiness deadline")
        time.sleep(.1)
    release = json.loads(path.read_text(encoding="utf-8"))
    outputs = release["ready_outputs"]
    if set(outputs) != set("ABCD") or len(set(outputs.values())) != 4:
        raise ValueError("Measurement release requires four distinct A/B/C/D ready outputs")
    for group, folder in outputs.items():
        folder = Path(folder)
        if not (folder / "ready.flag").is_file():
            raise RuntimeError(f"Group {group} is not ready; measurement not started")
        contract = json.loads((folder / "ready_contract.json").read_text(encoding="utf-8"))
        warm = json.loads((folder / "warmup.json").read_text(encoding="utf-8"))
        if (contract["group"] != group or contract["trace_sha256"] != trace_sha256 or
                contract["ready_snapshot_sha256"] != ready_sha256 or not warmup_complete(warm)):
            raise ValueError(f"Group {group} has incomplete warmup or mismatched trace/readiness")
    epoch = float(release["measurement_epoch_utc"])
    if not np.isfinite(epoch) or epoch < time.time() + .2:
        raise RuntimeError("Common measurement release arrived too late")
    return epoch, release


def run_trial(run_dir, output, group, algorithm, seed, rate, stride, cpu, warmup_epoch,
              measurement_epoch, repeat, *, smoke=False, executed_repeats=4, release_path=None):
    if rate not in (10, 100):
        raise ValueError("Predeclared measurement rates are 10/100Hz; computational warmup is fixed at 10Hz")
    if executed_repeats < 3 or not 0 <= repeat < executed_repeats:
        raise ValueError("At least three executed repetitions and a valid repeat index required")
    run_dir, output = Path(run_dir).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    psutil.Process().cpu_affinity([cpu])
    if psutil.Process().cpu_affinity() != [cpu]: raise RuntimeError("Affinity was not applied")
    declaration = json.loads((run_dir / "declaration.json").read_text(encoding="utf-8"))
    config = declaration["config"]
    folder = run_dir / f"seed_{seed}"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if sha256_file(folder / "models.joblib") != manifest["model_sha256"]: raise ValueError("Model hash changed")
    for relative, expected in manifest["source_hashes"].items():
        if sha256_file(BASE / config["source_snapshot"] / "code" / relative) != expected:
            raise ValueError("Source snapshot changed after fit")
    sys.path.insert(0, str(BASE / config["source_snapshot"] / "code"))
    bundle = joblib.load(folder / "models.joblib")  # Only this run's locally fitted, hash-verified artifact.
    seal = json.loads((folder / "selection_seal.json").read_text(encoding="utf-8"))
    if digest(seal["selection"]) != seal["selection_sha256"]: raise ValueError("Selection seal changed")
    selected = [r for r in seal["selection"] if r["group"] == group and r["algorithm"] == algorithm and
                r["stride_seconds"] == stride and r["budget_per_hour"] == 1.]
    if len(selected) != 1: raise ValueError("Ambiguous frozen load recipe")
    recipe = selected[0]
    if not recipe["eligible"]:
        # Keep full A/B/C/D workload even if no effectiveness recipe was viable;
        # this declared preset is a cost probe, not an approved detector result.
        recipe = {**recipe, "rule_limit": 4., "ml_limit": float(bundle["trends"][algorithm].threshold + 3.),
                  "cost_probe_preset": "validation_ineligible_conservative_declared_preset"}
    duration = 60 if smoke else config["load_measurement_seconds"]
    warmup = config["load_warmup_seconds"]
    block = trace(seed, rate, warmup, duration)
    contract = {"group": group, "algorithm": algorithm, "seed": seed, "rate": rate, "stride_seconds": stride,
        "repeat": repeat, "current_repeat_zero_based": repeat, "smoke": smoke,
        "affinity": [cpu], "affinity_exclusive": False,
        "planned_default_repeats": config["load_repeats"], "intended_full_repeat_total": executed_repeats,
        "current_child_trial_count": 1, "completed_full_repeat_total": "external_coordinator_reports_after_all_trials",
        "repeat_override_reason": "four cyclic CPU assignments on shared Windows host; sealed quality config unchanged",
        "real_time_workers": 1, "trend_workers": 1, "blas_threads": 1, "sklearn_n_jobs": 1,
        "trace_sha256": trace_hash(block), "ready_snapshot_sha256": trace_hash(block, True),
        "ready_observation_seconds": 900, "ready_prefix_normal_only": True,
        "warmup_seconds": warmup, "measurement_seconds": duration,
        "computational_warmup_rate_hz": 10, "warmup_scheduled_rows": int(10 * warmup),
        "warmup_trace_contract": "condition-rate RNG arrays unchanged; only 10Hz-selected warmup arrivals visible; ready prefix and measurement rows preserved",
        "measurement_scheduled_rows": int(rate * duration), "queue_limit_each_path": 1000,
        "warmup_epoch_utc": warmup_epoch, "measurement_epoch_utc": measurement_epoch,
        "all_group_ready_release_required": release_path is not None,
        "warmup_ready_condition": "both paths complete==scheduled_jobs; no drops/errors/pending; empty queues",
        "drain_seconds": config["load_drain_seconds"], "recipe": recipe,
        "phase_deadline_contract": "stop submissions at anchor+duration; drain deadline fixed anchor+duration+60; late unsubmitted jobs remain in scheduled denominator",
        "scope": "local receipt -> feature preparation -> decision; no coordinator IPC or ESB/DB/network",
        "shared_host_limitations": "Windows affinity is not exclusive quota; host and SMT-sibling interference; CPU1 time-sharing, not multicore speedup"}
    write_json(output / "contract.json", contract)
    with threadpool_limits(limits=1):
        contract["resource_readback_before_warmup"] = resource_readback(bundle)
        write_json(output / "contract.json", contract)
        if not contract["resource_readback_before_warmup"]["validated"]:
            raise RuntimeError("Actual threadpool or applicable estimator n_jobs is not 1; trial not started")
        wait_utc(warmup_epoch)
        warm = phase(bundle, recipe, block, rate=10, stride=stride, source_offset=900, duration=warmup,
                     drain=config["load_drain_seconds"], anchor=time.perf_counter())
        write_json(output / "warmup.json", warm)
        if not warmup_complete(warm):
            raise RuntimeError("Warmup did not complete every scheduled input without drops/errors/pending; measurement not started")
        write_json(output / "ready_contract.json", contract)
        (output / "ready.flag").write_text("warmup_all_inputs_complete", encoding="utf-8")
        if release_path is not None:
            measurement_epoch, release = measurement_release(release_path, contract["trace_sha256"],
                contract["ready_snapshot_sha256"], config["load_drain_seconds"] + 15)
            contract["measurement_epoch_utc"] = measurement_epoch
            contract["all_group_ready_release"] = release
            write_json(output / "contract.json", contract)
        if time.time() > measurement_epoch - .2:
            raise RuntimeError("Missed common measurement barrier; no partial measurement allowed")
        contract["resource_readback_before_measurement"] = resource_readback(bundle)
        write_json(output / "contract.json", contract)
        if not contract["resource_readback_before_measurement"]["validated"]:
            raise RuntimeError("Actual threadpool or estimator n_jobs changed after warmup; measurement not started")
        wait_utc(measurement_epoch)
        start_epoch = time.time()
        anchor = time.perf_counter() + measurement_epoch - time.time()
        measured = phase(bundle, recipe, block, rate=rate, stride=stride, source_offset=900 + warmup,
                         duration=duration, drain=config["load_drain_seconds"], anchor=anchor, measurement=True)
        contract["actual_start_epoch_utc"] = start_epoch
        contract["resource_readback_after_measurement"] = resource_readback(bundle)
        contract["threadpools"] = contract["resource_readback_after_measurement"]["threadpools"]
    pd.DataFrame(measured.pop("records")).to_csv(output / "raw_jobs.csv", index=False)
    measured["contract"] = contract
    measured["resource_validation_status"] = ("passed" if contract["resource_readback_after_measurement"]["validated"]
                                             else "failed_actual_threadpool_or_estimator_limit")
    write_json(output / "result.json", measured)
    if not contract["resource_readback_after_measurement"]["validated"]:
        raise RuntimeError("Actual resource limit changed during measurement; partial result preserved as failed")
    return {"output": str(output), "group": group, "pending_after_drain": measured["pending_after_drain"],
            "not_submitted_measurement_end": measured["realtime"]["not_submitted_measurement_end"] +
                                             measured["trend"]["not_submitted_measurement_end"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run-dir", "output", "group", "algorithm"): parser.add_argument(f"--{name}", required=True)
    for name in ("seed", "rate", "stride", "cpu", "repeat"): parser.add_argument(f"--{name}", type=int, required=True)
    parser.add_argument("--warmup-epoch", type=float, required=True)
    parser.add_argument("--measurement-epoch", type=float, required=True)
    parser.add_argument("--measurement-release", help="Atomic coordinator JSON with measurement_epoch_utc and ready_outputs A/B/C/D")
    parser.add_argument("--executed-repeats", type=int, default=4, help="Explicit load-only override; sealed config stays unchanged")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.group not in "ABCD" or len(args.group) != 1 or args.algorithm not in ("if", "kmeans"):
        parser.error("Expected one group A/B/C/D and algorithm if/kmeans")
    try:
        result = run_trial(args.run_dir, args.output, args.group, args.algorithm, args.seed,
            args.rate, args.stride, args.cpu, args.warmup_epoch, args.measurement_epoch, args.repeat,
            smoke=args.smoke, executed_repeats=args.executed_repeats, release_path=args.measurement_release)
    except Exception as exc:
        # A warmup failure can leave an uncancellable running inference thread.
        # contract/warmup files written above remain available for diagnosis.
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        os._exit(2)
    print(json.dumps(result), flush=True)
    # Running Python threads cannot be killed individually. Preserve the partial
    # measurements, then terminate this dedicated child at the drain deadline.
    if result["pending_after_drain"] or result["not_submitted_measurement_end"]: os._exit(2)
