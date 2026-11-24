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
 * - 판정 상세에도 나오지 않는 내부 식별자(configVersionId·inputManifestHash)는 보이지 않는다(툴팁·타자 효과 글자 수에서도 제외)
 * - 기여도 사실(`ml_attribution`, 워커 explanation.py `attribution_fact`)은 워커가 만든 문장만 보인다(수치·내부 값은 숨김, attributionItems)
 */
export type FactView = { head: string; items: string[] };

const hiddenFactNames = new Set(["configVersionId", "inputManifestHash"]);
/** 화면에 보일 근거만(저장 순서 그대로). 모두 빠지면 빈 목록 — 근거 목록 없이 확인할 것만 보인다. */
export const shownFacts = <T extends { name: string }>(facts: T[]) => facts.filter((fact) => !hiddenFactNames.has(fact.name));

const factNames: Record<string, string> = {
  decisionId: "판정 ID",
  status: "검사 완료",
  ruleStatus: "수행 상태",
  mlStatus: "수행 상태",
  decisionCode: "판정 코드",
  anomalyScore: "이상 점수",
  rules: "Rule 판정 근거",
  models: "ML 판정 근거",
  ml_attribution: "기여도", // 워커가 기여도 저장값으로 만든 사실(explanation.py ATTRIBUTION_FACT)
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
  typeof value === "string" && Object.hasOwn(labels, value) ? labels[value] : briefValue(value);

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

/**
 * 기여도 사실의 항목 줄: ML 방법마다 워커 `ml_attribution_text.render`가 내는 순서(summary → notes[] → counterfactual → limitation)로
 * 문장 문자열만 그대로 옮긴다. scoreActual·threshold·direction·topK 등 수치·내부 값과 methodId 문자열은 보이지 않는다.
 * ML 방법이 둘 이상이면 방법마다 먼저 판정 상세의 방법 표시 이름(`머신러닝 모델(<key>)`) 한 줄. 모양이 어긋난 원소·문장은 건너뛴다.
 */
const ATTRIBUTION_FACT = "ml_attribution";
const sentence = (v: unknown): string[] => (typeof v === "string" && v ? [v] : []);
export const attributionItems = (value: unknown): string[] => {
  const methods = Array.isArray(value)
    ? value.flatMap((entry) => {
        if (!record(entry)) return [];
        const lines = [...sentence(entry.summary), ...(Array.isArray(entry.notes) ? entry.notes.flatMap(sentence) : []), ...sentence(entry.counterfactual),
          ...sentence(entry.limitation)];
        return lines.length ? [{ name: typeof entry.methodId === "string" ? methodLabel(entry.methodId) : null, lines }] : [];
      })
    : [];
  return methods.flatMap((method) => (methods.length > 1 && method.name ? [method.name, ...method.lines] : method.lines));
};

/**
 * 근거 한 건 → 제목 줄 + 항목 줄. 배열 값은 제목(이름)만 두고 항목마다 한 줄, 빈 배열은 `이름: —`.
 * 보일 문장이 하나도 없는 기여도 사실은 null(줄 자체를 두지 않음 — 내부 키가 드러나는 기본 축약으로 떨어지지 않게).
 */
export const factView = (fact: { name: string; value: unknown }, v4: boolean): FactView | null => {
  const head = Object.hasOwn(factNames, fact.name) ? factNames[fact.name] : fact.name;
  const { value } = fact;
  if (fact.name === ATTRIBUTION_FACT) {
    const items = attributionItems(value);
    return items.length ? { head, items } : null;
  }
  if (Array.isArray(value)) {
    if (!value.length) return { head: `${head}: ${DASH}`, items: [] };
    const item = fact.name === "rules" ? (entry: unknown) => ruleItem(entry, v4) : fact.name === "models" ? (entry: unknown) => modelItem(entry, v4) : briefValue;
    return { head, items: value.map(item) };
  }
  return { head: `${head}: ${scalarValue(fact.name, value)}`, items: [] };
};
