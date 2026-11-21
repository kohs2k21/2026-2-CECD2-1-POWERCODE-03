"""Versioned, explicitly selected detection policy and bounded parallel execution."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
import threading
import time
from typing import Callable

# 방법 실행 슬롯(룰 레인·모델) 수 = 프로세스의 동시 판정 수. 기본 2는 폴링 1 + 직접 전달 1(단일 run(), Trainer 비교 평가).
# Detector 서비스(runner.run_workers)는 시작 때 configure_capacity(작업자 수 + 1)로 바꾼다(detection-decision.md D20).
DEFAULT_CAPACITY = 2
_LANES = ("rule", "ml")
_POOLS = {name: ThreadPoolExecutor(max_workers=DEFAULT_CAPACITY, thread_name_prefix="detection-"+name) for name in _LANES}
_SLOTS = {name: threading.BoundedSemaphore(DEFAULT_CAPACITY) for name in _LANES}
_CAPACITY_LOCK = threading.Lock()
_capacity = DEFAULT_CAPACITY


def capacity() -> int:
    """지금 룰·ML 슬롯 수(둘이 같다)."""
    return _capacity


def configure_capacity(concurrent: int) -> None:
    """룰·ML 슬롯과 실행 풀을 동시 판정 수 concurrent개로 다시 만든다. 프로세스 시작 때 판정 전에 한 번 부른다.

    같은 값이면 아무것도 하지 않는다. 잡혀 있는 슬롯이 있으면(판정 진행 중) 바꾸지 않고 RuntimeError
    (`detection_capacity_busy`)다. 표(_POOLS·_SLOTS)는 같은 dict를 고쳐 쓰므로 가져다 쓴 모듈도 새 값을 본다.
    """
    global _capacity
    if type(concurrent) is not int or concurrent < 1:
        raise ValueError("invalid_detection_capacity")
    with _CAPACITY_LOCK:
        if concurrent == _capacity:
            return
        if any(_SLOTS[name]._value != _capacity for name in _LANES):
            raise RuntimeError("detection_capacity_busy")
        old = [_POOLS[name] for name in _LANES]
        for name in _LANES:
            _SLOTS[name] = threading.BoundedSemaphore(concurrent)
            _POOLS[name] = ThreadPoolExecutor(max_workers=concurrent, thread_name_prefix="detection-"+name)
        _capacity = concurrent
    for pool in old:
        pool.shutdown(wait=False)


POLICY_V2_KEYS = frozenset({"category-k-of-n-or-v1"})
# 운영 설정을 저장한 적이 없는 상태(리비전 0)의 내장 기본 정책.
DEFAULT_POLICY_V2 = {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1},
                     "deadlineMs": 3000}

@dataclass(frozen=True)
class Policy:
    mode: str
    deadline_ms: int
    combination: str
    legacy: bool = False

    def document(self):
        return {"mode": self.mode, "deadlineMs": self.deadline_ms,
                "combination": self.combination, "legacy": self.legacy}


@dataclass(frozen=True)
class PolicyV2:
    policy_key: str
    k_items: tuple[tuple[str, int], ...]
    deadline_ms: int

    @property
    def k(self):
        return dict(self.k_items)

    def combination(self):
        return {"version": 2, "policyKey": self.policy_key, "k": self.k}

    def document(self):
        return {**self.combination(), "deadlineMs": self.deadline_ms}


def parse_policy(spec: dict, *, required: bool = False) -> Policy:
    value = spec.get("detectionPolicy")
    if value is None and not required:
        # An explicit compatibility policy, never rewritten into historical configs.
        return Policy("parallel", 3000, "any_anomaly", True)
    if not isinstance(value, dict) or set(value) != {"mode", "deadlineMs", "combination"}:
        raise ValueError("detection_policy_required")
    mode, deadline, combination = value["mode"], value["deadlineMs"], value["combination"]
    if mode not in {"rule_only", "ml_only", "parallel"} or type(deadline) is not int or not 100 <= deadline <= 30000:
        raise ValueError("invalid_detection_policy")
    if combination != ("any_anomaly" if mode == "parallel" else "selected_path"):
        raise ValueError("unsupported_detection_combination")
    return Policy(mode, deadline, combination)


def parse_policy_v2(value) -> PolicyV2:
    """k의 범위(저장 시 모두 1, 실행 시 k<=n)는 형태 검사가 아니므로 여기서 보지 않는다."""
    if not isinstance(value, dict) or set(value) != {"version", "policyKey", "k", "deadlineMs"}:
        raise ValueError("invalid_detection_policy")
    deadline, k = value["deadlineMs"], value["k"]
    if type(value["version"]) is not int or value["version"] != 2 or type(deadline) is not int or \
            not 100 <= deadline <= 30000:
        raise ValueError("invalid_detection_policy")
    if not isinstance(k, dict) or set(k) != {"hard", "soft", "ml"} or any(type(item) is not int for item in k.values()):
        raise ValueError("invalid_detection_policy")
    if not isinstance(value["policyKey"], str) or value["policyKey"] not in POLICY_V2_KEYS:
        raise ValueError("unsupported_detection_policy")
    return PolicyV2(value["policyKey"], tuple((name, k[name]) for name in ("hard", "soft", "ml")), deadline)


def parse_versioned_policy(spec: dict, *, required: bool = False) -> Policy | PolicyV2:
    """version 키가 있으면 v2만 인정하고, 없으면 기존 v1·legacy 판독을 그대로 쓴다."""
    value = spec.get("detectionPolicy")
    if isinstance(value, dict):
        if "version" in value:
            return parse_policy_v2(value)
        if value.keys() & {"policyKey", "k"}:
            raise ValueError("invalid_detection_policy")
    return parse_policy(spec, required=required)


def execute(policy: Policy, rule: Callable, ml: Callable, *, started_at: float | None = None) -> dict:
    """Callbacks are pure computation: no DB writes, even after a deadline expires."""
    selected = {"rule": rule, "ml": ml} if policy.mode == "parallel" else {
        "rule" if policy.mode == "rule_only" else "ml": rule if policy.mode == "rule_only" else ml}
    results = {name: {"status": "skipped", "anomalous": None, "evidence": []} for name in ("rule", "ml")}
    futures = {}
    started = time.monotonic() if started_at is None else started_at
    deadline = started + policy.deadline_ms / 1000
    slots = {}
    def guarded(callback, slot):
        try:
            return {**callback(),"_completedAt":time.monotonic()}
        except Exception:
            return {"status": "failed", "anomalous": None, "evidence": [{"reason": "component_failed"}],"_completedAt":time.monotonic()}
        finally:
            slot.release()
    for name, callback in selected.items():
        slot = _SLOTS[name]
        if time.monotonic() >= deadline:
            results[name] = {"status":"not_ready","anomalous":None,"evidence":[{"reason":"deadline_exceeded"}]}
        elif slot.acquire(blocking=False):
            future = _POOLS[name].submit(guarded, callback, slot)
            futures[future] = name
            slots[future] = slot
        else:
            results[name] = {"status": "not_ready", "anomalous": None, "evidence": [{"reason": "worker_busy"}]}
    done, pending = wait(futures, timeout=max(0, policy.deadline_ms / 1000 - (time.monotonic() - started)))
    for future in done:
        value = future.result().copy()
        completed = value.pop("_completedAt")
        results[futures[future]] = value if completed <= deadline else {
            "status":"not_ready","anomalous":None,"evidence":[{"reason":"deadline_exceeded"}]}
    for future in pending:
        # Cancelled-before-start tasks must return their capacity here.
        if future.cancel():
            slots[future].release()
        results[futures[future]] = {"status": "not_ready", "anomalous": None, "evidence": [{"reason": "deadline_exceeded"}]}
    complete = all(results[name]["status"] == "succeeded" for name in selected)
    has_result = any(results[name]["status"] == "succeeded" or results[name].get("anomalous") is True for name in selected)
    anomaly = True if any(results[name].get("anomalous") is True for name in selected) else False if complete else None
    return {"status": "complete" if complete else "partial" if has_result else "unavailable",
            "anomalous": anomaly, "requestedMode": policy.mode,
            "actualPaths": [name for name in selected if results[name]["status"] == "succeeded"],
            "policy": policy.document(), **results}
