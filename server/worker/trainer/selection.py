"""Training selection is independent of immutable evaluation membership.

정책 두 가지(contracts/trainer-service.md "선정 정책 v2"):

- `train-success-only-v1`: snapshot train 분할 중 status=S, 명시 anomaly 라벨 제외.
- `train-success-excluded-v2`: v1 조건 + 행의 시각이 모든 학습 제외 구간 `[from, until)` 밖. 구간 비교 시각 열은
  학습 `timeBasis`와 같은 열이다(사용자 11/15 결정, 현행 `persisted_at`). 구간이 1개 이상인데 시각이 없는 행은
  구간 판단 불가로 빼고 따로 센다. train 분할에만 적용하고 validation/test 멤버십은 건드리지 않는다.

모델 fit·판정선 보정·fit 연산 통계·합성 기준선(ADBench·ESB generator)이 같은 판단 함수 `exclusion_reason`을 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping

VERSION = 'train-success-only-v1'
VERSION_EXCLUDED = 'train-success-excluded-v2'
MAX_EXCLUDED_INTERVALS = 32
EXCLUDED_NOTE_MAX = 200
REASON_CODES = ('known_incident', 'maintenance', 'collection_anomaly', 'value_spike', 'other')
DEFAULT_REASON_CODE = 'other'
# 학습 timeBasis → 구간 비교에 쓰는 관측 시각 열. 스냅샷이 다른 기준을 지원하면 그 열을 여기 더한다.
INTERVAL_TIME_COLUMNS = {'persisted_at': 'o.persisted_at'}
_INTERVAL_KEYS = frozenset({'from', 'until', 'reasonCode', 'note'})

# exclusion_reason 반환값. None이면 학습에 쓴다.
NOT_SUCCESS = 'status_not_success'
EXPLICIT_ANOMALY = 'explicit_anomaly'
TIME_UNKNOWN = 'time_unknown'
EXCLUDED_INTERVAL = 'excluded_interval'


def _instant(value: Any, *, milliseconds: bool = True) -> datetime:
    """오프셋이 있는 ISO 8601 시각. API가 구간을 UTC 밀리초로 정규화해 보내므로 구간 경계는 밀리초 아래 자릿수를 받지 않는다."""
    if not isinstance(value, str):
        raise ValueError('invalid_job_input')
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('invalid_job_input') from exc
    if moment.tzinfo is None or moment.utcoffset() is None or (milliseconds and moment.microsecond % 1000):
        raise ValueError('invalid_job_input')
    return moment.astimezone(timezone.utc)


def _truncate_ms(moment: datetime) -> datetime:
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


def utc_text(moment: datetime) -> str:
    """`YYYY-MM-DDTHH:MM:SS.mmmZ` — API `new Date(value).toISOString()`과 같은 고정 길이 UTC 문자열."""
    moment = moment.astimezone(timezone.utc)
    return moment.strftime('%Y-%m-%dT%H:%M:%S.') + f'{moment.microsecond // 1000:03d}Z'


def intervals_hash(intervals: list[Mapping[str, str]]) -> str:
    """`[{from, until}]`(from 순, `utc_text` 형식)의 compact JSON(UTF-8) SHA-256 hex. 빈 목록은 `[]`의 hash."""
    document = [{'from': item['from'], 'until': item['until']} for item in sorted(intervals, key=lambda item: item['from'])]
    text = json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Exclusion:
    """접수된 학습 제외 구간. `intervals`는 from 순 `(from, until)` UTC 시각이다."""
    intervals: tuple[tuple[datetime, datetime], ...]
    stored: tuple[dict[str, Any], ...]
    time_basis: str
    digest: str

    def contains(self, moment: datetime) -> bool:
        return any(start <= moment < end for start, end in self.intervals)

    def settings(self) -> list[dict[str, Any]]:
        """`ml_config_models.training_settings.excludedIntervals`에 남길 목록(from 순, UTC)."""
        return [dict(item) for item in self.stored]


def parse_exclusion(value: Any, period: Mapping[str, Any], time_basis: str) -> Exclusion:
    """워커 쪽 방어 검사. API가 이미 400으로 거른 형식이라 위반은 모두 `invalid_job_input` 하나로 끝낸다."""
    if time_basis not in INTERVAL_TIME_COLUMNS:
        raise ValueError('config_schema_not_supported_yet')
    if not isinstance(value, list) or len(value) > MAX_EXCLUDED_INTERVALS:
        raise ValueError('invalid_job_input')
    # 기간은 밀리초 아래 자릿수가 올 수 있다. API(`Date.parse`)와 같이 밀리초로 잘라 비교한다.
    period_from, period_until = (_truncate_ms(_instant(period.get(key), milliseconds=False)) for key in ('from', 'until'))
    parsed = []
    for item in value:
        if not isinstance(item, Mapping) or not set(item) <= _INTERVAL_KEYS:
            raise ValueError('invalid_job_input')
        start, end = _instant(item.get('from')), _instant(item.get('until'))
        reason = item.get('reasonCode', DEFAULT_REASON_CODE)
        note = item.get('note')
        if not start < end or start < period_from or end > period_until or reason not in REASON_CODES or \
                not (note is None or (isinstance(note, str) and len(note) <= EXCLUDED_NOTE_MAX)):
            raise ValueError('invalid_job_input')
        parsed.append((start, end, {'from': utc_text(start), 'until': utc_text(end), 'reasonCode': reason, 'note': note}))
    parsed.sort(key=lambda item: item[0])
    if any(parsed[index - 1][1] > parsed[index][0] for index in range(1, len(parsed))):
        raise ValueError('invalid_job_input')
    stored = tuple(item[2] for item in parsed)
    return Exclusion(tuple((item[0], item[1]) for item in parsed), stored, time_basis, intervals_hash(list(stored)))


def stored_exclusion(settings: Mapping[str, Any]) -> Exclusion | None:
    """저장된 학습 후보 `training_settings`에서 v2 선정 조건을 되살린다(합성 기준선용). v1 후보는 None."""
    selection = settings.get('trainingSelection') if isinstance(settings, Mapping) else None
    if not isinstance(selection, Mapping) or selection.get('policyVersion') != VERSION_EXCLUDED:
        return None
    period = settings.get('trainingPeriod')
    if not isinstance(period, Mapping):
        raise ValueError('training_selection_invalid')
    try:
        exclusion = parse_exclusion(settings.get('excludedIntervals'), period, selection.get('intervalTimeBasis'))
    except ValueError as exc:
        raise ValueError('training_selection_invalid') from exc
    if exclusion.digest != selection.get('excludedIntervalsHash'):
        raise ValueError('training_selection_invalid')
    return exclusion


def training_times(conn: Any, snapshot_id: Any, time_basis: str) -> dict[Any, Any]:
    """snapshot train 멤버의 `observation_id → 구간 비교 시각`. 값이 없으면 None이다."""
    column = INTERVAL_TIME_COLUMNS.get(time_basis)
    if column is None:
        raise ValueError('config_schema_not_supported_yet')
    with conn.cursor() as cur:
        cur.execute(f"""SELECT m.observation_id, {column}
                          FROM data_snapshot_members AS m
                          JOIN data_observations AS o ON o.id = m.observation_id
                         WHERE m.snapshot_id = %s AND m.split = 'train'""", (snapshot_id,))
        return dict(cur.fetchall())


def exclusion_reason(row, exclusion: Exclusion | None = None, times: Mapping[Any, Any] | None = None) -> str | None:
    """학습 행 판단(모든 학습·기준선 경로 공용). 행은 `(observation_id, raw, split, label_evidence)`."""
    if row[1].get('status') != 'S':
        return NOT_SUCCESS
    label = row[3].get('label') if isinstance(row[3], dict) else None
    if label == 'anomaly':
        return EXPLICIT_ANOMALY
    if exclusion is not None and exclusion.intervals:
        moment = (times or {}).get(row[0])
        if moment is None:
            return TIME_UNKNOWN
        if exclusion.contains(moment):
            return EXCLUDED_INTERVAL
    return None


def select_training(rows, exclusion: Exclusion | None = None, times: Mapping[Any, Any] | None = None):
    """`exclusion`이 없으면 v1 결과 그대로(키·값 불변), 있으면 v2 필드를 더한다. 제외 건수는 서로 겹치지 않는다."""
    selected, counts = [], {}
    reasons = {EXPLICIT_ANOMALY: 0, TIME_UNKNOWN: 0, EXCLUDED_INTERVAL: 0}
    for row in rows:
        status = row[1].get('status')
        key = status if isinstance(status, str) and status else '<missing>'
        counts[key] = counts.get(key, 0) + 1
        reason = exclusion_reason(row, exclusion, times)
        if reason is None:
            selected.append(row)
        elif reason in reasons:
            reasons[reason] += 1
    selection = {'policyVersion': VERSION, 'split': 'train', 'populationCount': len(rows),
                 'selectedCount': len(selected), 'excludedCount': len(rows) - len(selected),
                 'excludedExplicitAnomalyCount': reasons[EXPLICIT_ANOMALY], 'statusCounts': counts,
                 'normalGroundTruth': False}
    if exclusion is not None:
        selection.update(policyVersion=VERSION_EXCLUDED, timeBasis=exclusion.time_basis,
                         intervalTimeBasis=exclusion.time_basis, excludedIntervalCount=len(exclusion.intervals),
                         excludedByIntervalCount=reasons[EXCLUDED_INTERVAL],
                         excludedTimeUnknownCount=reasons[TIME_UNKNOWN], excludedIntervalsHash=exclusion.digest)
    return selected, selection
