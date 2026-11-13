import { versionLabel } from "@/features/detection/models/VersionList";
import { DASH, numberText, percentText } from "@/features/operations/format";
import type { EvaluationItem } from "@/services/api/pipeline.api";
import type { CompareVersion, ComparisonStatus, EvaluationPolicy, EvaluationSet, ItemCounts, Rate, RecommendationState } from "./evaluationsApi";

/** 평가 결과 화면 표시 형식. 서버 값을 옮기기만 하고 판정을 다시 계산하지 않는다. */
export const ruleConditionText = (revision: number | null | undefined) => (revision === null || revision === undefined ? DASH : `운영 룰 버전 ${revision} 고정`);

const isEsb = (set: EvaluationSet) => set.synthetic?.profileId.startsWith("esb-") === true;
const isAdbench = (set: EvaluationSet) => set.synthetic?.profileId.startsWith("adbench-") === true;
export const setKindLabel = (set: EvaluationSet) => (isEsb(set) ? "ESB 시나리오" : isAdbench(set) ? "ADBench 수치 합성" : "원본 분할만");
export const setUnitLabel = (set: EvaluationSet) => (isEsb(set) ? "주입 PROCESS 단위" : isAdbench(set) ? "합성 샘플 단위" : setKindLabel(set));
export const setNote = (set: EvaluationSet | undefined) => {
  if (!set) return null;
  if (isEsb(set)) return "단위: 주입 PROCESS 실행. 비주입 원본은 정상 확정 정답이 아니므로 FP·TN·정밀도는 정의 불가입니다.";
  if (isAdbench(set)) return "단위: ADBench 합성 샘플. 라벨은 분포 소속(합성 정상/이상)이며 ESB 장애 정답이 아닙니다.";
  return null;
};
/** 정답 행 이름(와이어프레임 혼동행렬). */
export const truthRowLabels = (set: EvaluationSet | undefined) =>
  set && isEsb(set) ? { anomaly: "주입 이상", normal: "비주입 원본(라벨 없음)" } : set && isAdbench(set) ? { anomaly: "합성 이상", normal: "합성 정상" } : { anomaly: "이상 정답", normal: "정상 정답" };

export const scenarioText = (set: EvaluationSet) => {
  const scenarios = set.synthetic?.scenarios ?? [];
  if (!scenarios.length) return DASH;
  const severities = new Set(scenarios.map((item) => item.severity));
  return severities.size === 1
    ? `${scenarios.map((item) => item.scenario).join(", ")} · 강도 ${scenarios[0].severity}`
    : scenarios.map((item) => `${item.scenario} 강도 ${item.severity}`).join(", ");
};
export const rowCountText = (set: EvaluationSet) => (set.generation ? `${numberText(set.generation.rowCount)}행 (${numberText(set.generation.truthCount)})` : DASH);
const shortHash = (value: string) => `${value.slice(0, 6)}…${value.slice(-4)}`;
export const fixedInfoText = (set: EvaluationSet, full = false) => {
  const parts = [set.synthetic ? `seed ${set.synthetic.seed}` : null, set.generation ? (full ? set.generation.contentHash : shortHash(set.generation.contentHash)) : null].filter(Boolean);
  return parts.length ? parts.join(" · ") : DASH;
};

export const SET_STATUS_LABELS = { queued: "대기", ready: "준비", failed: "실패" } as const;
export const REC_STATE_LABELS: Record<RecommendationState, string> = { current: "현재 추천", applied: "이미 적용됨", stale: "지난 추천", none: "추천 없음" };
export const comparisonStatus = (status: ComparisonStatus, failedCount: number) => {
  if (status === "queued") return { label: "대기", variant: "info" } as const;
  if (status === "running") return { label: "진행 중", variant: "info" } as const;
  if (status === "failed") return { label: "실패", variant: "critical" } as const;
  return failedCount > 0 ? ({ label: "완료 · 일부 실패", variant: "warning" } as const) : ({ label: "완료", variant: "success" } as const);
};

export const labelOf = (versions: CompareVersion[] | undefined, id: string | null | undefined) => {
  if (!id) return DASH;
  const version = versions?.find((item) => item.id === id);
  return version ? versionLabel(version) : id.slice(0, 8);
};

const UNDEFINED_REASON = "verified_normal_labels_unavailable";
/** 비율 값. 정상 라벨이 없어 정의되지 않는 값은 `정의 불가`, 그 밖의 null은 사유 코드를 그대로 보인다. */
export const rateText = (rate: Rate | undefined) => {
  if (!rate) return DASH;
  if (rate.value === null) return rate.reason === UNDEFINED_REASON ? "정의 불가" : `미측정 · ${rate.reason ?? DASH}`;
  return `${percentText(rate.value)}${rate.coverage === null || rate.coverage === undefined ? "" : ` (커버리지 ${percentText(rate.coverage)})`}`;
};
export const countText = (value: number | null | undefined) => (value === null || value === undefined ? "정의 불가" : numberText(value));

/**
 * 항목 평가 정책(계약 v1: v1·v2는 구성 정책·룰, v3는 접수 고정 운영 정책). 버전끼리는 모델 단독 결과로 비교하고 이 값을 함께 보인다.
 */
export const policyText = (policy: EvaluationPolicy | undefined) => {
  if (!policy) return DASH;
  const rules = policy.ruleCount ? ` · 룰 ${numberText(policy.ruleCount)}개` : "";
  if (policy.source === "accepted") return `운영 정책(접수 때 고정)${rules}`;
  return `구성 정책${policy.detectionPolicy?.mode ? ` · ${policy.detectionPolicy.mode}` : ""}${rules}`;
};

/** 평가 제외 행 사유 이름(사용자 승인 11/14). 모르는 코드는 코드 그대로 보인다. */
export const EXCLUDED_REASON_LABELS: Record<string, string> = { model_inputs_undefined: "입력 변수 계산 불가(예: 처리 건수 0)" };
/** 평가 전에 뺀 행(`counts.excludedCount`). 예전 결과(키 없음)는 null — 화면은 그 줄을 숨긴다. */
export const excludedText = (counts: ItemCounts | undefined) => {
  if (counts?.excludedCount === undefined) return null;
  const reasons = Object.entries(counts.excludedReasons ?? {}).filter(([, count]) => count > 0)
    .map(([code, count]) => ` · ${EXCLUDED_REASON_LABELS[code] ?? code} ${numberText(count)}행`);
  return `${numberText(counts.excludedCount)}행${reasons.join("")}`;
};
/** 값 하나: null이면 `미측정 · <사유>`. */
export const valueOrReason = (value: number | null | undefined, reason: string | null | undefined, format: (value: number) => string) =>
  value === null || value === undefined ? `미측정 · ${reason ?? DASH}` : format(value);
/** 실행 비용 지표 하나(`execution-cost-v1`). 측정되지 않은 묶음·값은 사유 코드를 그대로 보인다. */
export const costMetricText = (family: EvaluationItem | undefined, key: string, format: (value: number) => string) => {
  if (!family || family.status !== "measured") return `미측정 · ${family?.reason ?? DASH}`;
  const metric = family.metrics?.[key];
  return valueOrReason(metric?.value, metric?.reason, format);
};
