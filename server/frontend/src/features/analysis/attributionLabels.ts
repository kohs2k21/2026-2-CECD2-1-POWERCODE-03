import type { AttributionFailureCode, AttributionReason } from "./data/attribution";

/**
 * 판정 상세 기여도 표시 전용 이름표(detection-decision.md "6.1 ML 기여도" 절 이름표). 다른 화면의 변수 이름은 바꾸지 않는다.
 * - 단위가 확정되지 않은 건수 변수는 원래 필드 이름으로 쓰고(TOTAL_COUNT는 `처리량(TOTAL_COUNT)`), 값이 함께 나오는 곳(반사실 문장·묶음 괄호·표)에서만 이름 뒤 `(단위 미확인)`
 * - 목록에 없는 변수는 변수 이름 그대로(단위 없음)
 */
export type AttributionFeatureLabel = { name: string; unitUnknown: boolean; unit: string | null };
const featureLabels: Record<string, AttributionFeatureLabel> = {
  total_count: { name: "처리량(TOTAL_COUNT)", unitUnknown: true, unit: null },
  success_count: { name: "SUCCESS_COUNT", unitUnknown: true, unit: null },
  error_count: { name: "ERROR_COUNT", unitUnknown: true, unit: null },
  retry_count: { name: "RETRY_COUNT", unitUnknown: true, unit: null },
  elapsed_ms: { name: "처리 시간", unitUnknown: false, unit: "ms" },
  error_ratio: { name: "오류 비율 값", unitUnknown: false, unit: null },
};
/** 자기 키만 본다(`constructor`·`__proto__` 같은 상속 속성을 집지 않음). */
export const attributionFeatureLabel = (feature: string): AttributionFeatureLabel =>
  Object.hasOwn(featureLabels, feature) ? featureLabels[feature] : { name: feature, unitUnknown: false, unit: null };
export const UNIT_UNKNOWN = "(단위 미확인)";

/**
 * 방법 이름과 상태 낱말(키 없음·skipped·failed·저장 형식을 읽을 수 없는 기록).
 * 사유·실패 코드 이름은 analysis-ui.md 판정 상세 기여도 이름 표와 같다.
 */
export const ATTRIBUTION_TITLE = "기여도";
export const attributionStatusWords = { absent: "기여도 미계산", skipped: "기여도 계산 안 함", failed: "기여도 계산 실패", unreadable: "기여도 표시 불가" } as const;
export const attributionReasonLabels: Record<AttributionReason, string> = {
  not_anomalous: "이상 판정이 아님",
  method_unresolved: "모델 판정이 끝나지 않음",
  too_many_players: "변수 묶음이 12개 초과",
  baseline_unavailable: "학습 중앙값 없음",
};
export const attributionFailureLabels: Record<AttributionFailureCode, string> = {
  reference_unavailable: "모델 참조 없음",
  score_not_reproduced: "저장된 원점수를 다시 계산하지 못함",
  efficiency_mismatch: "기여도 합이 맞지 않음",
  invalid_score: "계산 점수가 유효하지 않음",
  non_finite_value: "저장할 수 없는 값",
  size_exceeded: "기록 크기 초과",
  exception: "그 밖의 오류",
};

/**
 * scale 1 구성원(`players[].scaleIsUnit` = true)의 기준값 자리 글자. 전역 중앙값이 여러 어댑터가 섞인 값이라 숫자를 보이지 않는다.
 * 요약 문장(`{이름} 평소 값과 다름 {비율}%`, 양수 1개면 `{이름}뿐입니다(평소 값과 다름).`)·표·묶음 괄호가 이 값을 쓴다. 반사실은 attributionText.ts `replacedAlone`.
 */
export const SCALE_UNIT_BASELINE = "평소 값과 다름";
/** 묶음 괄호의 기준값 자리(scale 1이 아닌 구성원). */
export const medianText = (baseline: string) => `중앙값 ${baseline}`;
