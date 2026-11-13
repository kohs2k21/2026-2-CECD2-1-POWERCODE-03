"""시도 시작마다 운영 상태를 읽어 고정한다. 읽을 수 없거나 맞지 않으면 판정 없이 시도를 실패시킨다."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
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


@dataclass(frozen=True)
class ActiveModel:
    config_version_id: uuid.UUID
    model_key: str

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
    active_models: tuple[ActiveModel, ...]
    # 켜진 관계 규칙(v3 행만, 저장 순). 정보 표시 kind(allowed_branch)도 들어 있다 — 방법 수에는 판정 kind만 센다.
    relation_rules: tuple[RelationRule, ...] = ()
    # 프로토콜 항목(v3 행만, 저장 순, 꺼진 항목 포함 — 꺼진 항목의 인터페이스는 적용 안 함, protocol-rules.md 4.1).
    protocol_rules: tuple[ProtocolRule, ...] = ()


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
    if not isinstance(items, list):
        raise ValueError("invalid_active_model_set")
    models = []
    for item in items:
        try:
            model = ActiveModel(uuid.UUID(item["configVersionId"]), item["modelKey"])
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid_active_model_set") from exc
        if not isinstance(model.model_key, str):
            raise ValueError("invalid_active_model_set")
        method_category(model.method_id)
        models.append(model)
    return tuple(models)


def pin_operational_state(conn: Any) -> PinnedState:
    """뷰 한 문장으로 세 리비전을 읽고 그 룰 세트 리비전의 스냅샷을 대조한다(리비전 행은 불변)."""
    with conn.cursor() as cur:
        cur.execute("""SELECT rule_set_revision,rule_set_content_hash,rule_set_content_hash_version,
                              active_model_set_revision,active_models,detection_policy_revision,detection_policy
                         FROM ops_operational_state""")
        rows = cur.fetchall()
        if len(rows) != 1:
            raise ValueError("operational_state_unavailable")
        rule_revision, rule_hash, hash_version, model_revision, models, policy_revision, policy_value = rows[0]
        cur.execute("""SELECT content_hash,content_hash_version,snapshot FROM ops_rule_set_revisions
                        WHERE revision=%s""", (rule_revision,))
        stored = cur.fetchone()
    if stored is None:
        raise ValueError("rule_set_snapshot_unavailable")
    if stored[0] != rule_hash or stored[1] != hash_version or rule_set_content_hash(stored[2], hash_version) != rule_hash:
        raise ValueError("rule_set_hash_mismatch")
    check_rule_set_version(stored[2], hash_version)
    policy = _policy(policy_revision, policy_value)
    relation_rules = read_relation_rules(stored[2].get("relationRules", [])) if hash_version == 3 else ()
    protocol_rules = read_protocol_rules(stored[2].get("protocolRules", [])) if hash_version == 3 else ()
    judged = tuple(rule for rule in relation_rules if rule.kind in JUDGMENT_KINDS)
    protocols = tuple(rule for rule in protocol_rules if rule.enabled)
    # 판정 규칙의 위험도를 하드룰 선택과 같은 객체에 고정한다(evidence 대조가 같은 값을 본다).
    hard_rules = read_hard_rule_snapshot(stored[2]["hardRules"], tuple((rule.id, rule.severity) for rule in judged),
                                         tuple((rule.id, rule.severity) for rule in protocols))
    state = PinnedState(policy, PinnedRevisions(rule_revision, rule_hash, policy_revision, model_revision),
                        hard_rules, _soft_rules(stored[2]["softRules"]), _active_models(models), relation_rules,
                        protocol_rules)
    ensure_executable(policy, [*state.hard_rules.enabled_ids, *(rule.id for rule in judged),
                               *(rule.id for rule in protocols),
                               *(rule["id"] for rule in state.soft_rules),
                               *(model.method_id for model in state.active_models)])
    return state
