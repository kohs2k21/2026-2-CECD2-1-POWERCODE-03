"""PROCESS 원천 시각 기준 거래 묶음·시간순 분할(순수 함수, DB 없음).

수치 SR 기준선(Rule v3 묶음)은 참조(이전 TRAIN)와 현재(이후) 기간이 겹치지 않아야 한다. 저장 시각(`persisted_at`)이
아니라 원천 시각으로 자르기 위해 아래 규칙을 쓴다.

1. 행 시각(`process_source_time`): Detector 사건 묶음의 `occurred_time`과 같은 해석이다. END_TIME을 먼저, 없거나
   해석 실패면 START_TIME을 `derived_expression` 열 해석(`datetime.fromisoformat`)으로 읽는다. 오프셋이 있으면 그대로,
   없으면 검증된 원본 시간대(`source_tz`), `source_tz`가 None(원본 시간대 미검증)이면 가정 시간대 Asia/Seoul을 붙인다.
   잘못된 시간대 이름·빈 문자열("검증됐지만 시간대 없음")이면 시간대 없는 원문은 쓰지 않는다. UTC 변환이 datetime
   범위를 넘으면 해석 실패다. 둘 다 못 쓰면 None이며 `persisted_at` 등 다른 시각으로 대신하지 않는다
   (사건 묶음의 decided_at 대체와 다른 점).
2. 거래 묶음(`group_by_transaction`): `transaction_id` 문자열이 같은 행이 한 묶음이다. 묶음 시각은 해석된 행 시각의
   최솟값이다. 시각을 못 읽은 행·거래 ID가 없는 행은 빼고 사유별로 센다. 선택 창 `window=(from, until)`이 있으면
   행 시각이 [from, until) 밖인 행도 묶기 전에 빼고 `outside_source_window`로 센다(행 단위라 경계에 걸친 거래는
   창 안의 행만 남는다).
3. 순서와 블록(`time_blocks`): (묶음 시각, transaction_id) 순으로 정렬하고 같은 묶음 시각은 한 블록으로 합친다.
   분할 경계는 블록 사이에만 생기므로 같은 시각이나 한 거래가 두 쪽으로 갈리지 않는다.
4. 기간 문자열(`format_utc`): 한 가지 고정 형식 `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`(UTC, 마이크로초 6자리).
   길이가 같아 문자열 비교가 시각 비교와 같다(Rule v3 묶음은 기간을 문자열로 비교한다).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from derived_expression import TABLE, evaluate as evaluate_derived, source_absent

ASSUMED_TIMEZONE = "Asia/Seoul"  # detector.incidents.ASSUMED_TIMEZONE과 같은 값(시험에서 대조)
BASIS_OFFSET = "offset"
BASIS_VERIFIED_TZ = "verified_tz"
BASIS_ASSUMED_TZ = "assumed_tz"
BASES = (BASIS_OFFSET, BASIS_VERIFIED_TZ, BASIS_ASSUMED_TZ)
FIELD_END = "end"
FIELD_START = "start"
ONE_MICROSECOND = timedelta(microseconds=1)


def _zone(name: str | None) -> ZoneInfo | None:
    """detector.incidents._zone과 같은 규칙: 없는 이름·경로·디렉터리·비문자열은 시간대 없음."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError, TypeError):
        return None


def process_source_time(start_raw: Any, end_raw: Any, source_tz: str | None = None
                        ) -> tuple[datetime | None, str | None, str | None]:
    """(UTC aware 시각 | None, 근거 offset/verified_tz/assumed_tz, 사용 열 end/start)."""
    row = {"end_time_raw": end_raw, "start_time_raw": start_raw}
    zone = _zone(ASSUMED_TIMEZONE if source_tz is None else source_tz)
    naive_basis = BASIS_ASSUMED_TZ if source_tz is None else BASIS_VERIFIED_TZ
    for column, name, raw in (("END_TIME", FIELD_END, end_raw), ("START_TIME", FIELD_START, start_raw)):
        if source_absent(raw):
            continue
        try:
            value = evaluate_derived({"op": "column", "table": TABLE, "column": column}, row)
            if value is None:
                continue
            if value.tzinfo is None or value.utcoffset() is None:
                if zone is None:
                    continue
                value, basis = value.replace(tzinfo=zone), naive_basis
            else:
                basis = BASIS_OFFSET
            return value.astimezone(timezone.utc), basis, name
        except (OverflowError, ValueError):
            continue
    return None, None, None


def format_utc(value: datetime) -> str:
    """고정 형식 UTC 문자열. tz 없는 값은 받지 않는다(가정을 숨기지 않음)."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware_datetime_required")
    v = value.astimezone(timezone.utc)
    # strftime %Y는 플랫폼마다 1000년 미만 0 채움이 달라 직접 맞춘다(고정 길이 유지).
    return (f"{v.year:04d}-{v.month:02d}-{v.day:02d}T{v.hour:02d}:{v.minute:02d}:{v.second:02d}"
            f".{v.microsecond:06d}+00:00")


@dataclass(frozen=True)
class TransactionGroup:
    transaction_id: str
    time: datetime  # UTC aware, 묶음 안 해석된 행 시각의 최솟값
    rows: tuple[Any, ...]  # 입력 행 그대로(시각을 해석한 행만), 입력 순서 보존


@dataclass
class Grouping:
    groups: list[TransactionGroup]
    counts: dict[str, Any] = field(default_factory=dict)


def _text(row: Mapping[str, Any], key: str) -> Any:
    return row.get(key) if isinstance(row, Mapping) else None


def group_by_transaction(rows: Iterable[Any], source_tz: str | None = None, *,
                         get: Callable[[Any, str], Any] = _text,
                         window: tuple[datetime | None, datetime | None] | None = None) -> Grouping:
    """행(`transaction_id`·`start_time_raw`·`end_time_raw` 키)을 거래별로 묶고 (묶음 시각, 거래 ID) 순으로 낸다.

    `get(row, key)`로 행 형태를 바꿀 수 있다. 행 값은 세기만 하고 기록하지 않는다.
    `window=(from, until)`: tz 있는 시각, 각각 None이면 그쪽 제한 없음. 행 시각 < from 또는 ≥ until이면 제외.
    """
    lower, upper = window if window is not None else (None, None)
    for bound in (lower, upper):
        if bound is not None and (bound.tzinfo is None or bound.utcoffset() is None):
            raise ValueError("aware_window_required")
    if lower is not None and upper is not None and not lower < upper:
        raise ValueError("invalid_source_window")
    members: dict[str, list[Any]] = {}
    times: dict[str, datetime] = {}
    counts: dict[str, Any] = {"rows_in": 0, "rows_grouped": 0, "excluded_unparsed_time": 0,
                              "excluded_missing_transaction": 0, "outside_source_window": 0,
                              "basis": {basis: 0 for basis in BASES},
                              "field": {FIELD_END: 0, FIELD_START: 0}}
    for row in rows:
        counts["rows_in"] += 1
        transaction = get(row, "transaction_id")
        if source_absent(transaction) or not isinstance(transaction, str):
            counts["excluded_missing_transaction"] += 1
            continue
        value, basis, used = process_source_time(get(row, "start_time_raw"), get(row, "end_time_raw"), source_tz)
        if value is None:
            counts["excluded_unparsed_time"] += 1
            continue
        if (lower is not None and value < lower) or (upper is not None and value >= upper):
            counts["outside_source_window"] += 1
            continue
        counts["rows_grouped"] += 1
        counts["basis"][basis] += 1
        counts["field"][used] += 1
        members.setdefault(transaction, []).append(row)
        times[transaction] = value if transaction not in times else min(times[transaction], value)
    groups = [TransactionGroup(key, times[key], tuple(members[key])) for key in members]
    groups.sort(key=lambda group: (group.time, group.transaction_id))
    counts["groups"] = len(groups)
    return Grouping(groups, counts)


def time_blocks(groups: Sequence[TransactionGroup]) -> list[list[TransactionGroup]]:
    """정렬된 묶음을 같은 묶음 시각끼리 한 블록으로 합친다. 정렬이 아니면 거부한다."""
    blocks: list[list[TransactionGroup]] = []
    previous: tuple[datetime, str] | None = None
    for group in groups:
        key = (group.time, group.transaction_id)
        if previous is not None and key <= previous:
            raise ValueError("groups_not_strictly_ordered")
        previous = key
        if blocks and blocks[-1][0].time == group.time:
            blocks[-1].append(group)
        else:
            blocks.append([group])
    return blocks


def split_blocks(groups: Sequence[TransactionGroup], ratios: Sequence[float] = (0.7, 0.15, 0.15)
                 ) -> list[list[TransactionGroup]]:
    """블록 단위 시간순 분할. 분할마다 블록이 1개 이상이 되도록 경계를 정한다.

    경계 k = floor(블록 수 × 누적 비율)이며 앞 분할에 최소 1블록, 뒤 분할들 몫으로 1블록씩 남긴다.
    비율은 문자열 표기 그대로 분수로 더해 부동소수 누적 오차를 피한다.
    (스냅샷 `assign_splits`는 블록 3개일 때 validation이 빌 수 있다. 여기서는 빈 분할을 만들지 않는다.)
    """
    parts = [Fraction(str(ratio)) for ratio in ratios]
    if len(parts) < 2 or any(part <= 0 for part in parts) or sum(parts) != 1:
        raise ValueError("invalid_split_ratios")
    blocks = time_blocks(groups)
    if len(blocks) < len(parts):
        raise ValueError("time_split_unavailable")
    result: list[list[TransactionGroup]] = []
    start, cumulative = 0, Fraction(0)
    for index, part in enumerate(parts[:-1]):
        cumulative += part
        end = int(len(blocks) * cumulative)
        end = max(start + 1, min(end, len(blocks) - (len(parts) - 1 - index)))
        result.append([group for block in blocks[start:end] for group in block])
        start = end
    result.append([group for block in blocks[start:] for group in block])
    return result


@dataclass(frozen=True)
class ReferenceCut:
    reference: list[TransactionGroup]
    current: list[TransactionGroup]
    reference_period: dict[str, str]
    current_period: dict[str, str]
    counts: dict[str, int]


def cut_reference_current(groups: Sequence[TransactionGroup], reference_ratio: float = 0.8) -> ReferenceCut:
    """앞 블록을 참조, 뒤 블록을 현재로 자른다. 참조 end_exclusive = 현재 start_inclusive(첫 현재 묶음 시각).

    현재 end_exclusive = 마지막 현재 묶음 시각 + 1µs. 기간은 묶음 시각 기준이며 묶음 안 다른 행 시각은 더 늦을 수 있다.
    """
    ratio = Fraction(str(reference_ratio))
    if not 0 < ratio < 1:
        raise ValueError("invalid_reference_ratio")
    reference, current = split_blocks(groups, (str(ratio), str(1 - ratio)))
    reference_period = {"start_inclusive": format_utc(reference[0].time),
                        "end_exclusive": format_utc(current[0].time)}
    current_period = {"start_inclusive": format_utc(current[0].time),
                      "end_exclusive": format_utc(current[-1].time + ONE_MICROSECOND)}
    if not reference_period["end_exclusive"] <= current_period["start_inclusive"] or \
            not reference_period["start_inclusive"] < reference_period["end_exclusive"]:
        raise ValueError("reference_period_overlap")
    counts = {"reference_groups": len(reference), "current_groups": len(current),
              "reference_rows": sum(len(group.rows) for group in reference),
              "current_rows": sum(len(group.rows) for group in current),
              "reference_blocks": len(time_blocks(reference)), "current_blocks": len(time_blocks(current))}
    return ReferenceCut(reference, current, reference_period, current_period, counts)
