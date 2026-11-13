import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { IconArrowsMaximize, IconArrowsMinimize, IconChevronRight, IconChevronsLeft } from "@tabler/icons-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription } from "@/components/ui/card";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DASH, stampText } from "../labels";
import { incidentErrorTitle, incidentPeriods, type IncidentList, type IncidentPeriod } from "./incidentApi";
import {
  INCIDENT_CRITERIA_NOTE,
  INCIDENT_PARTIAL,
  INCIDENT_SHOW_ALL,
  criteriaKindLabel,
  criteriaTone,
  criteriaValueText,
  durationText,
  incidentEmptyV3Note,
  incidentFilterNote,
  incidentFlagLabels,
  incidentFlagTitles,
  incidentPartialTitle,
  longValueOmittedTitle,
  periodLabels,
  timeBasisKind,
  timeBasisMarks,
  timeBasisTitle,
} from "./incidentLabels";
import { IncidentTimeline } from "./IncidentTimeline";

type Query<T> = { data: T | undefined; isPending: boolean; isError: boolean; isPlaceholderData: boolean; error: unknown; refetch: () => unknown };

/** 이어짐 칸: 구간 2개 이상이면 "N개 구간", 병합·표시 배지. 아무것도 없을 때만 "—"(배지 앞에 "—"를 붙이지 않는다). */
const ChainCell = ({ item }: { item: IncidentList["items"][number] }) => {
  const parts: ReactNode[] = [];
  if (item.chain.length > 1) parts.push(<span key="segments">{item.chain.length}개 구간</span>);
  if (item.chain.mergedIncidents > 0) parts.push(<Badge key="merged" variant="outline">병합 {item.chain.mergedIncidents}</Badge>);
  for (const flag of item.flags)
    parts.push(
      <Badge key={flag} variant="warning" title={incidentFlagTitles[flag]}>
        {incidentFlagLabels[flag]}
      </Badge>,
    );
  if (item.truncated)
    parts.push(
      <Badge key="partial" variant="outline" title={incidentPartialTitle}>
        {INCIDENT_PARTIAL}
      </Badge>,
    );
  return parts.length ? <span className="flex flex-wrap items-center gap-1.5">{parts}</span> : <>{DASH}</>;
};

/**
 * 사건 목록(contracts/incidents-ui.md 5절): 위 시간축(흐름마다 막대) + 아래 표(기간 · 묶은 기준 · 거래 수 · 해제 · 이어짐). 줄 → 상세.
 * 쪽 넘김은 커서(주소 `cursor`), "처음"으로 첫 쪽. 기간은 주소 `period`. 기본은 거래 2건 이상만, "모두 보기" = 주소 `all=1`.
 */
export const IncidentListView = ({
  period,
  cursor,
  all,
  query,
  detailHref,
  onChange,
  isWide,
  onToggleWide,
}: {
  period: IncidentPeriod;
  cursor: string | null;
  all: boolean;
  query: Query<IncidentList>;
  detailHref: (id: string) => string;
  onChange: (next: { period?: IncidentPeriod; cursor?: string | null; all?: boolean }) => void;
  isWide: boolean;
  onToggleWide: (value: boolean) => void;
}) => {
  const data = query.data;
  let body: ReactNode;
  if (query.isPending && !data) body = <LoadingState />;
  else if (!data) body = <ErrorState title={incidentErrorTitle(query.error, "사건 목록")} onRetry={() => void query.refetch()} />;
  else if (!data.items.length)
    body = (
      <EmptyState>
        <span>
          {period === "all" ? "저장된 사건이 없습니다." : `${periodLabels[period]} 사건이 없습니다.`}
          {!all && ` (${incidentFilterNote}만 표시)`}
        </span>
        <span className="text-xs">{incidentEmptyV3Note}</span>
      </EmptyState>
    );
  else
    body = (
      <>
        <IncidentTimeline items={data.items} detailHref={detailHref} />
        <Table aria-label="사건 표">
          <TableHeader>
            <TableRow>
              <TableHead>기간</TableHead>
              <TableHead>묶은 기준</TableHead>
              <TableHead className="text-right">거래 수</TableHead>
              <TableHead className="text-right">해제</TableHead>
              <TableHead>이어짐</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.items.map((item) => {
              const basisMark = timeBasisMarks[timeBasisKind(item.occurredBasis)];
              return (
                <TableRow key={item.id} data-incident-row={item.id}>
                  <TableCell>
                    <Link to={detailHref(item.id)} className="font-medium text-link hover:underline">
                      {stampText(item.startAt)} ~ {stampText(item.endAt)}
                    </Link>
                    <div className="text-xs text-muted-foreground">
                      {durationText(item.durationMs)}
                      {basisMark && (
                        <span data-time-basis title={timeBasisTitle}>
                          {" · "}
                          {basisMark}
                        </span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    <span className="flex min-w-0 items-center gap-2">
                      <span aria-hidden="true" className={`inline-block size-2 shrink-0 rounded-full ${criteriaTone(item.criteria.kind)}`} />
                      <span className="shrink-0 text-muted-foreground">{criteriaKindLabel(item.criteria.kind)}</span>
                      {item.criteria.value ? (
                        <span className="max-w-[24rem] truncate font-mono text-sm" title={item.criteria.value}>
                          {item.criteria.value}
                        </span>
                      ) : (
                        <span className="text-sm text-muted-foreground" title={item.criteria.longValue ? longValueOmittedTitle : undefined}>
                          {criteriaValueText(item.criteria)}
                        </span>
                      )}
                    </span>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {item.counts.transactions.toLocaleString("ko-KR")}
                    <div className="text-xs text-muted-foreground">판정 {item.counts.decisions.toLocaleString("ko-KR")}건</div>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {item.counts.releasedTransactions ? item.counts.releasedTransactions.toLocaleString("ko-KR") : DASH}
                  </TableCell>
                  <TableCell>
                    <ChainCell item={item} />
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </>
    );

  return (
    <Card className="gap-4">
      <CardContent className="grid gap-4">
        <header className="flex items-start justify-between gap-3">
          <div className="grid gap-1">
            <h2 className="text-section-title">사건</h2>
            <CardDescription>{INCIDENT_CRITERIA_NOTE}</CardDescription>
          </div>
          <Button
            variant="outline"
            size="icon"
            onClick={() => onToggleWide(!isWide)}
            aria-label={isWide ? "콤팩트 화면으로 보기" : "넓은 화면으로 보기"}
          >
            {isWide ? <IconArrowsMinimize aria-hidden="true" /> : <IconArrowsMaximize aria-hidden="true" />}
          </Button>
        </header>
        <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground" aria-live="polite">
          <span id="incident-period-label">기간</span>
          <Select value={period} onValueChange={(value) => onChange({ period: value as IncidentPeriod, cursor: null })}>
            <SelectTrigger size="sm" aria-labelledby="incident-period-label">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {incidentPeriods.map((value) => (
                <SelectItem key={value} value={value}>
                  {periodLabels[value]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <label className="flex items-center gap-1.5">
            <Switch checked={all} onCheckedChange={(value) => onChange({ all: value, cursor: null })} aria-label={INCIDENT_SHOW_ALL} />
            {INCIDENT_SHOW_ALL}
          </label>
          <strong className="text-foreground">
            {data ? `사건 ${data.total.toLocaleString("ko-KR")}건` : "사건 —"}
            {!all && <span className="font-normal text-muted-foreground"> · {incidentFilterNote}</span>}
          </strong>
          {data?.truncated && <Badge variant="warning">오래된 사건 일부 제외</Badge>}
        </div>
        {query.isError && data && (
          <ErrorState title={`${incidentErrorTitle(query.error, "사건 목록")} · 이전 관측값 표시`} onRetry={() => void query.refetch()} />
        )}
        <section className="grid gap-4" aria-label="사건 목록" aria-busy={query.isPlaceholderData || undefined}>
          {body}
        </section>
        {data && (cursor || data.nextCursor) && (
          <nav aria-label="페이지" className="flex items-center justify-end gap-1 text-sm">
            <Button variant="ghost" size="sm" disabled={!cursor} onClick={() => onChange({ cursor: null })}>
              <IconChevronsLeft aria-hidden="true" />
              처음
            </Button>
            <Button variant="ghost" size="sm" disabled={!data.nextCursor} onClick={() => onChange({ cursor: data.nextCursor })}>
              다음
              <IconChevronRight aria-hidden="true" />
            </Button>
          </nav>
        )}
      </CardContent>
    </Card>
  );
};
