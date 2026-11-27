import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { IconBrain } from "@tabler/icons-react";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { motionClasses } from "@/lib/motion";
import { cn } from "@/lib/utils";
import type { DecisionDetail, EvidenceSummary, EvidenceSummaryMlRow, EvidenceSummaryObserved, EvidenceSummaryRuleRow } from "../data/analysisApi";
import { evidenceSummaryLabels as L, riskLevelVariant } from "../labels";
import { streamPlanner, useTextStream } from "../hooks/useTextStream";

/**
 * 판정 상세 `AI 판정 근거 요약`(analysis-ui.md): 서버 evidenceSummary 다섯 블록을 위에서부터 그대로 그린다.
 * ① 앞말 배지 + 한 줄 요약 ② 한 문단(코드 문장을 공백 한 칸으로 이은 값, 다듬은 문단이 오면 그 문단 — paragraph) ③ 규칙 행 표·머신러닝 모델 행 표
 * (행이 없으면 그 표 없음, ML 표가 빈 이유 한 줄) ④ 확인할 것 ⑤ 고정 안내(작은 글자). 화면은 문장을 만들거나 숫자를 다시 계산하지 않는다.
 * 앞말 배지 색: 위험도 단계 앞말 = 판정의 위험도 단계 색, 이상 징후 등급 = 주의 색, 그 밖 = 기본.
 * 흘러나오기(reveal=true, useTextStream): 배지 → 한 줄 요약 어절 → 문단 어절 → 표 머리·행 → 빈 이유 → 확인할 것 제목·항목 어절 → 고정 안내 순으로
 * 조각이 붙으며 카드가 아래로 자란다. 끝나면 효과 없는 표시와 같은 DOM이다.
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

/**
 * 흘러나오기 조각: 어절(공백으로 나뉜 덩어리) 하나, keepNames가 묶는 이름은 통째로 한 조각(줄바꿈 묶음 유지).
 * lead = 조각 앞 공백(그 조각과 함께 붙는다), tail = 글 끝 공백. 조각을 이으면 원래 글자와 같다.
 */
type Token = { lead: string; body: string; name: boolean; tail: string };
export const streamTokens = (text: string): Token[] => {
  const tokens: Token[] = [];
  let lead = "";
  const plain = (segment: string) => {
    for (const part of segment.split(/(\s+)/)) {
      if (!part) continue;
      if (/^\s+$/.test(part)) lead += part;
      else {
        tokens.push({ lead, body: part, name: false, tail: "" });
        lead = "";
      }
    }
  };
  let last = 0;
  for (const match of text.matchAll(NAME_WITH_PARENS)) {
    plain(text.slice(last, match.index));
    tokens.push({ lead, body: match[0], name: true, tail: "" });
    lead = "";
    last = match.index + match[0].length;
  }
  plain(text.slice(last));
  if (lead && tokens.length > 0) tokens[tokens.length - 1].tail = lead;
  return tokens;
};

type Stream = { streaming: boolean; visible: (index: number) => boolean };
/** 흐르는 중이면 나온 조각만(새 조각마다 `.stream-in` 페이드), 끝나면 평소 표시(keepNames) 그대로. first = 첫 조각 번호. */
const StreamText = ({ text, first, stream }: { text: string; first: number; stream: Stream }) => {
  if (!stream.streaming) return <>{keepNames(text)}</>;
  return (
    <>
      {streamTokens(text).map((token, index) =>
        stream.visible(first + index) ? (
          <Fragment key={index}>
            {token.lead}
            <span className="stream-in">{token.name ? <span className="whitespace-nowrap">{token.body}</span> : token.body}</span>
            {token.tail}
          </Fragment>
        ) : null,
      )}
    </>
  );
};
/** 통째 조각(표 행·작은 글 블록)의 페이드 클래스. */
const fade = (stream: Stream, className?: string) => cn(className, stream.streaming && "stream-in") || undefined;

/** 블록 2 문단: 이 한 곳에서만 그린다. stream·first가 오면 어절 단위로 흘러나온다. */
// 고정 클래스는 cn(tailwind-merge) 밖에 둔다 — break-words와 break-keep을 같은 묶음으로 보고 앞의 것을 지우기 때문이다.
const PARAGRAPH = "break-words break-keep text-sm leading-6";
export const SummaryParagraph = ({ text, className, stream, first = 0 }: { text: string; className?: string; stream?: Stream; first?: number }) => (
  <p data-evidence-summary-block="paragraph" className={className ? `${PARAGRAPH} ${className}` : PARAGRAPH}>
    {stream ? <StreamText text={text} first={first} stream={stream} /> : keepNames(text)}
  </p>
);
export const codeParagraph = (summary: EvidenceSummary) => summary.sentences.join(" ");

/** lines: [머리 행, 행…] 조각 번호. 아직 안 나온 행은 그리지 않는다. */
const RuleTable = ({ rows, lines, stream }: { rows: EvidenceSummaryRuleRow[]; lines: number[]; stream: Stream }) => {
  const unitUnknown = rows.some((r) => r.observed.some((o) => o.unitUnknown));
  return (
    <Table data-evidence-summary-table="rule" className="table-fixed">
      <TableHeader>
        <TableRow className={fade(stream)}>
          <TableHead className="w-[45%]">{L.ruleCondition}</TableHead>
          <TableHead className="w-[35%]" title={unitUnknown ? L.unitUnknownHead : undefined}>{L.observed}</TableHead>
          <TableHead>{L.result}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, index) =>
          stream.visible(lines[index + 1]) ? (
            <TableRow key={`${row.methodId}-${index}`} data-method-id={row.methodId} className={fade(stream)}>
              <TableCell className={CELL}>{keepNames(row.condition)}</TableCell>
              <TableCell className={CELL} title={row.observed.some((o) => o.unitUnknown) ? L.unitUnknownCell : undefined}>{keepNames(observedText(row.observed))}</TableCell>
              <TableCell className={CELL}>{row.result}</TableCell>
            </TableRow>
          ) : null,
        )}
      </TableBody>
    </Table>
  );
};

/**
 * 참가자 1개 = 구성원 수만큼 줄. 묶음의 둘째 구성원부터 이름을 들여 쓰고, 구분선은 참가자 사이에만 둔다. 영향 칸은 참가자마다 한 번.
 * lines: [머리 행, 참가자 행…] 조각 번호 — 참가자(묶음) 하나가 한 조각이다(영향 칸이 여러 줄을 묶어 행 사이에서 끊지 않는다).
 */
const MlTable = ({ rows, lines, stream }: { rows: EvidenceSummaryMlRow[]; lines: number[]; stream: Stream }) => {
  const unitUnknown = rows.some((r) => r.members.some((m) => m.unitUnknown));
  return (
    <Table data-evidence-summary-table="ml" className="table-fixed">
      <TableHeader>
        <TableRow className={fade(stream)}>
          <TableHead className="w-[34%]">{L.mlVariable}</TableHead>
          <TableHead className="w-[24%]" title={unitUnknown ? L.unitUnknownHead : undefined}>{L.observed}</TableHead>
          <TableHead className="w-[24%]" title={L.usualTooltip}>{L.usual}</TableHead>
          <TableHead>{L.impact}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, playerIndex) =>
          stream.visible(lines[playerIndex + 1]) ? (
            <Fragment key={`${row.methodId}-${row.playerId}`}>
              {row.members.map((member, index) => (
                <TableRow
                  key={`${member.name}-${index}`}
                  data-player-id={row.playerId}
                  className={fade(stream, cn(index < row.members.length - 1 && "border-b-0"))}
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
          ) : null,
        )}
      </TableBody>
    </Table>
  );
};

/**
 * 조각 번호·시각 나누기(그리는 순서와 같아야 한다): 배지 1 · 한 줄 요약 어절 · (쉼) 문단 어절 · (쉼) 규칙 표 머리+행 · ML 표 머리+참가자 · 빈 이유 1
 * · (쉼) 확인할 것 제목 1 + 항목마다 어절 · (쉼) 고정 안내 1.
 */
const streamPlan = (summary: EvidenceSummary, paragraphText: string | null, ruleRows: EvidenceSummaryRuleRow[], mlRows: EvidenceSummaryMlRow[]) => {
  const planner = streamPlanner();
  const badge = planner.rows(1)[0];
  const summaryLine = planner.words(streamTokens(summary.summaryLine.text).length);
  planner.pause();
  const paragraph = paragraphText ? planner.words(streamTokens(paragraphText).length) : [];
  if (paragraphText) planner.pause();
  const rule = ruleRows.length > 0 ? planner.rows(1 + ruleRows.length) : [];
  const ml = mlRows.length > 0 ? planner.rows(1 + mlRows.length) : [];
  const note = summary.contributionNote ? planner.rows(1)[0] : -1;
  if (rule.length > 0 || ml.length > 0 || note >= 0) planner.pause();
  const checksTitle = summary.checks.length > 0 ? planner.rows(1)[0] : -1;
  const checks = summary.checks.map((check) => planner.words(streamTokens(check.text).length));
  if (checksTitle >= 0) planner.pause();
  const notice = planner.rows(1)[0];
  return { badge, summaryLine, paragraph, rule, ml, note, checksTitle, checks, notice, schedule: planner.finish() };
};

/**
 * paragraph: 블록 2에 보일 문단(다듬은 문단). 없으면 코드 문단.
 * opened=false: 접힌 카드 — 제목 줄 왼쪽 제목, 오른쪽 [AI 근거 생성 요청](onOpen) 버튼, 본문은 남은 높이를 채우는 둥근 사각형 영역(옅은 배경·테두리) 가운데에
 *   아이콘 + L.notGenerated(사용자 11/28). 한 열(늘어나지 않는 카드)에서는 낮은 높이.
 *   다섯 블록과 블록 2 다듬기 요청은 누른 뒤다. 펼칠 때 카드(제목 줄)는 그대로 두고 본문만 바꾼다(제목이 다시 나타나는 움직임 없음).
 *   누른 순간의 카드 높이를 흘러나오는 동안만 최소 높이로 이어 받아 카드 높이가 줄었다 늘지 않는다(흐르기가 끝나면 풀어 효과 없는 표시와 같은 DOM).
 * fill: 같은 높이 두 열(판정 상세 넓은 본문)에서 카드가 늘어난 높이를 채운다 — 빈 상태는 가운데, 펼친 뒤 고정 안내는 맨 아래.
 * reveal: 흘러나오기를 시작할지(부르는 쪽이 정한다). focusOnMount: 누른 버튼이 사라진 자리 대신 첫 블록(한 줄 요약)에 초점을 둔다.
 * 흐르는 동안 카드는 aria-busy(조각마다 읽어 대지 않게, live 영역 아님). 처음 그린 뒤 문단이 바뀌면(다듬은 문단 도착) 그 문단만 한 번 페이드한다.
 */
export const EvidenceSummaryCard = ({ detail, summary, paragraph, opened = true, onOpen, reveal = false, focusOnMount = false, fill = false, className }: {
  detail: DecisionDetail; summary: EvidenceSummary; paragraph?: string | null; opened?: boolean; onOpen?: () => void; reveal?: boolean; focusOnMount?: boolean;
  fill?: boolean; className?: string;
}) => {
  const ruleRows = summary.table.filter((row): row is EvidenceSummaryRuleRow => row.kind === "rule");
  const mlRows = summary.table.filter((row): row is EvidenceSummaryMlRow => row.kind === "ml");
  const text = paragraph || codeParagraph(summary);
  // 기준 문단 = 펼친 순간(누름·바로 펼침)의 문단. 접힌 동안 바뀐 문단은 그대로 따라가고, 펼친 뒤 바뀌면(다듬은 문단 도착) 그 문단만 한 번 페이드한다.
  const [openedText, setOpenedText] = useState<string | null>(opened ? text : null);
  if (opened && openedText === null) setOpenedText(text);
  const baseText = openedText ?? text;
  const replaced = openedText !== null && text !== openedText;
  const plan = streamPlan(summary, baseText || null, ruleRows, mlRows);
  const stream = useTextStream(reveal, plan.schedule);
  const { visible } = stream;
  const first = useRef<HTMLDivElement>(null);
  const empty = useRef<HTMLDivElement>(null);
  const [hold, setHold] = useState<number | undefined>(undefined);
  useEffect(() => {
    if (focusOnMount) first.current?.focus();
  }, [focusOnMount]);
  if (!opened)
    return (
      <SectionCard
        title={L.title}
        headingLevel={2}
        fill={fill}
        className={className}
        action={
          <Button
            variant="outline"
            size="sm"
            data-evidence-summary-open="true"
            onClick={() => {
              // 카드 전체 높이(제목 줄 버튼 포함)를 이어 받는다 — 버튼이 빠져 제목 줄이 낮아져도 카드가 줄었다 늘지 않게.
              const height = empty.current?.closest<HTMLElement>('[data-slot="section-card"]')?.offsetHeight;
              if (height) setHold(height);
              onOpen?.();
            }}
          >
            <IconBrain aria-hidden="true" />
            {L.generate}
          </Button>
        }
      >
        <div
          ref={empty}
          role="status"
          data-evidence-summary-empty="true"
          className="flex min-h-24 flex-1 flex-col items-center justify-center gap-2 rounded-lg border bg-muted px-4 py-6 text-center text-sm text-muted-foreground"
        >
          <IconBrain aria-hidden="true" className="size-6" />
          {L.notGenerated}
        </div>
      </SectionCard>
    );
  const showTable = (ruleRows.length > 0 && visible(plan.rule[0])) || (mlRows.length > 0 && visible(plan.ml[0])) || (plan.note >= 0 && visible(plan.note));
  return (
    <SectionCard title={L.title} headingLevel={2} aria-busy={stream.streaming} fill={fill} className={className} style={hold && stream.streaming ? { minHeight: hold } : undefined}>
      <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div
        ref={first}
        tabIndex={focusOnMount ? -1 : undefined}
        data-evidence-summary-block="line"
        className="flex min-w-0 flex-wrap items-baseline gap-2 rounded-sm"
      >
        <Badge variant={prefixVariant(detail, summary)} className={fade(stream)}>{summary.summaryLine.prefix}</Badge>
        {visible(plan.summaryLine[0] ?? plan.badge) && (
          <p className="min-w-0 break-words break-keep font-medium">
            <StreamText text={summary.summaryLine.text} first={plan.summaryLine[0] ?? 0} stream={stream} />
          </p>
        )}
      </div>
      {/* 문단은 자기 차례(첫 조각)가 되어야 그린다 — 다듬은 문단이 흐르는 도중 일찍 와도 한 줄 요약 뒤에 나온다 */}
      {text && (plan.paragraph.length === 0 || visible(plan.paragraph[0])) && (replaced
        ? <SummaryParagraph key={text} text={text} className={motionClasses.enter} />
        : <SummaryParagraph text={text} stream={stream} first={plan.paragraph[0]} />)}
      {showTable && (
        <div data-evidence-summary-block="table" className="grid min-w-0 gap-3">
          {ruleRows.length > 0 && visible(plan.rule[0]) && <RuleTable rows={ruleRows} lines={plan.rule} stream={stream} />}
          {mlRows.length > 0 && visible(plan.ml[0]) && <MlTable rows={mlRows} lines={plan.ml} stream={stream} />}
          {summary.contributionNote && visible(plan.note) && (
            <p data-evidence-summary-note="true" className={fade(stream, "text-sm text-muted-foreground")}>
              {summary.contributionNote}
            </p>
          )}
        </div>
      )}
      {summary.checks.length > 0 && visible(plan.checksTitle) && (
        <div data-evidence-summary-block="checks" className="grid gap-1">
          <h3 className={fade(stream, "text-sm font-medium")}>{L.checks}</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {summary.checks.map((check, index) =>
              plan.checks[index].length === 0 || visible(plan.checks[index][0]) ? (
                <li key={`${check.code}-${index}`} className="break-words break-keep">
                  <StreamText text={check.text} first={plan.checks[index][0] ?? 0} stream={stream} />
                </li>
              ) : null,
            )}
          </ul>
        </div>
      )}
      {visible(plan.notice) && (
        <p data-evidence-summary-block="notice" className={fade(stream, "mt-auto text-xs text-muted-foreground")}>
          {summary.notice}
        </p>
      )}
      </div>
    </SectionCard>
  );
};
