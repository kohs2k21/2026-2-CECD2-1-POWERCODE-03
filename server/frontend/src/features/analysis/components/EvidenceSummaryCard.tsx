import { Fragment, type ReactNode } from "react";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { DecisionDetail, EvidenceSummary, EvidenceSummaryMlRow, EvidenceSummaryObserved, EvidenceSummaryRuleRow } from "../data/analysisApi";
import { evidenceSummaryLabels as L, riskLevelVariant } from "../labels";

/**
 * 판정 상세 `AI 판정 근거 요약`(analysis-ui.md): 서버 evidenceSummary 다섯 블록을 위에서부터 그대로 그린다.
 * ① 앞말 배지 + 한 줄 요약 ② 한 문단(코드 문장을 공백 한 칸으로 이은 값, 다듬은 문단이 오면 그 문단 — paragraph) ③ 규칙 행 표·머신러닝 모델 행 표
 * (행이 없으면 그 표 없음, ML 표가 빈 이유 한 줄) ④ 확인할 것 ⑤ 고정 안내(작은 글자). 화면은 문장을 만들거나 숫자를 다시 계산하지 않는다.
 * 앞말 배지 색: 위험도 단계 앞말 = 판정의 위험도 단계 색, 이상 징후 등급 = 주의 색, 그 밖 = 기본.
 */
const prefixVariant = (detail: DecisionDetail, summary: EvidenceSummary) => {
  if (summary.summaryLine.prefixKind === "risk" && detail.riskStatus === "rated" && detail.riskLevel) return riskLevelVariant[detail.riskLevel];
  return summary.summaryLine.prefixKind === "grade" ? "warning" : "secondary";
};
// 긴 값이 다른 열을 누르지 않게 줄바꿈을 허용한다(표 머리는 한 줄). 한글은 낱말 단위로 줄바꿈한다.
const CELL = "whitespace-normal break-words break-keep align-top";
/** 서버가 준 값이 숫자 표기(쉼표·소수 포함)일 때만 단위를 한 칸 띄워 붙인다. 낱말·값 없음 글자에는 붙이지 않는다. */
const NUMBER_TEXT = /^-?\d[\d,]*(\.\d+)?$/;
const withUnit = (value: string, unit: string | null) => (unit && NUMBER_TEXT.test(value) ? `${value} ${unit}` : value);
const observedText = (entries: EvidenceSummaryObserved[]) => entries.map((o) => `${o.name ? `${o.name} ` : ""}${withUnit(o.value, o.unit)}`).join(", ");

/**
 * 괄호가 붙은 이름(`처리량(TOTAL_COUNT)`·`F(실패)`)이 중간에서 끊기지 않게 그 이름만 줄바꿈 없는 묶음으로 감싼다. 글자는 바꾸지 않는다.
 * 띄어 쓴 건수 계열 이름(`오류 건수(ERROR_COUNT)`·`재시도 횟수(RETRY_COUNT)` — 원본 열 이름 괄호)은 앞 낱말까지 함께 묶는다.
 */
const NAME_WITH_PARENS = /[가-힣]+ (?:건수|횟수)\([A-Z][A-Z0-9_]*\)|[^\s·,()]+\([^\s()]+\)/g;
export const keepNames = (text: string): ReactNode => {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(NAME_WITH_PARENS)) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    parts.push(<span key={match.index} className="whitespace-nowrap">{match[0]}</span>);
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts.length === 1 ? parts[0] : parts;
};

/** 블록 2 문단: 이 한 곳에서만 그린다. */
export const SummaryParagraph = ({ text }: { text: string }) => (
  <p data-evidence-summary-block="paragraph" className="break-words break-keep text-sm leading-6">
    {keepNames(text)}
  </p>
);
export const codeParagraph = (summary: EvidenceSummary) => summary.sentences.join(" ");

const RuleTable = ({ rows }: { rows: EvidenceSummaryRuleRow[] }) => {
  const unitUnknown = rows.some((r) => r.observed.some((o) => o.unitUnknown));
  return (
    <Table data-evidence-summary-table="rule" className="table-fixed">
      <TableHeader>
        <TableRow>
          <TableHead className="w-[45%]">{L.ruleCondition}</TableHead>
          <TableHead className="w-[35%]" title={unitUnknown ? L.unitUnknownHead : undefined}>{L.observed}</TableHead>
          <TableHead>{L.result}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, index) => (
          <TableRow key={`${row.methodId}-${index}`} data-method-id={row.methodId}>
            <TableCell className={CELL}>{keepNames(row.condition)}</TableCell>
            <TableCell className={CELL} title={row.observed.some((o) => o.unitUnknown) ? L.unitUnknownCell : undefined}>{keepNames(observedText(row.observed))}</TableCell>
            <TableCell className={CELL}>{row.result}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
};

/** 참가자 1개 = 구성원 수만큼 줄. 묶음의 둘째 구성원부터 이름을 들여 쓰고, 구분선은 참가자 사이에만 둔다. 영향 칸은 참가자마다 한 번. */
const MlTable = ({ rows }: { rows: EvidenceSummaryMlRow[] }) => {
  const unitUnknown = rows.some((r) => r.members.some((m) => m.unitUnknown));
  return (
    <Table data-evidence-summary-table="ml" className="table-fixed">
      <TableHeader>
        <TableRow>
          <TableHead className="w-[34%]">{L.mlVariable}</TableHead>
          <TableHead className="w-[24%]" title={unitUnknown ? L.unitUnknownHead : undefined}>{L.observed}</TableHead>
          <TableHead className="w-[24%]" title={L.usualTooltip}>{L.usual}</TableHead>
          <TableHead>{L.impact}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <Fragment key={`${row.methodId}-${row.playerId}`}>
            {row.members.map((member, index) => (
              <TableRow
                key={`${member.name}-${index}`}
                data-player-id={row.playerId}
                className={cn(index < row.members.length - 1 && "border-b-0")}
              >
                <TableCell className={cn(CELL, index > 0 && "pl-6")}>{keepNames(member.name)}</TableCell>
                <TableCell className={CELL} title={member.unitUnknown ? L.unitUnknownCell : undefined}>{withUnit(member.actual, member.unit)}</TableCell>
                <TableCell className={CELL}>{withUnit(member.usual, member.unit)}</TableCell>
                {index === 0 && (
                  <TableCell className={CELL} rowSpan={row.members.length}>
                    {row.impactPercent}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </Fragment>
        ))}
      </TableBody>
    </Table>
  );
};

/** paragraph: 블록 2에 보일 문단(다듬은 문단). 없으면 코드 문단. */
export const EvidenceSummaryCard = ({ detail, summary, paragraph }: { detail: DecisionDetail; summary: EvidenceSummary; paragraph?: string | null }) => {
  const ruleRows = summary.table.filter((row): row is EvidenceSummaryRuleRow => row.kind === "rule");
  const mlRows = summary.table.filter((row): row is EvidenceSummaryMlRow => row.kind === "ml");
  const text = paragraph || codeParagraph(summary);
  return (
    <SectionCard title={L.title} headingLevel={2}>
      <div data-evidence-summary-block="line" className="flex min-w-0 flex-wrap items-baseline gap-2">
        <Badge variant={prefixVariant(detail, summary)}>{summary.summaryLine.prefix}</Badge>
        <p className="min-w-0 break-words break-keep font-medium">{keepNames(summary.summaryLine.text)}</p>
      </div>
      {text && <SummaryParagraph text={text} />}
      {(ruleRows.length > 0 || mlRows.length > 0 || summary.contributionNote) && (
        <div data-evidence-summary-block="table" className="grid min-w-0 gap-3">
          {ruleRows.length > 0 && <RuleTable rows={ruleRows} />}
          {mlRows.length > 0 && <MlTable rows={mlRows} />}
          {summary.contributionNote && (
            <p data-evidence-summary-note="true" className="text-sm text-muted-foreground">
              {summary.contributionNote}
            </p>
          )}
        </div>
      )}
      {summary.checks.length > 0 && (
        <div data-evidence-summary-block="checks" className="grid gap-1">
          <h3 className="text-sm font-medium">{L.checks}</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {summary.checks.map((check, index) => (
              <li key={`${check.code}-${index}`} className="break-words break-keep">
                {keepNames(check.text)}
              </li>
            ))}
          </ul>
        </div>
      )}
      <p data-evidence-summary-block="notice" className="text-xs text-muted-foreground">
        {summary.notice}
      </p>
    </SectionCard>
  );
};
