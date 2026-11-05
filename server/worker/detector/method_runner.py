"""방법 단위 실행과 기한 처리. 방법 콜백은 DB를 쓰지 않고, 호출자 스레드만 on_progress에서 DB를 쓴다."""
from __future__ import annotations

from dataclasses import dataclass, field
from queue import Empty, SimpleQueue
import time
from typing import Any, Callable, Iterable, Mapping

from detector.method_results import with_elapsed
from detector.policy import _POOLS, _SLOTS


@dataclass(frozen=True)
class MethodOutcome:
    """방법 스레드가 돌려주는 값 전부. 측정값도 결과와 함께 넘겨 채택 여부를 같이 정한다."""
    result: dict[str, Any]
    measured: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MethodExecution:
    methods: list[dict[str, Any]]
    # 기한 안에 채택된 방법의 측정값만 있다. 버린 결과의 값은 여기까지 오지 않는다.
    measured: dict[str, Mapping[str, Any]]


@dataclass(frozen=True)
class MethodTask:
    method_id: str
    lane: str  # rule | ml
    run: Callable[[], MethodOutcome]
    # (cause, status=..., failure_code=...)를 받아 결과를 내지 못한 방법의 결과를 만든다.
    unresolved: Callable[..., dict[str, Any]]


def execute_methods(tasks: Iterable[MethodTask], deadline_ms: int, *, started_at: float,
                    on_progress: Callable[[list[dict[str, Any]]], None] | None = None) -> MethodExecution:
    """방법마다 결과 1개를 tasks 순서로 돌려준다. 기한을 넘긴 결과는 측정값과 함께 버리고 timeout으로 남긴다.

    룰은 수가 많고 계산이 짧아 한 슬롯에서 차례로 돌린다. 슬롯 수는 Detector 동시 작업 수에 맞춰져 있어서
    방법마다 슬롯을 잡으면 정상 입력도 capacity로 끝난다. 모델은 시간이 길어 모델마다 슬롯을 잡는다.
    on_progress는 아직 미도착 방법이 있을 때만 호출하며 미도착 방법은 cause=pending으로 넘긴다.
    """
    tasks = list(tasks)
    deadline = started_at + deadline_ms / 1000
    arrivals: SimpleQueue = SimpleQueue()
    results: dict[str, dict[str, Any]] = {}
    adopted: dict[str, Mapping[str, Any]] = {}

    def measured(moment: float | None = None) -> float:
        return ((time.monotonic() if moment is None else moment) - started_at) * 1000

    def settle(task: MethodTask, cause: str) -> None:
        results[task.method_id] = with_elapsed(task.unresolved(cause), measured())

    def run_one(task: MethodTask) -> None:
        try:
            outcome = task.run()
        except Exception:
            # 한 방법의 오류가 다른 방법의 결과를 지우지 않는다.
            outcome = MethodOutcome(task.unresolved("failure", status="failed", failure_code="exception"))
        arrivals.put((task, outcome, time.monotonic()))

    def run_lane(name: str, group: list[MethodTask]) -> None:
        try:
            for task in group:
                if time.monotonic() >= deadline:
                    return
                run_one(task)
        finally:
            _SLOTS[name].release()

    def accept(item: tuple[MethodTask, MethodOutcome, float]) -> None:
        task, outcome, completed = item
        if completed <= deadline:
            results[task.method_id] = with_elapsed(outcome.result, measured(completed))
            adopted[task.method_id] = outcome.measured
        else:
            settle(task, "timeout")

    def drain() -> None:
        while True:
            try:
                accept(arrivals.get_nowait())
            except Empty:
                return

    rule_tasks = [task for task in tasks if task.lane == "rule"]
    lanes = [("rule", rule_tasks)] if rule_tasks else []
    lanes += [("ml", [task]) for task in tasks if task.lane == "ml"]
    futures = []
    try:
        for name, group in lanes:
            if time.monotonic() >= deadline:
                for task in group:
                    settle(task, "timeout")
            elif _SLOTS[name].acquire(blocking=False):
                futures.append((_POOLS[name].submit(run_lane, name, group), name))
            else:
                for task in group:
                    settle(task, "capacity")
        while len(results) < len(tasks):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                accept(arrivals.get(timeout=remaining))
            except Empty:
                # 대기가 시계 해상도만큼 일찍 끝날 수 있다. 기한은 위 검사로만 판정한다.
                continue
            drain()
            if on_progress is not None and len(results) < len(tasks):
                on_progress([results.get(task.method_id) or task.unresolved("pending") for task in tasks])
        drain()
        for task in tasks:
            if task.method_id not in results:
                settle(task, "timeout")
    finally:
        for future, name in futures:
            # 시작 전에 취소된 작업은 run_lane이 돌지 않으므로 여기서 슬롯을 돌려준다.
            if future.cancel():
                _SLOTS[name].release()
    return MethodExecution([results[task.method_id] for task in tasks], adopted)
