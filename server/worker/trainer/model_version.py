"""모델 버전 구성 `configuration_spec` schemaVersion 3의 생성·판독(contracts/model-version.md).

학습 1회 = 모델 버전 1개다. v1·v2 구성은 이 모듈이 만들지 않고 기존 경로가 그대로 만든다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Mapping

from methods.registry import CATALOG_VERSION, MlMethod

SCHEMA_VERSION = 3
CONTRACT_VERSION = "model-version-v1"
CONTENT_HASH_VERSION = 2
READABLE_SCHEMA_VERSIONS = (1, 2, SCHEMA_VERSION)
# 이 키가 하나라도 있고 detectionPolicy 키가 없는 train 요청이 v3 산출이다(model-version.md 생성 경로 (A)).
# detectionPolicy가 함께 있으면 v3 생성이 열리기 전의 임시 예외로 schemaVersion 1을 만든다.
REQUEST_KEYS = ("modelKey", "parameters", "excludedIntervals", "timeBasis", "evaluationSetId")
DEFAULT_TIME_BASIS = "persisted_at"
# 학습 요청에 이름 키가 없어 기간으로 만든다. 한국은 일광절약시간이 없어 고정 오프셋이다.
_DISPLAY_TIMEZONE = timezone(timedelta(hours=9))
_DISPLAY_NAME_MAX = 120


def is_model_version_request(spec: Mapping[str, Any]) -> bool:
    return "detectionPolicy" not in spec and any(key in spec for key in REQUEST_KEYS)


def check_training_scope(spec: Mapping[str, Any]) -> None:
    """Trainer가 아직 적용하지 못하는 키는 무시해서 성공시키지 않는다(API와 같은 코드)."""
    if spec.get("excludedIntervals") is not None or spec.get("timeBasis", DEFAULT_TIME_BASIS) != DEFAULT_TIME_BASIS:
        raise ValueError("config_schema_not_supported_yet")
    if is_model_version_request(spec) and spec.get("ruleVersionIds"):
        raise ValueError("invalid_job_input")


def training_period(spec: Mapping[str, Any]) -> dict[str, str]:
    return {"from": spec["from"], "until": spec["until"], "timeBasis": spec.get("timeBasis", DEFAULT_TIME_BASIS)}


def _display_day(value: str) -> str:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment.astimezone(_DISPLAY_TIMEZONE).date().isoformat()


def display_name(method: MlMethod, period: Mapping[str, str]) -> str:
    return f"{method.display_name} {_display_day(period['from'])}~{_display_day(period['until'])}"[:_DISPLAY_NAME_MAX]


def build_spec(*, config_id: Any, job_id: Any, source_id: Any, feature_set_id: Any, snapshot_id: Any,
               method: MlMethod, parameters: Mapping[str, Any], parameters_requested: bool,
               model_artifact_id: Any, fit_artifact_id: Any, selection: Mapping[str, Any],
               period: Mapping[str, str]) -> dict[str, Any]:
    feature_set = str(feature_set_id)
    return {
        "schemaVersion": SCHEMA_VERSION, "contractVersion": CONTRACT_VERSION,
        "contentHashVersion": CONTENT_HASH_VERSION, "sourceId": str(source_id), "baseVersionId": None,
        "displayName": display_name(method, period), "catalogVersion": CATALOG_VERSION,
        "featureSetId": feature_set, "trainingSnapshotId": str(snapshot_id),
        "methods": [{"methodId": method.method_id, "kind": method.kind, "enabled": True,
                     "methodVersion": method.method_version, "featureSetId": feature_set,
                     "parameters": dict(parameters),
                     "parameterSource": "user" if parameters_requested else "catalog_default",
                     "artifacts": {"candidateConfigVersionId": str(config_id), "trainJobId": str(job_id),
                                   "modelArtifactId": str(model_artifact_id),
                                   "fitArtifactId": str(fit_artifact_id)}}],
        "trainingSelection": dict(selection),
        "createdFrom": {"kind": "train", "trainJobId": str(job_id), "recommendationJobId": None},
    }


def content_hash(spec: Mapping[str, Any]) -> str:
    """`contentHashVersion: 2`. 룰 세트 contentHash와 같은 직렬화이고 v1·v2 행의 hash 코드와는 다르다."""
    document = {"sourceId": spec["sourceId"], "featureSetId": spec["featureSetId"],
                "methods": [{"methodId": method["methodId"], "enabled": method["enabled"],
                             "parameters": method["parameters"], "featureSetId": method["featureSetId"],
                             "artifacts": {"modelArtifactId": method["artifacts"]["modelArtifactId"],
                                           "fitArtifactId": method["artifacts"]["fitArtifactId"]}}
                            for method in spec["methods"]]}
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_schema_version(spec: Any) -> int:
    """모르는 구성은 v1 규칙으로 해석하지 않고 멈춘다(model-version.md 판독 규칙)."""
    version = spec.get("schemaVersion") if isinstance(spec, Mapping) else None
    if type(version) is not int or version not in READABLE_SCHEMA_VERSIONS:
        raise ValueError("unsupported_config_schema")
    return version


def read_model_key(spec: Mapping[str, Any]) -> str:
    """v3 구성의 `<model_key>`. 계약 형식이 아니면 판독하지 않는다."""
    methods = spec.get("methods")
    method = methods[0] if isinstance(methods, list) and len(methods) == 1 else None
    method_id = method.get("methodId") if isinstance(method, Mapping) else None
    if spec.get("contractVersion") != CONTRACT_VERSION or spec.get("contentHashVersion") != CONTENT_HASH_VERSION or \
            "detectionPolicy" in spec or not isinstance(method_id, str) or not method_id.startswith("ml:") or \
            method.get("kind") != "ml" or method.get("enabled") is not True:
        raise ValueError("unsupported_config_schema")
    return method_id.split(":", 1)[1]
