"""시스템 자원 표본기(contracts/collector-metrics.md 4절). 별도 compose 서비스 `resources`로 실행한다.

- CPU %: `/proc/stat` 첫 줄 누적 시간의 두 표본 차이. 첫 표본·카운터 역행은 측정 불가(이전 값·0으로 채우지 않음)
- 메모리: `/proc/meminfo` `MemTotal`·`MemAvailable`. 없으면 측정 불가
- 디스크: 읽기 전용 바인드 경로의 `statvfs` → 기존 `ops_storage_measurements`. 경로가 비었거나 없거나 마운트 지점이 아니면
  `unavailable`·`mount_not_visible`(컨테이너 루트 파일시스템 값을 대신 적지 않는다)
- Docker 소켓·외부 명령을 쓰지 않는다. 값이 없으면 measured 행을 만들지 않는다
"""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SAMPLE_SECONDS = 60
RESOURCE_RETENTION = "24 hours"
STORAGE_RETENTION = "7 days"
WARNING_SPEC_VERSION = "disk-warning-v1"
WARNING_PERCENT = 80.0
CRITICAL_PERCENT = 90.0
SCOPES = ("host_proc_unverified", "host")
STORAGE_KEYS = ("database_data", "artifacts")


@dataclass(frozen=True)
class CpuTimes:
    total: int
    idle: int


def parse_cpu(text: str) -> CpuTimes:
    """`/proc/stat` 첫 `cpu ` 줄. total = user..steal(8칸, guest는 user·nice에 이미 포함), idle = idle + iowait."""
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == "cpu":
            values = [int(value) for value in parts[1:9]]
            if len(values) < 5 or any(value < 0 for value in values):
                break
            return CpuTimes(sum(values), values[3] + values[4])
    raise ValueError("proc_stat_unparsed")


def cpu_percent(previous: CpuTimes | None, current: CpuTimes | None) -> tuple[float | None, str | None]:
    if current is None:
        return None, "proc_stat_unavailable"
    if previous is None:
        return None, "cpu_first_sample"
    total, idle = current.total - previous.total, current.idle - previous.idle
    if total <= 0 or idle < 0 or idle > total:
        return None, "cpu_counter_reset"
    return round((1 - idle / total) * 100, 3), None


def parse_memory(text: str) -> tuple[int, int]:
    """(사용 바이트 = MemTotal − MemAvailable, 전체 바이트). kB 단위만 받는다."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if name in ("MemTotal", "MemAvailable") and len(parts) == 2 and parts[1] == "kB":
            values[name] = int(parts[0]) * 1024
    total, available = values.get("MemTotal"), values.get("MemAvailable")
    if not total or total <= 0 or available is None or not 0 <= available <= total:
        raise ValueError("meminfo_unparsed")
    return total - available, total


def warning_state(used: int, capacity: int) -> str:
    percent = used / capacity * 100
    return "critical" if percent >= CRITICAL_PERCENT else "warning" if percent >= WARNING_PERCENT else "normal"


def measure_disk(key: str, path: str | None, *, ismount: Callable[[str], bool] = os.path.ismount,
                 statvfs: Callable[[str], Any] | None = None, stat: Callable[[str], Any] = os.stat) -> dict[str, Any]:
    row: dict[str, Any] = {"storage_key": key, "filesystem_key": None, "status": "unavailable", "capacity_bytes": None,
                           "used_bytes": None, "available_bytes": None, "warning_state": "unknown", "reason_code": None}
    if not path or not ismount(path):
        return {**row, "reason_code": "mount_not_visible"}
    statvfs = statvfs or getattr(os, "statvfs", None)  # POSIX only (the container); never guessed elsewhere
    if statvfs is None:
        return {**row, "reason_code": "statvfs_failed"}
    try:
        info = statvfs(path)
        device = stat(path).st_dev
    except OSError:
        return {**row, "reason_code": "statvfs_failed"}
    capacity = info.f_blocks * info.f_frsize
    available = info.f_bavail * info.f_frsize
    used = (info.f_blocks - info.f_bfree) * info.f_frsize
    if capacity <= 0 or used < 0 or available < 0:
        return {**row, "reason_code": "statvfs_failed"}
    # 사용률 = used / (used + 일반 사용자 가용). df Use%와 같은 분모(예약 블록 제외).
    denominator = used + available
    state = warning_state(used, denominator) if denominator > 0 else "unknown"
    return {**row, "filesystem_key": f"dev:{device}", "status": "measured", "capacity_bytes": capacity,
            "used_bytes": used, "available_bytes": available, "warning_state": state}


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return None


class Sampler:
    def __init__(self, *, proc_root: Path = Path("/proc"), scope: str = "host_proc_unverified",
                 disks: dict[str, str | None] | None = None, clock: Callable[[], float] = time.monotonic):
        if scope not in SCOPES:
            raise ValueError("invalid_resource_scope")
        self.proc_root, self.scope, self.clock = proc_root, scope, clock
        self.disks = dict(disks or {})
        if any(key not in STORAGE_KEYS for key in self.disks):
            raise ValueError("invalid_storage_key")
        self.previous: CpuTimes | None = None
        self.previous_at: float | None = None

    def resource_row(self) -> dict[str, Any]:
        stat_text = read_text(self.proc_root / "stat")
        now = self.clock()
        try:
            current = parse_cpu(stat_text) if stat_text is not None else None
        except ValueError:
            current = None
        percent, cpu_reason = cpu_percent(self.previous, current)
        interval = (now - self.previous_at) * 1000 if percent is not None and self.previous_at is not None else None
        if percent is not None and (interval is None or interval <= 0):
            percent, cpu_reason, interval = None, "cpu_counter_reset", None
        self.previous, self.previous_at = (current, now) if current is not None else (None, None)
        memory_text = read_text(self.proc_root / "meminfo")
        try:
            used, total = parse_memory(memory_text) if memory_text is not None else (None, None)
            memory_reason = None if memory_text is not None else "meminfo_unavailable"
        except ValueError:
            used, total, memory_reason = None, None, "meminfo_unavailable"
        return {"scope": self.scope,
                "cpu_status": "measured" if percent is not None else "unavailable", "cpu_percent": percent,
                "cpu_reason_code": cpu_reason, "sample_interval_ms": interval,
                "memory_status": "measured" if used is not None else "unavailable", "memory_used_bytes": used,
                "memory_total_bytes": total, "memory_reason_code": memory_reason}

    def disk_rows(self) -> list[dict[str, Any]]:
        return [measure_disk(key, path) for key, path in self.disks.items()]


def store(conn: Any, resource: dict[str, Any], disks: list[dict[str, Any]]) -> None:
    """한 트랜잭션: 자원 1행 + 디스크 키별 1행 + 보존 기간 지난 행 정리. measured_at은 DB 시계."""
    with conn.transaction():
        conn.execute("""INSERT INTO ops_resource_measurements(id,scope,cpu_status,cpu_percent,cpu_reason_code,sample_interval_ms,
            memory_status,memory_used_bytes,memory_total_bytes,memory_reason_code,measured_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())""",
            (uuid.uuid4(), resource["scope"], resource["cpu_status"], resource["cpu_percent"], resource["cpu_reason_code"],
             resource["sample_interval_ms"], resource["memory_status"], resource["memory_used_bytes"],
             resource["memory_total_bytes"], resource["memory_reason_code"]))
        for row in disks:
            conn.execute("""INSERT INTO ops_storage_measurements(id,storage_key,filesystem_key,target_kind,status,capacity_bytes,
                used_bytes,available_bytes,database_bytes,writer_access_verified,warning_spec_version,warning_state,reason_code,measured_at)
                VALUES(%s,%s,%s,'filesystem',%s,%s,%s,%s,NULL,false,%s,%s,%s,clock_timestamp())""",
                (uuid.uuid4(), row["storage_key"], row["filesystem_key"], row["status"], row["capacity_bytes"], row["used_bytes"],
                 row["available_bytes"], WARNING_SPEC_VERSION, row["warning_state"], row["reason_code"]))
        conn.execute(f"DELETE FROM ops_resource_measurements WHERE measured_at<clock_timestamp()-interval '{RESOURCE_RETENTION}'")
        conn.execute(f"""DELETE FROM ops_storage_measurements WHERE storage_key = ANY(%s) AND target_kind='filesystem'
            AND measured_at<clock_timestamp()-interval '{STORAGE_RETENTION}'""", (list(STORAGE_KEYS),))


def from_environment() -> Sampler:
    disks = {"artifacts": os.environ.get("RESOURCE_ARTIFACTS_PATH") or None,
             "database_data": os.environ.get("RESOURCE_DATABASE_DATA_PATH") or None}
    return Sampler(scope=os.environ.get("RESOURCE_PROC_SCOPE", "host_proc_unverified"), disks=disks)


def run(*, once: bool = False) -> None:
    import psycopg
    interval = float(os.environ.get("RESOURCE_SAMPLE_SECONDS", SAMPLE_SECONDS))
    if not 10 <= interval <= 3600:
        raise ValueError("invalid_resource_sample_seconds")
    sampler = from_environment()
    conn = None
    while True:
        started = time.monotonic()
        resource, disks = sampler.resource_row(), sampler.disk_rows()
        try:
            if conn is None or conn.closed:
                conn = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10, autocommit=True)
            store(conn, resource, disks)
        except Exception as exc:  # 다음 주기에 다시 연결한다. 값·접속 정보는 출력하지 않는다.
            print(json.dumps({"resourceSampleError": type(exc).__name__}), flush=True)
            if conn is not None:
                conn.close()
            conn = None
        if once:
            if conn is not None:
                conn.close()
            return
        time.sleep(max(0.0, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    run(once=parser.parse_args().once)
