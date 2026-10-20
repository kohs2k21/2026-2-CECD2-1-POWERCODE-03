"""사용자가 지정한 저장 기간을 재현 가능한 PROCESS 스냅샷으로 고정한다.

멤버 선택 규칙(`latest-per-execution-v1`, Issue #69):

1. 기간 필터를 먼저 적용한다. `source_id`·`layer='P'`·`execution_id IS NOT NULL`이고
   `persisted_at`이 `[from, until)` 안이며 snapshot cutoff(`clock_timestamp()`) 이전인 관측만 후보다.
2. 후보 안에서 PROCESS(`execution_id`)마다 최신 revision **1개만** 멤버로 고른다. 선택 순서는
   Backend 운영 보고와 동일하게 `revision_no DESC, persisted_at DESC, id DESC`다
   (`server/backend/README.md` "PROCESS 식별" 규칙: cutoff 전에 저장된 최신 revision).
   따라서 기간 밖(예: `until` 이후)에 저장된 더 새로운 revision은 선택 대상이 아니며, 기간 안의
   최신 revision이 멤버가 된다. Backend도 `persisted_at < until`로 먼저 거른 뒤 순위를 매긴다.
   단 Backend는 `until` 상한만 걸고 전체 이력에서 순위를 매기므로, 동일성은 `until` 상한 기준이다.
   더 높은 revision이 `from` 이전에, 낮은 revision이 기간 안에 저장된 비단조 사례에서만 결과가 갈린다.
3. 최소 행(20)·최소 execution 그룹(10)·분할 가능 블록(3) 검사는 선택 **후** 개수 기준이다.

실데이터(Transaction Data 추출본)는 execution당 revision이 정확히 1개라 이 규칙으로 수치가 바뀌지
않는다. 재수집·지연 재도착으로 같은 실행키가 다른 내용으로 다시 들어와 revision이 늘어날 때
같은 PROCESS가 평가 분모에 두 번 들어가지 않도록 보호하는 방어 규칙이다.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

REVISION_RULE = "latest-per-execution-v1"
REVISION_ORDER = ("revision_no DESC", "persisted_at DESC", "id DESC")
MAX_ROWS = 1000000
MIN_MEMBER_ROWS = 20
MIN_EXECUTION_GROUPS = 10
MIN_BLOCKS = 3


@dataclass(frozen=True)
class SnapshotResult:
    id: uuid.UUID
    manifest_hash: str
    counts: dict[str, int]


@dataclass(frozen=True)
class SelectedMember:
    observation_id: uuid.UUID
    execution_id: uuid.UUID
    revision_no: int
    persisted_at: Any


def select_latest_revisions(rows: Iterable[tuple[Any, Any, Any, Any]]) -> tuple[list[SelectedMember], int]:
    """기간 필터를 통과한 후보 `(id, execution_id, revision_no, persisted_at)`에서 execution별 최신 1개를 고른다.

    순서: `revision_no DESC, persisted_at DESC, id DESC`(Backend 보고 규칙과 동일). 반환은
    `(선택 멤버 목록, 제외된 이전 revision 수)`이며 멤버 목록은 `(persisted_at, execution_id)` 순이다.
    SQL 없이 시험할 수 있도록 순수 함수로 둔다.
    """
    chosen: dict[uuid.UUID, SelectedMember] = {}
    superseded = 0
    for observation_id, execution_id, revision_no, persisted_at in rows:
        if execution_id is None or revision_no is None:
            raise ValueError("snapshot_member_identity_missing")
        candidate = SelectedMember(uuid.UUID(str(observation_id)), uuid.UUID(str(execution_id)), int(revision_no), persisted_at)
        current = chosen.get(candidate.execution_id)
        if current is None:
            chosen[candidate.execution_id] = candidate
            continue
        superseded += 1
        if _revision_order_key(candidate) > _revision_order_key(current):
            chosen[candidate.execution_id] = candidate
    members = sorted(chosen.values(), key=lambda item: (item.persisted_at, str(item.execution_id)))
    return members, superseded


def _revision_order_key(member: SelectedMember) -> tuple[int, Any, str]:
    # 큰 값이 우선: revision_no, persisted_at, id 모두 내림차순 비교의 키.
    return (member.revision_no, member.persisted_at, str(member.observation_id))


def split_blocks(members: list[SelectedMember]) -> list[list[SelectedMember]]:
    """`persisted_at` 순으로 정렬된 멤버를 시간 역전이 없는 블록으로 묶는다.

    같은 `persisted_at`(또는 이전 블록 끝 이하)에 저장된 멤버는 한 블록에 둬서 분할 경계가
    같은 시각을 가르지 않게 한다. execution당 멤버가 1개이므로 각 멤버의 기간은 한 시점이다.
    """
    blocks: list[tuple[Any, list[SelectedMember]]] = []
    for member in members:
        if blocks and member.persisted_at <= blocks[-1][0]:
            block_end, block_members = blocks[-1]
            block_members.append(member)
            blocks[-1] = (max(block_end, member.persisted_at), block_members)
        else:
            blocks.append((member.persisted_at, [member]))
    return [block for _, block in blocks]


def assign_splits(blocks: list[list[SelectedMember]]) -> list[tuple[SelectedMember, str]]:
    """블록 단위 시간순 70/15/15 분할. 최소 블록 수 미만이면 거부한다."""
    if len(blocks) < MIN_BLOCKS:
        raise ValueError("snapshot_time_split_unavailable")
    train_end = max(1, int(len(blocks) * .7))
    validation_end = min(len(blocks) - 1, max(train_end + 1, int(len(blocks) * .85)))
    assigned: list[tuple[SelectedMember, str]] = []
    for index, block in enumerate(blocks):
        split = "train" if index < train_end else "validation" if index < validation_end else "test"
        assigned.extend((member, split) for member in block)
    return assigned


def build_snapshot(conn: Any, *, source_id: uuid.UUID, from_time: str, until_time: str,
                   actor_id: str) -> SnapshotResult:
    """수집 시각 기준 기간을 고정하고 execution별 최신 revision 1개를 한 분할에만 배정한다.

    기간 필터(`from`/`until`/cutoff)는 SQL에서 먼저 적용하고, 그 결과 안에서 `select_latest_revisions`로
    execution별 최신 revision을 고른다(모듈 docstring 규칙 1·2). 선택 기준은 `selection_spec`에
    `revisionRule`·`revisionOrder`·`candidateRows`·`supersededRevisionCount`로 기록한다.
    """
    snapshot_id = uuid.uuid4()
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT clock_timestamp()")
            cutoff = cur.fetchone()[0]
            cur.execute(
                """SELECT id, execution_id, revision_no, persisted_at
                     FROM data_observations
                    WHERE source_id = %s AND layer = 'P' AND execution_id IS NOT NULL
                      AND persisted_at >= %s::timestamptz AND persisted_at < %s::timestamptz
                      AND persisted_at <= %s
                    ORDER BY persisted_at, id LIMIT %s""",
                (source_id, from_time, until_time, cutoff, MAX_ROWS + 1),
            )
            rows = cur.fetchall()
            if len(rows) > MAX_ROWS:
                raise ValueError("snapshot_row_limit_exceeded")
            members, superseded = select_latest_revisions(rows)
            if len(members) < MIN_MEMBER_ROWS:
                raise ValueError("snapshot_insufficient_process_rows")
            if len({member.execution_id for member in members}) < MIN_EXECUTION_GROUPS:
                raise ValueError("snapshot_insufficient_execution_groups")
            assigned = assign_splits(split_blocks(members))
            counts = {"train": 0, "validation": 0, "test": 0}
            membership: list[tuple[str, str]] = []
            cur.execute(
                """INSERT INTO data_snapshots
                     (id, source_id, status, observation_cutoff_at, selection_spec,
                      split_spec, schema_version, created_by_actor_id, created_at)
                   VALUES (%s, %s, 'building', %s, %s::jsonb, %s::jsonb, 'process-v1',
                           %s, clock_timestamp())""",
                (snapshot_id, source_id, cutoff,
                 json.dumps({"from": from_time, "until": until_time, "timeBasis": "persisted_at", "maxRows": MAX_ROWS,
                             "revisionRule": REVISION_RULE, "revisionOrder": list(REVISION_ORDER),
                             "candidateRows": len(rows), "supersededRevisionCount": superseded}),
                 json.dumps({"version": "execution-block-v1", "method": "chronological_nonoverlapping_execution_block", "train": .7, "validation": .15,
                             "test": .15, "labelSource": "unknown"}), actor_id),
            )
            for member, split in assigned:
                cur.execute(
                    """INSERT INTO data_snapshot_members
                         (snapshot_id, source_id, observation_id, split, input_as_of, label_evidence)
                       VALUES (%s, %s, %s, %s, %s,
                         (SELECT jsonb_build_object('label', label, 'reasonCode', reason_code,
                                                    'revisionNo', revision_no)
                            FROM data_label_evidence
                           WHERE observation_id = %s AND created_at <= %s
                           ORDER BY revision_no DESC LIMIT 1))""",
                    (snapshot_id, source_id, member.observation_id, split, member.persisted_at,
                     member.observation_id, cutoff),
                )
                counts[split] += 1
                membership.append((str(member.observation_id), split))
            membership.sort()
            manifest_hash = hashlib.sha256(json.dumps(membership, separators=(",", ":")).encode()).hexdigest()
            cur.execute(
                """UPDATE data_snapshots SET status = 'ready', manifest_hash = %s,
                          ready_at = clock_timestamp() WHERE id = %s""",
                (manifest_hash, snapshot_id),
            )
    return SnapshotResult(snapshot_id, manifest_hash, counts)
