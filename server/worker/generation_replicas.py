"""Rule v3 필드 생성 복제본. GPU마다 자식 프로세스 하나에 `DesignModel`을 따로 올려 한 작업의 필드를 나눠 처리한다.

- 켜기: `LLM_REPLICA_GPUS`(컨테이너 안 GPU 번호 목록, 예 "1,2,3"). 비어 있으면 복제본 없음 = 기존 직렬 실행과 같다.
- 프로세스(스레드 아님): HF generate는 토큰마다 파이썬에서 커널을 내므로 한 프로세스의 여러 스레드는 GIL로 거의
  빨라지지 않는다. `spawn`으로 새 인터프리터를 띄우므로 주 프로세스의 CUDA 상태를 물려받지 않는다.
- GPU 지정: 주 프로세스는 컨테이너 env `CUDA_VISIBLE_DEVICES`(기본 compose "0") 그대로 한 장만 본다. 자식은 시작하자마자
  (torch import·CUDA 초기화 전) 자기 env만 `CUDA_VISIBLE_DEVICES=<그 GPU>`로 바꾼다. 그래서 자식 안에서도
  `device_count()==1`·`cuda:0`이라 `DesignModel` 코드는 그대로다. 너무 늦게 바뀌어 여러 장이 보이면 `DesignModel`이
  `expose_one_bf16_gpu`로 거부하므로 잘못된 GPU를 쓰는 일 없이 그 복제본만 꺼진다.
- 전달: 파이프(pickle). 번들 결과의 Decimal 등 값 형식이 JSON 왕복으로 바뀌지 않는다. 예외 클래스는 넘기지 않고
  종류 코드로 바꿔 부모에서 같은 종류로 다시 올린다(`TimeoutError`, 번들 `InputFailure`·`ModelFailure`, 그 밖은
  `GenerationUnavailable("generation_failed")`). 작업 폴더(`/tmp` 아래)는 같은 컨테이너라 경로만 넘긴다.
- 실패: 적재 실패·어댑터 SHA 불일치·시작 시간 초과면 그 복제본을 쓰지 않고 `rule_v3_replica_disabled: <gpu> <코드>`
  한 줄을 남긴다. llm 시작을 막지 않는다. 실행 중 자식이 죽으면 그 복제본을 빼고 그 필드는 `generation_failed`.
- 대화형 생성(explain)은 주 프로세스만 처리한다. 복제본은 자기 `GenerationGate`를 가지며 Rule v3 필드만 받는다.
- 종료: 자식은 daemon이라 부모가 정상 종료하면 multiprocessing이 끝낸다. 부모가 신호로 죽으면 자식의 감시 스레드가
  부모 종료를 보고 스스로 끝난다(컨테이너는 init이 끝나면 남은 프로세스도 함께 정리된다).
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ENV_GPUS = "LLM_REPLICA_GPUS"
ENV_START_SECONDS = "LLM_REPLICA_START_SECONDS"
DEFAULT_START_SECONDS = 1800.0  # Base 4bit 적재 + LoRA 3개 + xgrammar 컴파일러. 넘기면 그 복제본만 끈다
POLL_SECONDS = 1.0
STOP_SECONDS = 10.0
_CODE = re.compile(r"[a-z][a-z0-9_]{0,99}")
_GPU = re.compile(r"\d{1,3}")
log = logging.getLogger(__name__)


def _code(exc: BaseException, fallback: str) -> str:
    return str(exc) if _CODE.fullmatch(str(exc)) else fallback


def _start_seconds() -> float:
    try:
        value = float(os.environ.get(ENV_START_SECONDS, DEFAULT_START_SECONDS))
    except ValueError:
        return DEFAULT_START_SECONDS
    return value if value > 0 else DEFAULT_START_SECONDS


def parse_gpus(value: str | None, main_visible: str | None) -> tuple[list[str], list[tuple[str, str]]]:
    """`LLM_REPLICA_GPUS` → (쓸 GPU 번호, [(항목, 끈 코드)]). 주 프로세스가 보는 GPU와 같은 번호는 쓰지 않는다."""
    gpus: list[str] = []
    disabled: list[tuple[str, str]] = []
    main = (main_visible or "").strip()
    for raw in (value or "").split(","):
        item = raw.strip()
        if not item:
            continue
        if not _GPU.fullmatch(item):
            disabled.append((item[:16], "replica_gpu_invalid"))
            continue
        gpu = str(int(item))
        if gpu in gpus:
            disabled.append((gpu, "replica_gpu_duplicate"))
        elif main and gpu == main:
            disabled.append((gpu, "replica_gpu_conflict"))
        else:
            gpus.append(gpu)
    return gpus, disabled


def load_design_runtime(roots: Sequence[str]):
    """자식 프로세스 기본 적재: 주 프로세스와 같은 `DesignModel`(Rule v3 포함)을 올리고 그 `rule_v3`를 돌려준다."""
    from generation_service import DesignModel
    bundle, base, rule_v3_bundle = (Path(root) for root in roots)
    model = DesignModel(bundle, base, rule_v3_bundle)
    if model.rule_v3 is None:
        raise RuntimeError(model.rule_v3_error if _CODE.fullmatch(str(model.rule_v3_error)) else "rule_v3_disabled")
    return model.rule_v3


def _watch_parent() -> None:
    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def watch():
        parent.join()
        os._exit(0)

    threading.Thread(target=watch, name="replica-parent-watch", daemon=True).start()


def _reply(runtime, paths: Mapping[str, Any], out: Any) -> tuple:
    package = runtime.package
    # 순서는 `_item_v3`의 except 순서와 같다(TimeoutError → InputFailure → ModelFailure). InputFailure가 ValueError
    # 하위 클래스이므로 일반 ValueError는 그 뒤에 본다. ValueError는 주 프로세스 직접 실행처럼 작업 종료 실패가 되도록
    # 같은 종류로 다시 올리고, 문구는 runner와 같은 기준(코드 꼴이 아니면 invalid_job_input)으로 정리해 보낸다.
    try:
        return ("ok", runtime.run_field(paths, out))
    except TimeoutError:
        return ("error", "timeout", "generation_timeout")
    except package.io.InputFailure as exc:
        return ("error", "input", str(exc))
    except package.io.ModelFailure as exc:
        return ("error", "model", str(exc))
    except ValueError as exc:
        return ("error", "value", _code(exc, "invalid_job_input"))
    except Exception as exc:
        log.error("rule_v3_replica_field_failed: %s", type(exc).__name__)
        return ("error", "failed", type(exc).__name__)


def serve_replica(conn, gpu: str, loader: Callable[[Sequence[str]], Any], roots: Sequence[str]) -> None:
    """자식 프로세스 본체. 첫 메시지로 준비(`ready`, adapter_sha)나 실패 코드를 보내고 필드 요청을 하나씩 처리한다."""
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu  # torch·CUDA 초기화 전에 바꾼다(모듈 머리말)
    _watch_parent()
    try:
        runtime = loader(roots)
        conn.send(("ready", runtime.adapter_sha))
    except Exception as exc:
        conn.send(("failed", _code(exc, "replica_start_failed")))
        return
    while True:
        try:
            message = conn.recv()
        except (EOFError, OSError):
            return
        if not isinstance(message, tuple) or not message or message[0] != "run":
            return
        reply = _reply(runtime, message[1], message[2])
        try:
            conn.send(reply)
        except (EOFError, OSError):
            return
        except Exception:  # 결과를 pickle할 수 없다(번들 결과 형식 변화). 그 필드만 실패로 알린다
            log.error("rule_v3_replica_result_unpicklable")
            try:
                conn.send(("error", "failed", "result_unpicklable"))
            except Exception:
                return


def _close(conn) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


class ReplicaLost(RuntimeError):
    pass


class ReplicaRuntime:
    """복제본 대리 객체. `_item_v3`에는 `run_field`·`adapter_sha`만 대신하고 `package`(예외 클래스·capture)는 주 프로세스 것이다."""

    def __init__(self, gpu: str, process, conn, adapter_sha: str, package, pool: "ReplicaPool"):
        self.gpu = gpu
        self.adapter_sha = adapter_sha
        self.package = package
        self._process = process
        self._conn = conn
        self._pool = pool
        self._lock = threading.Lock()
        self.lost = False

    def _receive(self):
        while not self._conn.poll(POLL_SECONDS):
            if not self._process.is_alive():
                raise ReplicaLost("replica_exited")
        return self._conn.recv()

    def _lose(self, code: str) -> None:
        self.lost = True
        self._pool.disable(self, code)
        ReplicaPool._terminate(self._process)

    def run_field(self, paths: Mapping[str, Path], out: Path) -> dict[str, Any]:
        from generation_client import GenerationUnavailable
        with self._lock:
            if self.lost:
                raise GenerationUnavailable("generation_failed")
            try:
                self._conn.send(("run", dict(paths), out))
                reply = self._receive()
            except (EOFError, OSError, ReplicaLost):
                self._lose("replica_exited")
                raise GenerationUnavailable("generation_failed") from None
            except Exception:  # 응답을 부모에서 풀 수 없다. 메시지는 소비됐으므로 복제본은 계속 쓴다
                log.error("rule_v3_replica_result_unreadable: %s", self.gpu)
                raise GenerationUnavailable("generation_failed") from None
        if isinstance(reply, tuple) and len(reply) == 2 and reply[0] == "ok" and isinstance(reply[1], dict):
            return reply[1]
        if isinstance(reply, tuple) and len(reply) == 3 and reply[0] == "error":
            kind, message = reply[1], str(reply[2])
            if kind == "timeout":
                raise TimeoutError("generation_timeout")
            if kind == "input":
                raise self.package.io.InputFailure(message)
            if kind == "model":
                raise self.package.io.ModelFailure(message)
            if kind == "value":
                raise ValueError(message if _CODE.fullmatch(message) else "invalid_job_input")
            raise GenerationUnavailable("generation_failed")
        self._lose("replica_protocol_error")
        raise GenerationUnavailable("generation_failed")

    def stop(self) -> None:
        try:
            self._conn.send(("stop",))
        except Exception:
            pass


class ReplicaPool:
    """복제본 시작·준비 목록. `ready()`는 준비가 끝나고 빠지지 않은 복제본만 GPU 설정 순서로 준다."""

    def __init__(self, gpus: Sequence[str], roots: Sequence[str], expected_sha: str, package, *,
                 loader: Callable[[Sequence[str]], Any] = load_design_runtime, start_seconds: float | None = None,
                 context=None):
        self.gpus = list(gpus)
        self.roots = [str(root) for root in roots]
        self.expected_sha = expected_sha
        self.package = package
        self.loader = loader
        self.start_seconds = _start_seconds() if start_seconds is None else start_seconds
        self.context = context or multiprocessing.get_context("spawn")
        self._lock = threading.Lock()
        self._ready: dict[str, ReplicaRuntime] = {}
        self._processes: dict[str, Any] = {}
        self._threads: list[threading.Thread] = []
        self._closed = False
        self.disabled: dict[str, str] = {}

    def disable(self, replica: ReplicaRuntime | None, code: str, gpu: str | None = None) -> None:
        gpu = replica.gpu if replica is not None else gpu
        with self._lock:
            if replica is not None and self._ready.get(gpu) is replica:
                del self._ready[gpu]
            if gpu in self.disabled:
                return
            self.disabled[gpu] = code
        log.error("rule_v3_replica_disabled: %s %s", gpu, code)

    def ready(self) -> list[ReplicaRuntime]:
        with self._lock:
            return [self._ready[gpu] for gpu in self.gpus if gpu in self._ready and not self._ready[gpu].lost]

    def start(self) -> "ReplicaPool":
        for gpu in self.gpus:
            thread = threading.Thread(target=self._start_one, args=(gpu,), name=f"rule-v3-replica-{gpu}", daemon=True)
            self._threads.append(thread)
            thread.start()
        return self

    def wait_started(self, timeout: float | None = None) -> None:
        """모든 복제본이 준비되거나 꺼질 때까지 기다린다(시험·점검용, 운영 시작은 기다리지 않는다)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in self._threads:
            thread.join(None if deadline is None else max(0.0, deadline - time.monotonic()))

    def _start_one(self, gpu: str) -> None:
        process = parent_conn = None
        try:
            parent_conn, child_conn = self.context.Pipe(duplex=True)
            process = self.context.Process(target=serve_replica, args=(child_conn, gpu, self.loader, self.roots),
                                           name=f"rule-v3-replica-{gpu}", daemon=True)
            process.start()
            child_conn.close()  # 자식이 죽으면 부모 쪽 recv가 EOF를 받게 한다
            with self._lock:
                self._processes[gpu] = process
            deadline = time.monotonic() + self.start_seconds
            while not parent_conn.poll(POLL_SECONDS):
                if not process.is_alive():
                    raise ReplicaLost("replica_exited")
                if time.monotonic() >= deadline:
                    raise ReplicaLost("replica_start_timeout")
            message = parent_conn.recv()
        except ReplicaLost as exc:
            self._give_up(gpu, process, parent_conn, str(exc))
            return
        except Exception as exc:
            self._give_up(gpu, process, parent_conn, _code(exc, "replica_start_failed"))
            return
        if isinstance(message, tuple) and len(message) == 2 and message[0] == "ready":
            if message[1] != self.expected_sha:
                self._give_up(gpu, process, parent_conn, "rule_v3_adapter_mismatch", stop=True)
                return
            replica = ReplicaRuntime(gpu, process, parent_conn, message[1], self.package, self)
            with self._lock:
                closed = self._closed
                if not closed:
                    self._ready[gpu] = replica
            if closed:  # 준비 중에 종료가 시작됐다
                replica.stop()
                self._terminate(process, STOP_SECONDS)
                _close(parent_conn)
                return
            log.info("rule_v3_replica_ready: %s", gpu)
            return
        code = message[1] if isinstance(message, tuple) and len(message) == 2 and message[0] == "failed" and \
            isinstance(message[1], str) and _CODE.fullmatch(message[1]) else "replica_start_failed"
        self._give_up(gpu, process, parent_conn, code)

    def _give_up(self, gpu: str, process, conn, code: str, stop: bool = False) -> None:
        """시작 실패 정리: (필요하면 멈춤 요청) → 프로세스 종료 → 파이프 닫기 → 끈 로그. 정리 중 오류가 나도 로그는 남긴다."""
        try:
            if stop and conn is not None:
                try:
                    conn.send(("stop",))
                except Exception:  # 자식이 이미 끊었다(BrokenPipe 등)
                    pass
            self._terminate(process)
            _close(conn)
        finally:
            self.disable(None, code, gpu)

    @staticmethod
    def _terminate(process, grace: float = 1.0) -> None:
        """스스로 끝나는 중이면 잠깐 기다리고, 남아 있으면 terminate → kill 순으로 끝낸다."""
        if process is None:
            return
        process.join(grace)
        if process.is_alive():
            process.terminate()
            process.join(STOP_SECONDS)
        if process.is_alive():
            process.kill()
            process.join(STOP_SECONDS)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            replicas = list(self._ready.values())
            processes = list(self._processes.values())
            self._ready.clear()
        for replica in replicas:
            replica.stop()
        for process in processes:
            self._terminate(process, STOP_SECONDS)


def start_from_env(model, roots: Sequence[str | None]) -> ReplicaPool | None:
    """`LLM_REPLICA_GPUS`가 있으면 복제본을 백그라운드로 띄운다. 주 모델에 Rule v3가 없으면 띄우지 않는다."""
    gpus, invalid = parse_gpus(os.environ.get(ENV_GPUS), os.environ.get("CUDA_VISIBLE_DEVICES"))
    for gpu, code in invalid:
        log.error("rule_v3_replica_disabled: %s %s", gpu, code)
    if not gpus:
        return None
    runtime = getattr(model, "rule_v3", None)
    if runtime is None:
        for gpu in gpus:
            log.error("rule_v3_replica_disabled: %s %s", gpu, "rule_v3_not_configured")
        return None
    if any(root is None for root in roots):
        for gpu in gpus:
            log.error("rule_v3_replica_disabled: %s %s", gpu, "rule_v3_not_configured")
        return None
    try:
        return ReplicaPool(gpus, roots, runtime.adapter_sha, runtime.package).start()
    except Exception as exc:  # 복제본 문제로 llm 시작을 막지 않는다
        for gpu in gpus:
            log.error("rule_v3_replica_disabled: %s %s", gpu, _code(exc, "replica_start_failed"))
        return None
