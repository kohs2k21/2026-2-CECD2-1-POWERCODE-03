import { statusCodeLabels } from "@/features/analysis/labels";
import { HttpError } from "@/services/api/client";
import type {
  HardRule,
  HardRuleSeverity,
  HistoryDetail,
  HistoryItem,
  ProtocolRule,
  ProtocolRuleRequest,
  RelationKind,
  RelationRule,
  RelationRuleRequest,
  RuleSetChange,
  RuleSetChangeType,
  RuleSetRequest,
  RuleSetState,
  Severity,
  SeverityByCode,
  SoftRule,
  SoftRuleField,
  SoftRuleOperator,
  SoftRuleRequest,
  SoftRuleScope,
} from "./ruleSetApi";

/** 룰 설정 편집 초안과 화면 문구(와이어프레임 #93 룰 설정, rule-set.md v3 포함). 서버 호출 없음. */
export const HARD_TERM = "이상 규칙";
export const SOFT_TERM = "이상 징후 규칙";
/** 관계 규칙(relation-rules.md, rule-candidates-ui.md "관계 규칙 후보")·프로토콜 설정(결정 11/13 "프로토콜 설정"·"정상 프로토콜"). */
export const RELATION_TERM = "관계 규칙";
export const PROTOCOL_TERM = "프로토콜 설정";
export const PROTOCOL_ITEM_TERM = "정상 프로토콜";
/** 관계 규칙 종류 화면 이름(rule-candidates-ui.md 8절 용어 — candidateLabels.ts kindLabels와 같은 이름, 허용 분기 포함). */
export const relationKindLabels: Record<RelationKind, string> = {
  required_step: "필수 단계", precedes: "선행 관계", message_handoff: "메시지 인계", allowed_branch: "허용 분기",
};
/** 관계 규칙 경로 문구: 필수 단계는 단계 하나, 그 밖은 앞 → 뒤. */
export const relationPathText = (rule: { kind?: RelationKind; from: string | { processId: string }; to?: string | { processId: string } | null }) => {
  const from = typeof rule.from === "string" ? rule.from : rule.from.processId;
  const to = rule.to === null || rule.to === undefined ? null : typeof rule.to === "string" ? rule.to : rule.to.processId;
  return rule.kind === "required_step" || to === null ? from : `${from} → ${to}`;
};
export const operatorLabels: Record<SoftRuleOperator, string> = { gte: "≥", gt: ">", lte: "≤", lt: "<" };
export const operators = Object.keys(operatorLabels) as SoftRuleOperator[];
export const severityLabels: Record<Severity, string> = { critical: "위험", warning: "주의", info: "참고" };
export const severities: Severity[] = ["critical", "warning", "info"];
export const severityBadge: Record<Severity, "critical" | "warning" | "secondary"> = { critical: "critical", warning: "warning", info: "secondary" };

/** 코드 끝 글자와 뜻. 뜻이 없는 코드는 코드만 표시한다. */
const shortCode = (code: string) => code.split("_").at(-1) ?? code;
export const codeLabel = (code: string) => {
  const short = shortCode(code);
  return statusCodeLabels[short] ? `${short} ${statusCodeLabels[short]}` : short;
};

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
  /** 적용 범위(생성 후 불변). 기존 규칙은 저장값을 그대로 보낸다 — 빠뜨리면 서버가 전역으로 읽어 soft_rule_scope_immutable. */
  scope: SoftRuleScope;
};
export type RuleDraft = {
  base: RuleSetState;
  /** severity는 위험도를 바꿀 수 있는 규칙에만 있다(모양은 그 규칙의 내장 기본값과 같음). */
  hard: { id: string; enabled: boolean; severity?: HardRuleSeverity }[];
  soft: DraftSoftRule[];
  /** 관계 규칙: 조건은 승인 때 고정, 편집은 켜기·위험도만(rule-set.md v3.6). */
  relation: { id: string; enabled: boolean; severity: Severity }[];
  nextRef: number;
};

/** 위험도를 바꿀 수 있는 규칙이면 저장 요청에 위험도를 싣는다. */
const hardRequest = ({ id, enabled, severity, editable }: Pick<HardRule, "id" | "enabled" | "severity" | "editable">) =>
  ({ id, enabled, ...(editable.includes("severity") ? { severity } : {}) });
export const draftFromState = (state: RuleSetState): RuleDraft => ({
  base: state,
  hard: state.hardRules.map(hardRequest),
  soft: state.softRules.map(({ id, name, field, operator, threshold, severity, enabled, scope }) => ({
    key: id, id, name, field, operator, threshold: String(threshold), severity, enabled, scope: scope ?? null,
  })),
  relation: (state.relationRules ?? []).map(({ id, enabled, severity }) => ({ id, enabled, severity })),
  nextRef: 1,
});

/** 저장 요청 항목(서버 응답 항목 → 요청 키만). 관계 규칙 source는 새 항목에서만 받으므로 기존 항목은 보내지 않는다. */
export const softRequest = ({ id, name, field, operator, threshold, severity, enabled, scope }: SoftRule): SoftRuleRequest =>
  ({ id, name, field, operator, threshold, severity, enabled, scope: scope ?? null });
export const relationRequest = ({ id, kind, scope, from, to, match, severity, enabled }: RelationRule): RelationRuleRequest =>
  ({ id, kind, scope, from, to, match, severity, enabled });
export const protocolRequest = ({ id, interfaceId, paths, severity, enabled }: ProtocolRule): ProtocolRuleRequest =>
  ({ id, interfaceId, paths: paths.map(({ channels }) => ({ channels: [...channels] })), severity, enabled });

/**
 * 현재 운영 상태 그대로의 전체 저장 요청(rule-set.md 저장 = 전체 상태 치환). 프로토콜 설정·규칙 추천 승인처럼 룰 설정 편집 화면 밖에서 저장할 때
 * 이 요청에서 바꿀 배열만 바꿔 보낸다. 다른 규칙은 현재 값 그대로라 저장 결과 변경은 바꾼 항목뿐이다.
 */
export const requestFromState = (state: RuleSetState, confirmHardRuleDisable = false): RuleSetRequest => ({
  expectedRevision: state.revision,
  confirmHardRuleDisable,
  restoreFromRevision: null,
  note: null,
  hardRules: state.hardRules.map(hardRequest),
  softRules: state.softRules.map(softRequest),
  relationRules: (state.relationRules ?? []).map(relationRequest),
  protocolRules: (state.protocolRules ?? []).map(protocolRequest),
});

/** 저장 값 기준 비교: 이름은 앞뒤 공백 제외, 임계값은 숫자로. */
export const nameChanged = (rule: DraftSoftRule, base: { name: string }) => rule.name.trim() !== base.name;
export const thresholdChanged = (rule: DraftSoftRule, base: { threshold: number }) => rule.threshold.trim() === "" || Number(rule.threshold) !== base.threshold;
const softChanged = (rule: DraftSoftRule, base: RuleSetState["softRules"][number]) =>
  nameChanged(rule, base) || rule.operator !== base.operator || thresholdChanged(rule, base) ||
  rule.severity !== base.severity || rule.enabled !== base.enabled;

const byCodeOf = (severity: unknown) => (severity as Partial<SeverityByCode> | null | undefined)?.byCode;
/** 키 순서와 무관한 위험도 비교 값. */
const severityKey = (severity: unknown) => {
  const byCode = byCodeOf(severity);
  if (!byCode) return JSON.stringify(severity ?? null);
  return JSON.stringify([Object.keys(byCode).sort().map((code) => [code, byCode[code]]), (severity as SeverityByCode).default ?? null]);
};
export const severityChanged = (draft: HardRuleSeverity | undefined, base: HardRule["severity"]) => draft !== undefined && severityKey(draft) !== severityKey(base);

/** 변경 건수: 서버 `changes[]`와 같은 단위(이상 규칙은 켜기와 위험도가 각각 1건). */
export const draftChangeCount = (draft: RuleDraft) => {
  const hard = draft.hard.reduce((count, rule) => {
    const base = draft.base.hardRules.find((item) => item.id === rule.id);
    return count + (base?.enabled !== rule.enabled ? 1 : 0) + (base && severityChanged(rule.severity, base.severity) ? 1 : 0);
  }, 0);
  const kept = draft.soft.filter((rule) => rule.id !== null);
  const added = draft.soft.length - kept.length;
  const removed = draft.base.softRules.filter((rule) => !kept.some((item) => item.id === rule.id)).length;
  const edited = kept.filter((rule) => {
    const base = draft.base.softRules.find((item) => item.id === rule.id);
    return base ? softChanged(rule, base) : false;
  }).length;
  const relation = draft.relation.reduce((count, rule) => {
    const base = (draft.base.relationRules ?? []).find((item) => item.id === rule.id);
    return count + (base && base.enabled !== rule.enabled ? 1 : 0) + (base && base.severity !== rule.severity ? 1 : 0);
  }, 0);
  return hard + added + removed + edited + relation;
};

/** 저장 전 입력 확인(와이어프레임 ruleIssues). 그 밖의 검사는 서버가 코드로 돌려준다. */
export const draftIssues = (draft: RuleDraft, fieldName: (field: string) => string) =>
  draft.soft.flatMap((rule) => [
    ...(rule.name.trim() ? [] : [`${fieldName(rule.field)} 규칙: 이름을 입력해 주세요`]),
    ...(rule.threshold.trim() !== "" && Number.isFinite(Number(rule.threshold)) ? [] : [`${rule.name || fieldName(rule.field)}: 임계값을 숫자로 입력해 주세요`]),
  ]);

/** 룰 설정 화면 저장: 이상 규칙·이상 징후 규칙·관계 규칙(켜기·위험도)은 초안, 프로토콜 항목은 현재 값 그대로(프로토콜 설정 화면에서 편집). */
export const saveRequest = (draft: RuleDraft, confirmHardRuleDisable: boolean): RuleSetRequest => ({
  ...requestFromState(draft.base, confirmHardRuleDisable),
  hardRules: draft.hard,
  softRules: draft.soft.map(({ key, id, name, field, operator, threshold, severity, enabled, scope }) => ({
    id, ...(id === null ? { clientRef: key } : {}), name: name.trim(), field, operator, threshold: Number(threshold), severity, enabled, scope: scope ?? null,
  })),
  relationRules: (draft.base.relationRules ?? []).flatMap((rule) => {
    const edited = draft.relation.find((item) => item.id === rule.id);
    return edited ? [{ ...relationRequest(rule), enabled: edited.enabled, severity: edited.severity }] : [];
  }),
});
/** 되돌리기 본문: 그 리비전 스냅샷 값 그대로(rule-set.md). 서버가 스냅샷과 비교한다. v1·v2 스냅샷은 관계·프로토콜 항목이 없다(= 현재 항목 삭제). */
export const restoreRequest = (detail: HistoryDetail, state: RuleSetState, confirmHardRuleDisable: boolean): RuleSetRequest => ({
  expectedRevision: state.revision,
  confirmHardRuleDisable,
  restoreFromRevision: detail.revision,
  note: null,
  // 위험도는 편집 가능한 규칙만 보낸다. 이전 methodVersion 출처의 값은 서버가 무시하고 내장 기본값으로 채운다.
  hardRules: detail.snapshot.hardRules.map(({ id, enabled, severity }) => {
    const editable = state.hardRules.find((rule) => rule.id === id)?.editable.includes("severity");
    return { id, enabled, ...(editable && severity !== undefined ? { severity: severity as HardRuleSeverity } : {}) };
  }),
  softRules: detail.snapshot.softRules.map(softRequest),
  relationRules: (detail.snapshot.relationRules ?? []).map(relationRequest),
  protocolRules: (detail.snapshot.protocolRules ?? []).map(protocolRequest),
});

export const numberLabel = (value: unknown) =>
  Number(value).toLocaleString("ko-KR", { maximumFractionDigits: 20 });
const KIND: Record<RuleSetChangeType, [string, string]> = {
  hard_enabled: [HARD_TERM, "켜기"], hard_disabled: [HARD_TERM, "끄기"], hard_updated: [HARD_TERM, "수정"], soft_added: [SOFT_TERM, "추가"], soft_updated: [SOFT_TERM, "수정"],
  soft_removed: [SOFT_TERM, "삭제"], soft_enabled: [SOFT_TERM, "켜기"], soft_disabled: [SOFT_TERM, "끄기"],
  relation_added: [RELATION_TERM, "추가"], relation_removed: [RELATION_TERM, "삭제"], relation_updated: [RELATION_TERM, "수정"],
  relation_enabled: [RELATION_TERM, "켜기"], relation_disabled: [RELATION_TERM, "끄기"],
  protocol_added: [PROTOCOL_ITEM_TERM, "추가"], protocol_removed: [PROTOCOL_ITEM_TERM, "삭제"], protocol_updated: [PROTOCOL_ITEM_TERM, "수정"],
  protocol_enabled: [PROTOCOL_ITEM_TERM, "켜기"], protocol_disabled: [PROTOCOL_ITEM_TERM, "끄기"],
};
/** 경로 표시: 채널을 화살표로 잇는다(원래 값 그대로, 쪼개 읽지 않음). */
export const channelChainText = (channels: readonly string[]) => channels.join(" → ");
const diffLabel: Record<string, string> = { name: "이름", operator: "조건", threshold: "임계값", severity: "위험도", enabled: "켜기" };
const diffValue = (key: string, value: unknown) => {
  if (key === "operator") return operatorLabels[value as SoftRuleOperator] ?? String(value);
  if (key === "threshold") return numberLabel(value);
  if (key === "severity") return severityLabels[value as Severity] ?? String(value);
  if (key === "enabled") return value ? "켜짐" : "꺼짐";
  return String(value);
};
/** 코드별 위험도는 바뀐 코드만 쓴다. 기본 단계(`default`)가 바뀌면 함께 쓴다. */
const severityDiff = (before: unknown, after: unknown) => {
  const [from, to] = [byCodeOf(before) ?? {}, byCodeOf(after) ?? {}];
  const codes = [...new Set([...Object.keys(from), ...Object.keys(to)])].filter((code) => from[code] !== to[code])
    .map((code) => `위험도 ${codeLabel(code)} ${severityLabels[from[code]] ?? "—"} → ${severityLabels[to[code]] ?? "—"}`);
  const [defaultBefore, defaultAfter] = [(before as SeverityByCode | null)?.default, (after as SeverityByCode | null)?.default];
  if (defaultBefore !== defaultAfter) codes.push(`기본 위험도 ${severityLabels[defaultBefore!] ?? "—"} → ${severityLabels[defaultAfter!] ?? "—"}`);
  return codes.join(", ");
};
const pathsText = (paths: unknown) => (Array.isArray(paths) ? paths.map((channels) => channelChainText(channels as string[])).join(" / ") : "");
const diffText = (change: RuleSetChange) => {
  if (change.type === "protocol_updated") {
    const diff = change.diff as { pathsAdded?: string[][]; pathsRemoved?: string[][]; severity?: [Severity, Severity] } | undefined;
    return [
      diff?.pathsAdded?.length ? `허용 경로 추가 ${pathsText(diff.pathsAdded)}` : null,
      diff?.pathsRemoved?.length ? `허용 경로 삭제 ${pathsText(diff.pathsRemoved)}` : null,
      diff?.severity ? `위험도 ${severityLabels[diff.severity[0]]} → ${severityLabels[diff.severity[1]]}` : null,
    ].filter(Boolean).join(", ");
  }
  return Object.entries(change.diff ?? {}).map(([key, value]) => {
    const [before, after] = value as [unknown, unknown];
    return byCodeOf(after) ? severityDiff(before, after) : `${diffLabel[key] ?? key} ${diffValue(key, before)} → ${diffValue(key, after)}`;
  }).join(", ");
};
const changeName = (change: RuleSetChange) => {
  if (change.type.startsWith("relation_") && change.from !== undefined) {
    return `${change.kind ? relationKindLabels[change.kind] : ""} ${relationPathText({ kind: change.kind, from: change.from, to: change.to })}`.trim();
  }
  if (change.type.startsWith("protocol_") && change.interfaceId) return change.interfaceId;
  return change.displayName ?? change.name ?? change.id ?? change.clientRef ?? "";
};

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

/** 위험도 묶음(위험 → 주의 → 참고): 단일 값이면 코드 없음, byCode면 그 단계의 결과 코드. 코드가 늘어도 자동으로 묶인다. 기본 단계는 따로(defaultLevel). */
export const severityGroups = (severity: HardRule["severity"]): { level: Severity; codes: string[] }[] => {
  if (typeof severity === "string") return [{ level: severity, codes: [] }];
  const order: Severity[] = ["critical", "warning", "info"];
  return order.flatMap((level) => {
    const codes = Object.keys(severity.byCode).filter((code) => severity.byCode[code] === level);
    return codes.length ? [{ level, codes }] : [];
  });
};
/** `{byCode, default}` 모양의 기본 단계(등록되지 않은 코드). 없으면 null. */
export const defaultLevel = (severity: HardRule["severity"]) => (typeof severity === "string" ? null : severity.default ?? null);

export const softCondition = (rule: { field: string; operator: SoftRuleOperator; threshold: number | string }, field: SoftRuleField | undefined) =>
  `${field?.displayName ?? rule.field} ${operatorLabels[rule.operator]} ${numberLabel(rule.threshold)}${field ? ` ${field.unit}` : ""}`;

export const errorCode = (error: unknown) => (error instanceof HttpError ? (error.code ?? `http_${error.status}`) : error instanceof Error ? error.message : "request_failed");
