"""사용자가 지정한 저장 기간을 재현 가능한 PROCESS 스냅샷으로 고정한다."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SnapshotResult:
    id: uuid.UUID
    manifest_hash: str
    counts: dict[str, int]


def build_snapshot(conn: Any, *, source_id: uuid.UUID, from_time: str, until_time: str,
                   actor_id: str) -> SnapshotResult:
    """수집 시각 기준 기간을 고정하고 동일 실행을 한 분할에만 배정한다."""
    snapshot_id = uuid.uuid4()
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT clock_timestamp()")
            cutoff = cur.fetchone()[0]
            cur.execute(
                """SELECT id, execution_id, persisted_at
                     FROM data_observations
                    WHERE source_id = %s AND layer = 'P' AND execution_id IS NOT NULL
                      AND persisted_at >= %s::timestamptz AND persisted_at < %s::timestamptz
                      AND persisted_at <= %s
                    ORDER BY persisted_at, id LIMIT 1000001""",
                (source_id, from_time, until_time, cutoff),
            )
            rows = cur.fetchall()
            if len(rows) > 1000000:
                raise ValueError("snapshot_row_limit_exceeded")
            if len(rows) < 20:
                raise ValueError("snapshot_insufficient_process_rows")
            groups: dict[uuid.UUID, list[tuple[uuid.UUID, Any]]] = {}
            for observation_id, execution_id, persisted_at in rows:
                groups.setdefault(execution_id, []).append((observation_id, persisted_at))
            if len(groups) < 10:
                raise ValueError("snapshot_insufficient_execution_groups")
            ordered = sorted(groups.items(), key=lambda item: (min(row[1] for row in item[1]), str(item[0])))
            # 같은 실행의 관측 기간이 겹치는 묶음은 함께 분할해 시간 역전을 막는다.
            blocks: list[tuple[Any, list[tuple[uuid.UUID, list[tuple[uuid.UUID, Any]]]]]] = []
            for execution_id, observations in ordered:
                earliest = min(item[1] for item in observations)
                latest = max(item[1] for item in observations)
                if blocks and earliest <= blocks[-1][0]:
                    block_end, members = blocks[-1]
                    members.append((execution_id, observations))
                    blocks[-1] = (max(block_end, latest), members)
                else:
                    blocks.append((latest, [(execution_id, observations)]))
            if len(blocks) < 3:
                raise ValueError("snapshot_time_split_unavailable")
            train_end = max(1, int(len(blocks) * .7))
            validation_end = min(len(blocks) - 1, max(train_end + 1, int(len(blocks) * .85)))
            counts = {"train": 0, "validation": 0, "test": 0}
            membership: list[tuple[str, str]] = []
            cur.execute(
                """INSERT INTO data_snapshots
                     (id, source_id, status, observation_cutoff_at, selection_spec,
                      split_spec, schema_version, created_by_actor_id, created_at)
                   VALUES (%s, %s, 'building', %s, %s::jsonb, %s::jsonb, 'process-v1',
                           %s, clock_timestamp())""",
                (snapshot_id, source_id, cutoff,
                 json.dumps({"from": from_time, "until": until_time, "timeBasis": "persisted_at", "maxRows": 1000000}),
                 json.dumps({"method": "chronological_nonoverlapping_execution_block", "train": .7, "validation": .15,
                             "test": .15, "labelSource": "unknown"}), actor_id),
            )
            for index, (_, block) in enumerate(blocks):
                split = "train" if index < train_end else "validation" if index < validation_end else "test"
                for _, observations in block:
                    for observation_id, persisted_at in observations:
                        cur.execute(
                            """INSERT INTO data_snapshot_members
                                 (snapshot_id, source_id, observation_id, split, input_as_of, label_evidence)
                               VALUES (%s, %s, %s, %s, %s,
                                 (SELECT jsonb_build_object('label', label, 'reasonCode', reason_code,
                                                            'revisionNo', revision_no)
                                    FROM data_label_evidence
                                   WHERE observation_id = %s AND created_at <= %s
                                   ORDER BY revision_no DESC LIMIT 1))""",
                            (snapshot_id, source_id, observation_id, split, persisted_at,
                             observation_id, cutoff),
                        )
                        counts[split] += 1
                        membership.append((str(observation_id), split))
            membership.sort()
            manifest_hash = hashlib.sha256(json.dumps(membership, separators=(",", ":")).encode()).hexdigest()
            cur.execute(
                """UPDATE data_snapshots SET status = 'ready', manifest_hash = %s,
                          ready_at = clock_timestamp() WHERE id = %s""",
                (manifest_hash, snapshot_id),
            )
    return SnapshotResult(snapshot_id, manifest_hash, counts)
