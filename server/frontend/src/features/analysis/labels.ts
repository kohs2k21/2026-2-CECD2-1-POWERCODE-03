import type { Category, CompletenessFilter, DecisionItem, ItemSignal, ItemStage, SignalFilter, Sort } from "./data/analysisApi";
import { readEvidenceV4 } from "./data/evidence";

/** 이상 분석 화면 문구(와이어프레임 #93 analysis 용어). */
export const categoryLabels: Record<Category, string> = {
  all: "전체",
  anomaly: "이상",
  in_progress: "판정 중·중간 판정",
  undetermined: "정상 여부 미확인",
  failed_replaced: "판정 실패·대체",
};
export const signalLabels: Record<ItemSignal, string> = {
  yes: "이상",
  no: "이상 신호 없음",
  unknown: "정상 여부 미확인",
  pending: "판정 중",
  failed: "판정 실패",
};
export const signalFilterLabels: Record<SignalFilter, string> = {
  all: "전체",
  yes: "이상",
  no: "이상 신호 없음",
  unknown: "정상 여부 미확인",
};
export const completenessFilterLabels: Record<CompletenessFilter, string> = {
  all: "전체",
  complete: "완료",
  partial: "부분",
  unavailable: "판정불가",
};
export const stageLabels: Record<ItemStage, string> = {
  pending: "판정 중",
  provisional: "중간 판정",
  updated: "근거 추가",
  final: "최종",
  replaced: "최종 · 이전 중간 판정 대체됨",
  failed: "판정 실패(최종 없음)",
};
export const sortLabels: Record<Sort, string> = { newest: "최신순", oldest: "과거순" };
export const runLabels: Record<string, string> = {
  succeeded: "완료",
  failed: "실패",
  not_ready: "미완료",
  skipped: "꺼짐",
};
/** 작업 상태 표시(와이어프레임 용어). 그 밖의 값(예: blocked)은 저장된 상태 코드 그대로. */
export const jobStatusLabels: Record<string, string> = {
  queued: "대기",
  running: "실행 중",
  succeeded: "완료",
  failed: "실패",
};
export const DASH = "—";
export const UNAVAILABLE = "— 확인 불가";

/** 검사 완료: 최종 판정 status. 최종 판정이 없으면 판정 중 / 최종 없음. */
export const completenessText = (item: DecisionItem) =>
  item.status ? completenessFilterLabels[item.status] : item.kind === "failed" ? "최종 없음" : "판정 중";

/**
 * 판정 결과 문구(와이어프레임 resultText): 이상 신호가 있으면 이상. evidence v4 등급 anomaly_sign이 저장된 판정만 이상 징후.
 * 등급은 저장값만 쓴다(v3에서 등급을 추정하지 않음).
 */
export const resultText = (item: DecisionItem) => {
  if (item.signal !== "yes") return signalLabels[item.signal];
  const grade = readEvidenceV4(item.evidence)?.grade;
  return grade === "anomaly_sign" ? "이상 징후" : signalLabels.yes;
};
export const resultVariant = (item: DecisionItem): "critical" | "warning" | "success" | "secondary" | "info" =>
  item.signal === "yes"
    ? readEvidenceV4(item.evidence)?.grade === "anomaly_sign"
      ? "warning"
      : "critical"
    : item.signal === "no"
      ? "success"
      : item.signal === "failed"
        ? "critical"
        : item.signal === "pending"
          ? "info"
          : "secondary";

const two = (n: number) => String(n).padStart(2, "0");
/** 월/일 시:분:초. 값이 없으면 —. */
export const stampText = (iso: string | null | undefined) => {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return DASH;
  return `${two(d.getMonth() + 1)}/${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
};

/**
 * 판정 방법 표시 이름(detection-decision.md #92 C18 대분류 이름, C22 승인 이상 규칙 문구). 원래 ID는 툴팁에 둔다(와이어프레임 "코드는 툴팁").
 * - `hard:<id>` → 이상 규칙(승인 문구), 승인 문구가 없는 하드룰은 `이상 규칙`
 * - `soft:*` → 이상 징후 규칙, `ml:<key>` → 머신러닝 모델(<key>)
 * - 알 수 없는 접두는 null(방법 부분을 생략)
 */
const hardRuleNames: Record<string, string> = {
  "hard:process-status": "상태가 S가 아님",
  "hard:negative-duration": "시작 시각이 종료 시각보다 늦음",
};
export const methodLabel = (methodId: string): string | null => {
  if (methodId.startsWith("hard:")) return hardRuleNames[methodId] ? `이상 규칙(${hardRuleNames[methodId]})` : "이상 규칙";
  if (methodId.startsWith("soft:")) return "이상 징후 규칙";
  if (methodId.startsWith("ml:") && methodId.length > 3) return `머신러닝 모델(${methodId.slice(3)})`;
  return null;
};
/** 방법 ID 목록 → 표시 이름 목록(중복 제거, 알 수 없는 접두 제외). */
export const methodLabels = (methodIds: string[]) => [...new Set(methodIds.flatMap((id) => methodLabel(id) ?? []))];
