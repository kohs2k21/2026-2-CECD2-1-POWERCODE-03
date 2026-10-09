import { featureCatalog } from "../data/catalog";
import {
  currentConfigurationFingerprint,
  evaluationMatchesConfiguration,
} from "../data/configuration";
import type {
  DetectionData,
  EvaluationCondition,
  EvaluationResult,
  StorageObservation,
  VersionBundle,
} from "../data/types";

export const conditionsMatch = (
  left: EvaluationCondition | undefined,
  right: EvaluationCondition | undefined,
) =>
  Boolean(
    left &&
    right &&
    left.snapshotId === right.snapshotId &&
    left.protocolId === right.protocolId &&
    left.splitVersion === right.splitVersion &&
    left.scenario === right.scenario &&
    left.purpose === right.purpose &&
    left.evaluationSetId === right.evaluationSetId &&
    left.evaluationSetRevision === right.evaluationSetRevision,
  );

export const evaluationErrors = (
  condition: EvaluationCondition,
  data: DetectionData,
) => {
  const errors: string[] = [];
  const snapshot = data.snapshots.find(
    (item) => item.id === condition.snapshotId,
  );
  if (!snapshot) errors.push("평가 데이터셋을 선택해 주세요.");
  else {
    if (snapshot.state !== "ready")
      errors.push("데이터셋이 준비되지 않았습니다.");
    if (condition.splitVersion !== snapshot.splitVersion)
      errors.push("데이터셋의 분할 버전과 일치해야 합니다.");
  }
  if (!condition.protocolId.trim())
    errors.push("평가 프로토콜을 입력해 주세요.");
  if (!condition.splitVersion.trim()) errors.push("분할 버전을 입력해 주세요.");
  if (!condition.scenario.trim()) errors.push("시나리오를 입력해 주세요.");
  if (!condition.purpose.trim()) errors.push("평가 목적을 입력해 주세요.");
  return errors;
};

export const evaluationFor = (
  bundle: VersionBundle | undefined,
  data: DetectionData,
) => data.evaluations.find((item) => item.id === bundle?.evaluationId);

export const metricRows: {
  key: keyof EvaluationResult["metrics"];
  label: string;
  unit: "percent" | "ms";
}[] = [
  { key: "delayRecall", label: "지연 재현율", unit: "percent" },
  { key: "stallRecall", label: "먹통 재현율", unit: "percent" },
  { key: "burstRecall", label: "폭주 재현율", unit: "percent" },
  { key: "latencyMs", label: "판정 지연", unit: "ms" },
  { key: "unavailableRate", label: "판정 불가율", unit: "percent" },
];

export const metricText = (
  value: number | null | undefined,
  unit: "percent" | "ms",
) => {
  if (value == null || !Number.isFinite(value) || value < 0) return "—";
  return unit === "percent" && value <= 1
    ? `${(value * 100).toFixed(1)}%`
    : unit === "ms"
      ? `${value.toLocaleString("ko-KR")} ms`
      : "—";
};

export const versionLabels = {
  draft: "편집 중",
  candidate: "평가 후보",
  active: "운영 중",
  archived: "이전 버전",
} as const;

export const versionReadiness = (
  bundle: VersionBundle,
  data: DetectionData,
) => {
  const reasons: string[] = [];
  const model = data.models.find((item) => item.id === bundle.modelId);
  const features = bundle.featureIds.map((id) =>
    featureCatalog.find((item) => item.id === id),
  );
  const evaluation = evaluationFor(bundle, data);
  const artifact = data.modelArtifacts.find(
    (item) => item.id === bundle.modelArtifactId,
  );
  if (!artifact || artifact.state !== "succeeded")
    reasons.push("생성 완료한 모델 산출물을 확인할 수 없습니다.");
  else {
    if (artifact.available !== true)
      reasons.push("모델 산출물의 현재 가용성 확인이 필요합니다.");
    if (artifact.compatible !== true)
      reasons.push("모델 산출물의 현재 호환성 확인이 필요합니다.");
  }
  if (
    !currentConfigurationFingerprint(bundle, data) ||
    bundle.configurationFingerprint !==
      currentConfigurationFingerprint(bundle, data)
  )
    reasons.push(
      "구성 내용이 변경되었거나 확인되지 않았습니다. 새 후보와 재평가가 필요합니다.",
    );
  if (!bundle.trained) reasons.push("모델 학습이 완료되지 않았습니다.");
  if (!bundle.featureVersion.trim() || !bundle.preprocessingVersion.trim())
    reasons.push("피처·전처리 버전이 누락되었습니다.");
  if (!model) reasons.push("모델 구성을 확인할 수 없습니다.");
  if (!features.length || features.some((item) => !item))
    reasons.push("피처 구성이 누락되었거나 확인되지 않았습니다.");
  if (
    model &&
    features.some((item) => item && !model.supportedTypes.includes(item.type))
  )
    reasons.push("모델에서 지원하지 않는 입력 유형이 있습니다.");
  if (
    features.some(
      (item) =>
        item &&
        ["unconfirmed", "unsupported", "deferred"].includes(item.readiness),
    )
  )
    reasons.push("미확인 또는 보류 피처가 포함되어 있습니다.");
  if (!bundle.fitVersion)
    reasons.push("학습된 전처리 통계 버전을 확인해야 합니다.");
  if (bundle.compatible !== true)
    reasons.push(
      bundle.compatible === false
        ? "현재 입력 계약과 호환되지 않습니다."
        : "입력·전처리 호환성이 확인되지 않았습니다.",
    );
  if (bundle.artifactAvailable !== true)
    reasons.push(
      bundle.artifactAvailable === false
        ? "적용할 모델 산출물이 없습니다."
        : "모델 산출물의 가용성을 확인해야 합니다.",
    );
  if (!evaluation || evaluation.state !== "succeeded")
    reasons.push("성공한 평가 근거가 필요합니다.");
  else {
    if (!evaluationMatchesConfiguration(evaluation, bundle, data))
      reasons.push(
        "재평가 필요: 기록된 평가의 모델·룰 구성이 현재 구성과 일치하지 않습니다.",
      );
    if (evaluationErrors(evaluation.condition, data).length)
      reasons.push("평가 데이터셋과 평가 조건의 유효성을 확인해야 합니다.");
    if (
      evaluation.candidateId !== bundle.id ||
      evaluation.condition.snapshotId !== bundle.snapshotId
    )
      reasons.push("평가 근거가 선택한 구성·데이터셋과 일치하지 않습니다.");
    if (
      metricRows.some(
        ({ key, unit }) => metricText(evaluation.metrics[key], unit) === "—",
      )
    )
      reasons.push("평가 지표가 모두 측정되지 않았습니다.");
  }
  if (
    bundle.ruleVersionIds.some(
      (id) =>
        !data.ruleVersions.some(
          (rule) => rule.id === id && rule.state === "succeeded",
        ),
    )
  )
    reasons.push("완료한 룰 버전을 확인할 수 없습니다.");
  if (!bundle.explanationVersion)
    reasons.push("설명 구성 버전이 확인되지 않았습니다.");
  return reasons;
};

export const bytesText = (value: number | null) => {
  if (value == null || !Number.isFinite(value) || value < 0) return "—";
  if (value < 1024) return `${value} B`;
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 4);
  return `${(value / 1024 ** index).toFixed(1)} ${["B", "KiB", "MiB", "GiB", "TiB"][index]}`;
};

export const storageAssessment = (observation: StorageObservation) => {
  const {
    totalBytes,
    availableBytes,
    state,
    warningUsedPercent,
    warningAvailableBytes,
  } = observation;
  const valid =
    totalBytes != null &&
    availableBytes != null &&
    Number.isFinite(totalBytes) &&
    Number.isFinite(availableBytes) &&
    totalBytes > 0 &&
    availableBytes >= 0 &&
    availableBytes <= totalBytes;
  const usedPercent = valid ? (1 - availableBytes! / totalBytes!) * 100 : null;
  const insufficient =
    state === "fresh" &&
    ((usedPercent != null && usedPercent >= warningUsedPercent) ||
      (availableBytes != null &&
        Number.isFinite(availableBytes) &&
        availableBytes >= 0 &&
        availableBytes < warningAvailableBytes));
  const identified = Boolean(
    observation.path &&
    observation.observedAt &&
    Number.isFinite(Date.parse(observation.observedAt)),
  );
  const labels = {
    unknown: "관측 미확인",
    stale: "오래된 관측",
    failed: "관측 실패",
    fresh: insufficient
      ? "용량 부족 경고"
      : valid && identified
        ? "최근 관측"
        : "관측 정보 불완전",
  };
  return { usedPercent, insufficient, label: labels[state] };
};

export const validDateRange = (start: string, end: string) => {
  const isDate = (value: string) =>
    !value ||
    (/^\d{4}-\d{2}-\d{2}$/.test(value) &&
      Number.isFinite(Date.parse(value)) &&
      new Date(value).toISOString().slice(0, 10) === value);
  return isDate(start) && isDate(end) && (!start || !end || start <= end);
};
