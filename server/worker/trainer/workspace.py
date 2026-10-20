"""학습 attempt 임시 작업 폴더(`ARTIFACT_ROOT/tmp/<attempt>`)의 생성·정리.

성공 공개 규칙은 바꾸지 않는다: 검증된 산출물은 먼저 `models/`·`fit/`로 원자 이동(`os.replace`)하고
DB 등록·job 성공·queue ACK를 같은 트랜잭션으로 확정한다. 이 모듈은 그 뒤에 남는 임시 폴더와,
트랜잭션이 롤백된 뒤 등록되지 않은 채 남는 승격 파일만 제거한다.
"""
from __future__ import annotations

import logging
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

import psycopg

log = logging.getLogger(__name__)

PUBLISHED_KINDS = ("models", "fit")


def _temporary_root(root: Path) -> Path:
    root = Path(root).resolve(strict=True)
    temporary = root / "tmp"
    if temporary.is_symlink():
        raise ValueError("artifact_path_not_allowed")
    return temporary


def attempt_workspace(root: Path, attempt_id: uuid.UUID) -> Path:
    """attempt 전용 임시 폴더를 비운 상태로 만든다. symlink 경유는 거부한다."""
    temporary = _temporary_root(root)
    temporary.mkdir(exist_ok=True)
    output = temporary / str(uuid.UUID(str(attempt_id)))
    if output.is_symlink():
        raise ValueError("artifact_path_not_allowed")
    if output.exists():
        # attempt id는 claim마다 새로 발급되므로 남아 있는 내용은 이전 실행의 잔재다.
        discard_workspace(root, output)
    output.mkdir(exist_ok=False)
    return output


def discard_workspace(root: Path, output: Path) -> bool:
    """`tmp/` 아래 attempt 폴더와 부분 파일을 제거한다. `tmp/` 밖이나 `tmp/` 자체는 건드리지 않는다."""
    try:
        temporary = _temporary_root(root)
        target = Path(output)
        resolved = target.resolve()
        if target.is_symlink() or resolved == temporary.resolve() or not resolved.is_relative_to(temporary.resolve()):
            return False
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink(missing_ok=True)
        return not target.exists()
    except (OSError, ValueError) as exc:
        log.warning("training workspace cleanup skipped: %s (%s)", output, exc)
        return False


def discard_unpublished(root: Path, paths: Iterable[Any]) -> list[str]:
    """등록 트랜잭션이 롤백돼 DB 참조가 없는 승격 산출물(`models/`·`fit/`)을 제거한다.

    호출자는 `conn.transaction()` 블록이 예외로 끝난 뒤에만 부른다. 블록이 정상 종료했으면
    파일은 이미 `ml_artifacts`에 등록된 것이므로 호출하지 않는다.
    """
    removed: list[str] = []
    try:
        base = Path(root).resolve(strict=True)
    except OSError:
        return removed
    for value in paths:
        if not isinstance(value, (str, Path)):
            continue
        path = Path(value)
        try:
            resolved = path.resolve()
            if path.is_symlink() or not path.is_file() or path.suffix != ".joblib":
                continue
            if not any(resolved.is_relative_to(base / kind) for kind in PUBLISHED_KINDS):
                continue
            path.unlink()
            removed.append(str(path))
        except OSError as exc:
            log.warning("unpublished artifact cleanup skipped: %s (%s)", path, exc)
    return removed


@contextmanager
def training_workspace(root: Path, attempt_id: uuid.UUID) -> Iterator[tuple[Path, list[str]]]:
    """학습 attempt 임시 폴더를 제공하고, 성공·실패 모두 임시 폴더를 정리한다.

    산출물을 `_promote_output`으로 승격할 때마다 반환된 경로를 `promoted`에 넣는다.
    블록이 예외로 끝나면(등록 트랜잭션 롤백 포함) 승격했지만 미등록인 파일도 제거한다.
    단, 연결 단절(`psycopg.OperationalError`)은 커밋 여부를 알 수 없으므로 승격 파일을 보존한다.
    """
    output = attempt_workspace(root, attempt_id)
    promoted: list[str] = []
    try:
        yield output, promoted
    except psycopg.OperationalError as exc:
        # in-doubt commit: COMMIT 전송 후 응답 전 연결 단절이면 서버에는 등록됐을 수 있다.
        # 승격 파일은 보존하고(변경 전 동작) 경고만 남긴다. tmp 폴더 정리는 finally에서 그대로 한다.
        log.warning("connection lost during training publication; keeping %d promoted artifact file(s) for attempt %s: %s",
                    len(promoted), attempt_id, exc)
        raise
    except BaseException:
        discard_unpublished(root, promoted)
        raise
    finally:
        discard_workspace(root, output)


def cleanup_orphan_workspaces(conn: Any, root: Path) -> list[str]:
    """재시작 시 `tmp/<attempt>` 고아 폴더를 지운다. 임대가 살아 있는 running attempt 폴더는 남긴다.

    attempt UUID 형식이 아닌 항목은 출처를 알 수 없으므로 건드리지 않는다.
    """
    try:
        temporary = _temporary_root(root)
    except (OSError, ValueError):
        return []
    if not temporary.is_dir():
        return []
    candidates: dict[uuid.UUID, Path] = {}
    for entry in temporary.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            candidates[uuid.UUID(entry.name)] = entry
        except ValueError:
            continue
    if not candidates:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM run_job_attempts
                WHERE id = ANY(%s) AND status = 'running' AND lease_until > clock_timestamp()""",
            (list(candidates),),
        )
        live = {uuid.UUID(str(row[0])) for row in cur.fetchall()}
    removed: list[str] = []
    for attempt_id, entry in sorted(candidates.items(), key=lambda item: str(item[0])):
        if attempt_id in live:
            continue
        if discard_workspace(root, entry):
            removed.append(str(attempt_id))
    return removed
