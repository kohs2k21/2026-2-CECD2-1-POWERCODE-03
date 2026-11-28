"""방법 레지스트리 — `server/contracts/method-catalog.md` v3의 Python 원본.

- 소프트룰 등록 필드 6종(`SoftRuleField`)과 ML 방법 목록(`MlMethod`, 전부 `ml:*`)을 dataclass로 든다.
- ML 방법의 틀(`frame`)·결과 코드(`resultCode`)는 손으로 적지 않고 `inputRequirements.unit`·`methodId`에서
  만든다(method-catalog.md "틀·결과 코드·틀별 추천" 절). `validate_catalog`가 생성물의 규칙을 검사한다.
- 하드룰 목록은 Detector 소유(detection-decision.md)이므로 읽기 전용 참조 상수만 둔다.
- `python -m methods.registry --write`가 `server/contracts/method-catalog.json`을 생성한다(빌드 산출물,
  schema `method-catalog.schema.json` 통과본). API는 그 JSON을 읽는다.
- 실행 가능한 ML 방법은 어댑터(prepare/fit/score)를 `model_adapters`에 늦게 바인딩한다
  (`model_adapters`가 이 모듈의 기본값을 import하므로 여기서는 모듈 수준 import를 하지 않는다).
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

import derived_expression

CATALOG_VERSION = "method-catalog-v3"
# 계약 예시·sourceRef 줄 번호의 근거 코드 ref(method-catalog.md "근거 코드 origin/dev 7f24e03").
# 생성 JSON을 결정적으로(커밋본 == 재생성본) 두기 위해 실행 시점의 git ref를 읽지 않는다.
CATALOG_SOURCE_REF = "7f24e03"
CATALOG_PATH = Path(__file__).resolve().parents[2] / "contracts" / "method-catalog.json"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "method-catalog.schema.json"

CATEGORY_DISPLAY_NAMES = {"hard": "이상 규칙", "soft": "이상 징후 규칙", "ml": "머신러닝 모델"}

# 하드룰: Detector(detection-decision.md) 정의. 카탈로그에는 참조 블록만 실리고 항목은 없다.
HARD_RULES = {"definedBy": "detection-decision.md", "displayName": CATEGORY_DISPLAY_NAMES["hard"],
              "contractVersion": "detection-decision-v1", "idPattern": "^hard:[a-z][a-z0-9_-]{1,63}$"}
# 1차 내장 하드룰(#92 4.3·U3 확정 11/03, Detector 회신 methodVersion). 읽기 전용 참조이며 JSON에 넣지 않는다.
HARD_RULE_IDS: tuple[tuple[str, str], ...] = (("hard:process-status", "process-status-hr-v1"),
                                              ("hard:negative-duration", "negative-duration-v1"))

SOFT_RULE = {"displayName": CATEGORY_DISPLAY_NAMES["soft"],
             "instanceIdPattern": "^soft:[a-z][a-z0-9_]{1,53}-[0-9a-f]{8}$",
             "operators": ["gte", "gt", "lte", "lt"], "severities": ["info", "warning", "critical"],
             "maxRules": 32, "thresholdSerialization": "fixed-decimal-string"}
SOFT_OPERATORS = ("gte", "gt", "lte", "lt")
MISSING_POLICIES = ("not_applicable", "unavailable")

# 이상 ML 두 틀(method-catalog.md "틀·결과 코드·틀별 추천" 절). 틀은 입력 단위에서 1:1로 정해진다.
FRAMES = ("row", "trend")
FRAME_BY_UNIT = {"process_row": "row", "aggregated_window": "trend"}
TREND_METHOD_PREFIX = "ml:trend-"
# `ml:trend-` 접두 없이 frame=trend인 기존 연구 항목. 실행할 수 없는 동안만 허용하고, 실행 가능해질 때
# `ml:trend-<algo>`로 다시 등재한다. 새 항목을 여기에 더하지 않는다.
TREND_PREFIX_LEGACY = frozenset({"ml:eif"})
# 결과 코드 기계 규칙 `ML_<modelKey 대문자, '-'→'_'>`의 호환 예외(과거 판정 행·화면·집계). 이 하나뿐이다.
RESULT_CODE_EXCEPTIONS = {"ml:iforest": "IF_ANOMALY"}
# 틀 이름(잠정)과 틀별 추천. 추천은 그 틀에서 실행 가능한 방법을 넣는 변경에서만 채운다(그 전 null).
FRAME_ENTRIES: dict[str, dict[str, Any]] = {
    "row": {"displayName": "행 단위 모델", "recommendedMethodId": None, "recommendedParameters": None, "evidence": None},
    "trend": {"displayName": "시계열 모델", "recommendedMethodId": None, "recommendedParameters": None, "evidence": None},
}
FRAME_EVIDENCE_KEYS = ("reportRef", "evaluationSet", "metric", "decidedAt")


class MethodParameterError(ValueError):
    """`parameters`가 카탈로그 `parameterSchema`에 맞지 않는다."""


class CatalogRuleError(ValueError):
    """생성한 카탈로그가 틀·결과 코드·추천 규칙을 어긴다(오류 코드 `규칙:식별자`)."""


def frame_for_unit(unit: str) -> str:
    """`inputRequirements.unit` → 틀. 대응이 없는 단위는 틀을 지어내지 않고 실패한다."""
    if unit not in FRAME_BY_UNIT:
        raise CatalogRuleError(f"frame_unit_unknown:{unit}")
    return FRAME_BY_UNIT[unit]


def result_code_for(method_id: str) -> str:
    """ML 방법 결과 코드. `ml:lof` → `ML_LOF`, `ml:trend-iforest` → `ML_TREND_IFOREST`, 예외는 `ml:iforest`뿐."""
    if method_id in RESULT_CODE_EXCEPTIONS:
        return RESULT_CODE_EXCEPTIONS[method_id]
    return "ML_" + method_id.split(":", 1)[1].upper().replace("-", "_")


def _column(column: str) -> dict[str, Any]:
    return {"op": "column", "table": derived_expression.TABLE, "column": column}


def _source_ref(path: str, lines: str) -> dict[str, str]:
    return {"path": path, "lines": lines, "commit": CATALOG_SOURCE_REF}


def _compact(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


@dataclass(frozen=True)
class FieldValue:
    """소프트룰 필드 값 계산 결과. status: available / not_applicable / unavailable."""
    status: str
    value: Decimal | None
    reason: str | None = None


@dataclass(frozen=True)
class SoftRuleField:
    field_id: str
    display_name: str
    value_type: str  # integer | number
    unit: str  # count | ms | s | us | ratio
    source: dict[str, Any]  # {"kind": "column", "column", "ruleDslField"} | {"kind": "derived", "ast"}
    display_precision: int
    source_ref: dict[str, str]
    operators: tuple[str, ...] = SOFT_OPERATORS
    status: str = "supported"
    description: str | None = None
    display_precision_mode: str | None = None  # None → 스키마 기본 fixed
    source_missing_policy: dict[str, str] | None = None
    notes: str | None = None

    @property
    def ast(self) -> dict[str, Any]:
        if self.source["kind"] == "column":
            return _column(self.source["column"])
        return self.source["ast"]

    @property
    def source_columns(self) -> tuple[str, ...]:
        found: list[str] = []

        def walk(node: Mapping[str, Any]) -> None:
            if node.get("op") == "column":
                if node["column"] not in found:
                    found.append(node["column"])
                return
            for child in node.get("args", []):
                walk(child)

        walk(self.ast)
        return tuple(found)

    def missing_policy(self, column: str) -> str:
        return (self.source_missing_policy or {}).get(column, "unavailable")

    def evaluate(self, row: Mapping[str, Any]) -> FieldValue:
        """원천 행(`derived_expression.FIELDS` 키)에서 값을 계산한다. 평가 의미론은 Detector 몫."""
        missing = [column for column in self.source_columns
                   if derived_expression.source_absent(row.get(derived_expression.FIELDS[column][0]))]
        if missing:
            # not_applicable(이상 아님)이 하나라도 있으면 그 행은 평가 대상이 아니다.
            policies = {column: self.missing_policy(column) for column in missing}
            status = "not_applicable" if "not_applicable" in policies.values() else "unavailable"
            reason = "source_missing:" + ",".join(column for column, policy in policies.items() if policy == status)
            return FieldValue(status, None, reason)
        value = derived_expression.evaluate(self.ast, row)
        if value is None:
            return FieldValue("unavailable", None, "value_not_computable")
        return FieldValue("available", value)

    def to_catalog(self) -> dict[str, Any]:
        return _compact({
            "fieldId": self.field_id, "displayName": self.display_name, "description": self.description,
            "valueType": self.value_type, "unit": self.unit, "source": self.source,
            "operators": list(self.operators), "displayPrecision": self.display_precision,
            "displayPrecisionMode": self.display_precision_mode, "status": self.status,
            "sourceMissingPolicy": self.source_missing_policy, "sourceRef": self.source_ref, "notes": self.notes,
        })


def _count_field(field_id: str, display_name: str, column: str) -> SoftRuleField:
    return SoftRuleField(field_id, display_name, "integer", "count",
                         {"kind": "column", "column": column, "ruleDslField": f"P.{field_id}"},
                         0, _source_ref("server/worker/derived_expression.py", "9-11"))


SOFT_RULE_FIELDS: tuple[SoftRuleField, ...] = (
    _count_field("total_count", "총 건수", "TOTAL_COUNT"),
    _count_field("success_count", "성공 건수", "SUCCESS_COUNT"),
    _count_field("error_count", "오류 건수", "ERROR_COUNT"),
    _count_field("retry_count", "재시도 수", "RETRY_COUNT"),
    SoftRuleField(
        "elapsed_ms", "처리 시간", "number", "ms",
        {"kind": "derived", "ast": {"op": "datetime_diff", "unit": "ms",
                                     "args": [_column("END_TIME"), _column("START_TIME")]}},
        0, _source_ref("server/worker/derived_expression.py", "50-53, 89-94"),
        description="END_TIME - START_TIME (ms)", display_precision_mode="fixed",
        source_missing_policy={"END_TIME": "not_applicable", "START_TIME": "unavailable"},
        notes="END_TIME 누락은 값 null → 룰 평가 안 함(이상 아님, 11/02 9.4). START_TIME 누락·시각 파싱 실패 시 unavailable. "
              "음수(START_TIME > END_TIME)는 하드룰 영역"),
    SoftRuleField(
        "error_ratio", "오류 비율", "number", "ratio",
        {"kind": "derived", "ast": {"op": "divide", "args": [_column("ERROR_COUNT"), _column("TOTAL_COUNT")]}},
        4, _source_ref("server/worker/derived_expression.py", "101-102"),
        description="ERROR_COUNT / TOTAL_COUNT", display_precision_mode="significant",
        notes="TOTAL_COUNT = 0 이면 unavailable. p95가 1e-4 미만일 수 있어 고정소수 직렬화 필수, 표시는 유효숫자 4"),
)
SOFT_RULE_FIELDS_BY_ID = {item.field_id: item for item in SOFT_RULE_FIELDS}


@dataclass(frozen=True)
class MethodAdapter:
    """`model_adapters` 함수를 이름으로 늦게 바인딩한다(시험의 monkeypatch가 그대로 통한다)."""
    module: str
    prepare_name: str
    fit_name: str
    score_name: str

    def _resolve(self, name: str) -> Callable[..., Any]:
        return getattr(importlib.import_module(self.module), name)

    def prepare(self, *args: Any, **kwargs: Any) -> Any:
        return self._resolve(self.prepare_name)(*args, **kwargs)

    def fit(self, *args: Any, **kwargs: Any) -> Any:
        return self._resolve(self.fit_name)(*args, **kwargs)

    def score(self, *args: Any, **kwargs: Any) -> Any:
        return self._resolve(self.score_name)(*args, **kwargs)


@dataclass(frozen=True)
class MlMethod:
    method_id: str
    display_name: str
    status: str
    method_version: str
    ui_target: dict[str, str]
    executable: dict[str, bool]
    runtime: dict[str, str]
    parameter_schema: dict[str, Any]
    parameter_schema_status: str
    defaults: dict[str, Any] | None
    defaults_source: str
    input_requirements: dict[str, Any]
    source_ref: dict[str, str]
    executable_reason: dict[str, str] | None = None
    notes: str | None = None
    adapter: MethodAdapter | None = None
    kind: str = "ml"
    category: str = "ML"
    can_disable: bool = True

    @property
    def model_key(self) -> str:
        return self.method_id.split(":", 1)[1]

    @property
    def executable_process(self) -> bool:
        return bool(self.executable.get("process")) and self.adapter is not None

    @property
    def frame(self) -> str:
        return frame_for_unit(self.input_requirements["unit"])

    @property
    def result_code(self) -> str:
        return result_code_for(self.method_id)

    def resolve_parameters(self, parameters: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """기본값 위에 요청 값을 얹고 `parameterSchema`로 검증한다. 미지정 키는 기본값."""
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, Mapping):
            raise MethodParameterError("parameters_not_object")
        merged = {**(self.defaults or {}), **dict(parameters)}
        validate_parameters(self.parameter_schema, merged)
        return merged

    def to_catalog(self) -> dict[str, Any]:
        return _compact({
            "methodId": self.method_id, "kind": self.kind, "category": self.category, "frame": self.frame,
            "displayName": self.display_name, "status": self.status, "methodVersion": self.method_version,
            "resultCode": self.result_code,
            "uiTarget": self.ui_target, "executable": self.executable, "executableReason": self.executable_reason,
            "runtime": self.runtime, "parameterSchema": self.parameter_schema,
            "parameterSchemaStatus": self.parameter_schema_status, "defaults": self.defaults,
            "defaultsSource": self.defaults_source, "inputRequirements": self.input_requirements,
            "sourceRef": self.source_ref, "canDisable": self.can_disable, "notes": self.notes,
        })


def validate_parameters(schema: Mapping[str, Any], parameters: Mapping[str, Any]) -> None:
    """카탈로그 `parameterSchema` 부분집합(type/const/enum/min/max/required/additionalProperties=false) 검증.

    jsonschema 라이브러리 대신 쓰는 이유: 오류 코드를 키 단위로 고정하고, 런타임 의존을 늘리지 않기 위함.
    시험(`test_method_registry.py`)이 이 검증기와 jsonschema 결과가 같은지 대조한다.
    """
    if not isinstance(parameters, Mapping):
        raise MethodParameterError("parameters_not_object")
    properties = schema.get("properties", {})
    unknown = sorted(set(parameters) - set(properties))
    if unknown and schema.get("additionalProperties", True) is False:
        raise MethodParameterError("unknown_parameter:" + ",".join(unknown))
    missing = sorted(set(schema.get("required", [])) - set(parameters))
    if missing:
        raise MethodParameterError("missing_parameter:" + ",".join(missing))
    for key, rule in properties.items():
        if key not in parameters:
            continue
        value = parameters[key]
        if "const" in rule and value != rule["const"]:
            raise MethodParameterError(f"parameter_const_mismatch:{key}")
        if "enum" in rule and value not in rule["enum"]:
            raise MethodParameterError(f"parameter_enum_mismatch:{key}")
        expected = rule.get("type")
        if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            raise MethodParameterError(f"parameter_type_mismatch:{key}")
        if expected == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise MethodParameterError(f"parameter_type_mismatch:{key}")
        if expected == "string" and not isinstance(value, str):
            raise MethodParameterError(f"parameter_type_mismatch:{key}")
        if expected == "boolean" and not isinstance(value, bool):
            raise MethodParameterError(f"parameter_type_mismatch:{key}")
        if expected in ("integer", "number"):
            if "minimum" in rule and value < rule["minimum"]:
                raise MethodParameterError(f"parameter_below_minimum:{key}")
            if "maximum" in rule and value > rule["maximum"]:
                raise MethodParameterError(f"parameter_above_maximum:{key}")
            if "exclusiveMinimum" in rule and value <= rule["exclusiveMinimum"]:
                raise MethodParameterError(f"parameter_below_minimum:{key}")
            if "exclusiveMaximum" in rule and value >= rule["exclusiveMaximum"]:
                raise MethodParameterError(f"parameter_above_maximum:{key}")


IFOREST_THRESHOLD_POLICY = "train-internal-p99-v1"
IFOREST_DEFAULTS = {"n_estimators": 128, "max_samples": 512, "contamination": "auto", "random_state": 42,
                    "thresholdPolicy": IFOREST_THRESHOLD_POLICY}
IFOREST_PARAMETER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["n_estimators", "max_samples", "contamination", "random_state", "thresholdPolicy"],
    "properties": {"n_estimators": {"type": "integer", "minimum": 16, "maximum": 1024},
                   "max_samples": {"type": "integer", "minimum": 64, "maximum": 4096},
                   "contamination": {"const": "auto"},
                   "random_state": {"type": "integer", "minimum": 0},
                   "thresholdPolicy": {"const": IFOREST_THRESHOLD_POLICY}},
}

ML_METHODS: tuple[MlMethod, ...] = (
    MlMethod(
        "ml:iforest", "Isolation Forest", "supported", "process-iforest-v1",
        {"scope": "server", "path": "server/worker/model_adapters.py",
         "ref": "research/anomaly_detection/code/process_anomaly_pipeline/artifacts/process_if_v1.manifest.json"},
        {"process": True, "window": True},
        {"library": "scikit-learn", "artifactFormat": "process-iforest-v1"},
        IFOREST_PARAMETER_SCHEMA, "verified", dict(IFOREST_DEFAULTS),
        "server/worker/methods/registry.py IFOREST_DEFAULTS (= 7f24e03 server/worker/model_adapters.py:121-127)",
        {"unit": "process_row", "featureSet": "required", "numericOnly": True, "maxFeatures": 32, "minTrainRows": 600,
         "fitOperations": ["group_median", "group_centered"]},
        _source_ref("server/worker/model_adapters.py", "89-127"),
        adapter=MethodAdapter("model_adapters", "prepare_iforest", "train_iforest", "score_iforest"),
    ),
    MlMethod(
        "ml:minibatch_kmeans", "MiniBatch K-Means", "research", "research-minibatch-kmeans-v8",
        {"scope": "research", "path": "research/anomaly_detection/code/analysis/minibatch_kmeans_common.py"},
        {"process": False, "window": True}, {"library": "scikit-learn"},
        {"type": "object", "additionalProperties": False,
         "properties": {"k": {"type": "integer", "minimum": 2, "maximum": 64}, "batch_size": {"type": "integer", "minimum": 1},
                        "max_iter": {"type": "integer", "minimum": 1}, "n_init": {"type": "integer", "minimum": 1},
                        "random_state": {"type": "integer", "minimum": 0},
                        "quantile": {"type": "number", "minimum": 0, "maximum": 1}}},
        "research_declared",
        {"k": 6, "batch_size": 2048, "max_iter": 100, "n_init": 5, "random_state": 42, "quantile": 0.99},
        "research/anomaly_detection/code/analysis/minibatch_kmeans_common.py:35-46",
        {"unit": "process_row", "featureSet": "required", "numericOnly": True},
        _source_ref("research/anomaly_detection/code/analysis/minibatch_kmeans_common.py", "35-46"),
        executable_reason={"process": "server_adapter_missing"},
        notes="PROCESS 경로 피처 정의는 연구 인코더 기준, 서버 피처셋 호환 미확인(N13)",
    ),
    MlMethod(
        "ml:eif", "Extended Isolation Forest", "research", "research-eif-long-term-trend",
        {"scope": "research", "path": "research/evaluation/long_term_trend/models.py"},
        {"process": False, "window": False}, {"library": "research-bundle"},
        {"type": "object", "additionalProperties": False,
         "properties": {"ntrees": {"type": "integer", "minimum": 1}, "sample_size": {"type": "integer", "minimum": 1},
                        "extension_level": {"const": "dimension-1"}}},
        "research_declared", {"ntrees": 128, "sample_size": 256},
        "research/evaluation/if_feature_comparison/models.py:19",
        {"unit": "aggregated_window", "featureSet": "not_required"},
        _source_ref("research/evaluation/long_term_trend/models.py", "56-58"),
        executable_reason={"process": "research_only", "window": "research_only"},
    ),
)
ML_METHODS_BY_ID = {item.method_id: item for item in ML_METHODS}
ML_METHODS_BY_KEY = {item.model_key: item for item in ML_METHODS}
DEFAULT_MODEL_KEY = "iforest"


def method_for_key(model_key: str) -> MlMethod | None:
    return ML_METHODS_BY_KEY.get(model_key)


def resolve_training_method(spec: Mapping[str, Any]) -> tuple[MlMethod, dict[str, Any]]:
    """train 요청(`modelKey`·`parameters` 선택)을 실행 가능한 방법과 확정 파라미터로 바꾼다.

    오류 코드(계약 trainer-service.md·model-version.md 변경 제안 절):
    - 카탈로그에 없는 키 → `unsupported_model`
    - 등록됐지만 `executable.process=false`(어댑터 없음) → `method_not_executable:<methodId>`
    - `parameters`가 schema 위반 → `method_parameters_invalid:<methodId>`
    `modelKey`·`parameters` 키 부재(현행 v1·v2 요청)는 정상: IF 기본값.
    """
    key = spec.get("modelKey", DEFAULT_MODEL_KEY)
    method = method_for_key(key) if isinstance(key, str) else None
    if method is None:
        raise ValueError("unsupported_model")
    if not method.executable_process:
        raise ValueError(f"method_not_executable:{method.method_id}")
    try:
        parameters = method.resolve_parameters(spec.get("parameters"))
    except MethodParameterError as exc:
        raise ValueError(f"method_parameters_invalid:{method.method_id}") from exc
    return method, parameters


def iforest_parameters(parameters: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """`model_adapters.train_iforest`가 쓰는 확정 파라미터(기본값 + 요청 값, schema 검증)."""
    return ML_METHODS_BY_ID["ml:iforest"].resolve_parameters(parameters)


def _executable_in_frame(item: Mapping[str, Any], frame: str) -> bool:
    """그 틀에서 실행 가능한가. 행 = `executable.process`. 시계열은 서버 실행 경로(`executable.trend`)가 아직 없어 항상 거짓."""
    if frame == "row":
        return item.get("executable", {}).get("process") is True
    return False


def _check_frame_entry(frame: str, entry: Mapping[str, Any], methods: Mapping[str, Mapping[str, Any]]) -> None:
    if not isinstance(entry, Mapping) or set(entry) != {"displayName", "recommendedMethodId", "recommendedParameters", "evidence"}:
        raise CatalogRuleError(f"frame_entry_invalid:{frame}")
    if not isinstance(entry["displayName"], str) or not entry["displayName"].strip():
        raise CatalogRuleError(f"frame_display_name_missing:{frame}")
    method_id, parameters, evidence = entry["recommendedMethodId"], entry["recommendedParameters"], entry["evidence"]
    if method_id is None:
        if parameters is not None or evidence is not None:
            raise CatalogRuleError(f"frame_recommendation_incomplete:{frame}")
        return
    if parameters is None or evidence is None:
        raise CatalogRuleError(f"frame_recommendation_incomplete:{frame}")
    method = methods.get(method_id)
    if method is None:
        raise CatalogRuleError(f"frame_recommendation_unknown:{frame}")
    if method.get("frame") != frame:
        raise CatalogRuleError(f"frame_recommendation_frame_mismatch:{frame}")
    if not _executable_in_frame(method, frame):
        raise CatalogRuleError(f"frame_recommendation_not_executable:{frame}")
    try:
        validate_parameters(method["parameterSchema"], parameters)
    except MethodParameterError as exc:
        raise CatalogRuleError(f"frame_recommendation_parameters_invalid:{frame}") from exc
    if not isinstance(evidence, Mapping) or set(evidence) != set(FRAME_EVIDENCE_KEYS) or \
            not all(isinstance(evidence[key], str) and evidence[key].strip() for key in FRAME_EVIDENCE_KEYS):
        raise CatalogRuleError(f"frame_recommendation_evidence_invalid:{frame}")


def validate_catalog(catalog: Mapping[str, Any]) -> None:
    """틀·결과 코드·틀별 추천 규칙(method-catalog.md "틀·결과 코드·틀별 추천" 절). 위반이면 `CatalogRuleError`.

    - 방법마다 `frame` = `FRAME_BY_UNIT[inputRequirements.unit]`(1:1)
    - `ml:trend-` 접두 ⇔ `frame=trend`. 예외는 `TREND_PREFIX_LEGACY` 항목이 실행할 수 없는 동안뿐
    - `resultCode` = 기계 규칙(예외는 `RESULT_CODE_EXCEPTIONS` 하나), 카탈로그 전체에서 유일
    - `frames`는 두 틀 정확히, 틀마다 이름 1개·추천 0..1(같은 틀·그 틀에서 실행 가능한 방법만)
    """
    methods: dict[str, Mapping[str, Any]] = {}
    codes: dict[str, str] = {}
    for item in catalog["methods"]:
        method_id = item["methodId"]
        if method_id in methods:
            raise CatalogRuleError(f"method_duplicate:{method_id}")
        methods[method_id] = item
        frame = item.get("frame")
        if frame != frame_for_unit(item["inputRequirements"]["unit"]):
            raise CatalogRuleError(f"frame_unit_mismatch:{method_id}")
        prefixed = method_id.startswith(TREND_METHOD_PREFIX)
        legacy = (method_id in TREND_PREFIX_LEGACY and frame == "trend" and item["status"] != "supported"
                  and not any(item["executable"].values()))
        if prefixed != (frame == "trend") and not legacy:
            raise CatalogRuleError(f"trend_prefix_mismatch:{method_id}")
        code = item.get("resultCode")
        if code != result_code_for(method_id):
            raise CatalogRuleError(f"result_code_mismatch:{method_id}")
        if code in codes:
            raise CatalogRuleError(f"result_code_duplicate:{code}")
        codes[code] = method_id
    frames = catalog.get("frames")
    if not isinstance(frames, Mapping) or set(frames) != set(FRAMES):
        raise CatalogRuleError("frames_invalid")
    for frame in FRAMES:
        _check_frame_entry(frame, frames[frame], methods)


def frame_changes(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> list[str]:
    """직전 카탈로그(커밋본)와 비교해 `frame`이 바뀐 `methodId` 목록. 틀을 바꾸려면 새 `methodId`를 쓴다.

    `frame` 키가 없는 이전 판(v2)은 같은 1:1 규칙으로 `inputRequirements.unit`에서 읽는다.
    """
    def frames_of(catalog: Mapping[str, Any] | None) -> dict[str, str | None]:
        found: dict[str, str | None] = {}
        methods = catalog.get("methods") if isinstance(catalog, Mapping) else None
        for item in methods if isinstance(methods, list) else []:
            if isinstance(item, Mapping) and isinstance(item.get("methodId"), str):
                unit = (item.get("inputRequirements") or {}).get("unit")
                found[item["methodId"]] = item.get("frame", FRAME_BY_UNIT.get(unit))
        return found

    before, after = frames_of(previous), frames_of(current)
    return [method_id for method_id, frame in before.items() if method_id in after and after[method_id] != frame]


def build_catalog() -> dict[str, Any]:
    catalog = {
        "catalogVersion": CATALOG_VERSION,
        "generatedFrom": {"gitRef": CATALOG_SOURCE_REF},
        "categoryDisplayNames": dict(CATEGORY_DISPLAY_NAMES),
        "hardRules": dict(HARD_RULES),
        "softRule": json.loads(json.dumps(SOFT_RULE)),
        "softRuleFields": [item.to_catalog() for item in SOFT_RULE_FIELDS],
        "frames": json.loads(json.dumps(FRAME_ENTRIES)),
        "methods": [item.to_catalog() for item in ML_METHODS],
    }
    validate_catalog(catalog)
    return catalog


def render_catalog(catalog: Mapping[str, Any] | None = None) -> str:
    return json.dumps(catalog if catalog is not None else build_catalog(), ensure_ascii=False, indent=2) + "\n"


def write_catalog(path: Path | None = None) -> Path:
    target = Path(path) if path is not None else CATALOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_catalog())
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m methods.registry",
                                     description="method-catalog.json 생성(--write) / 커밋본 대조(--check) / 출력(기본)")
    parser.add_argument("--write", action="store_true", help="카탈로그 JSON을 파일로 쓴다")
    parser.add_argument("--check", action="store_true", help="커밋된 JSON이 재생성본과 같은지 확인한다(다르면 종료 코드 1)")
    parser.add_argument("--output", type=Path, default=None, help=f"대상 경로(기본 {CATALOG_PATH})")
    args = parser.parse_args(argv)
    target = args.output or CATALOG_PATH
    if args.check:
        existing = target.read_text(encoding="utf-8") if target.exists() else None
        if existing != render_catalog():
            print(f"method-catalog.json differs from registry: {target}", file=sys.stderr)
            return 1
        print(f"method-catalog.json matches registry: {target}")
        return 0
    if args.write:
        try:
            previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
        except ValueError:
            previous = None
        changed = frame_changes(previous, build_catalog())
        if changed:
            print("method frame changed (use a new methodId): " + ",".join(changed), file=sys.stderr)
            return 1
        print(f"wrote {write_catalog(target)}")
        return 0
    sys.stdout.write(render_catalog())
    return 0


if __name__ == "__main__":
    sys.exit(main())
