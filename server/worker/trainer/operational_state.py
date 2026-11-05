"""활성 모델 집합·운영 설정(판정 정책) 리비전 읽기. 변경 규칙은 API 몫이고 Trainer는 읽어서 기록만 한다.

Detector가 시도마다 읽는 것과 같은 뷰 `ops_operational_state` 한 문장을 쓴다. 판독 실패는 값을 지어내지 않고
작업을 멈춘다. 기본 정책·형식 검사는 #112 `detector/policy.py`가 병합되면 그쪽으로 합친다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
import uuid

from psycopg import errors

# 저장된 정책이 없는 상태(리비전 0)의 내장 기본 정책(model-version.md 운영 설정 절).
DEFAULT_POLICY = {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1},
                  "deadlineMs": 3000}
POLICY_KEYS = frozenset({"category-k-of-n-or-v1"})


@dataclass(frozen=True)
class ActiveModel:
    config_version_id: uuid.UUID
    model_key: str


@dataclass(frozen=True)
class OperationalState:
    active_model_set_revision: int
    active_models: tuple[ActiveModel, ...]
    detection_policy_revision: int
    detection_policy: Mapping[str, Any]

    @property
    def deadline_ms(self) -> int:
        return self.detection_policy["deadlineMs"]

    def result_fields(self) -> dict[str, Any]:
        """작업 `resultSummary`. 비교 작업 결과와 같은 이름이고 고정 시점만 시도 시작이다."""
        return {"activeModelSetRevision": self.active_model_set_revision,
                "detectionPolicy": {"revision": self.detection_policy_revision, "policy": self.detection_policy,
                                    "fixedAt": "attempt_start"}}

    def evidence_fields(self) -> dict[str, Any]:
        """평가 근거. evidence v4와 같은 이름으로 정책 값을 그대로 복사한다."""
        return {"activeModelSetRevision": self.active_model_set_revision,
                "detectionPolicyRevision": self.detection_policy_revision,
                "detectionPolicy": self.detection_policy}


def _check_policy(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"version", "policyKey", "k", "deadlineMs"}:
        raise ValueError("invalid_detection_policy")
    deadline, k = value["deadlineMs"], value["k"]
    if type(value["version"]) is not int or value["version"] != 2 or type(deadline) is not int or \
            not 100 <= deadline <= 30000:
        raise ValueError("invalid_detection_policy")
    if not isinstance(k, Mapping) or set(k) != {"hard", "soft", "ml"} or any(type(item) is not int for item in k.values()):
        raise ValueError("invalid_detection_policy")
    if not isinstance(value["policyKey"], str) or value["policyKey"] not in POLICY_KEYS:
        raise ValueError("unsupported_detection_policy")
    return value


def _policy(revision: int, value: Any) -> Mapping[str, Any]:
    if value is None:
        # 행이 없을 때만 리비전 0이다. 그 밖의 NULL은 저장소 이상이라 기본 정책으로 대신하지 않는다.
        if revision != 0:
            raise ValueError("detection_policy_unavailable")
        return DEFAULT_POLICY
    if revision == 0:
        raise ValueError("detection_policy_unavailable")
    return _check_policy(value)


def _active_models(items: Any) -> tuple[ActiveModel, ...]:
    if not isinstance(items, list):
        raise ValueError("invalid_active_model_set")
    models = []
    for item in items:
        try:
            model = ActiveModel(uuid.UUID(item["configVersionId"]), item["modelKey"])
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid_active_model_set") from exc
        if not isinstance(model.model_key, str) or not model.model_key:
            raise ValueError("invalid_active_model_set")
        models.append(model)
    return tuple(models)


def read_operational_state(conn: Any) -> OperationalState:
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT active_model_set_revision,active_models,detection_policy_revision,detection_policy
                             FROM ops_operational_state""")
            rows = cur.fetchall()
    except (errors.InsufficientPrivilege, errors.UndefinedTable) as exc:
        # 재시도해도 풀리지 않는 설치 문제다. 작업을 재시도 없이 끝내 원인 코드를 남긴다.
        raise ValueError("operational_state_unavailable") from exc
    if len(rows) != 1:
        raise ValueError("operational_state_unavailable")
    model_revision, models, policy_revision, policy = rows[0]
    return OperationalState(model_revision, _active_models(models), policy_revision, _policy(policy_revision, policy))
