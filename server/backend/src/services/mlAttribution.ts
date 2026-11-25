/**
 * ML 방법 결과의 기여도(`methodResults[ml].attribution`, detection-decision.md "6.1 ML 기여도" 절 저장 형식) 읽기.
 * 저장값만 읽고 다시 계산하지 않는다. AI 판정 근거 요약(evidenceSummary.ts)이 반올림·% 변환만 한다(koreanText.ts).
 * - 키 없음 → `absent`(이전 판정 = 기여도 미계산)
 * - `skipped`·`failed` → 사유·코드(알 수 없는 코드는 null)
 * - `succeeded` → 저장 형식 키를 모두 검사한다. 하나라도 형식이 어긋나거나 모르는 `version`·`status`면 `unreadable`(요약 `기여도 표시 불가`)
 * - `players[].scaleIsUnit`은 선택 키: 없거나 형식이 다르면 그 구성원에 규칙을 적용하지 않는다(나머지는 정상 표시)
 */
export const ATTRIBUTION_VERSION = "baseline-shapley-v1";
export const attributionReasons = ["not_anomalous", "method_unresolved", "too_many_players", "baseline_unavailable"] as const;
export const attributionFailureCodes = [
  "reference_unavailable",
  "score_not_reproduced",
  "efficiency_mismatch",
  "invalid_score",
  "non_finite_value",
  "size_exceeded",
  "exception",
] as const;
export type AttributionReason = (typeof attributionReasons)[number];
export type AttributionFailureCode = (typeof attributionFailureCodes)[number];
const record = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object" && !Array.isArray(v);

export type AttributionDirection = "higher_is_anomaly" | "lower_is_anomaly";

export type AttributionPlayer = {
  playerId: string;
  /** 구성원 변수 이름(featureNames 순). 1개면 단독 참가자. */
  features: string[];
  /** 구성원별 실제값(저장 문자열 그대로). */
  actual: Record<string, string>;
  /** 구성원별 학습 데이터 중앙값(저장 number 그대로). */
  baseline: Record<string, number>;
  /** 구성원별 scale 1 여부. 저장값이 bool인 구성원만 들어 있다. */
  scaleIsUnit: Record<string, boolean>;
  phi: number;
  share: number | null;
};
export type AttributionCounterfactual = { replaced: string[]; scoreAfter: number; warningAfterReplacement: boolean };
export type AttributionSucceeded = {
  status: "succeeded";
  scoreActual: number;
  /** 같은 ML 방법 결과의 `score`(음수·동률 판단 tol 기준). 없거나 숫자가 아니면 null(그때는 scoreActual로 대체). */
  methodScore: number | null;
  threshold: string;
  direction: AttributionDirection;
  baselineWarning: boolean;
  players: AttributionPlayer[];
  topK: string[];
  counterfactual: AttributionCounterfactual[];
  clearedAtK: number | null;
};
export type Attribution =
  | { status: "absent" }
  | { status: "unreadable" }
  | { status: "skipped"; reason: AttributionReason | null }
  | { status: "failed"; failureCode: AttributionFailureCode | null }
  | AttributionSucceeded;

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const nonEmpty = (v: unknown): v is string => typeof v === "string" && v !== "";
const oneOf = <T extends string>(v: unknown, allowed: readonly T[]): T | null =>
  typeof v === "string" && (allowed as readonly string[]).includes(v) ? (v as T) : null;
/** 10진 표기(지수 포함) 문자열. `0x10`·`Infinity`·공백 등은 받지 않는다. */
const DECIMAL_TEXT = /^[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/;
const sameList = (a: string[], b: string[]) => a.length === b.length && a.every((x, i) => x === b[i]);

const readPlayer = (v: unknown): AttributionPlayer | null => {
  if (!record(v) || !nonEmpty(v.playerId) || !Array.isArray(v.features) || !v.features.length || !v.features.every(nonEmpty)) return null;
  const features = v.features as string[];
  if (new Set(features).size !== features.length || !record(v.actual) || !record(v.baseline) || !finite(v.phi)) return null;
  if (!(v.share === null || finite(v.share))) return null;
  const actualIn = v.actual;
  const baselineIn = v.baseline;
  if (!features.every((name) => nonEmpty(actualIn[name]) && finite(baselineIn[name]))) return null;
  const scaleIn = record(v.scaleIsUnit) ? v.scaleIsUnit : {};
  const scaleIsUnit: Record<string, boolean> = {};
  for (const name of features) if (typeof scaleIn[name] === "boolean") scaleIsUnit[name] = scaleIn[name] as boolean;
  return {
    playerId: v.playerId,
    features,
    actual: Object.fromEntries(features.map((name) => [name, actualIn[name] as string])),
    baseline: Object.fromEntries(features.map((name) => [name, baselineIn[name] as number])),
    scaleIsUnit,
    phi: v.phi,
    share: v.share as number | null,
  };
};

const readSucceeded = (a: Record<string, unknown>, methodScore: number | null): AttributionSucceeded | null => {
  const direction = oneOf(a.direction, ["higher_is_anomaly", "lower_is_anomaly"] as const);
  const threshold = nonEmpty(a.threshold) && DECIMAL_TEXT.test(a.threshold) ? a.threshold : null;
  if (!finite(a.scoreActual) || !threshold || !direction || typeof a.baselineWarning !== "boolean") return null;
  if (!Array.isArray(a.players) || !Array.isArray(a.topK) || !Array.isArray(a.counterfactual)) return null;
  const players = a.players.map(readPlayer);
  if (players.some((p) => p === null)) return null;
  const valid = players as AttributionPlayer[];
  const ids = new Set(valid.map((p) => p.playerId));
  if (ids.size !== valid.length) return null;
  const topK = a.topK;
  if (topK.length > 3 || !topK.every((id): id is string => typeof id === "string" && ids.has(id)) || new Set(topK).size !== topK.length) return null;
  if (a.counterfactual.length !== topK.length) return null;
  const counterfactual: AttributionCounterfactual[] = [];
  for (const [index, c] of a.counterfactual.entries()) {
    if (!record(c) || !Array.isArray(c.replaced) || !finite(c.scoreAfter) || typeof c.warningAfterReplacement !== "boolean") return null;
    const replaced = c.replaced.filter((id): id is string => typeof id === "string");
    if (replaced.length !== c.replaced.length || !sameList(replaced, topK.slice(0, index + 1))) return null;
    counterfactual.push({ replaced, scoreAfter: c.scoreAfter, warningAfterReplacement: c.warningAfterReplacement });
  }
  // clearedAtK = warningAfterReplacement=false가 처음 되는 k(없으면 null). 저장값이 이와 어긋나면 읽지 않는다.
  const firstCleared = counterfactual.findIndex((c) => !c.warningAfterReplacement);
  const expected = firstCleared < 0 ? null : firstCleared + 1;
  if (!(a.clearedAtK === null || (Number.isInteger(a.clearedAtK) && a.clearedAtK === expected)) || (a.clearedAtK === null && expected !== null)) return null;
  return { status: "succeeded", scoreActual: a.scoreActual, methodScore, threshold, direction, baselineWarning: a.baselineWarning, players: valid, topK, counterfactual,
    clearedAtK: expected };
};

/** ML 방법 결과 항목 → 기여도. `attribution` 키가 없으면 미계산(absent). 같은 항목의 `score`도 함께 읽는다(tol 기준). */
export const readAttribution = (method: Record<string, unknown>): Attribution => {
  if (!("attribution" in method) || method.attribution === undefined) return { status: "absent" };
  const a = method.attribution;
  if (!record(a) || a.version !== ATTRIBUTION_VERSION) return { status: "unreadable" };
  if (a.status === "skipped") return { status: "skipped", reason: oneOf(a.reason, attributionReasons) };
  if (a.status === "failed") return { status: "failed", failureCode: oneOf(a.failureCode, attributionFailureCodes) };
  if (a.status === "succeeded") return readSucceeded(a, finite(method.score) ? method.score : null) ?? { status: "unreadable" };
  return { status: "unreadable" };
};
