"""PROCESS Rule then ML versus independent Rule/ML; sealed synthetic load only."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError, wait
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import joblib
import numpy as np
import pandas as pd
import psutil
import sklearn
from threadpoolctl import threadpool_info, threadpool_limits

from .models import sha256_file
from .v4 import BASE, write_json
from .v4_core import digest, window_frame
from .v4_load import trace, process_input
from process_anomaly_pipeline.data import prepare

MODES = ("sequential", "concurrent")


@dataclass(frozen=True)
class Prepared:
    counts: tuple
    complete: bool
    eligible: bool
    values: object


def prepare_shared(raw, detector):
    frame = prepare([json.loads(raw)])
    row = frame.iloc[0]
    complete = row.status in ("S", "F", "H", "Z")
    eligible = bool(complete and pd.notna(row.endTime) and pd.notna(row.startTime)
                    and np.isfinite(row.duration_ms) and row.duration_ms >= 0)
    values = detector.encoder.transform(frame) if detector is not None and eligible else None
    if values is not None:
        if not np.isfinite(values).all():
            raise ValueError("Non-finite PROCESS features")
        values.setflags(write=False)
    return Prepared(tuple(float(row[k]) for k in ("totalCount", "successCount", "errorCount")),
                    complete, eligible, values)


def count_rule(item):
    if not item.complete or not all(np.isfinite(item.counts)):
        return {"status": "unknown_count_relation", "prediction": None}
    total, success, error = item.counts
    return {"status": "complete", "prediction": bool(total != success + error)}


def ml_score(item, detector):
    if detector is None:
        return {"status": "model_missing", "prediction": None}
    if not item.eligible:
        return {"status": "unavailable_missing_completion", "prediction": None}
    if detector.algorithm != "if":
        raise ValueError("This sealed PROCESS benchmark requires IF")
    raw = -detector.model.score_samples(item.values)
    margin = float((detector.normalizer.transform(raw) - detector.threshold)[0])
    if not np.isfinite(margin):
        raise ValueError("Non-finite ML margin")
    return {"status": "complete", "prediction": margin > 0, "margin": margin}


def invoke(function, item):
    started = time.perf_counter()
    try:
        value = function(item)
    except Exception as exc:
        value = {"status": "error", "prediction": None, "error": f"{type(exc).__name__}: {exc}"}
    finished = time.perf_counter()
    return {**value, "started": started, "finished": finished, "compute_seconds": finished - started}


class Pair:
    def __init__(self, mode, detector, *, rule=count_rule, ml=None, preparer=None):
        if mode not in MODES:
            raise ValueError("Unknown PROCESS execution mode")
        self.mode, self.detector, self.rule = mode, detector, rule
        self.ml = ml or (lambda item: ml_score(item, detector))
        self.preparer = preparer or (lambda raw: prepare_shared(raw, detector))
        # Same preparation resources; sequential workers remain idle by design.
        self.rule_executor = ThreadPoolExecutor(1, thread_name_prefix="pair-rule")
        self.ml_executor = ThreadPoolExecutor(1, thread_name_prefix="pair-ml")

    def execute(self, raw, deadline):
        started = time.perf_counter()
        try:
            item = self.preparer(raw)
            features_hash = hashlib.sha256(item.values.tobytes()).hexdigest() if item.values is not None else None
        except Exception as exc:
            return {"status": "prepare_error", "prediction": None, "finished": time.perf_counter(),
                    "error": f"{type(exc).__name__}: {exc}"}
        prepared = time.perf_counter()
        if self.mode == "sequential":
            rule = invoke(self.rule, item)
            ml = invoke(self.ml, item)  # Always both, including a Rule hit/error.
        else:
            futures = {"rule": self.rule_executor.submit(invoke, self.rule, item),
                       "ml": self.ml_executor.submit(invoke, self.ml, item)}
            branches = {}
            for name, future in futures.items():
                try:
                    branches[name] = future.result(timeout=max(0., deadline - time.perf_counter()))
                except TimeoutError:
                    branches[name] = {"status": "timeout", "prediction": None}
            rule, ml = branches["rule"], branches["ml"]
        for branch in (rule, ml):
            if branch.get("finished", deadline) > deadline:
                branch.update(status="timeout", prediction=None)
        known = [b["prediction"] for b in (rule, ml) if b["status"] == "complete"]
        complete = len(known) == 2
        return {"status": "complete" if complete else "partial", "rule": rule, "ml": ml,
                "prediction": True if any(known) else (False if complete else None),
                "feature_sha256": features_hash,
                "prepare_started": started, "prepare_finished": prepared,
                "prepare_seconds": prepared - started, "finished": time.perf_counter()}

    def close(self):
        for executor in (self.rule_executor, self.ml_executor):
            executor.shutdown(wait=False, cancel_futures=True)


class Jobs:
    """One active PROCESS and at most queue_limit waiting; no late record writes."""
    def __init__(self, queue_limit=1000):
        self.executor = ThreadPoolExecutor(1)
        self.capacity = threading.BoundedSemaphore(queue_limit + 1)
        self.entries, self.active, self.peak = [], 0, 0
        self.lock = threading.Lock()

    def submit(self, job_id, scheduled, job, input_end):
        submitted = time.perf_counter()
        entry = {"job_id": job_id, "scheduled": scheduled, "submitted": submitted,
                 "producer_lateness_seconds": max(0., submitted - scheduled)}
        self.entries.append(entry)
        if submitted >= input_end:
            entry["status"] = "not_submitted_input_end"
            return
        if not self.capacity.acquire(blocking=False):
            entry["status"] = "drop_queue_overflow"
            return
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        def execute():
            started = time.perf_counter()
            with self.lock:
                entry["started"] = started
            try:
                try:
                    value = job()
                except Exception as exc:
                    value = {"status": "error", "prediction": None,
                             "error": f"{type(exc).__name__}: {exc}"}
                finished = time.perf_counter()
                return {**value, "finished": finished, "queue_wait_seconds": started - submitted,
                        "compute_seconds": finished - started,
                        "local_latency_seconds": finished - scheduled}
            finally:
                with self.lock:
                    self.active -= 1
                self.capacity.release()
        entry["future"] = self.executor.submit(execute)

    def close(self, deadline):
        futures = [e["future"] for e in self.entries if "future" in e]
        wait(futures, timeout=max(0., deadline - time.perf_counter()))
        frozen = []
        with self.lock:
            for entry in self.entries:
                record = {k: v for k, v in entry.items() if k != "future"}
                future = entry.get("future")
                if future is not None:
                    if future.done() and not future.cancelled():
                        value = future.result()
                        if value["finished"] <= deadline:
                            record.update(value)
                        else:
                            record.update(status="timeout_running", prediction=None)
                    else:
                        record.update(status="timeout_running" if "started" in record else "pending_at_deadline",
                                      prediction=None)
                        future.cancel()
                frozen.append(record)
        self.executor.shutdown(wait=False, cancel_futures=True)
        return frozen


def summary(records, duration):
    complete = [r for r in records if r["status"] == "complete"]
    failed = len(records) - len(complete)
    def q(key, p):
        values = [r[key] for r in complete if key in r]
        return float(np.quantile(values, p)) if values else None
    return {"scheduled": len(records), "complete": len(complete), "failed_or_partial": failed,
            "status_counts": {s: sum(r["status"] == s for r in records) for s in sorted({r["status"] for r in records})},
            "throughput_per_second": len(complete) / duration,
            "latency_p95_seconds": q("local_latency_seconds", .95), "latency_p99_seconds": q("local_latency_seconds", .99),
            "producer_lateness_p95_seconds": q("producer_lateness_seconds", .95),
            "queue_wait_p95_seconds": q("queue_wait_seconds", .95), "compute_p95_seconds": q("compute_seconds", .95),
            "all_scheduled_failed_or_over_3s_fraction": (failed + sum(r["local_latency_seconds"] > 3 for r in complete)) / len(records) if records else None,
            "rule_coverage": sum(r.get("rule", {}).get("status") == "complete" for r in records) / len(records) if records else None,
            "ml_finite_coverage": sum(r.get("ml", {}).get("status") == "complete" for r in records) / len(records) if records else None}


def make_trace(seed, rate, warmup, duration):
    block = trace(seed, rate, warmup, duration)
    warm_mask = (block.arrivals >= block.start + 900) & (block.arrivals < block.start + 900 + warmup)
    block.durations[warm_mask], block.counts[warm_mask] = .25, 2.
    block.completions[warm_mask] = block.arrivals[warm_mask]
    records = []
    for index in range(len(block.arrivals)):
        raw = process_input(block, index)
        # Logical counterrelation is a Rule audit; it is not an ML quality label.
        if block.arrivals[index] >= block.start + 900 + warmup and index % 20 == 3:
            raw["errorCount"] = 1
            block.contract_invalid[index] = True
        records.append(json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False))
    for array in (block.arrivals, block.durations, block.counts, block.completions, block.contract_invalid):
        array.setflags(write=False)
    return block, tuple(records)


def phase(pair, block, raw, history, *, offset, duration, rate, rule_limit, drain=60):
    jobs, timers = Jobs(), Jobs()
    indices = np.flatnonzero((block.arrivals >= block.start + offset) &
                             (block.arrivals < block.start + offset + duration))
    if len(indices) != int(rate * duration):
        raise ValueError("Scheduled PROCESS count differs from declared rate")
    timeline = sorted([(i / rate, "process", int(index)) for i, index in enumerate(indices)] +
                      [(float(t), "timer", int(t / 5)) for t in np.arange(0., duration, 5.)])
    anchor = time.perf_counter()
    input_end, deadline = anchor + duration, anchor + duration + drain
    process = psutil.Process()
    cpu_start = sum(process.cpu_times()[:2])
    rss_samples, stop = [], threading.Event()
    def sample():
        while not stop.is_set():
            rss_samples.append(process.memory_info().rss)
            stop.wait(.1)
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    for relative, path, index in timeline:
        scheduled = anchor + relative
        delay = scheduled - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        if path == "process":
            jobs.submit(index, scheduled, lambda index=index: pair.execute(raw[index], deadline), input_end)
        else:
            def timer(clock=block.start + offset + relative):
                frame = window_frame(block, history, 5, width=300, clocks=[clock])
                row = frame.iloc[0]
                if not row.eligible or not np.isfinite(row.rule_score):
                    return {"status": "unavailable", "prediction": None}
                return {"status": "complete", "prediction": bool(row.rule_score > rule_limit)}
            timers.submit(index, scheduled, timer, input_end)
    delay = input_end - time.perf_counter()
    if delay > 0:
        time.sleep(delay)
    rows, windows = jobs.close(deadline), timers.close(deadline)
    stop.set()
    sampler.join()
    return {"process": summary(rows, duration), "timer_rule": summary(windows, duration),
            "records": rows, "timer_records": windows, "peak_pending_process": jobs.peak,
            "anchor": anchor, "input_end": input_end, "drain_deadline": deadline,
            "wall_seconds_including_drain": time.perf_counter() - anchor,
            "child_cpu_seconds_including_drain": sum(process.cpu_times()[:2]) - cpu_start,
            "child_peak_rss_bytes_including_drain": max(rss_samples, default=process.memory_info().rss)}


def verify_bundle(run_dir, seed):
    folder = Path(run_dir).resolve() / f"seed_{seed}"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    path = folder / "models.joblib"
    approved_hash = "fe790aa7e8bc9040837fe861c8aaac09ae400165d0ffd62364f27b6d65ce1b94"
    if manifest["model_sha256"] != approved_hash or sha256_file(path) != approved_hash:
        raise ValueError("Sealed model hash changed")
    declaration = json.loads((Path(run_dir) / "declaration.json").read_text(encoding="utf-8"))
    if declaration["sklearn_version"] != sklearn.__version__:
        raise ValueError("Sealed sklearn version differs")
    source = BASE / declaration["config"]["source_snapshot"] / "code"
    for relative, expected in manifest["source_hashes"].items():
        if sha256_file(source / relative) != expected:
            raise ValueError("Sealed source snapshot changed")
    sys.path.insert(0, str(source))
    bundle = joblib.load(path)
    detector = bundle["process"]
    if detector.algorithm != "if" or not np.isfinite(detector.threshold):
        raise ValueError("Expected sealed PROCESS global IF and finite threshold")
    if hasattr(detector.model, "n_jobs"):
        detector.model.n_jobs = 1
    return bundle, manifest


def trial(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    process = psutil.Process()
    process.cpu_affinity([args.cpu])
    if process.cpu_affinity() != [args.cpu]:
        raise RuntimeError("Measured interpreter CPU affinity was not applied")
    bundle, manifest = verify_bundle(args.run_dir, args.seed)
    pair = Pair(args.mode, bundle["process"])
    block, raw = make_trace(args.seed, args.rate, args.warmup, args.duration)
    contract = {"mode": args.mode, "seed": args.seed, "rate": args.rate, "repeat": args.repeat,
                "trial_scope": "smoke_unpaired_not_comparison" if args.duration < 60 else "paired_exploration" if args.duration == 60 else "paired_followup",
                "measurement_seconds": args.duration, "warmup_seconds": args.warmup,
                "model_sha256": manifest["model_sha256"], "threshold": bundle["process"].threshold,
                "rule_limit": args.rule_limit, "trace_sha256": digest(raw),
                "manifest_sha256": sha256_file(Path(args.run_dir) / f"seed_{args.seed}" / "manifest.json"),
                "numpy_version": np.__version__, "pandas_version": pd.__version__, "psutil_version": psutil.__version__,
                "platform": sys.platform, "python_version": sys.version,
                "history_sha256": digest(sorted((str(k), v) for k, v in bundle["history"].values.items())),
                "features": list(bundle["process"].encoder.columns), "preparation": "prepare+encoder once per PROCESS, timed shared read-only values",
                "process_inflight": 1, "queue_waiting_limit": 1000, "rule_workers": 1, "ml_workers": 1,
                "timer_workers": 1, "timer_width": 300, "timer_stride": 5, "drain_seconds": 60,
                "cpu_affinity": process.cpu_affinity(), "pid": os.getpid(), "interpreter": sys.executable,
                "sklearn_version": sklearn.__version__, "scope": "synthetic local PROCESS receipt; no production/ESB SLA or ML quality claim"}
    write_json(output / "contract.json", contract)
    with threadpool_limits(limits=1):
        pools = threadpool_info()
        if not pools or any(p["num_threads"] != 1 for p in pools):
            raise RuntimeError("BLAS/OpenMP actual thread limit is not 1")
        # History is loaded, never fitted; replay 900s timer readiness outside measurement.
        for clock in np.arange(block.start, block.start + 900, 5.):
            window_frame(block, bundle["history"], 5, width=300, clocks=[clock])
        warm = phase(pair, block, raw, bundle["history"], offset=900, duration=args.warmup,
                     rate=10, rule_limit=args.rule_limit)
        write_json(output / "warmup.json", warm)
        if warm["process"]["failed_or_partial"] != 0 or warm["timer_rule"]["failed_or_partial"] != 0:
            pair.close()
            raise RuntimeError("Warmup failed or unavailable; no measurement started")
        measured = phase(pair, block, raw, bundle["history"], offset=900 + args.warmup,
                         duration=args.duration, rate=args.rate, rule_limit=args.rule_limit)
        measured["contract"], measured["threadpools"] = contract, threadpool_info()
        write_json(output / "result.json", measured)
    pair.close()
    return measured


def predictions(result):
    return {r["job_id"]: (r["rule"]["status"], r["rule"]["prediction"], r["ml"]["status"],
                           r["ml"]["prediction"], r["ml"].get("margin"), r["prediction"], r["feature_sha256"])
            for r in result["records"] if r["status"] in ("complete", "partial")}


def coordinate(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    psutil.Process().cpu_affinity([0, 1, 2, 3])
    order = [MODES if i % 2 == 0 else tuple(reversed(MODES)) for i in range(4)]
    declaration = {"repeats": 4, "order": order, "rates": [10, 100], "cpu": args.cpu,
                   "duration": args.duration, "warmup": args.warmup, "seed": args.seed,
                   "rule_limit": args.rule_limit, "source_sha256": sha256_file(__file__),
                   "protocol_sha256": sha256_file(BASE / "docs" / "PROCESS_PAIR_PROTOCOL.md"),
                   "model_sha256": sha256_file(Path(args.run_dir) / f"seed_{args.seed}" / "models.joblib"),
                   "thread_environment": {k: "1" for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")}}
    write_json(output / "declaration.json", declaration)
    comparisons = []
    for rate in (10, 100):
        for repeat, modes in enumerate(order):
            results = {}
            for mode in modes:
                folder = output / f"{rate}Hz_{repeat}_{mode}"
                command = [sys.executable, "-m", "research.evaluation.detector_dual_path.process_pair", "--child",
                           "--run-dir", str(Path(args.run_dir).resolve()), "--output", str(folder), "--mode", mode,
                           "--rate", str(rate), "--repeat", str(repeat), "--seed", str(args.seed),
                           "--cpu", str(args.cpu), "--duration", str(args.duration), "--warmup", str(args.warmup),
                           "--rule-limit", str(args.rule_limit)]
                environment = {**os.environ, **declaration["thread_environment"]}
                completed = subprocess.run(command, env=environment, timeout=args.duration + args.warmup + 240,
                                           capture_output=True, text=True)
                path = folder / "result.json"
                if not path.is_file():
                    write_json(output / "failed_trial.json", {"folder": str(folder), "returncode": completed.returncode,
                                                               "stderr": completed.stderr[-4000:]})
                    raise RuntimeError("Child failed before producing measured results")
                results[mode] = json.loads(path.read_text(encoding="utf-8"))
            seq, concurrent = (results[m] for m in MODES)
            if any(seq["contract"][k] != concurrent["contract"][k] for k in
                   ("trace_sha256", "model_sha256", "threshold", "history_sha256", "features", "rule_limit")):
                raise ValueError("Paired trial immutable inputs/configuration differ")
            a, b = predictions(seq), predictions(concurrent)
            common = sorted(set(a) & set(b))
            seq_complete = {r["job_id"] for r in seq["records"] if r["status"] == "complete"}
            par_complete = {r["job_id"] for r in concurrent["records"] if r["status"] == "complete"}
            comparisons.append({"rate": rate, "repeat": repeat, "common_complete": len(seq_complete & par_complete),
                                "common_finalized": len(common),
                                "prediction_mismatches": [i for i in common if a[i] != b[i]],
                                "only_sequential_complete": len(seq_complete - par_complete), "only_concurrent_complete": len(par_complete - seq_complete),
                                "sequential": seq["process"], "concurrent": concurrent["process"]})
            write_json(output / "comparison.json", {"pairs": comparisons, "completed_pairs": len(comparisons)})
    return {"output": str(output), "completed_pairs": len(comparisons)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--mode", choices=MODES, default="sequential")
    for name, default in (("seed", 451), ("cpu", 4), ("duration", 60), ("warmup", 60), ("rate", 10), ("repeat", 0)):
        parser.add_argument(f"--{name}", type=int, default=default)
    parser.add_argument("--rule-limit", type=float, default=1.)
    args = parser.parse_args()
    if (args.duration not in (60, 600) or args.warmup != 60 or args.cpu != 4 or args.rate not in (10, 100)
            or args.seed != 451 or not np.isfinite(args.rule_limit) or args.rule_limit != 1.):
        parser.error("Predeclared seed451, Rule1, 60/600s, warmup60, CPU4, 10/100Hz only")
    try:
        result = trial(args) if args.child else coordinate(args)
        print(json.dumps({"status": "complete", "output": args.output}), flush=True)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        os._exit(2)
    if args.child:
        # Dedicated interpreter exits after saving its final immutable snapshot.
        os._exit(0)


if __name__ == "__main__":
    main()
