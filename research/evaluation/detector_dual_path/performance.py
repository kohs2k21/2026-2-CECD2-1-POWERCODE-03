"""Actual model compute interference on one process with two worker slots.

This measures local wall-clock compute and contention, not ESB/API end-to-end
latency. BLAS/sklearn thread settings and submitted input schedule stay fixed.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import statistics
import threading
import time

import numpy as np
from threadpoolctl import threadpool_limits


def percentiles(values):
    if not values:
        return {"p50_ms": None, "p95_ms": None, "p99_ms": None, "over_3s_rate": None}
    return {"p50_ms": float(np.quantile(values, .5) * 1000),
            "p95_ms": float(np.quantile(values, .95) * 1000),
            "p99_ms": float(np.quantile(values, .99) * 1000),
            "over_3s_rate": float(np.mean(np.asarray(values) > 3))}


class ResourceSampler:
    def __init__(self, interval=.01):
        try:
            import psutil
        except ImportError as exc:
            raise RuntimeError("psutil required for measured CPU/process-tree memory") from exc
        self.psutil = psutil
        self.process = psutil.Process()
        self.interval = interval
        self.samples = []
        self.stop = threading.Event()

    def __enter__(self):
        self.cpu_start = sum(self.process.cpu_times()[:2])
        self.wall_start = time.perf_counter()
        def sample():
            while not self.stop.is_set():
                processes = [self.process, *self.process.children(recursive=True)]
                total = 0
                for process in processes:
                    try:
                        total += process.memory_info().rss
                    except (self.psutil.NoSuchProcess, self.psutil.AccessDenied):
                        continue
                self.samples.append(total)
                self.stop.wait(self.interval)
        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()
        self.wall_seconds = time.perf_counter() - self.wall_start
        self.cpu_seconds = sum(self.process.cpu_times()[:2]) - self.cpu_start
        self.peak_rss_bytes = max(self.samples, default=self.process.memory_info().rss)


def _trial(pair, row_frames, window_frames, mode, input_rate_hz):
    row_times, window_times, window_queue = [], [], []
    def row_job(frame, submitted):
        begin = time.perf_counter()
        pair.score_rows(frame)
        end = time.perf_counter()
        return end - submitted, end - begin
    def trend_loop(start):
        output = []
        for index, frame in enumerate(window_frames):
            due = start + index / input_rate_hz
            delay = due - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            begin = time.perf_counter()
            pair.score_window(frame)
            end = time.perf_counter()
            output.append((end - begin, max(0., begin - due)))
        return output
    with threadpool_limits(limits=1), ResourceSampler() as resources:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="detector-lab") as workers:
            start = time.perf_counter()
            trend = workers.submit(trend_loop, start) if mode == "dual" else None
            futures = []
            for index, frame in enumerate(row_frames):
                due = start + index / input_rate_hz
                delay = due - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                futures.append(workers.submit(row_job, frame, due))
            for future in futures:
                row_times.append(future.result()[0])
            if trend:
                for elapsed, queued in trend.result():
                    window_times.append(elapsed)
                    window_queue.append(queued)
    return {"mode": mode, **percentiles(row_times),
            "row_jobs": len(row_frames), "window_jobs": len(window_frames) if mode == "dual" else 0,
            "cpu_seconds": resources.cpu_seconds, "wall_seconds": resources.wall_seconds,
            "peak_rss_bytes": resources.peak_rss_bytes,
            "cpu_equivalent_cores": resources.cpu_seconds / resources.wall_seconds,
            "window_compute": percentiles(window_times),
            "window_queue_p95_ms": float(np.quantile(window_queue, .95) * 1000) if window_queue else None,
            "window_queue_max_ms": max(window_queue, default=0.) * 1000,
            "window_stale_jobs": sum(value > 1 / input_rate_hz for value in window_queue)}


def benchmark_pair(pair, row_frames, window_frames, *, repeats=3, warmup=10, input_rate_hz=100.):
    if repeats < 3 or warmup < 1 or input_rate_hz <= 0 or not row_frames or not window_frames:
        raise ValueError("At least three repeats, warmup, and both input paths required")
    for detector in (pair.row, pair.window):
        if hasattr(detector.model, "n_jobs"):
            detector.model.n_jobs = 1
    with threadpool_limits(limits=1):
        for i in range(warmup):
            pair.score_rows(row_frames[i % len(row_frames)])
            pair.score_window(window_frames[i % len(window_frames)])
    trials = []
    for repeat in range(repeats):
        order = ("realtime", "dual") if repeat % 2 == 0 else ("dual", "realtime")
        for mode in order:
            trials.append({"repeat": repeat, **_trial(pair, row_frames, window_frames, mode, input_rate_hz)})
    summaries = {}
    for mode in ("realtime", "dual"):
        selected = [trial for trial in trials if trial["mode"] == mode]
        summaries[mode] = {key: {"median": statistics.median(trial[key] for trial in selected),
                                "min": min(trial[key] for trial in selected),
                                "max": max(trial[key] for trial in selected)}
                           for key in ("p50_ms", "p95_ms", "p99_ms", "cpu_seconds", "peak_rss_bytes", "wall_seconds")}
    alone = summaries["realtime"]["p95_ms"]["median"]
    return {"measurement": "actual_local_wall_clock_model_compute",
            "end_to_end_esb_claim": False, "python_workers": 2,
            "blas_threads": 1, "sklearn_n_jobs": 1, "host_cpu_count": os.cpu_count(),
            "input_rate_hz": input_rate_hz, "warmup_jobs_each_path": warmup,
            "repeats": repeats, "fixed_pair": pair.metadata(), "trials": trials,
            "summary": summaries,
            "dual_p95_change_ratio": summaries["dual"]["p95_ms"]["median"] / alone - 1 if alone else None,
            "limitations": ["shared-process worker slots; no OS CPU cap",
                            "model computation only; no PostgreSQL/network/ESB/API",
                            "sampling RSS includes parent and live children; not container cgroup memory",
                            "trend jobs deliberately share the fixed input rate for contention stress; not operational schedule"]}
