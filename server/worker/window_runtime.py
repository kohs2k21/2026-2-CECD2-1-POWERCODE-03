"""최신 v12 모델의 연속 1분 Process 입력·추론."""
from __future__ import annotations

import importlib.metadata
import json
import math
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from research_bundle import verify


@lru_cache(maxsize=2)
def model(root_string: str, algorithm: str):
    root = verify(Path(root_string))
    manifest = json.loads((root / "anomaly/models/manifest.json").read_text())
    for package, version in manifest["dependencies"].items():
        if importlib.metadata.version(package) != version:
            raise ValueError("v12_dependency_version_mismatch")
    code = str(root / "anomaly/code")
    if code not in sys.path:
        sys.path.insert(0, code)
    from model_research_v8.inference import load
    item = next(item for item in manifest["models"] if item["algorithm"] == algorithm)
    return load(root / "anomaly/models" / item["file"], item["sha256"]), item["sha256"]


def parse_time(value, zone):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ZoneInfo(zone))
        return pd.Timestamp(stamp).tz_convert("UTC")
    except (ValueError, TypeError):
        return None


def inputs(records, history_start, until, zone, lateness_seconds, max_minutes=43200):
    """기한 내 수신한 관측만 사용하고 0건 구간과 재시작 후 이력을 보존한다."""
    if zone != "Asia/Seoul":
        raise ValueError("v12_requires_verified_kst_clock")
    start = pd.Timestamp(history_start).tz_convert("UTC").floor("min")
    end = pd.Timestamp(until).tz_convert("UTC").floor("min")
    if end <= start or (end - start).total_seconds() / 60 > max_minutes:
        raise ValueError("window_history_range_unavailable")
    arrivals, completions, streams, latest = [], [], {}, {}
    rejected = 0
    for observation_id, execution_id, revision, persisted, raw in records:
        adapter, process = raw.get("adapter_type"), raw.get("process_id")
        if not isinstance(adapter, str) or not adapter or not isinstance(process, str) or not process:
            rejected += 1
            continue
        key = json.dumps([adapter, process], separators=(",", ":"))
        begun = parse_time(raw.get("start_time_raw"), zone)
        finished = parse_time(raw.get("end_time_raw"), zone)
        persisted = pd.Timestamp(persisted).tz_convert("UTC")
        if begun is None or begun < start or begun >= end:
            rejected += 1
            continue
        streams[key] = (adapter, process, min(begun.floor("min"), streams.get(key, (None, None, begun.floor("min")))[2]))
        arrival_deadline = begun.floor("min") + pd.Timedelta(minutes=1, seconds=lateness_seconds)
        if persisted <= arrival_deadline:
            previous = latest.get(("arrival", execution_id))
            if previous is None or revision > previous[0]:
                latest[("arrival", execution_id)] = (revision, key, begun)
        else:
            rejected += 1
        if finished is None or finished < begun or finished >= end:
            continue
        deadline = finished.floor("min") + pd.Timedelta(minutes=1, seconds=lateness_seconds)
        if persisted > deadline:
            rejected += 1
            continue
        try:
            total = float(raw["total_count"])
            if not math.isfinite(total) or isinstance(raw["total_count"], bool) or total < 0:
                continue
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        previous = latest.get(("completion", execution_id))
        if previous is None or revision > previous[0]:
            latest[("completion", execution_id)] = (revision, key, finished, total, (finished - begun).total_seconds() * 1000)
    for (kind, _), item in latest.items():
        if kind == "arrival":
            arrivals.append((item[1], item[2].floor("min")))
        else:
            adapter, process, _ = streams[item[1]]
            # 연구의 KST-naive 달력 초 규약을 정확히 보존한다.
            naive = item[2].tz_convert(zone).tz_localize(None)
            completions.append({"case_id": item[1], "adapterType": adapter, "processId": process,
                                "totalCount": item[3], "duration": item[4], "end_seconds": naive.value / 1e9})
    counts = {}
    for key, minute in arrivals:
        counts[(key, minute)] = counts.get((key, minute), 0) + 1
    native = []
    grid_rows = sum(int((end - first).total_seconds() / 60) for _, _, first in streams.values())
    if grid_rows > 100000:
        raise ValueError("window_grid_limit_exceeded")
    for key, (adapter, process, first) in sorted(streams.items()):
        for minute in pd.date_range(first, end, freq="min", inclusive="left"):
            naive = minute.tz_convert(zone).tz_localize(None)
            native.append({"case_id": key, "adapterType": adapter, "processId": process,
                           "window_start": naive, "window_end": naive + pd.Timedelta(minutes=1),
                           "execution_arrivals": counts.get((key, minute), 0)})
    columns = ["case_id", "adapterType", "processId", "totalCount", "duration", "end_seconds"]
    return pd.DataFrame(native), pd.DataFrame(completions, columns=columns), rejected
