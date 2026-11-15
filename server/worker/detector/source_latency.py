"""판정 행의 원천 발생 → 판정 지연 열을 채울지 정한다(detection-decision.md 18절, collector-metrics.md 3.3).

값 계산(`decided_at − source_log_occurred_at`)은 판정 INSERT 안에서 DB 시계로 한다. 이 모듈은 조건만 본다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

# 관측 clock_status 중 원천 시각을 지연에 쓰는 값. clock_status는 Collector가 정한 값을 그대로 읽는다(다시 판정하지 않음).
# assumed_tz = 시간대 표기 없는 원천 시각을 KST로 해석한 시각(사용자 11/15: ESB 원천·서버 모두 KST) — 포함한다.
# unparsed·absent·030 이전 unknown은 원천 시각이 없어 쓰지 않는다.
MEASURED_CLOCK_STATUSES = frozenset({"offset", "verified_tz", "assumed_tz"})


def source_latency_measurable(*, clock_status: Any, ingest_phase: Any, revision_no: Any,
                              source_log_occurred_at: Any, rechecked: bool) -> bool:
    """모두 참일 때만 measured: 지연에 쓰는 clock_status의 원천 시각 ∧ 실시간 조회(live) 출처 ∧ 첫 revision ∧ 재판정 아님.

    출처 행이 없거나(옛 CSV 재생 경로) 030 이전 출처 행이면 ingest_phase가 None이라 거짓이다.
    revision_no가 None(실행 식별 없음)이거나 정수가 아니면 거짓이다. 음수 지연은 여기서 거르지 않는다.
    """
    return (clock_status in MEASURED_CLOCK_STATUSES
            and ingest_phase == "live"
            and type(revision_no) is int and revision_no == 1
            and isinstance(source_log_occurred_at, datetime)
            and rechecked is False)


def decision_source_time(observation: Any, *, rechecked: bool) -> datetime | None:
    """판정 행 `source_log_occurred_at`에 복사할 관측 값. 측정 조건이 거짓이면 None(판정 행은 unavailable)."""
    occurred = observation.source_log_occurred_at
    measurable = source_latency_measurable(clock_status=observation.clock_status, ingest_phase=observation.ingest_phase,
                                           revision_no=observation.revision_no, source_log_occurred_at=occurred,
                                           rechecked=rechecked)
    return occurred if measurable else None
