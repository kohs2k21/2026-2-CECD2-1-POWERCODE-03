import { record } from "./data/analysisApi";
import {
  DASH,
  completenessFilterLabels,
  methodLabel,
  resultCodeLabel,
  riskLevelLabels,
  ruleKeyLabel,
  ruleResultLabels,
  runLabels,
} from "./labels";

/**
 * AI 판정 근거 요약의 저장 근거(facts) 표시. facts는 설명 작업 입력 savedEvidence의 최상위 키 그대로다
 * (API jobRoutes explain 접수: decisionId·configVersionId·status·ruleStatus·mlStatus·decisionCode·anomalyScore·inputManifestHash·rules·models,
 * 워커 explanation.py `facts_for`). 표시만 바꾸고 값은 다시 계산하지 않는다.
 * - 이름·값은 판정 상세에 이미 있는 이름표(판정 요약·Rule/ML 판정 근거 표·세부 방법별 결과)를 쓴다. 원래 키는 툴팁
 * - 이름표가 없는 키·값은 원래 값 그대로. 객체·배열은 JSON 대신 `키 값 · 키 값` 축약(값 없는 키는 생략)
 * - 배열 값(rules·models 등)은 항목마다 한 줄
 */
export type FactView = { head: string; items: string[] };

const factNames: Record<string, string> = {
  decisionId: "판정 ID",
  status: "검사 완료",
  ruleStatus: "수행 상태",
  mlStatus: "수행 상태",
  decisionCode: "판정 코드",
  anomalyScore: "이상 점수",
  rules: "Rule 판정 근거",
  models: "ML 판정 근거",
};

/** 이름표 없는 값의 읽을 수 있는 축약. 문자열은 그대로, 값 없음은 —. */
export const briefValue = (value: unknown): string => {
  if (value === null || value === undefined) return DASH;
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.length ? value.map(briefValue).join(", ") : DASH;
  if (record(value)) {
    const entries = Object.entries(value).filter(([, item]) => item !== null && item !== undefined);
    if (!entries.length) return DASH;
    return entries.map(([key, item]) => `${key} ${Array.isArray(item) || record(item) ? `(${briefValue(item)})` : briefValue(item)}`).join(" · ");
  }
  return String(value);
};

const label = (labels: Record<string, string>, value: unknown) =>
  typeof value === "string" && value in labels ? labels[value] : briefValue(value);

/** Rule 판정 근거 한 줄: `방법 · 결과 · 상태 F · 위험도 위험`(+ 사유 코드). 이상일 때만 위험도(세부 방법별 결과 표와 같음). */
const ruleItem = (entry: unknown, v4: boolean) => {
  if (!record(entry) || typeof entry.ruleKey !== "string" || !entry.ruleKey) return briefValue(entry);
  const parts = [ruleKeyLabel(entry.ruleKey, v4)];
  if (entry.result !== null && entry.result !== undefined) parts.push(label(ruleResultLabels, entry.result));
  if (typeof entry.observedStatus === "string" && entry.observedStatus) parts.push(`상태 ${entry.observedStatus}`);
  if (entry.result === "hit" && entry.severity !== null && entry.severity !== undefined) parts.push(`위험도 ${label(riskLevelLabels, entry.severity)}`);
  if (typeof entry.reason === "string" && entry.reason) parts.push(entry.reason);
  return parts.join(" · ");
};

/** ML 판정 근거 한 줄: `모델 · 이상|정상 확인|미판정`(판정 상세 ML 판정 근거 표, v4는 세부 방법별 결과의 방법 이름). */
const modelItem = (entry: unknown, v4: boolean) => {
  if (!record(entry) || typeof entry.modelKey !== "string" || !entry.modelKey) return briefValue(entry);
  const name = v4 ? (methodLabel(`ml:${entry.modelKey}`) as string) : entry.modelKey;
  const result = entry.anomalous === true ? "이상" : entry.anomalous === false ? "정상 확인" : "미판정";
  return [name, result, ...(typeof entry.reason === "string" && entry.reason ? [entry.reason] : [])].join(" · ");
};

const scalarValue = (name: string, value: unknown) => {
  switch (name) {
    case "status":
      return typeof value === "string" && value !== "all" ? label(completenessFilterLabels, value) : briefValue(value);
    case "ruleStatus":
      return `Rule ${label(runLabels, value)}`;
    case "mlStatus":
      return `ML ${label(runLabels, value)}`;
    case "decisionCode":
      return typeof value === "string" && value ? resultCodeLabel(value) : briefValue(value);
    default:
      return briefValue(value);
  }
};

/** 근거 한 건 → 제목 줄 + 항목 줄. 배열 값은 제목(이름)만 두고 항목마다 한 줄, 빈 배열은 `이름: —`. */
export const factView = (fact: { name: string; value: unknown }, v4: boolean): FactView => {
  const head = factNames[fact.name] ?? fact.name;
  const { value } = fact;
  if (Array.isArray(value)) {
    if (!value.length) return { head: `${head}: ${DASH}`, items: [] };
    const item = fact.name === "rules" ? (entry: unknown) => ruleItem(entry, v4) : fact.name === "models" ? (entry: unknown) => modelItem(entry, v4) : briefValue;
    return { head, items: value.map(item) };
  }
  return { head: `${head}: ${scalarValue(fact.name, value)}`, items: [] };
};
