"""시도 시작마다 운영 상태를 읽어 고정한다. 읽을 수 없거나 맞지 않으면 판정 없이 시도를 실패시킨다."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from typing import Any, Mapping
import uuid

from detector.decision import ensure_executable
from detector.evidence_v4 import PinnedRevisions
from detector.hard_rules import (V3_HARD_RULE_IDS, HardRuleSelection,
                                 read_hard_rule_snapshot)
from detector.method_results import method_category
from detector.policy import DEFAULT_POLICY_V2, PolicyV2, parse_policy_v2
from detector.protocol_rules import ProtocolRule, path_key as _protocol_path_key, read_protocol_rules
from detector.relation_rules import JUDGMENT_KINDS, RelationRule, read_relation_rules
from detector.relation_graph import graph_value_absent
from detector.soft_rules import fixed_decimal
from methods.registry import FRAMES, method_for_key

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActiveModel:
    config_version_id: uuid.UUID
    model_key: str
    # 활성 스냅샷 항목의 틀(`frame`, 없으면 row — model-version.md "두 틀 — 활성 집합 규칙" 절). 카탈로그 frame과의
    # 일치는 고정 때(_split_frames) 본다.
    frame: str = "row"

    @property
    def method_id(self) -> str:
        return "ml:" + self.model_key


@dataclass(frozen=True)
class PinnedState:
    """그 시도의 모든 방법·evidence가 쓰는 입력. 도중에 저장된 룰·모델·정책은 다음 시도부터 적용된다."""
    policy: PolicyV2
    revisions: PinnedRevisions
    hard_rules: HardRuleSelection
    soft_rules: tuple[Mapping[str, Any], ...]
    # 행 틀 활성 모델만(활성 집합 순). 실행 검사·모델 읽기·행 과제·비교 평가 종합·n_ml이 이 목록을 쓴다.
    active_models: tuple[ActiveModel, ...]
    # 켜진 관계 규칙(v3 행만, 저장 순). 정보 표시 kind(allowed_branch)도 들어 있다 — 방법 수에는 판정 kind만 센다.
    relation_rules: tuple[RelationRule, ...] = ()
    # 프로토콜 항목(v3 행만, 저장 순, 꺼진 항목 포함 — 꺼진 항목의 인터페이스는 적용 안 함, protocol-rules.md 4.1).
    protocol_rules: tuple[ProtocolRule, ...] = ()
    # 시계열 틀 활성 모델(활성 집합 순). PROCESS 판정 방법이 아니라 행 판정에 넣지 않고 보관만 한다(시계열 판정은 후속).
    trend_models: tuple[ActiveModel, ...] = ()
    # 스냅샷 frame=trend인데 카탈로그 frame이 다른 항목의 methodId. 시계열 틀만 멈출 대상이고 행 판정은 계속한다.
    trend_frame_mismatch: tuple[str, ...] = ()


HASH_VERSIONS = (1, 2, 3)
# v3 전용 키가 v1·v2 행에 있으면 저장 검증을 거치지 않은 행이다(relation-rules.md 8절, protocol-rules.md 6절).
_V3_ONLY_KEYS = {"relationRules": "relation_rules_require_v3", "protocolRules": "protocol_rules_require_v3"}
_SOFT_SCOPE_KEYS = ("processId", "adapterType", "processHubId")  # 판정 키. interfaceId는 표시용이라 hash에서 뺀다
_SOFT_SCOPE_STORED_KEYS = frozenset({*_SOFT_SCOPE_KEYS, "interfaceId"})


def _canonical_severity_v3(value: Any) -> Any:
    """rule-set.md v3.3: byCode는 [[code, level], …] 코드 포인트 순 배열(숫자처럼 생긴 키의 JS 객체 순서 차이를 없앤다)."""
    if isinstance(value, str):
        return value
    if not isinstance(value, Mapping) or "byCode" not in value or set(value) - {"byCode", "default"}:
        raise ValueError("rule_set_hash_mismatch")
    by_code = value["byCode"]
    if not isinstance(by_code, Mapping) or not all(isinstance(code, str) for code in by_code):
        raise ValueError("rule_set_hash_mismatch")
    entry: dict[str, Any] = {"byCode": [[code, by_code[code]] for code in sorted(by_code)]}
    if "default" in value:
        entry["default"] = value["default"]
    return entry


def _canonical_hard_rule(item: Mapping[str, Any], version: int) -> dict[str, Any]:
    entry = {"id": item["id"], "enabled": item["enabled"]}
    if version == 1:
        return entry
    return {**entry, "severity": item["severity"] if version == 2 else _canonical_severity_v3(item["severity"])}


def _canonical_soft_scope(scope: Any) -> dict[str, Any] | None:
    if scope is None:
        return None
    if not isinstance(scope, Mapping):
        raise ValueError("rule_set_hash_mismatch")
    kept = {key: scope[key] for key in _SOFT_SCOPE_KEYS if scope.get(key) is not None}
    return kept or None


def _canonical_soft_rule(item: Mapping[str, Any], version: int) -> dict[str, Any]:
    entry = {"id": item["id"], "field": item["field"], "operator": item["operator"],
             "threshold": fixed_decimal(item["threshold"]), "severity": item["severity"], "enabled": item["enabled"]}
    return {**entry, "scope": _canonical_soft_scope(item.get("scope"))} if version == 3 else entry


def _step(value: Any) -> dict[str, Any] | None:
    return None if value is None else {"processId": value["processId"]}


def _canonical_relation_rule(item: Mapping[str, Any]) -> dict[str, Any]:
    """relation-rules.md 8절: required_step만 scope.interfaceId가 판정 키, 나머지는 adapterType만. source 제외."""
    scope = item.get("scope")
    if scope is not None and not isinstance(scope, Mapping):
        raise ValueError("rule_set_hash_mismatch")
    if item["kind"] == "required_step":
        canonical_scope = None if scope is None else {"interfaceId": scope["interfaceId"]}
    else:
        canonical_scope = {"adapterType": scope["adapterType"]} if scope and scope.get("adapterType") is not None else None
    return {"id": item["id"], "kind": item["kind"], "scope": canonical_scope, "from": _step(item["from"]),
            "to": _step(item["to"]), "match": item["match"], "severity": item["severity"], "enabled": item["enabled"]}


def path_key(channels: Any) -> str:
    """protocol-rules.md 5.3: 채널마다 % → %25 다음 : → %3A, 그 뒤 :로 잇는다(순서를 바꾸면 다른 경로가 같은 키가 된다)."""
    if not isinstance(channels, list) or not all(isinstance(channel, str) for channel in channels):
        raise ValueError("rule_set_hash_mismatch")
    return _protocol_path_key(channels)


def _canonical_protocol_rule(item: Mapping[str, Any]) -> dict[str, Any]:
    """rule-set.md v3.3: paths는 channels만, pathKey 코드 포인트 순(Python str 비교). source 제외."""
    paths = [path["channels"] for path in item["paths"]]
    return {"id": item["id"], "interfaceId": item["interfaceId"], "paths": sorted(paths, key=path_key),
            "severity": item["severity"], "enabled": item["enabled"]}


def _by_id(items: Any) -> list[Mapping[str, Any]]:
    return sorted(items, key=lambda item: item["id"])


def rule_set_content_hash(snapshot: Mapping[str, Any], version: int) -> str:
    """rule-set.md 리비전 형식 절(v1·v2)·v3 절의 정규 객체 SHA-256. 그 행의 hash 버전 규칙으로 계산한다."""
    if type(version) is not int or version not in HASH_VERSIONS:
        raise ValueError("rule_set_hash_version_unknown")
    try:
        document = {"hardRules": [_canonical_hard_rule(item, version) for item in _by_id(snapshot["hardRules"])],
                    "softRules": [_canonical_soft_rule(item, version) for item in _by_id(snapshot["softRules"])]}
        if version == 3:
            # v3 정규 객체는 네 키를 항상 둔다(스냅샷에 없으면 []).
            document["relationRules"] = [_canonical_relation_rule(item)
                                         for item in _by_id(snapshot.get("relationRules", []))]
            document["protocolRules"] = [_canonical_protocol_rule(item)
                                         for item in _by_id(snapshot.get("protocolRules", []))]
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("rule_set_hash_mismatch") from exc
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _enabled_ids(snapshot: Mapping[str, Any], key: str) -> list[Any]:
    items = snapshot.get(key, [])
    if not isinstance(items, list) or not all(
            isinstance(item, Mapping) and type(item.get("enabled")) is bool for item in items):
        raise ValueError(f"invalid_rule_set_snapshot:{key}")
    return [item.get("id") for item in items if item["enabled"]]


def check_rule_set_version(snapshot: Mapping[str, Any], version: int) -> None:
    """hash 대조 뒤 버전별 구조 검사(protocol-rules.md 10절 1단계, rule-set.md v3.7).

    v1·v2 행: v3 전용 요소(relationRules·protocolRules 키, 소프트룰 scope 키, 새 내장 하드룰)가 있으면 실패.
    v3 행: 관계·프로토콜 배열의 enabled 모양만 여기서 본다. 관계 규칙·scope 있는 소프트룰(5단계)과 프로토콜 항목·
    새 내장 하드룰 2개(6단계)는 판정을 갖췄다(검증은 read_relation_rules·read_protocol_rules·_soft_rules).
    """
    soft_rules, hard_rules = snapshot.get("softRules"), snapshot.get("hardRules")
    if not isinstance(soft_rules, list) or not isinstance(hard_rules, list):
        raise ValueError("rule_set_hash_mismatch")
    if version < 3:
        for key, code in _V3_ONLY_KEYS.items():
            if key in snapshot:
                raise ValueError(code)
        if any(isinstance(item, Mapping) and "scope" in item for item in soft_rules) or any(
                isinstance(item, Mapping) and item.get("id") in V3_HARD_RULE_IDS for item in hard_rules):
            raise ValueError("relation_rules_require_v3")
        return
    _enabled_ids(snapshot, "relationRules")
    _enabled_ids(snapshot, "protocolRules")


def _policy(revision: int, value: Any) -> PolicyV2:
    if value is None:
        # 저장된 행이 없으면 리비전 0이다. 그 밖의 NULL은 저장소 이상이라 기본 정책으로 대신하지 않는다.
        if revision != 0:
            raise ValueError("detection_policy_unavailable")
        return parse_policy_v2(DEFAULT_POLICY_V2)
    if revision == 0:
        raise ValueError("detection_policy_unavailable")
    return parse_policy_v2(value)


def _check_soft_scope(method_id: Any, scope: Any) -> None:
    """relation-rules.md 4절·rule-set.md v3.6: null 또는 {processId(필수), adapterType?, processHubId?, interfaceId?},
    값은 없음이 아닌 문자열. 저장 검증을 거치지 않은 모양이면 시도 실패(판정 범위를 짐작하지 않는다)."""
    if scope is None:
        return
    if not isinstance(scope, Mapping) or "processId" not in scope or set(scope) - _SOFT_SCOPE_STORED_KEYS or any(
            not isinstance(value, str) or graph_value_absent(value) for value in scope.values()):
        raise ValueError(f"invalid_soft_rule_scope:{method_id}")


def _soft_rules(items: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(items, list):
        raise ValueError("invalid_soft_rule_snapshot")
    for item in items:
        method_id = item.get("id") if isinstance(item, Mapping) else None
        # sr:* 같은 다른 대분류가 소프트룰 자리에 있으면 저장 검증을 거치지 않은 비정상이다.
        if method_category(method_id) != "soft":
            raise ValueError(f"method_kind_not_allowed:{method_id}")
        if type(item.get("enabled")) is not bool:
            raise ValueError(f"invalid_soft_rule_enabled:{method_id}")
        _check_soft_scope(method_id, item.get("scope"))  # v1·v2 행의 scope 키는 check_rule_set_version이 먼저 막는다
    return tuple(item for item in items if item["enabled"])


def _active_models(items: Any) -> tuple[ActiveModel, ...]:
    """활성 스냅샷 항목 전체(두 틀, 저장 순). `frame` 키가 없으면 row, 있으면 row·trend 중 하나여야 한다."""
    if not isinstance(items, list):
        raise ValueError("invalid_active_model_set")
    models = []
    for item in items:
        try:
            model = ActiveModel(uuid.UUID(item["configVersionId"]), item["modelKey"], item.get("frame", "row"))
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid_active_model_set") from exc
        if not isinstance(model.model_key, str) or not isinstance(model.frame, str) or model.frame not in FRAMES:
            raise ValueError("invalid_active_model_set")
        method_category(model.method_id)
        models.append(model)
    # 같은 방법이 두 번(틀이 달라도) 있으면 저장 검증을 거치지 않은 집합이다. 행 틀만 세는 실행 검사 전에 전체로 본다.
    method_ids = [model.method_id for model in models]
    if len(set(method_ids)) != len(method_ids):
        raise ValueError("duplicate_method_id")
    return tuple(models)


# 고정은 시도마다 일어나므로 같은 불일치 로그는 (활성 집합 리비전, methodId, 스냅샷 frame)마다 한 번만 남긴다.
_LOGGED_FRAME_MISMATCH: set[tuple[Any, str, str]] = set()


def _log_frame_mismatch(revision: Any, model: ActiveModel, catalog_frame: str) -> None:
    key = (revision, model.method_id, model.frame)
    if key in _LOGGED_FRAME_MISMATCH:
        return
    _LOGGED_FRAME_MISMATCH.add(key)
    LOGGER.warning("frame_mismatch activeModelSetRevision=%s methodId=%s snapshotFrame=%s catalogFrame=%s",
                   revision, model.method_id, model.frame, catalog_frame)


def _split_frames(models: tuple[ActiveModel, ...], revision: Any
                  ) -> tuple[tuple[ActiveModel, ...], tuple[ActiveModel, ...], tuple[str, ...]]:
    """(행 틀 모델, 시계열 틀 모델, 시계열 틀 불일치 methodId). 틀은 스냅샷 frame 기준이다(카탈로그 값이 아님).

    항목마다 스냅샷 frame과 이 이미지의 카탈로그 frame을 비교해 불일치 항목이 속한 틀만 fail-closed
    (model-version.md "두 틀 — 활성 집합 규칙" 절 고정 시 불일치): 행 틀이면 판정 행 없이 시도 실패(`frame_mismatch`,
    룰 판정도 멈춤), 시계열 틀이면 그 틀만 멈출 대상으로 남기고 행 판정은 계속한다. 카탈로그에 없는 방법은 비교하지
    않는다 — 그 방법만 not_executable 미판정이고 집합은 그대로다.
    """
    row, trend, trend_mismatch = [], [], []
    for model in models:
        method = method_for_key(model.model_key)
        if method is not None and method.frame != model.frame:
            _log_frame_mismatch(revision, model, method.frame)
            if model.frame == "row":
                raise ValueError("frame_mismatch")
            trend_mismatch.append(model.method_id)
        (row if model.frame == "row" else trend).append(model)
    return tuple(row), tuple(trend), tuple(trend_mismatch)


# 호출자가 hash 버전을 주지 않으면 리비전 행의 content_hash_version을 쓴다(None과 구별하는 표지).
_HASH_VERSION_FROM_ROW = object()


def read_rule_set_snapshot(conn: Any, revision: Any, content_hash: Any,
                           hash_version: Any = _HASH_VERSION_FROM_ROW) -> tuple[Mapping[str, Any], Any]:
    """룰 세트 리비전 1개의 스냅샷을 읽어 대조한다(리비전 행은 불변). (스냅샷, hash 버전)을 돌려준다.

    검사(fail-closed): 행 없음 → rule_set_snapshot_unavailable, 저장 hash ≠ content_hash·저장 hash 버전 ≠ hash_version·
    그 버전 규칙으로 다시 계산한 hash ≠ content_hash → rule_set_hash_mismatch, 그 뒤 버전별 구조 검사(check_rule_set_version).
    hash_version을 주지 않으면(운영 뷰가 아닌 고정 리비전 판독) 그 행의 hash 버전으로 다시 계산한다.
    """
    with conn.cursor() as cur:
        cur.execute("""SELECT content_hash,content_hash_version,snapshot FROM ops_rule_set_revisions
                        WHERE revision=%s""", (revision,))
        stored = cur.fetchone()
    if stored is None:
        raise ValueError("rule_set_snapshot_unavailable")
    version = stored[1] if hash_version is _HASH_VERSION_FROM_ROW else hash_version
    if stored[0] != content_hash or stored[1] != version or rule_set_content_hash(stored[2], version) != content_hash:
        raise ValueError("rule_set_hash_mismatch")
    check_rule_set_version(stored[2], version)
    return stored[2], version


def pin_fixed_state(conn: Any, rule_set_revision: Any, rule_set_hash: Any, policy_revision: Any, policy_value: Any,
                    active_models: Any, *, active_model_set_revision: Any,
                    rule_set_hash_version: Any = _HASH_VERSION_FROM_ROW) -> PinnedState:
    """주어진 리비전 값으로 운영 상태를 고정한다. 운영 뷰를 읽지 않을 뿐 pin_operational_state와 같은 검사를 한다.

    입력 형식은 운영 뷰 열과 같다: policy_value = detection_policy(jsonb, 리비전 0이면 NULL), active_models =
    [{configVersionId, modelKey, frame?}, …](frame 없으면 row). 비교 평가(Trainer)가 접수 때 고정한 값으로 부른다.
    """
    snapshot, hash_version = read_rule_set_snapshot(conn, rule_set_revision, rule_set_hash, rule_set_hash_version)
    policy = _policy(policy_revision, policy_value)
    relation_rules = read_relation_rules(snapshot.get("relationRules", [])) if hash_version == 3 else ()
    protocol_rules = read_protocol_rules(snapshot.get("protocolRules", [])) if hash_version == 3 else ()
    judged = tuple(rule for rule in relation_rules if rule.kind in JUDGMENT_KINDS)
    protocols = tuple(rule for rule in protocol_rules if rule.enabled)
    # 판정 규칙의 위험도를 하드룰 선택과 같은 객체에 고정한다(evidence 대조가 같은 값을 본다).
    hard_rules = read_hard_rule_snapshot(snapshot["hardRules"], tuple((rule.id, rule.severity) for rule in judged),
                                         tuple((rule.id, rule.severity) for rule in protocols))
    # 검사 순서는 그대로: 리비전 값 → 소프트룰 → 활성 집합(틀 분리·불일치) → 실행 검사.
    revisions = PinnedRevisions(rule_set_revision, rule_set_hash, policy_revision, active_model_set_revision)
    soft_rules = _soft_rules(snapshot["softRules"])
    row_models, trend_models, trend_mismatch = _split_frames(_active_models(active_models), active_model_set_revision)
    state = PinnedState(policy, revisions, hard_rules, soft_rules, row_models, relation_rules, protocol_rules,
                        trend_models, trend_mismatch)
    # "탐지 방법 0개"·k 검사는 행 판정 방법만 센다(n_ml = 행 틀 모델 수). 시계열 모델만 있고 룰이 0개면 방법 0개다.
    ensure_executable(policy, [*state.hard_rules.enabled_ids, *(rule.id for rule in judged),
                               *(rule.id for rule in protocols),
                               *(rule["id"] for rule in state.soft_rules),
                               *(model.method_id for model in state.active_models)])
    return state


def pin_operational_state(conn: Any) -> PinnedState:
    """뷰 한 문장으로 세 리비전을 읽고 그 값으로 pin_fixed_state를 부른다(같은 트랜잭션, 리비전 행은 불변)."""
    with conn.cursor() as cur:
        cur.execute("""SELECT rule_set_revision,rule_set_content_hash,rule_set_content_hash_version,
                              active_model_set_revision,active_models,detection_policy_revision,detection_policy
                         FROM ops_operational_state""")
        rows = cur.fetchall()
    if len(rows) != 1:
        raise ValueError("operational_state_unavailable")
    rule_revision, rule_hash, hash_version, model_revision, models, policy_revision, policy_value = rows[0]
    return pin_fixed_state(conn, rule_revision, rule_hash, policy_revision, policy_value, models,
                           active_model_set_revision=model_revision, rule_set_hash_version=hash_version)
