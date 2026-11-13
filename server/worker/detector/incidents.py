"""층 C 거래 간 사건 묶음(contracts/incidents.md)의 순수 로직. DB를 읽거나 쓰지 않는다.

주기 작업이 잠금 안에서 읽은 새 최종 판정과 열린 사건 상태를 받아, 추가만 하는(append-only) 행동
(새 사건·구성원 추가·병합)을 결정적으로 돌려준다. 표의 UPDATE·DELETE에 해당하는 행동은 없다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping
import hashlib
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from derived_expression import TABLE, evaluate as evaluate_derived
from detector.method_results import source_absent

# 사건 주기 작업 직렬화 잠금 pg_try_advisory_xact_lock(81741, 0)의 첫 키(DB 배정).
# 같은 트랜잭션에서 81739(룰 세트 저장)·81740(MANIFEST_LOCK_CLASS)은 잡지 않는다.
INCIDENT_LOCK_CLASS = 81741
INCIDENT_LOCK_KEY = 0

CRITERIA_VERSION = "incident-gap-v1"
ELIGIBLE_GRADES = ("anomaly", "anomaly_sign")
TIME_BASIS = "process_time_raw"
TIME_FALLBACK = "decided_at"
OCCURRED_BASES = (TIME_BASIS, TIME_FALLBACK)
# 원본 시간대가 검증되지 않았을 때(clock_basis.status != verified) 시간대 없는 원문에 붙이는 가정 시간대.
# 사용자 결정(11/12): 원천 로그 시각은 한국 시각으로 본다. 고객 측 확인 뒤 바뀔 수 있다(바뀌면 기준 버전을 올린다).
ASSUMED_TIMEZONE = "Asia/Seoul"
# 실패 코드가 아닌 응답코드. 고객 코드 사전이 생기면 교체하고 기준 버전을 올린다.
SUCCESS_RESPONSE_CODES = frozenset({"0000", "S"})
KEY_RESPONSE_CODE = "response_code"
KEY_CHANNEL = "channel_id"
# group_key 값 부분(이스케이프 뒤)의 UTF-8 최대 바이트. 넘으면 원래 값의 SHA-256으로 바꾼다.
# 019 UNIQUE(source_id, criteria_version, group_key, opened_at) btree 항목 한도(약 2.7KB)를 넘지 않게 한다.
GROUP_KEY_MAX_BYTES = 512
GROUP_KEY_HASH_PREFIX = "sha256:"
# 사건 id를 만드는 uuid5 이름공간. 값을 바꾸면 모든 사건 id가 바뀐다.
INCIDENT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:powercode:detector:incident")

_ASCII_SPACE = " \t\r\n"


def graph_value_absent(value: str | None) -> bool:
    """그래프 계약의 없음 판정(DEPEND_PROCESS_ID가 아닌 열). ASCII 공백·탭·CR·LF만 앞뒤에서 뺀다."""
    return value is None or value.strip(_ASCII_SPACE) in ("", "\\N")


def escape_id_component(value: str) -> str:
    """그래프 계약의 유일한 변환: '%' -> '%25' 다음 ':' -> '%3A'."""
    return value.replace("%", "%25").replace(":", "%3A")


def transaction_node_id(transaction_id: str | None) -> str | None:
    """층 A 거래 노드 id. 값이 없으면 연결할 거래 노드가 없다(None)."""
    return None if graph_value_absent(transaction_id) else "transaction:" + escape_id_component(transaction_id)


def incident_node_id(incident_id: str) -> str:
    return "incident:" + escape_id_component(incident_id)


def _seconds(value: timedelta) -> int | float:
    seconds = value.total_seconds()
    return int(seconds) if seconds.is_integer() else seconds


def _zone(name: str | None) -> ZoneInfo | None:
    """잘못된 시간대 이름은 작업을 멈추지 않고 시간대 없음으로 본다(검증된 원본이면 그 원본은 decided_at 대체).

    없는 이름, 경로 형태, 디렉터리 이름("Asia"), 너무 긴 이름(OSError), 문자열이 아닌 값(TypeError) 모두 해당한다.
    """
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError, TypeError):
        return None


@dataclass(frozen=True)
class Criteria:
    """사건 묶음 기준. 값을 하나라도 바꾸면 version도 바꾼다(run_incidents.criteria·criteria_version)."""
    version: str = CRITERIA_VERSION
    gap: timedelta = timedelta(minutes=5)
    max_span: timedelta = timedelta(minutes=60)
    keys: tuple[str, ...] = (KEY_RESPONSE_CODE, KEY_CHANNEL)
    success_codes: frozenset[str] = SUCCESS_RESPONSE_CODES
    assumed_timezone: str = ASSUMED_TIMEZONE

    def __post_init__(self):
        if not self.version or self.gap <= timedelta(0) or self.max_span < self.gap:
            raise ValueError("invalid_incident_criteria")
        if not self.keys or set(self.keys) - {KEY_RESPONSE_CODE, KEY_CHANNEL} or len(set(self.keys)) != len(self.keys):
            raise ValueError("invalid_incident_keys")
        if _zone(self.assumed_timezone) is None:
            raise ValueError("invalid_incident_assumed_timezone")

    def document(self) -> dict:
        """run_incidents.criteria에 넣는 JSON 객체."""
        return {"version": self.version, "gapSeconds": _seconds(self.gap),
                "maxSpanSeconds": _seconds(self.max_span), "keys": list(self.keys),
                "responseCodeSource": "transaction_latest_single",
                "successResponseCodes": sorted(self.success_codes),
                "excludedKeys": {"process_hub_id": "single_value_in_data"},
                "groupKeyMaxBytes": GROUP_KEY_MAX_BYTES, "groupKeyLongValue": "sha256_utf8_hex",
                "eligibleGrades": list(ELIGIBLE_GRADES), "timeBasis": TIME_BASIS,
                "timeColumns": ["END_TIME", "START_TIME"], "timeFallback": TIME_FALLBACK,
                "assumedTimezone": self.assumed_timezone}


DEFAULT_CRITERIA = Criteria()


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}_must_be_aware")


@dataclass(frozen=True)
class DecisionCandidate:
    """run_decision_events(final) 커서로 읽은 판정 1건과 묶음에 쓰는 원천 값."""
    decision_id: str
    event_id: int
    execution_id: str
    source_id: str
    grade: str | None  # v4 evidence composite.grade. v3 판정은 None
    decided_at: datetime
    end_time_raw: str | None = None
    start_time_raw: str | None = None
    transaction_id: str | None = None
    transaction_response_code: str | None = None  # 계약의 거래 응답코드 조인 결과. 후보 0·2개 이상이면 None
    channel_id: str | None = None

    def __post_init__(self):
        _aware(self.decided_at, "decided_at")
        if type(self.event_id) is not int:
            raise ValueError("event_id_must_be_int")
        for name in ("end_time_raw", "start_time_raw", "transaction_id", "transaction_response_code", "channel_id"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name}_must_be_text")


@dataclass(frozen=True)
class OpenIncident:
    """작업이 읽은 기존 사건 상태. last_member_at은 해제 여부와 무관한 구성원 occurred_at 최댓값."""
    incident_id: str
    group_key: str
    opened_at: datetime
    last_member_at: datetime
    continues_from: str | None = None
    has_continuation: bool = False
    merged_into: str | None = None

    def __post_init__(self):
        _aware(self.opened_at, "opened_at")
        _aware(self.last_member_at, "last_member_at")
        if self.last_member_at < self.opened_at:
            raise ValueError("last_member_before_opened")


@dataclass(frozen=True)
class NewIncident:
    incident_id: str
    source_id: str
    criteria_version: str
    criteria: dict
    group_key: str
    time_basis: str
    opened_at: datetime
    continues_from: str | None

    @property
    def node_id(self) -> str:
        return incident_node_id(self.incident_id)


@dataclass(frozen=True)
class AddMember:
    incident_id: str
    decision_id: str
    execution_id: str
    occurred_at: datetime
    occurred_basis: str
    transaction_node_id: str | None


@dataclass(frozen=True)
class MergeInto:
    incident_id: str
    target_incident_id: str


@dataclass(frozen=True)
class Skipped:
    decision_id: str
    reason: str  # already_member | grade_not_eligible | no_group_key


@dataclass(frozen=True)
class GroupingResult:
    """DB에는 incidents → members → merges 순서로 넣고 실행 행을 마지막에 넣는다."""
    incidents: tuple[NewIncident, ...]
    members: tuple[AddMember, ...]
    merges: tuple[MergeInto, ...]
    skipped: tuple[Skipped, ...]

    @property
    def fallback_members(self) -> int:
        return sum(member.occurred_basis == TIME_FALLBACK for member in self.members)


def group_key_value(value: str) -> str:
    """group_key의 값 부분. 이스케이프한 값의 UTF-8 길이가 GROUP_KEY_MAX_BYTES 이하면 그대로, 넘으면
    "sha256:<원래 값 UTF-8의 SHA-256 hex>"(같은 긴 값끼리는 계속 묶임). 이스케이프한 값에는 ':'가 남지 않으므로
    짧은 값이 해시 형태와 겹치지 않는다. 원래 값은 저장하지 않는다(화면은 구성원 판정의 원천 행에서 읽음).
    """
    escaped = escape_id_component(value)
    if len(escaped.encode("utf-8", "surrogatepass")) <= GROUP_KEY_MAX_BYTES:
        return escaped
    return GROUP_KEY_HASH_PREFIX + hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def group_key(candidate: DecisionCandidate, criteria: Criteria = DEFAULT_CRITERIA) -> str | None:
    """기준 키를 우선순위대로 보고 처음 쓸 수 있는 키 1개를 고른다. 값은 원래 값 그대로 이스케이프한다(긴 값은 해시)."""
    for key in criteria.keys:
        if key == KEY_RESPONSE_CODE:
            value = candidate.transaction_response_code
            # 성공 코드 비교만 ASCII 공백류를 뺀 값으로 한다. 키에는 원래 값을 쓴다.
            if graph_value_absent(value) or value.strip(_ASCII_SPACE) in criteria.success_codes:
                continue
        else:
            value = candidate.channel_id
            if graph_value_absent(value):
                continue
        return f"{key}:{group_key_value(value)}"
    return None


def resolve_transaction_response_code(rows: Iterable[tuple[str | None, int | None, str | None]]) -> str | None:
    """거래 응답코드 조인 규칙. rows = 같은 원본·같은 거래 ID 문자열의 TRANSACTION (execution_id, revision_no, response_code).

    실행마다 가장 큰 리비전 1건으로 줄인 뒤 후보가 정확히 1개일 때만 그 값을 쓴다. 0개·2개 이상이면 None이며
    PROCESS response_code로 대신하지 않는다. execution_id가 NULL인 행(실행 식별 불가)이 하나라도 있으면
    어느 실행인지 모호하므로 고르지 않고 None이다. 같은 실행 안에서 revision_no가 같은 행은
    DB UNIQUE(execution_id, revision_no)로 생기지 않는다고 전제한다.
    """
    latest: dict[str, tuple[int, str | None]] = {}
    for execution, revision, code in rows:
        if execution is None:
            return None
        if execution not in latest or revision > latest[execution][0]:
            latest[execution] = (revision, code)
    return next(iter(latest.values()))[1] if len(latest) == 1 else None


def occurred_time(candidate: DecisionCandidate, source_timezone: str | None,
                  assumed_timezone: str = ASSUMED_TIMEZONE) -> tuple[datetime, str]:
    """END_TIME, 없거나 해석 실패면 START_TIME을 하드룰과 같은 해석(derived_expression 열 해석)으로 읽는다.

    오프셋이 있는 원문은 그 오프셋을 그대로 쓴다. 시간대 없는 원문에 붙이는 시간대:
    - source_timezone이 None(원본 시간대 미검증, clock_basis.status != verified) → assumed_timezone(기본 Asia/Seoul)
    - 그 밖(검증된 원본의 timezone) → 그 시간대. 이름이 잘못됐으면 시간대 없음으로 보고 다음 열로 간다
    둘 다 못 쓰면 decided_at. 원본 시간대를 쓰든 가정 시간대를 쓰든 출처는 process_time_raw다.
    candidate.decided_at은 tz-aware여야 한다(저장 경로는 세션 시간대 UTC로 읽음).
    반환 시각은 UTC다. UTC로 바꿀 때 datetime 범위(1~9999년)를 넘는 값(예 0001-01-01T00:00+09:00,
    9999-12-31T23:59:59-05:00)은 해석 실패로 보고 다음 열로 간다(OverflowError·ValueError).
    """
    row = {"end_time_raw": candidate.end_time_raw, "start_time_raw": candidate.start_time_raw}
    zone = _zone(assumed_timezone if source_timezone is None else source_timezone)
    for column, raw in (("END_TIME", candidate.end_time_raw), ("START_TIME", candidate.start_time_raw)):
        if source_absent(raw):
            continue
        try:
            value = evaluate_derived({"op": "column", "table": TABLE, "column": column}, row)
            if value is None:
                continue
            if value.tzinfo is None or value.utcoffset() is None:
                if zone is None:
                    continue
                value = value.replace(tzinfo=zone)
            return value.astimezone(timezone.utc), TIME_BASIS
        except (OverflowError, ValueError):
            continue
    return candidate.decided_at.astimezone(timezone.utc), TIME_FALLBACK


# datetime 범위 끝(UTC). 창·구간 계산이 범위를 넘으면 이 값으로 고정한다(판정 시각은 늘 이 범위 안이라 비교 결과가 같다).
UTC_MIN = datetime.min.replace(tzinfo=timezone.utc)
UTC_MAX = datetime.max.replace(tzinfo=timezone.utc)


def shift_clamped(value: datetime, delta: timedelta) -> datetime:
    """value + delta. datetime 범위를 넘으면 UTC_MIN·UTC_MAX로 고정한다(OverflowError 없음)."""
    try:
        return value + delta
    except OverflowError:
        return UTC_MAX if delta > timedelta(0) else UTC_MIN


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def incident_id(source_id: str, criteria_version: str, key: str, opened_at: datetime) -> str:
    """DB UNIQUE(source_id, criteria_version, group_key, opened_at)와 같은 네 값의 uuid5."""
    name = ":".join(escape_id_component(part) for part in (str(source_id), criteria_version, key, _utc_text(opened_at)))
    return str(uuid.uuid5(INCIDENT_NAMESPACE, name))


@dataclass
class _State:
    incident_id: str
    group_key: str
    opened_at: datetime
    last_member_at: datetime
    continues_from: str | None
    has_continuation: bool
    merged_into: str | None
    sort_key: tuple = field(init=False)

    def __post_init__(self):
        self.sort_key = (self.opened_at, self.incident_id)


def _merge_root_state(states: Mapping[str, _State], state: _State) -> _State | None:
    """읽은 상태 안의 병합 뿌리. 뿌리가 상태 밖이면 대상이 병합되지 않았는지 알 수 없어 None."""
    seen = set()
    while state.merged_into is not None:
        if state.incident_id in seen or state.merged_into not in states:
            return None
        seen.add(state.incident_id)
        state = states[state.merged_into]
    return state


# 묶음 그래프에서 뿌리를 알 수 없는 사건(병합 뿌리가 상태 밖)과 상태 밖 앞 구간을 모두 이 한 노드로 본다.
# 이 노드가 그래프에 하나라도 있으면(이어짐 간선의 어느 쪽이든) 그 실행에서는 병합하지 않는다: 모르는 묶음의 실제 뿌리가
# 병합할 두 묶음 중 하나일 수 있어 경로 유무를 알 수 없다. 이어짐 간선이 없는 모르는 뿌리 사건은 그래프에 들어가지 않는다.
# 묶음 구성 사건 자체가 상태에서 빠지면 그 간선은 보이지 않으므로 상태 읽기 범위(계약 6.1 닫힘)는 여전히 필요하다.
_UNKNOWN_GROUP = "\0unknown-group"


def _group_edges(states: Mapping[str, _State]) -> dict[str, set[str]]:
    """묶음 단위 이어짐 그래프. 묶음 = 병합 뿌리, 간선 = X가 든 묶음 → continues_from(X)가 든 묶음."""
    def node(incident: str) -> str:
        state = states.get(incident)
        root = _merge_root_state(states, state) if state else None
        return root.incident_id if root else _UNKNOWN_GROUP
    edges: dict[str, set[str]] = {}
    for state in states.values():
        if state.continues_from is not None:
            edges.setdefault(node(state.incident_id), set()).add(node(state.continues_from))
    return edges


def _reaches(edges: Mapping[str, set[str]], start: str, goal: str) -> bool:
    """start에서 간선을 1개 이상 따라 goal에 닿는지."""
    seen, stack = set(), list(edges.get(start, ()))
    while stack:
        current = stack.pop()
        if current == goal:
            return True
        if current not in seen:
            seen.add(current)
            stack.extend(edges.get(current, ()))
    return False


def _merge_makes_cycle(states: Mapping[str, _State], root: _State, other_root: _State) -> bool:
    """두 묶음(병합 뿌리)을 병합하면 묶음 이어짐 그래프에 고리가 생기는지. 어느 쪽에서든 다른 쪽으로 경로가 있으면 생긴다.

    같은 이어짐 사슬이 두 묶음에 걸친 경우(사슬을 따라 경로가 있음)와 제3 묶음을 거치는 경로(G → K → H)를 모두 잡는다.
    병합할 때마다 지금 상태로 다시 계산한다(같은 실행 안의 앞선 병합·새 사건 반영).
    그래프에 모르는 묶음이 있으면 경로를 확정할 수 없으므로 고리로 본다(병합 거부).
    """
    edges = _group_edges(states)
    if _UNKNOWN_GROUP in edges or any(_UNKNOWN_GROUP in targets for targets in edges.values()):
        return True
    return _reaches(edges, root.incident_id, other_root.incident_id) or \
        _reaches(edges, other_root.incident_id, root.incident_id)


def group_decisions(source_id: str, candidates: Iterable[DecisionCandidate],
                    open_incidents: Iterable[OpenIncident] = (), assigned_decision_ids: Iterable[str] = (),
                    criteria: Criteria = DEFAULT_CRITERIA, *, source_timezone: str | None = None) -> GroupingResult:
    """새 판정을 사건에 배정한다. 같은 입력이면 같은 결과이고, 결과를 반영한 상태로 다시 돌리면 행동이 없다.

    - assigned_decision_ids: 이미 어느 사건의 구성원인 판정(UNIQUE(decision_id)). 재실행 멱등성의 근거
    - open_incidents: 같은 원본·기준 버전에서 후보 시각 근처의 사건(병합된 사건 포함, merged_into로 표시)
    - source_timezone: 검증된 원본 시간대(clock_basis.status = verified의 timezone). None이면 시간대 없는 원문에
      criteria.assumed_timezone을 붙인다(occurred_time). ""은 verified인데 시간대 없음 → 시간대 없는 원문은 decided_at 대체
    - 입력 시각(decided_at·opened_at·last_member_at)은 UTC tz-aware여야 한다. 다른 시간대여도 비교는 맞지만 datetime 범위
      끝 근처에서 UTC 변환이 넘칠 수 있다. 저장 경로는 SET LOCAL TimeZone = 'UTC'로 읽어 보장한다
    """
    span, gap = criteria.max_span, criteria.gap
    states: dict[str, _State] = {}
    for incident in open_incidents:
        if incident.incident_id in states:
            raise ValueError("duplicate_open_incident")
        states[incident.incident_id] = _State(incident.incident_id, incident.group_key,
                                              incident.opened_at.astimezone(timezone.utc),
                                              incident.last_member_at.astimezone(timezone.utc),
                                              incident.continues_from, incident.has_continuation,
                                              incident.merged_into)
    # 병합된 사건도 자기 구간으로 참여·이어짐 판단을 한다. 병합 대상만 병합 뿌리다.
    by_key: dict[str, list[_State]] = {}
    for state in states.values():
        by_key.setdefault(state.group_key, []).append(state)

    assigned = set(assigned_decision_ids)
    skipped: list[Skipped] = []
    prepared = []
    for candidate in candidates:
        if str(candidate.source_id) != str(source_id):
            raise ValueError("candidate_source_mismatch")
        if candidate.decision_id in assigned:
            skipped.append(Skipped(candidate.decision_id, "already_member"))
            continue
        if candidate.grade not in ELIGIBLE_GRADES:
            skipped.append(Skipped(candidate.decision_id, "grade_not_eligible"))
            continue
        key = group_key(candidate, criteria)
        if key is None:
            skipped.append(Skipped(candidate.decision_id, "no_group_key"))
            continue
        assigned.add(candidate.decision_id)
        at, basis = occurred_time(candidate, source_timezone, criteria.assumed_timezone)
        prepared.append((at, candidate.event_id, candidate.decision_id, key, basis, candidate))
    prepared.sort(key=lambda item: item[:3])

    created: list[NewIncident] = []
    members: list[AddMember] = []
    merges: list[MergeInto] = []
    document = criteria.document()
    for at, _, decision, key, basis, candidate in prepared:
        pool = by_key.get(key, ())
        joinable = [s for s in pool if s.opened_at <= at <= shift_clamped(s.opened_at, span)
                    and at - s.last_member_at <= gap]
        before = [s for s in pool if shift_clamped(s.opened_at, -gap) <= at < s.opened_at]
        if joinable:
            target = min(joinable, key=lambda s: s.sort_key)
        else:
            overflow = [s for s in pool if at > shift_clamped(s.opened_at, span) and at - s.last_member_at <= gap
                        and not s.has_continuation]
            previous = max(overflow, key=lambda s: s.sort_key) if overflow else None
            new_id = incident_id(source_id, criteria.version, key, at)
            if new_id in states:
                # 도달 불가: 같은 키·같은 opened_at(= at) 사건이 상태에 있으면 last_member_at >= opened_at = at이라
                # 참여 조건(o <= at <= o + span, at - l <= gap)이 늘 성립해 joinable이 비지 않는다.
                # 상태 읽기가 그 사건을 빠뜨리면 이 검사도 못 하고 DB UNIQUE 위반이 된다(계약 6.1 범위).
                raise ValueError("incident_id_collision")
            target = _State(new_id, key, at, at, previous.incident_id if previous else None, False, None)
            if previous:
                previous.has_continuation = True
            states[new_id] = target
            by_key.setdefault(key, []).append(target)
            created.append(NewIncident(new_id, str(source_id), criteria.version, document, key, TIME_BASIS,
                                       at, target.continues_from))
        members.append(AddMember(target.incident_id, decision, candidate.execution_id, at, basis,
                                 transaction_node_id(candidate.transaction_id)))
        target.last_member_at = max(target.last_member_at, at)
        root = _merge_root_state(states, target)
        if root is None:
            continue
        for other in sorted(joinable + before, key=lambda s: s.sort_key):
            other_root = _merge_root_state(states, other)
            if other_root is None or other_root is root or _merge_makes_cycle(states, root, other_root):
                continue
            # 병합 뒤 묶음 그래프는 merged_into를 따라 다시 계산되므로 두 묶음이 한 노드가 된다.
            other_root.merged_into = root.incident_id
            merges.append(MergeInto(other_root.incident_id, root.incident_id))
    return GroupingResult(tuple(created), tuple(members), tuple(merges), tuple(skipped))


def state_windows(candidates: Iterable[DecisionCandidate], assigned_decision_ids: Iterable[str] = (),
                  criteria: Criteria = DEFAULT_CRITERIA, *, source_timezone: str | None = None
                  ) -> dict[str, tuple[datetime, datetime]]:
    """열린 사건 상태를 읽을 시간 창(계약 6.1의 시작 집합). 키마다 [최소 시각 − max_span − gap, 최대 시각 + gap].

    group_decisions와 같은 자격(이미 구성원 아님·등급·키)을 통과한 후보만 본다. 창 밖 사건은 참여(o ≤ t ≤ o + span)·
    이어짐(t − gap ≤ l ≤ o + span)·시작 직전 병합(t < o ≤ t + gap)·id 충돌(o = t) 어느 조건에도 걸리지 않는다.
    """
    assigned = set(assigned_decision_ids)
    times: dict[str, list[datetime]] = {}
    for candidate in candidates:
        if candidate.decision_id in assigned or candidate.grade not in ELIGIBLE_GRADES:
            continue
        key = group_key(candidate, criteria)
        if key is None:
            continue
        times.setdefault(key, []).append(occurred_time(candidate, source_timezone, criteria.assumed_timezone)[0])
    low = criteria.max_span + criteria.gap
    return {key: (shift_clamped(min(values), -low), shift_clamped(max(values), criteria.gap))
            for key, values in sorted(times.items())}


def check_result_scope(result: GroupingResult, source_id: str, criteria_version: str,
                       known_incidents: Mapping[str, tuple[str, str]]) -> None:
    """DB가 검사하지 않는 같은 원본·같은 기준 버전 조건을 쓰기 전에 확인한다.

    known_incidents = 읽은 상태의 사건 id → (source_id, criteria_version). 새 사건은 이 함수가 결과에서 더한다.
    구성원의 사건, 병합 양쪽, continues_from이 모두 같은 원본·기준 버전의 사건이어야 한다. 어긋나면 ValueError.
    """
    scope = (str(source_id), criteria_version)
    known = {incident: (str(source), version) for incident, (source, version) in known_incidents.items()}
    for incident, value in known.items():
        if value != scope:
            raise ValueError("incident_state_scope_mismatch")
    for new in result.incidents:
        if (str(new.source_id), new.criteria_version) != scope or new.incident_id in known:
            raise ValueError("incident_new_scope_mismatch")
        known[new.incident_id] = scope
    for new in result.incidents:
        if new.continues_from is not None and new.continues_from not in known:
            raise ValueError("incident_continues_from_out_of_scope")
    for member in result.members:
        if member.incident_id not in known:
            raise ValueError("incident_member_out_of_scope")
    for merge in result.merges:
        if merge.incident_id not in known or merge.target_incident_id not in known:
            raise ValueError("incident_merge_out_of_scope")


def merge_root(incident: str, merged_into: Mapping[str, str]) -> str:
    """읽기 1단계: merged_into를 끝까지 따라간다. 화면은 같은 뿌리의 사건을 하나로 보인다."""
    seen = set()
    while incident in merged_into:
        if incident in seen:
            raise ValueError("merge_cycle")
        seen.add(incident)
        incident = merged_into[incident]
    return incident


def _member_order_key(row: tuple[str, str, str, int | None, datetime, int]) -> tuple[bool, int, datetime, int]:
    """계약 8절 정렬 `revision_no DESC NULLS LAST, decided_at DESC, event_sequence DESC`의 max 키.

    revision_no가 있는 행이 없는 행보다 앞선다(NULLS LAST). event_sequence는 판정 표에서 UNIQUE라 순서가 완전하다.
    """
    _, _, _, revision_no, decided_at, event_sequence = row
    return (revision_no is not None, revision_no if revision_no is not None else 0, decided_at, event_sequence)


def home_bundle_by_execution(member_rows: Iterable[tuple[str, str, str, int | None, datetime, int]],
                             merged_into: Mapping[str, str]) -> dict[str, str]:
    """실행별 자리 묶음(계약 8절): 실행의 구성원 판정 중 8절 정렬 맨 앞 판정이 든 사건의 merge_root.

    member_rows = (decision_id, execution_id, incident_id, revision_no | None, decided_at, event_sequence).
    읽는 사건과 같은 기준 버전의 구성원 행만 넘긴다(열린 항목 10). 최신 판정이 아니라 최신 '구성원' 판정 기준이라
    묶음 작업이 따라잡기 전에는 직전 자리를 유지한다.
    """
    best: dict[str, tuple[str, str, str, int | None, datetime, int]] = {}
    for row in member_rows:
        execution = row[1]
        current = best.get(execution)
        if current is None or _member_order_key(row) > _member_order_key(current):
            best[execution] = row
    return {execution: merge_root(row[2], merged_into) for execution, row in best.items()}


def active_execution_count(members: Iterable[tuple[str, str]], latest_grade_by_execution: Mapping[str, str | None],
                           *, bundle_id: str, home_by_execution: Mapping[str, str]) -> int:
    """묶음 bundle_id(merge_root)의 구성원 수(계약 8절). 실행당 1.

    members = 그 묶음 구성원 (decision_id, execution_id). 실행의 최신 판정이 이상·이상 징후이고 자리 묶음
    (home_bundle_by_execution)이 bundle_id인 실행만 센다. 자리가 다른 묶음이면 이 묶음에서는 해제처럼 다룬다.
    """
    return len({execution for _, execution in members
                if latest_grade_by_execution.get(execution) in ELIGIBLE_GRADES
                and home_by_execution.get(execution) == bundle_id})


def shown(active_count: int, min_members: int = 2) -> bool:
    """읽기 시점 필터. min_members는 기준 버전과 별개인 화면 변수다."""
    return active_count >= min_members
