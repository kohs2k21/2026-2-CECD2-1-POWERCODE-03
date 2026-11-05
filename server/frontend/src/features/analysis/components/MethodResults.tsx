import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { EvidenceV4, MethodCategory, MethodResult } from "../data/evidence";
import { DASH, methodLabel, resultCodeLabel, riskLevelLabels, runLabels } from "../labels";

/**
 * 종합 근거(대분류) + 세부 방법별 결과(와이어프레임 dCats·dMethods). evidence v4 저장값만 읽는다(재계산 없음, detection-decision.md D7).
 * 정책 값(k·deadlineMs·composite.policy)은 보이지 않는다(C23·사용자 9.8). 방법은 표시 이름, 방법 ID는 툴팁.
 */
export const categoryNames: Record<MethodCategory, string> = { hard: "이상 규칙", soft: "이상 징후 규칙", ml: "머신러닝 모델" };
const categoryOrder: MethodCategory[] = ["hard", "soft", "ml"];
/** 와이어프레임 resultLabel(T/N/U/·) */
const pending = (m: MethodResult) => m.status === "not_ready" && m.cause === "pending";
const resultText = (m: MethodResult) => (m.anomalous === true ? "이상" : m.anomalous === false ? "정상 확인" : pending(m) ? DASH : "미판정");
/** 소프트룰 비교 연산자(와이어프레임 OPS ≥ > ≤ <). */
const operators: Record<string, string> = { gte: "≥", gt: ">", lte: "≤", lt: "<" };
/** 이상 규칙 조건 문구(detection-decision.md 4.3 conditionText, C22 승인). */
const hardConditions: Record<string, string> = {
  "hard:process-status": "STATUS ≠ S",
  "hard:negative-duration": "START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음)",
  "hard:success-with-error": "STATUS = S, ERROR_COUNT > 0",
  "hard:count-conservation": "TOTAL_COUNT ≠ SUCCESS_COUNT + ERROR_COUNT",
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
  return m.anomalous === true && m.code ? resultCodeLabel(m.code) : DASH;
};
const criterion = (m: MethodResult) => {
  if (m.category === "soft" && m.condition?.threshold)
    return withUnit(`${m.condition.field ?? ""} ${operators[m.condition.operator ?? ""] ?? m.condition.operator ?? ""} ${m.condition.threshold}`.trim(), m.condition.unit);
  if (m.category === "ml") return m.scoreDetail?.threshold ? `판정선 ${m.scoreDetail.threshold}` : DASH;
  return hardConditions[m.methodId] ?? m.methodVersion ?? DASH;
};
/** 위험도 단계(와이어프레임 levelCell): 이상이 아니면 —, 이상인데 단계가 없으면(ML) 단계 없음. */
const levelText = (m: MethodResult) =>
  m.anomalous !== true ? DASH : m.riskLevel && m.riskLevel in riskLevelLabels ? riskLevelLabels[m.riskLevel as keyof typeof riskLevelLabels] : "단계 없음";
const reasonCell = (m: MethodResult) => {
  if (pending(m)) return { text: DASH };
  if (m.cause) {
    const code = [m.cause, m.failureCode].filter(Boolean).join(" · ");
    return causeLabels[m.cause] ? { text: causeLabels[m.cause], title: code } : { text: code };
  }
  return m.reasons.length ? { text: m.reasons.join(", ") } : { text: DASH };
};
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

export const MethodResults = ({ evidence, observedStatus }: { evidence: EvidenceV4; observedStatus: string | null }) => {
  const methods = evidence.methodResults;
  const categories = categoryOrder.filter((category) => evidence.categoryResults[category] || methods.some((m) => m.category === category));
  return (
    <div className="grid gap-4">
      <div className="grid gap-2">
        <h3 className="text-sm font-medium">종합 근거</h3>
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
            {methods.map((m) => {
              const reason = reasonCell(m);
              return (
                <TableRow key={m.methodId} data-method={m.methodId}>
                  <TableCell>{categoryNames[m.category]}</TableCell>
                  <TableHead scope="row" className="h-auto font-normal" title={m.methodId}>
                    {methodLabel(m.methodId) ?? DASH}
                  </TableHead>
                  <TableCell title={m.status ?? undefined}>{runText(m)}</TableCell>
                  <TableCell>{resultText(m)}</TableCell>
                  <TableCell>{measured(m, observedStatus)}</TableCell>
                  <TableCell>{criterion(m)}</TableCell>
                  <TableCell title={m.anomalous === true && !m.riskLevel ? (m.riskLevelReason ?? undefined) : undefined}>{levelText(m)}</TableCell>
                  <TableCell>{m.elapsedMs === null ? DASH : `${number(m.elapsedMs)} ms`}</TableCell>
                  <TableCell title={reason.title}>{reason.text}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
};
