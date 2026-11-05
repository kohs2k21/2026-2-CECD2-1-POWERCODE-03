"""시도 시작마다 운영 상태를 읽어 고정한다. 읽을 수 없거나 맞지 않으면 판정 없이 시도를 실패시킨다."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping
import uuid

from detector.decision import ensure_executable
from detector.evidence_v4 import PinnedRevisions
from detector.hard_rules import HardRuleSelection, read_hard_rule_snapshot
from detector.method_results import method_category
from detector.policy import DEFAULT_POLICY_V2, PolicyV2, parse_policy_v2
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


def _canonical_hard_rule(item: Mapping[str, Any], version: int) -> dict[str, Any]:
    entry = {"id": item["id"], "enabled": item["enabled"]}
    return {**entry, "severity": item["severity"]} if version == 2 else entry


def _canonical_soft_rule(item: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": item["id"], "field": item["field"], "operator": item["operator"],
            "threshold": fixed_decimal(item["threshold"]), "severity": item["severity"], "enabled": item["enabled"]}


def rule_set_content_hash(snapshot: Mapping[str, Any], version: int) -> str:
    """rule-set.md 리비전 형식 절의 정규 객체 SHA-256. 그 행의 hash 버전 규칙으로 계산한다."""
    if type(version) is not int or version not in (1, 2):
        raise ValueError("rule_set_hash_version_unknown")
    try:
        document = {"hardRules": [_canonical_hard_rule(item, version)
                                  for item in sorted(snapshot["hardRules"], key=lambda item: item["id"])],
                    "softRules": [_canonical_soft_rule(item)
                                  for item in sorted(snapshot["softRules"], key=lambda item: item["id"])]}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("rule_set_hash_mismatch") from exc
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _policy(revision: int, value: Any) -> PolicyV2:
    if value is None:
        # 저장된 행이 없으면 리비전 0이다. 그 밖의 NULL은 저장소 이상이라 기본 정책으로 대신하지 않는다.
        if revision != 0:
            raise ValueError("detection_policy_unavailable")
        return parse_policy_v2(DEFAULT_POLICY_V2)
    if revision == 0:
        raise ValueError("detection_policy_unavailable")
    return parse_policy_v2(value)


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
    policy = _policy(policy_revision, policy_value)
    state = PinnedState(policy, PinnedRevisions(rule_revision, rule_hash, policy_revision, model_revision),
                        read_hard_rule_snapshot(stored[2]["hardRules"]), _soft_rules(stored[2]["softRules"]),
                        _active_models(models))
    ensure_executable(policy, [*state.hard_rules.enabled_ids, *(rule["id"] for rule in state.soft_rules),
                               *(model.method_id for model in state.active_models)])
    return state
