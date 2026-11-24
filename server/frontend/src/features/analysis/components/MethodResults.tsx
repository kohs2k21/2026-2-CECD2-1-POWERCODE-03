import { Fragment } from "react";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { pathReasonLabels, pathReasonText } from "@/features/detection/rules/protocols/protocolLabels";
import { reasonLabels as relationReasonLabels } from "@/features/detection/rules/candidates/candidateLabels";
import { evidenceGroups, type EvidenceV4, type MethodCategory, type MethodResult, type RelationNote } from "../data/evidence";
import {
  DASH,
  RELATION_HELD,
  RELATION_VIOLATION,
  categoryLabels,
  evidenceGroupLabels,
  handoffLabels,
  methodLabel,
  protocolHitLabels,
  relationKindLabel,
  resultCodeLabel,
  responseCodeText,
  riskLevelLabels,
  runLabels,
  signalLabels,
} from "../labels";
import { MlAttribution } from "./MlAttribution";

/**
 * 종합 근거(대분류) + 관계 확인 보류 + 세부 방법별 결과(와이어프레임 dCats·dMethods). evidence v4 저장값만 읽는다(재계산 없음, detection-decision.md D7).
 * 정책 값(k·deadlineMs·composite.policy)은 보이지 않는다(C23·사용자 9.8). 방법은 표시 이름, 방법 ID는 툴팁.
 * 세부 방법은 근거 구분(내장 이상 규칙 → 관계 규칙 → 정상 프로토콜 → 이상 징후 규칙 → 머신러닝 모델) 순으로 묶는다. 관계 규칙 위반은 결과 "규약 위반"(relation-rules.md 1절).
 * 머신러닝 모델 행 아래에는 저장된 기여도(`attribution`)를 한 줄 더 둔다(목록 화면에는 없다). 키가 있으면 상태와 무관하게 보이고,
 * 키가 없으면(`기여도 미계산`) 최종 판정의 실행 상태 완료(succeeded) ML 행에만 둔다 — 중간 판정 기록·미완료·실패 행은 키가 없어도 줄이 없다.
 * relationNotes의 수집 미완료 보류(`held`)는 종합 판정에 들지 않지만 "관계 확인 보류"로 함께 보인다(relation-rules.md 5절 위험 — 보류가 정상으로 묻히지 않게).
 */
export const categoryNames: Record<MethodCategory, string> = { hard: "이상 규칙", soft: "이상 징후 규칙", ml: "머신러닝 모델" };
const categoryOrder: MethodCategory[] = ["hard", "soft", "ml"];
/**
 * 종합 근거 설명 문단(와이어프레임 dCatsNote 문구 그대로). 용어는 화면 표의 대분류 이름·판정 결과 문구에서 가져온다.
 * 종합 규칙은 detection-decision.md 등급 표(hard T → 이상, soft·ml만 T → 이상 징후, 켠 대분류 모두 N → 정상, 그 밖 미확인)와 같다.
 */
export const categoryNote =
  `세부 방법 → 대분류 → 종합. 대분류마다 결과를 정한 뒤 종합합니다. ${categoryNames.hard} 대분류가 이상이면 ${categoryLabels.anomaly}, ` +
  `${categoryNames.soft}·${categoryNames.ml} 대분류만 이상이면 ${categoryLabels.anomaly_sign}, 켠 대분류가 모두 정상 확인일 때만 ${signalLabels.no}, 그 밖은 ${signalLabels.unknown}.`;
/** 와이어프레임 resultLabel(T/N/U/·) */
const pending = (m: MethodResult) => m.status === "not_ready" && m.cause === "pending";
const resultText = (m: MethodResult) => (m.anomalous === true ? (m.group === "relation" ? RELATION_VIOLATION : "이상") : m.anomalous === false ? "정상 확인" : pending(m) ? DASH : "미판정");
/** 소프트룰 비교 연산자(와이어프레임 OPS ≥ > ≤ <). */
const operators: Record<string, string> = { gte: "≥", gt: ">", lte: "≤", lt: "<" };
/** 이상 규칙 조건 문구(detection-decision.md 4.3 conditionText, C22 승인). */
const hardConditions: Record<string, string> = {
  "hard:process-status": "STATUS ≠ S",
  "hard:negative-duration": "START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음)",
  "hard:success-with-error": "STATUS = S, ERROR_COUNT > 0",
  "hard:count-conservation": "TOTAL_COUNT ≠ SUCCESS_COUNT + ERROR_COUNT",
  // 룰 세트 v3 내장 규칙 conditionText(Detector hard_rules.py — 룰 설정 화면이 서버 값으로 보이는 문장)
  "hard:transaction-failed": "거래 STATUS = F, 응답 코드가 0000·S·없음이 아님",
  "hard:unregistered-interface": "프로토콜 설정에 없는 인터페이스의 거래(켜진 프로토콜 항목이 1개 이상일 때만)",
};
/** 관계 규칙 방법 표시: `관계 규칙(선행 관계)`(kind는 규칙 추천과 같은 이름), kind를 모르면 `관계 규칙`. */
const methodName = (m: MethodResult) => {
  const kind = m.group === "relation" ? relationKindLabel(m.relationKind) : null;
  return kind ? `${evidenceGroupLabels.relation}(${kind})` : (methodLabel(m.methodId) ?? DASH);
};
/** 관계 규칙 위반 기록(Detector relation_rules.py evidence) → 규칙 추천 위반 사유 이름(rule-candidates-ui.md 8절)·인계 비교 이름. */
const violationNames: Record<string, string> = {
  start_recorded: relationReasonLabels.successor_is_start_step,
  other_predecessor: relationReasonLabels.other_predecessor,
  predecessor_missing: relationReasonLabels.predecessor_missing,
};
const relationHit = (m: MethodResult) => {
  if (m.hit.violation) return violationNames[m.hit.violation] ?? m.hit.violation;
  if (m.relationKind === "required_step" && m.hit.present === false) return relationReasonLabels.step_absent;
  if (m.relationKind === "message_handoff" && m.hit.handoffResults.includes("mismatch")) return handoffLabels.mismatch;
  return DASH;
};
/** 미판정 사유 표시 이름(와이어프레임 REASON_LABEL). 그 밖의 원인 코드는 코드 그대로. */
const causeLabels: Record<string, string> = { timeout: "기한 초과" };
const withUnit = (value: string, unit: string | null) => (unit ? `${value} ${unit}` : value);
const number = (value: number) => value.toLocaleString("ko-KR", { maximumFractionDigits: 4 });

const measured = (m: MethodResult, observedStatus: string | null) => {
  if (pending(m)) return DASH;
  if (m.category === "soft" && m.observedValue !== null)
    return withUnit(`${m.condition?.field ? `${m.condition.field} ` : ""}${m.observedValue}`, m.condition?.unit ?? null);
  if (m.category === "ml") {
    const raw = m.scoreDetail?.raw ?? m.score;
    if (raw === null) return DASH;
    // 와이어프레임 mlMeasure: `원점수 0.61 · 판정선 대비 1.20배`(저장된 threshold_ratio만)
    return `원점수 ${number(raw)}${m.scoreDetail?.ratio != null ? ` · 판정선 대비 ${m.scoreDetail.ratio.toFixed(2)}배` : ""}`;
  }
  if (m.methodId === "hard:process-status" && observedStatus) return `상태 ${observedStatus}`;
  if (m.anomalous !== true) return DASH;
  if (m.group === "relation") return relationHit(m);
  if (m.group === "protocol" && m.code && protocolHitLabels[m.code]) return protocolHitLabels[m.code];
  if (m.methodId === "hard:transaction-failed" && m.hit.responseCode) return responseCodeText(m.hit.responseCode, null);
  return m.code ? resultCodeLabel(m.code) : DASH;
};
const criterion = (m: MethodResult) => {
  if (m.category === "soft" && m.condition?.threshold)
    return withUnit(`${m.condition.field ?? ""} ${operators[m.condition.operator ?? ""] ?? m.condition.operator ?? ""} ${m.condition.threshold}`.trim(), m.condition.unit);
  if (m.category === "ml") return m.scoreDetail?.threshold ? `판정선 ${m.scoreDetail.threshold}` : DASH;
  // 관계 규칙·프로토콜 항목의 조건은 판정 evidence에 없다(룰 세트 스냅샷에만) — 내부 methodVersion 대신 —.
  if (m.group === "relation" || m.methodId.startsWith("hard:proto-")) return DASH;
  return hardConditions[m.methodId] ?? m.methodVersion ?? DASH;
};
/** 위험도 단계(와이어프레임 levelCell): 이상이 아니면 —, 이상인데 단계가 없으면(ML) 단계 없음. */
const levelText = (m: MethodResult) =>
  m.anomalous !== true ? DASH : m.riskLevel && m.riskLevel in riskLevelLabels ? riskLevelLabels[m.riskLevel as keyof typeof riskLevelLabels] : "단계 없음";
/** 세부 사유: 프로토콜 방법은 경로 사유 이름(protocol-rules.md 3.4, 프로토콜 설정 화면과 같은 이름), 그 밖은 코드 그대로. */
const reasonName = (m: MethodResult, reason: string) => (m.group === "protocol" ? pathReasonText(reason) : reason);
/** 미판정 원인 + 세부 사유(`capacity · graph_truncated` — relation-rules.md 9절 "cause 이름과 세부 사유를 함께"). 원래 코드는 툴팁. */
const reasonCell = (m: MethodResult): { text: string; title?: string } => {
  if (pending(m)) return { text: DASH };
  const details = m.reasons.map((reason) => reasonName(m, reason));
  const raw = m.cause ? [m.cause, m.failureCode, ...m.reasons].filter(Boolean).join(" · ") : m.reasons.join(", ");
  const shown = m.cause
    ? [causeLabels[m.cause] ?? [m.cause, m.failureCode].filter(Boolean).join(" · "), ...details].join(" · ")
    : details.join(", ");
  if (!shown) return { text: DASH };
  return shown === raw ? { text: shown } : { text: shown, title: raw };
};
/** 관계 확인 보류 방법 이름: 관계 규칙 kind / 거래 실패 규칙(`kind: "transaction"`) / 정상 프로토콜. 규칙 id는 툴팁. */
const heldMethod = (note: RelationNote) => {
  const kind = relationKindLabel(note.kind);
  if (kind) return `${evidenceGroupLabels.relation}(${kind})`;
  if (note.kind === "transaction") return methodLabel("hard:transaction-failed") as string;
  if (note.kind === "protocol") return evidenceGroupLabels.protocol;
  return note.kind ?? DASH;
};
/** 관계 확인 보류 사유: 기다리는 기록(waitingFor) → 규칙 추천·프로토콜 설정의 기존 사유 이름 + 단계 ID. 원래 reason 코드는 툴팁. */
const heldReason = (note: RelationNote) => {
  const waiting = note.waitingFor;
  if (!waiting) return note.reason ?? DASH;
  if (waiting.layer === "T") return pathReasonLabels.no_transaction_row;
  const name =
    waiting.direction === "IN"
      ? relationReasonLabels.no_in_message
      : waiting.direction === "OUT"
        ? relationReasonLabels.no_out_message
        : relationReasonLabels.predecessor_missing;
  return waiting.processId ? `${name} · ${waiting.processId}` : name;
};
const groupRank = (m: MethodResult) => evidenceGroups.indexOf(m.group);
const runText = (m: MethodResult) => (pending(m) ? "실행 중" : m.status ? (runLabels[m.status] ?? m.status) : DASH);
/** 대분류 결과(와이어프레임: 이상 / 정상 확인 / 판정 중 / 미판정 / 꺼짐). 저장된 categoryResults.result 그대로, 결과 대기 방법이 있으면 판정 중. */
const categoryResultText = (result: string | null, methods: MethodResult[]) =>
  result === "anomalous"
    ? "이상"
    : result === "normal"
      ? "정상 확인"
      : result === "skipped"
        ? "꺼짐"
        : result === "undetermined"
          ? methods.some(pending)
            ? "판정 중"
            : "미판정"
          : (result ?? DASH);

/** 기여도 줄을 둘지: 키가 있으면 항상, 키가 없으면 최종 판정의 succeeded ML 행만. */
const attributionShown = (m: MethodResult, final: boolean) =>
  m.attribution !== null && (m.attribution.status !== "absent" || (final && m.status === "succeeded"));

export const MethodResults = ({ evidence, observedStatus, final = false }: { evidence: EvidenceV4; observedStatus: string | null; final?: boolean }) => {
  const methods = evidence.methodResults;
  // 근거 구분 순으로 묶되 같은 구분 안은 저장 순서 그대로(Detector 순서: 내장 → 관계 → 프로토콜)
  const grouped = methods.map((m, index) => ({ m, index })).sort((a, b) => groupRank(a.m) - groupRank(b.m) || a.index - b.index).map(({ m }) => m);
  const held = evidence.relationNotes.filter((note) => note.result === "held");
  const categories = categoryOrder.filter((category) => evidence.categoryResults[category] || methods.some((m) => m.category === category));
  return (
    <div className="grid gap-4">
      <div className="grid gap-2">
        <h3 className="text-sm font-medium">종합 근거</h3>
        <p className="text-sm text-muted-foreground" data-category-note="true">{categoryNote}</p>
        <Table>
          <TableCaption>대분류 결과를 합산하거나 점수로 바꾸지 않습니다</TableCaption>
          <TableHeader>
            <TableRow>
              {["대분류", "켠 방법", "이상", "정상 확인", "미판정", "실행 중", "대분류 결과"].map((head) => (
                <TableHead key={head}>{head}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {categories.map((category) => {
              const items = methods.filter((m) => m.category === category);
              const stored = evidence.categoryResults[category];
              return (
                <TableRow key={category}>
                  <TableHead scope="row" className="h-auto font-normal">
                    {categoryNames[category]}
                  </TableHead>
                  <TableCell>{`${stored?.n ?? items.length}개`}</TableCell>
                  <TableCell>{items.filter((m) => m.anomalous === true).length}</TableCell>
                  <TableCell>{items.filter((m) => m.anomalous === false).length}</TableCell>
                  <TableCell>{items.filter((m) => m.anomalous === null && !pending(m)).length}</TableCell>
                  <TableCell>{items.filter(pending).length}</TableCell>
                  <TableCell>{categoryResultText(stored?.result ?? null, items)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      {held.length > 0 && (
        <div className="grid gap-2" data-relation-held="true">
          <h3 className="text-sm font-medium">{RELATION_HELD}</h3>
          <Table>
            <TableHeader>
              <TableRow>
                {["방법", "사유"].map((head) => (
                  <TableHead key={head}>{head}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {held.map((note, index) => (
                <TableRow key={`${note.ruleId ?? note.kind ?? "note"}-${index}`} data-held={note.ruleId ?? note.kind ?? ""}>
                  <TableHead scope="row" className="h-auto font-normal" title={note.ruleId ?? undefined}>
                    {heldMethod(note)}
                  </TableHead>
                  <TableCell title={note.reason ?? undefined}>{heldReason(note)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <div className="grid gap-2">
        <h3 className="text-sm font-medium">세부 방법별 결과</h3>
        <Table>
          <TableCaption>머신러닝 모델 점수는 모델마다 척도가 달라 모델끼리 비교하거나 위험도에 반영하지 않습니다</TableCaption>
          <TableHeader>
            <TableRow>
              {["대분류", "방법", "실행 상태", "결과", "측정값", "당시 기준", "위험도 단계", "처리 시간", "사유"].map((head) => (
                <TableHead key={head}>{head}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {grouped.map((m) => {
              const reason = reasonCell(m);
              return (
                <Fragment key={m.methodId}>
                  <TableRow data-method={m.methodId} data-group={m.group}>
                    <TableCell>{categoryNames[m.category]}</TableCell>
                    <TableHead scope="row" className="h-auto font-normal" title={m.methodId}>
                      {methodName(m)}
                    </TableHead>
                    <TableCell title={m.status ?? undefined}>{runText(m)}</TableCell>
                    <TableCell>{resultText(m)}</TableCell>
                    <TableCell>{measured(m, observedStatus)}</TableCell>
                    <TableCell>{criterion(m)}</TableCell>
                    <TableCell title={m.anomalous === true && !m.riskLevel ? (m.riskLevelReason ?? undefined) : undefined}>{levelText(m)}</TableCell>
                    <TableCell>{m.elapsedMs === null ? DASH : `${number(m.elapsedMs)} ms`}</TableCell>
                    <TableCell title={reason.title}>{reason.text}</TableCell>
                  </TableRow>
                  {m.attribution && attributionShown(m, final) && (
                    <TableRow data-attribution-of={m.methodId}>
                      <TableCell colSpan={9}>
                        {/* w-0 min-w-full: 기여도 내용(긴 문장·안쪽 표)이 위 표의 자동 열 폭 계산에 들어가지 않고 칸 전체 폭만 쓴다 */}
                        <div className="w-0 min-w-full" data-attribution-box="">
                          <MlAttribution attribution={m.attribution} />
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
};
