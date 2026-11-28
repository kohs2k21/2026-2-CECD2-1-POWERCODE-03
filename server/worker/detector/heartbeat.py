"""판정 시도의 임대 갱신(heartbeat)을 작업자 루프마다 스레드 1개가 맡는다.

`trainer.lease.TrainingLeaseHeartbeat`는 시도마다 스레드를 하나 띄우고 멈출 때 join한다. 판정 시도는 대부분 수십 ms라
갱신(20초 뒤)까지 가지 않는데도 판정마다 스레드 생성·join 비용이 든다. 여기서는 작업자 루프(연결 1개)마다
`SharedLeaseHeartbeat` 하나가 그 루프의 시도를 등록받아 스레드 1개로 갱신한다. 의미는 TrainingLeaseHeartbeat와 같다.

- 등록(`lease(...).start()`) 뒤 interval_seconds(20초)가 지난 시도마다 같은 함수 `renew_training_ownership`으로 시도·큐 임대를
  lease_seconds(60초)로 연장하고, 다음 갱신은 그 갱신이 끝난 때부터 다시 interval_seconds 뒤다(TrainingLeaseHeartbeat의
  `wait(interval)` → 갱신 반복과 같은 간격).
- 갱신은 작업자 연결과 다른 연결로 한다. 처음 갱신할 때 열고(autocommit) 다음 갱신에 다시 쓴다. 갱신 스레드가 끝날 때
  (`close()`·예외 무관) 그 연결을 닫는다.
- 다시 쓰는 연결은 쉬는 동안 끊겼을 수 있다(유휴 시간 제한·네트워크 유휴 차단·`pg_terminate_backend`). 그래서 이전 갱신에서 연
  연결로 한 갱신이 연결 수준 오류(`psycopg.OperationalError`·`InterfaceError`, 또는 그 뒤 연결이 닫힘·끊김)로 끝나면 그 연결을
  닫고 새 연결로 그 갱신을 한 번만 다시 한다. TrainingLeaseHeartbeat는 갱신마다 새 연결을 열므로, 끊긴 유휴 연결 때문에 정상
  시도를 상실로 만들지 않는 결과가 같다.
- 갱신이 False(소유권 없음, 다시 하지 않음)이거나 새 연결에서도 예외면 그 시도만 `lost`가 참이 되고 더 갱신하지 않는다
  (TrainingLeaseHeartbeat는 그때 스레드가 끝난다). 예외면 그 연결을 닫고 다음 갱신은 새 연결로 한다.
- `stop()`(등록 해제)이 돌아온 뒤에는 그 시도의 갱신이 없다. 그 시도의 갱신이 진행 중이면 끝날 때까지 기다린다
  (TrainingLeaseHeartbeat의 join과 같은 보장). 다른 시도의 갱신은 기다리지 않는다.
- 교착: stop()이 기다리는 것은 자기 시도의 진행 중 갱신뿐이다. 그 갱신이 막히는 것은 그 시도의 작업·시도·큐 행을 잠근
  트랜잭션(작업자 연결) 때문뿐이므로, 호출자는 지금처럼 실패 경로에서 ROLLBACK한 뒤, 성공 경로에서는 COMMIT한 뒤 stop()을
  부른다(runner `_process_message`·`_fail_pending`·`process_detection_batch`). 한 작업자의 시도만 등록하므로 다른 작업자의 행
  잠금이 이 스레드를 막는 일은 없다. 작업자 트랜잭션이 행을 잠근 동안 그 시도의 갱신이 기다리면 같은 작업자의 다른 시도 갱신도
  그 트랜잭션이 끝날 때까지 늦어진다. 작업자는 트랜잭션을 연 채 이 스레드를 기다리지 않으므로 늦어질 뿐 막히지 않는다.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

import psycopg

from trainer.lease import renew_training_ownership

# TrainingLeaseHeartbeat 기본값과 같다. 객체를 만들 때 읽는다(시험이 간격을 줄일 수 있게).
LEASE_SECONDS = 60
INTERVAL_SECONDS = 20.0


class SharedLease:
    """시도 1건의 등록. TrainingLeaseHeartbeat와 같은 인터페이스(start·stop·lost)다."""

    def __init__(self, owner: "SharedLeaseHeartbeat", message: Any, job_id: uuid.UUID, attempt_id: uuid.UUID,
                 attempt_token: uuid.UUID) -> None:
        self._owner = owner
        self.message, self.job_id = message, job_id
        self.attempt_id, self.attempt_token = attempt_id, attempt_token
        self.due: float | None = None  # 다음 갱신 시각(time.monotonic). 등록 전·해제 뒤·상실 뒤에는 쓰지 않는다
        self.started = False
        self.renewing = False
        self._lost = False

    @property
    def lost(self) -> bool:
        return self._lost

    def start(self) -> None:
        self._owner._register(self)

    def stop(self) -> None:
        self._owner._unregister(self)


class SharedLeaseHeartbeat:
    """등록된 시도들의 임대를 스레드 1개·연결 1개로 갱신한다(작업자 루프 하나 또는 직접 전달 서버 하나가 쓴다)."""

    def __init__(self, connection_factory: Callable[[], Any], *, lease_seconds: int | None = None,
                 interval_seconds: float | None = None, name: str = "detector-heartbeat",
                 renew: Callable[..., bool] | None = None) -> None:
        lease_seconds = LEASE_SECONDS if lease_seconds is None else lease_seconds
        interval_seconds = INTERVAL_SECONDS if interval_seconds is None else interval_seconds
        if lease_seconds < 1 or interval_seconds <= 0 or interval_seconds >= lease_seconds:
            raise ValueError("갱신 간격은 임대 시간보다 짧은 양수여야 한다")
        self.connection_factory = connection_factory
        self.lease_seconds, self.interval_seconds = lease_seconds, interval_seconds
        self._renew = renew or renew_training_ownership
        self._cond = threading.Condition()
        self._leases: set[SharedLease] = set()  # 갱신 대상(등록됐고 상실·해제 전)
        self._closed = False
        self._waiting_until: float | None = None  # 스레드가 기다리는 중이면 깨어날 시각(무기한이면 inf)
        self._conn: Any = None  # 갱신 스레드만 쓴다
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def __enter__(self) -> "SharedLeaseHeartbeat":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def lease(self, message: Any, job_id: uuid.UUID, attempt_id: uuid.UUID, attempt_token: uuid.UUID) -> SharedLease:
        return SharedLease(self, message, job_id, attempt_id, attempt_token)

    def _register(self, lease: SharedLease) -> None:
        with self._cond:
            if lease.started:
                raise RuntimeError("detection_heartbeat_already_started")
            lease.started = True
            if self._closed:
                # 닫힌 뒤(종료 중) 시작한 시도는 갱신할 수 없으므로 바로 상실이다. 예외 대신 lost로 알려 시도 코드가
                # 지금의 상실 처리(detection_lease_lost → 시도 실패 기록)를 그대로 하게 한다.
                lease._lost = True
                return
            lease.due = time.monotonic() + self.interval_seconds
            self._leases.add(lease)
            # 기존 등록의 갱신 시각은 늘 새 등록보다 이르므로 스레드를 깨우는 것은 기다릴 시각이 없을 때(비어 있었을 때)뿐이다.
            if self._waiting_until is not None and lease.due < self._waiting_until:
                self._cond.notify_all()

    def _unregister(self, lease: SharedLease) -> None:
        with self._cond:
            self._leases.discard(lease)
            while lease.renewing:
                self._cond.wait()

    def close(self, timeout: float = 5.0) -> None:
        """스레드를 멈춘다. 남은 등록은 더 갱신하지 않으므로 상실로 표시한다. 진행 중 갱신은 최대 timeout초 기다린다."""
        with self._cond:
            self._closed = True
            for lease in self._leases:
                lease._lost = True
            self._leases.clear()
            self._cond.notify_all()
        # 연결은 갱신 스레드만 다룬다. join이 timeout으로 끝나도(갱신이 DB에서 막힘) 그 스레드가 끝날 때 닫는다(_run).
        if self._thread.ident is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def _next(self) -> SharedLease | None:
        """갱신할 시도 하나(가장 이른 것). 닫히면 None. 고른 시도는 renewing으로 표시한다."""
        with self._cond:
            while not self._closed:
                now = time.monotonic()
                lease = min(self._leases, key=lambda item: item.due, default=None)
                if lease is not None and lease.due <= now:
                    self._waiting_until = None
                    lease.renewing = True
                    return lease
                self._waiting_until = float("inf") if lease is None else lease.due
                self._cond.wait(None if lease is None else lease.due - now)
            self._waiting_until = None
            return None

    def _run(self) -> None:
        try:
            while True:
                lease = self._next()
                if lease is None:
                    return
                renewed = False
                try:
                    renewed = self._renew_once(lease)
                finally:
                    with self._cond:
                        lease.renewing = False
                        if not renewed:
                            lease._lost = True
                            self._leases.discard(lease)
                        elif lease in self._leases:
                            lease.due = time.monotonic() + self.interval_seconds
                        self._cond.notify_all()
        finally:
            # 어떻게 끝나든(close·예외) 갱신 연결을 남기지 않는다.
            self._close_connection()

    def _renew_once(self, lease: SharedLease) -> bool:
        """갱신 1번. 이전 갱신에서 연 연결이 연결 수준 오류로 끝나면 새 연결로 한 번만 다시 한다. False는 다시 하지 않는다."""
        reused = self._usable(self._conn)
        conn = None
        try:
            conn = self._connection()
            return self._renew_with(conn, lease)
        except Exception as exc:  # noqa: BLE001 - 연결 오류도 소유권 상실로 취급한다(TrainingLeaseHeartbeat와 같음)
            # 연결 상태는 닫기 전에 본다(닫은 뒤에는 늘 닫힘으로 보인다).
            retry = reused and self._connection_error(exc, conn)
            self._close_connection()
            if not retry:
                return False
        try:
            return self._renew_with(self._connection(), lease)
        except Exception:  # noqa: BLE001 - 새 연결에서도 실패하면 상실
            self._close_connection()
            return False

    def _renew_with(self, conn: Any, lease: SharedLease) -> bool:
        return bool(self._renew(conn, lease.message, lease.job_id, lease.attempt_id, lease.attempt_token,
                                lease_seconds=self.lease_seconds))

    @staticmethod
    def _usable(conn: Any) -> bool:
        return conn is not None and not getattr(conn, "closed", False) and not getattr(conn, "broken", False)

    @staticmethod
    def _connection_error(exc: Exception, conn: Any) -> bool:
        """쉬는 동안 끊긴 연결에서 나는 오류인지. 그 밖의 오류(SQL 오류 등)는 다시 해도 같으므로 다시 하지 않는다."""
        return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)) or \
            (conn is not None and not SharedLeaseHeartbeat._usable(conn))

    def _connection(self) -> Any:
        """쓸 수 있는 갱신 연결. 없거나 닫힘·끊김이면 새로 연다."""
        if not self._usable(self._conn):
            self._close_connection()
            self._conn = self.connection_factory()
        return self._conn

    def _close_connection(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
