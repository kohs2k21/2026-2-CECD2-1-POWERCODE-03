"""Process 행용 Isolation Forest 모델 연결 지점.

피처 v5·Rule v2는 내부 생성 서비스로 연결하고 IF는 운영 변수셋으로 새로 학습한다.
최신 v12 시간창 모델은 window_worker의 별도 입력 계약으로 실행한다.
"""

from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler


FORMAT_VERSION = "process-iforest-v1"
MIN_FIT_ROWS = 500
MIN_THRESHOLD_ROWS = 100
NUMERIC_FIELDS = ("success_count", "error_count", "total_count", "retry_count")


class ModelNotConfigured(RuntimeError):
    """실행 환경·번들·생성 서비스가 아직 구성되지 않았다."""


def recommend_features(rows: Sequence[Mapping[str, Any]]):
    """허용된 P 수치 필드 중 충분히 관측된 필드를 안전한 변수로 등록한다."""
    if os.environ.get("FEATURE_DESIGN_PROVIDER", "research") == "research":
        if not os.environ.get("RESEARCH_BUNDLE_ROOT"):
            raise ModelNotConfigured("research_bundle_not_configured")
        from research_design import recommend_features as design
        from generation_client import GenerationUnavailable
        try:
            return design(rows)
        except GenerationUnavailable as exc:
            raise ModelNotConfigured(str(exc)) from exc
    if os.environ.get("FEATURE_DESIGN_PROVIDER") != "numeric":
        raise ValueError("unsupported_feature_design_provider")
    if not rows:
        raise ValueError("feature_reference_empty")
    definitions = []
    for name in NUMERIC_FIELDS:
        observed = sum(_finite_number(row.get(name)) is not None for row in rows)
        if observed >= max(1, math.ceil(len(rows) * .9)):
            definitions.append({"name": name, "expression": {"op": "field", "field": f"P.{name}"}})
    if not definitions:
        raise ValueError("feature_reference_insufficient")
    return definitions


def recommend_rules(rows: Sequence[Mapping[str, Any]], feature_set_id: str,
                    definitions: list[dict[str, Any]] | None = None):
    """Rule v2가 검증된 변수·측정 근거에서 SR 초안을 제안한다."""
    from research_design import recommend_rules as design
    from generation_client import GenerationUnavailable
    if definitions is None or not os.environ.get("RESEARCH_BUNDLE_ROOT"):
        raise ModelNotConfigured("research_rule_context_not_configured")
    try:
        return design(rows, feature_set_id, definitions)
    except GenerationUnavailable as exc:
        raise ModelNotConfigured(str(exc)) from exc


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _vector(features: Mapping[str, Any], names: Sequence[str]) -> list[float]:
    if not isinstance(features, Mapping) or set(features) != set(names):
        raise ValueError("feature_schema_mismatch")
    values = [_finite_number(features[name]) for name in names]
    if any(value is None for value in values):
        raise ValueError("features_unavailable")
    return values  # type: ignore[return-value]


def train_iforest(rows: Sequence[Mapping[str, Any]], feature_names: Sequence[str],
                  output_directory: str, *, preprocessing: dict | None = None) -> dict[str, Any]:
    """Train 분할 안의 별도 기준 행으로 P99 판정선을 보정한다."""
    names = list(feature_names)
    if not names or len(names) > 32 or len(set(names)) != len(names) or not all(
        isinstance(name, str) and name for name in names
    ):
        raise ValueError("invalid_feature_names")
    vectors = []
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("features"), Mapping):
            raise ValueError("invalid_training_row")
        if row.get("label") == "anomaly":
            continue
        if row.get("missing"):
            continue
        vectors.append(_vector(row["features"], names))
    if len(vectors) < MIN_FIT_ROWS + MIN_THRESHOLD_ROWS:
        raise ValueError("insufficient_complete_training_rows")
    values = np.asarray(vectors, dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("invalid_training_values")
    # _snapshot_rows는 UUID 정렬이므로 고정 시드로 섞어 서로 겹치지 않는 집합을 만든다.
    shuffled = values[np.random.default_rng(42).permutation(len(values))]
    threshold_count = max(MIN_THRESHOLD_ROWS, math.ceil(len(shuffled) * .2))
    fit_rows, threshold_rows = shuffled[:-threshold_count], shuffled[-threshold_count:]
    if len(fit_rows) < MIN_FIT_ROWS:
        raise ValueError("insufficient_complete_training_rows")
    if len(np.unique(fit_rows, axis=0)) < 2:
        raise ValueError("insufficient_distinct_training_vectors")
    scaler = RobustScaler().fit(fit_rows)
    fit_values = scaler.transform(fit_rows)
    forest = IsolationForest(
        n_estimators=128, max_samples=min(512, len(fit_values)),
        contamination="auto", max_features=1.0, bootstrap=False,
        random_state=42, n_jobs=2,
    ).fit(fit_values)
    threshold_scores = -forest.score_samples(scaler.transform(threshold_rows))
    boundary = float(np.quantile(threshold_scores, .99))
    if not math.isfinite(boundary):
        raise ValueError("invalid_model_threshold")
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / "model.joblib"
    fit_path = directory / "fit.joblib"
    metadata = {"formatVersion": FORMAT_VERSION, "sklearnVersion": sklearn.__version__,
                "featureNames": names}
    if preprocessing is not None:
        from feature_fit import state_hash
        metadata['preprocessingHash'] = state_hash(preprocessing)
    joblib.dump({**metadata, "forest": forest}, model_path)
    joblib.dump({**metadata, "scaler": scaler, **({'preprocessing': preprocessing} if preprocessing is not None else {})}, fit_path)
    return {"modelPath": str(model_path), "fitPath": str(fit_path),
            "formatVersion": FORMAT_VERSION, "runtimeVersion": sklearn.__version__,
            "thresholdSpec": {"direction": "higher_is_anomaly", "value": str(boundary),
                              "quantile": .99, "referenceCount": len(threshold_rows)}}


@lru_cache(maxsize=4)
def _load_pair(model_path: str, fit_path: str, model_mtime: int, fit_mtime: int) -> tuple[Any, Any, tuple[str, ...]]:
    # 호출자는 DB에 저장된 SHA-256을 검증한 경로만 넘겨야 한다. pickle은 신뢰된 자체 산출물만 로드한다.
    model = joblib.load(model_path)
    fit = joblib.load(fit_path)
    if not isinstance(model, dict) or not isinstance(fit, dict):
        raise ValueError("invalid_model_artifact")
    for item in (model, fit):
        if item.get("formatVersion") != FORMAT_VERSION or item.get("sklearnVersion") != sklearn.__version__:
            raise ValueError("incompatible_model_runtime")
    from feature_fit import state_hash
    preprocessing = fit.get('preprocessing')
    if model.get('preprocessingHash') != fit.get('preprocessingHash') or \
            (preprocessing is not None and state_hash(preprocessing) != fit.get('preprocessingHash')) or \
            (preprocessing is None and fit.get('preprocessingHash') is not None):
        raise ValueError('preprocessing_hash_mismatch')
    names = model.get("featureNames")
    if not isinstance(names, list) or not names or names != fit.get("featureNames"):
        raise ValueError("model_feature_schema_mismatch")
    forest, scaler = model.get("forest"), fit.get("scaler")
    if not isinstance(forest, IsolationForest) or not isinstance(scaler, RobustScaler):
        raise ValueError("invalid_model_artifact")
    if forest.n_features_in_ != len(names) or scaler.n_features_in_ != len(names):
        raise ValueError("model_feature_schema_mismatch")
    return forest, scaler, tuple(names)


def _prepared(model_reference: Mapping[str, Any]) -> tuple[Any, Any, tuple[str, ...]]:
    try:
        model_path = Path(model_reference["modelPath"]).resolve(strict=True)
        fit_path = Path(model_reference["fitPath"]).resolve(strict=True)
    except (KeyError, TypeError, OSError, ValueError) as exc:
        raise ValueError("model_artifact_unavailable") from exc
    if not model_path.is_file() or not fit_path.is_file() or model_path.suffix != ".joblib" or fit_path.suffix != ".joblib":
        raise ValueError("model_artifact_unavailable")
    forest, scaler, names = _load_pair(str(model_path), str(fit_path),
                                       model_path.stat().st_mtime_ns, fit_path.stat().st_mtime_ns)
    expected = model_reference.get("featureNames")
    if expected is not None and (not isinstance(expected, (list, tuple)) or list(expected) != list(names)):
        raise ValueError("model_feature_schema_mismatch")
    return forest, scaler, names


def prepare_iforest(model_reference: Mapping[str, Any]) -> None:
    """활성 구성의 파일을 메모리에 적재하고 포맷·버전·열 순서를 검사한다."""
    _prepared(model_reference)


@lru_cache(maxsize=4)
def _preprocessing(path: str, modified: int):
    return joblib.load(path).get('preprocessing')


def transform_iforest_features(model_reference, definitions, raw):
    """Only called with artifact paths whose DB content hashes were verified."""
    from feature_fit import transform, requires_fit, definitions_hash
    from feature_runtime import calculate_features
    _prepared(model_reference)
    path = Path(model_reference['fitPath']).resolve(strict=True)
    state = _preprocessing(str(path), path.stat().st_mtime_ns)
    expected = model_reference.get('definitionsHash')
    if expected is not None and expected != definitions_hash(definitions):
        raise ValueError('preprocessing_definition_mismatch')
    if state is not None:
        return transform(definitions, raw, state)
    if any(requires_fit(item['expression']) for item in definitions):
        raise ValueError('preprocessing_state_required')
    return calculate_features(definitions, raw)


def score_iforest(model_reference: Mapping[str, Any], features: Mapping[str, Any]) -> float:
    """Process 관측 한 건의 원시 이상 점수(-score_samples)를 반환한다."""
    forest, scaler, names = _prepared(model_reference)
    value = -forest.score_samples(scaler.transform([_vector(features, names)]))[0]
    if not math.isfinite(value):
        raise ValueError("invalid_model_score")
    return float(value)
