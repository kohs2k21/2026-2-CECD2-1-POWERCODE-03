import { HttpError } from "@/services/api/client";
import type {
  HardRule,
  HistoryDetail,
  HistoryItem,
  RuleSetChange,
  RuleSetRequest,
  RuleSetState,
  Severity,
  SoftRuleField,
  SoftRuleOperator,
} from "./ruleSetApi";

/** 룰 설정 편집 초안과 화면 문구(와이어프레임 #93 룰 설정, rule-set.md). 서버 호출 없음. */
export const HARD_TERM = "이상 규칙";
export const SOFT_TERM = "이상 징후 규칙";
export const operatorLabels: Record<SoftRuleOperator, string> = { gte: "≥", gt: ">", lte: "≤", lt: "<" };
export const operators = Object.keys(operatorLabels) as SoftRuleOperator[];
export const severityLabels: Record<Severity, string> = { critical: "위험", warning: "주의", info: "참고" };
export const severities: Severity[] = ["info", "warning", "critical"];
export const severityBadge: Record<Severity, "critical" | "warning" | "secondary"> = { critical: "critical", warning: "warning", info: "secondary" };

export type DraftSoftRule = {
  /** 화면 키: 기존 규칙은 id, 새 규칙은 clientRef. */
  key: string;
  id: string | null;
  name: string;
  field: string;
  operator: SoftRuleOperator;
  /** 입력칸 값(문자열). 저장할 때 숫자로 바꾼다. */
  threshold: string;
  severity: Severity;
  enabled: boolean;
};
export type RuleDraft = {
  base: RuleSetState;
  hard: { id: string; enabled: boolean }[];
  soft: DraftSoftRule[];
  nextRef: number;
};

export const draftFromState = (state: RuleSetState): RuleDraft => ({
  base: state,
  hard: state.hardRules.map(({ id, enabled }) => ({ id, enabled })),
  soft: state.softRules.map(({ id, name, field, operator, threshold, severity, enabled }) => ({
    key: id, id, name, field, operator, threshold: String(threshold), severity, enabled,
  })),
  nextRef: 1,
});

/** 저장 값 기준 비교: 이름은 앞뒤 공백 제외, 임계값은 숫자로. */
export const nameChanged = (rule: DraftSoftRule, base: { name: string }) => rule.name.trim() !== base.name;
export const thresholdChanged = (rule: DraftSoftRule, base: { threshold: number }) => rule.threshold.trim() === "" || Number(rule.threshold) !== base.threshold;
const softChanged = (rule: DraftSoftRule, base: RuleSetState["softRules"][number]) =>
  nameChanged(rule, base) || rule.operator !== base.operator || thresholdChanged(rule, base) ||
  rule.severity !== base.severity || rule.enabled !== base.enabled;

/** 변경 건수: 규칙 하나당 1건(서버 `changes[]`와 같은 단위). */
export const draftChangeCount = (draft: RuleDraft) => {
  const hard = draft.hard.filter((rule) => draft.base.hardRules.find((item) => item.id === rule.id)?.enabled !== rule.enabled).length;
  const kept = draft.soft.filter((rule) => rule.id !== null);
  const added = draft.soft.length - kept.length;
  const removed = draft.base.softRules.filter((rule) => !kept.some((item) => item.id === rule.id)).length;
  const edited = kept.filter((rule) => {
    const base = draft.base.softRules.find((item) => item.id === rule.id);
    return base ? softChanged(rule, base) : false;
  }).length;
  return hard + added + removed + edited;
};

/** 저장 전 입력 확인(와이어프레임 ruleIssues). 그 밖의 검사는 서버가 코드로 돌려준다. */
export const draftIssues = (draft: RuleDraft, fieldName: (field: string) => string) =>
  draft.soft.flatMap((rule) => [
    ...(rule.name.trim() ? [] : [`${fieldName(rule.field)} 규칙: 이름을 입력해 주세요`]),
    ...(rule.threshold.trim() !== "" && Number.isFinite(Number(rule.threshold)) ? [] : [`${rule.name || fieldName(rule.field)}: 임계값을 숫자로 입력해 주세요`]),
  ]);

export const saveRequest = (draft: RuleDraft, confirmHardRuleDisable: boolean): RuleSetRequest => ({
  expectedRevision: draft.base.revision,
  confirmHardRuleDisable,
  restoreFromRevision: null,
  note: null,
  hardRules: draft.hard,
  softRules: draft.soft.map(({ key, id, name, field, operator, threshold, severity, enabled }) => ({
    id, ...(id === null ? { clientRef: key } : {}), name: name.trim(), field, operator, threshold: Number(threshold), severity, enabled,
  })),
});
/** 되돌리기 본문: 그 리비전 스냅샷 값 그대로(rule-set.md). 서버가 스냅샷과 비교한다. */
export const restoreRequest = (detail: HistoryDetail, expectedRevision: number, confirmHardRuleDisable: boolean): RuleSetRequest => ({
  expectedRevision,
  confirmHardRuleDisable,
  restoreFromRevision: detail.revision,
  note: null,
  hardRules: detail.snapshot.hardRules.map(({ id, enabled }) => ({ id, enabled })),
  softRules: detail.snapshot.softRules.map(({ id, name, field, operator, threshold, severity, enabled }) => ({ id, name, field, operator, threshold, severity, enabled })),
});

export const numberLabel = (value: unknown) =>
  Number(value).toLocaleString("ko-KR", { maximumFractionDigits: 20 });
const KIND: Record<RuleSetChange["type"], [string, string]> = {
  hard_enabled: [HARD_TERM, "켜기"], hard_disabled: [HARD_TERM, "끄기"], soft_added: [SOFT_TERM, "추가"], soft_updated: [SOFT_TERM, "수정"],
  soft_removed: [SOFT_TERM, "삭제"], soft_enabled: [SOFT_TERM, "켜기"], soft_disabled: [SOFT_TERM, "끄기"],
};
const diffLabel: Record<string, string> = { name: "이름", operator: "조건", threshold: "임계값", severity: "위험도", enabled: "켜기" };
const diffValue = (key: string, value: unknown) => {
  if (key === "operator") return operatorLabels[value as SoftRuleOperator] ?? String(value);
  if (key === "threshold") return numberLabel(value);
  if (key === "severity") return severityLabels[value as Severity] ?? String(value);
  if (key === "enabled") return value ? "켜짐" : "꺼짐";
  return String(value);
};
const diffText = (change: RuleSetChange) =>
  Object.entries(change.diff ?? {}).map(([key, [before, after]]) => `${diffLabel[key] ?? key} ${diffValue(key, before)} → ${diffValue(key, after)}`).join(", ");
const changeName = (change: RuleSetChange) => change.displayName ?? change.name ?? change.id ?? "";

export const changeLine = (change: RuleSetChange) => {
  const [category, kind] = KIND[change.type];
  return `${category} ${kind} · ${changeName(change)}${change.diff ? ` · ${diffText(change)}` : ""}`;
};
/** 변경 기록 요약(와이어프레임 changeSummary). r0은 최초 적용, 되돌리기는 출처를 앞에 쓴다. */
export const historySummary = (item: HistoryItem) => {
  const prefix = item.restoredFromRevision === null ? "" : `버전 ${item.restoredFromRevision} 상태로 되돌림 · `;
  if (!item.changes.length) return `${prefix}${item.revision === 0 ? "최초 적용" : "변경 없음"}`;
  const groups = new Map<string, string[]>();
  for (const change of item.changes) {
    const [category, kind] = KIND[change.type];
    const key = `${category} ${kind}`;
    groups.set(key, [...(groups.get(key) ?? []), `${changeName(change)}${change.diff ? `(${diffText(change)})` : ""}`]);
  }
  return prefix + [...groups].map(([key, names]) => `${key} ${names.length}건: ${names.join(", ")}`).join(" · ");
};

/** 위험도 표시 묶음: 단일 값, 또는 byCode를 단계별로 묶은 코드(`PROCESS_STATUS_F` → `F`). */
export const severityGroups = (severity: HardRule["severity"]): { level: Severity; codes: string[] }[] => {
  if (typeof severity === "string") return [{ level: severity, codes: [] }];
  const order: Severity[] = ["critical", "warning", "info"];
  return order.flatMap((level) => {
    const codes = Object.entries(severity.byCode).filter(([, value]) => value === level).map(([code]) => code.split("_").at(-1) ?? code);
    return codes.length ? [{ level, codes }] : [];
  });
};

export const softCondition = (rule: { field: string; operator: SoftRuleOperator; threshold: number | string }, field: SoftRuleField | undefined) =>
  `${field?.displayName ?? rule.field} ${operatorLabels[rule.operator]} ${numberLabel(rule.threshold)}${field ? ` ${field.unit}` : ""}`;

export const errorCode = (error: unknown) => (error instanceof HttpError ? (error.code ?? `http_${error.status}`) : error instanceof Error ? error.message : "request_failed");
