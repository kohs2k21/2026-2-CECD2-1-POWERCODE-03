import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { IconArrowLeft, IconArrowsMaximize, IconArrowsMinimize } from "@tabler/icons-react";
import { paths } from "@/app/routes";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DASH, stampText } from "../labels";
import { incidentErrorTitle, type IncidentDetail, type IncidentMemberRow, type IncidentOccurredBasis } from "./incidentApi";
import {
  GRADE_UNKNOWN,
  INCIDENT_CRITERIA_NOTE,
  INCIDENT_PARTIAL,
  RELEASED,
  criteriaKindLabel,
  criteriaText,
  criteriaValueText,
  durationText,
  gradeUnknownTitle,
  incidentFlagLabels,
  incidentFlagTitles,
  incidentGradeLabels,
  incidentPartialTitle,
  longValueOmittedTitle,
  memberOrderNotes,
  memberTimeHeads,
  releasedTitle,
  rowTimeBasisKind,
  secondsText,
  timeBasisKind,
  timeBasisMarks,
  timeBasisTitle,
  type TimeBasisKind,
} from "./incidentLabels";
import { useIncidentMembers } from "./useIncidents";

/** 쪽 번호별로 고정된 setter(렌더마다 새 함수가 아니어서 MemberPage effect가 반복되지 않는다). */
const useCallbackMap = (set: (update: (value: Record<number, string | null>) => Record<number, string | null>) => void) => {
  const [cache] = useState(() => new Map<number, (value: string | null) => void>());
  return (index: number) => {
    if (!cache.has(index)) cache.set(index, (value) => set((current) => (current[index] === value ? current : { ...current, [index]: value })));
    return cache.get(index)!;
  };
};
type Query<T> = { data: T | undefined; isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown };
const number = (value: number) => value.toLocaleString("ko-KR");
const gradeVariant = (grade: string | null) => (grade === "anomaly" ? "critical" : grade === "anomaly_sign" ? "warning" : "secondary");
/** 시각 출처 표시(원천 시각이면 없음, Detector incidents.md 3절·8절). */
const BasisMark = ({ kind }: { kind: TimeBasisKind }) => {
  const mark = timeBasisMarks[kind];
  return mark ? (
    <Badge variant="outline" title={timeBasisTitle} data-time-basis={kind}>
      {mark}
    </Badge>
  ) : null;
};

/**
 * 구성원 한 쪽(거래별 줄). 판정 → 판정 상세(실행 순서 그래프).
 * 시각 출처: 흐름 전체가 판정 시각이면 칸 머리가 "판정 시각"이라 줄 표시는 하지 않고, 섞였을 때만 판정 시각 줄에 표시한다.
 */
const MemberRows = ({ rows, returnTo, flowBasis }: { rows: IncidentMemberRow[]; returnTo: string; flowBasis: TimeBasisKind }) => (
  <>
    {rows.map((row) => (
      <TableRow key={row.key} data-member-row={row.transactionId ?? ""}>
        <TableCell className="font-mono text-sm">{row.transactionId ?? <span className="text-muted-foreground">거래 ID 없음</span>}</TableCell>
        <TableCell>{row.interfaceId ?? DASH}</TableCell>
        <TableCell className="tabular-nums">
          {stampText(row.firstOccurredAt)}
          {row.lastOccurredAt && row.lastOccurredAt !== row.firstOccurredAt && <> ~ {stampText(row.lastOccurredAt)}</>}
          {flowBasis === "mixed" && rowTimeBasisKind(row.occurredBasis) !== "source" && (
            <div>
              <BasisMark kind={rowTimeBasisKind(row.occurredBasis)} />
            </div>
          )}
        </TableCell>
        <TableCell>
          <ul className="grid gap-1">
            {row.decisions.map((decision) => (
              <li key={decision.decisionId} className="flex flex-wrap items-center gap-1.5">
                <Link
                  to={paths.analysisDecision(decision.decisionId)}
                  state={{ returnTo }}
                  className="text-link hover:underline"
                  title={decision.decisionId}
                >
                  {decision.processId ?? decision.decisionId.slice(0, 8)}
                </Link>
                {decision.grade && <Badge variant={gradeVariant(decision.grade)}>{incidentGradeLabels[decision.grade]}</Badge>}
                {decision.released && (
                  <Badge variant="outline" title={releasedTitle}>
                    {RELEASED}
                    {decision.latestGrade ? ` · 최신 ${incidentGradeLabels[decision.latestGrade]}` : ""}
                  </Badge>
                )}
                {decision.gradeUnknown && (
                  <Badge variant="outline" title={gradeUnknownTitle}>
                    {GRADE_UNKNOWN}
                  </Badge>
                )}
              </li>
            ))}
            {row.decisionsTruncated && <li className="text-xs text-muted-foreground">판정 {number(row.decisionCount)}건 중 {row.decisions.length}건</li>}
          </ul>
        </TableCell>
        <TableCell>{row.released ? <Badge variant="outline" title={releasedTitle}>{RELEASED}</Badge> : DASH}</TableCell>
      </TableRow>
    ))}
  </>
);

/** 구성원 쪽 하나(커서별 조회). 마지막 쪽이면 다음 커서를 위로 알린다. */
const MemberPage = ({
  id,
  cursor,
  onNext,
  returnTo,
  flowBasis,
}: {
  id: string;
  cursor: string | null;
  onNext: (next: string | null) => void;
  returnTo: string;
  flowBasis: TimeBasisKind;
}) => {
  const query = useIncidentMembers(id, cursor);
  const nextCursor = query.data?.nextCursor;
  useEffect(() => {
    if (nextCursor !== undefined) onNext(nextCursor);
  }, [nextCursor, onNext]);
  if (query.isPending)
    return (
      <TableRow>
        <TableCell colSpan={5}>
          <LoadingState />
        </TableCell>
      </TableRow>
    );
  if (!query.data)
    return (
      <TableRow>
        <TableCell colSpan={5}>
          <ErrorState title={incidentErrorTitle(query.error, "사건 구성원")} onRetry={() => void query.refetch()} />
        </TableCell>
      </TableRow>
    );
  return <MemberRows rows={query.data.items} returnTo={returnTo} flowBasis={flowBasis} />;
};

/** 구성원 표: 첫 쪽부터, "더 보기"로 다음 커서 쪽을 덧붙인다(마지막 쪽의 nextCursor만 쓴다). */
const Members = ({ id, basis }: { id: string; basis: IncidentOccurredBasis }) => {
  const flowBasis = timeBasisKind(basis);
  const location = useLocation();
  const first = useIncidentMembers(id, null);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [nextByPage, setNextByPage] = useState<Record<number, string | null>>({});
  const next = nextByPage[cursors.length - 1] ?? null;
  const returnTo = location.pathname + location.search;
  const setPageNext = useCallbackMap(setNextByPage);
  return (
    <SectionCard
      title="거래별 구성원"
      description={first.data ? `거래 ${number(first.data.total)}건 · ${memberOrderNotes[flowBasis]}` : memberOrderNotes[flowBasis]}
      aria-label="거래별 구성원"
    >
      <Table aria-label="거래별 구성원 표">
        <TableHeader>
          <TableRow>
            <TableHead>거래 ID</TableHead>
            <TableHead>인터페이스</TableHead>
            <TableHead title={flowBasis === "source" ? undefined : timeBasisTitle}>{memberTimeHeads[flowBasis]}</TableHead>
            <TableHead>판정(단계)</TableHead>
            <TableHead>상태</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {cursors.map((cursor, index) => (
            <MemberPage
              key={cursor ?? "first"}
              id={id}
              cursor={cursor}
              returnTo={returnTo}
              flowBasis={flowBasis}
              onNext={setPageNext(index)}
            />
          ))}
        </TableBody>
      </Table>
      {next && (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" onClick={() => setCursors((list) => [...list, next])}>
            더 보기
          </Button>
        </div>
      )}
    </SectionCard>
  );
};

/** 사건 상세(contracts/incidents-ui.md 5절): 머리(기준·값·기간·건수) · 이어진 구간(2개 이상일 때) · 거래별 구성원. */
export const IncidentDetailView = ({
  query,
  backHref,
  isWide,
  onToggleWide,
}: {
  query: Query<IncidentDetail>;
  backHref: string;
  isWide: boolean;
  onToggleWide: (value: boolean) => void;
}) => {
  const detail = query.data;
  let body: ReactNode;
  if (query.isPending && !detail) body = <LoadingState />;
  else if (!detail) body = <ErrorState title={incidentErrorTitle(query.error, "사건")} onRetry={() => void query.refetch()} />;
  else {
    const counts = detail.counts;
    body = (
      <>
        <SectionCard
          title={criteriaText(detail.criteria)}
          description={INCIDENT_CRITERIA_NOTE}
          aria-label="사건 요약"
          action={[
            ...detail.flags.map((flag) => (
              <Badge key={flag} variant="warning" title={incidentFlagTitles[flag]}>
                {incidentFlagLabels[flag]}
              </Badge>
            )),
            ...(detail.truncated
              ? [
                  <Badge key="partial" variant="outline" title={incidentPartialTitle}>
                    {INCIDENT_PARTIAL}
                  </Badge>,
                ]
              : []),
          ]}
        >
          <DescriptionList
            items={[
              { label: "묶은 기준", value: criteriaKindLabel(detail.criteria.kind) },
              {
                label: "기준값",
                value: detail.criteria.value ? (
                  <span className="line-clamp-3 font-mono break-all" title={detail.criteria.value}>
                    {detail.criteria.value}
                  </span>
                ) : (
                  <span className="text-muted-foreground" title={detail.criteria.longValue ? longValueOmittedTitle : undefined}>
                    {criteriaValueText(detail.criteria)}
                  </span>
                ),
              },
              {
                label: "기간",
                value: (
                  <span className="flex flex-wrap items-center gap-1.5">
                    {`${stampText(detail.startAt)} ~ ${stampText(detail.endAt)} · ${durationText(detail.durationMs)}`}
                    <BasisMark kind={timeBasisKind(detail.occurredBasis)} />
                  </span>
                ),
              },
              { label: "거래 수", value: `${number(counts.transactions)}건` },
              { label: "판정 수", value: `${number(counts.decisions)}건` },
              {
                label: RELEASED,
                value: counts.releasedTransactions || counts.releasedDecisions ? `거래 ${number(counts.releasedTransactions)}건 · 판정 ${number(counts.releasedDecisions)}건` : DASH,
              },
              { label: "구간", value: `${detail.segments.length}개` },
              { label: "묶음 간격 · 구간 최대 길이", value: `${secondsText(detail.criteria.gapSeconds)} · ${secondsText(detail.criteria.maxSpanSeconds)}` },
              ...(counts.missingTransactionId ? [{ label: "거래 ID 없음 판정", value: `${number(counts.missingTransactionId)}건` }] : []),
              ...(counts.unknownGrade ? [{ label: `${GRADE_UNKNOWN} 판정`, value: `${number(counts.unknownGrade)}건 · 해제로 셈` }] : []),
            ]}
          />
        </SectionCard>
        {detail.segments.length > 1 && (
          <SectionCard title="이어진 구간" description="구간 최대 길이를 넘어 끊긴 뒤 이어진 구간" aria-label="이어진 구간">
            <Table aria-label="이어진 구간 표">
              <TableHeader>
                <TableRow>
                  <TableHead>구간</TableHead>
                  <TableHead>기간</TableHead>
                  <TableHead className="text-right">거래 수</TableHead>
                  <TableHead className="text-right">판정 수</TableHead>
                  <TableHead className="text-right">병합된 사건</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {detail.segments.map((segment, index) => (
                  <TableRow key={segment.id} data-segment-row={index + 1}>
                    <TableCell title={segment.id}>{index + 1}</TableCell>
                    <TableCell className="tabular-nums">
                      {stampText(segment.openedAt)} ~ {stampText(segment.endAt)} · {durationText(segment.durationMs)}
                      {timeBasisKind(detail.occurredBasis) === "mixed" && timeBasisKind(segment.occurredBasis) !== "source" && (
                        <>
                          {" "}
                          <BasisMark kind={timeBasisKind(segment.occurredBasis)} />
                        </>
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{number(segment.counts.transactions)}</TableCell>
                    <TableCell className="text-right tabular-nums">{number(segment.counts.decisions)}</TableCell>
                    <TableCell className="text-right tabular-nums">{segment.mergedIncidentIds.length || DASH}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </SectionCard>
        )}
        <Members key={detail.id} id={detail.id} basis={detail.occurredBasis} />
      </>
    );
  }
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <Button asChild variant="ghost" size="sm">
          <Link to={backHref}>
            <IconArrowLeft aria-hidden="true" />
            사건 목록
          </Link>
        </Button>
        <Button
          variant="outline"
          size="icon"
          onClick={() => onToggleWide(!isWide)}
          aria-label={isWide ? "콤팩트 화면으로 보기" : "넓은 화면으로 보기"}
        >
          {isWide ? <IconArrowsMinimize aria-hidden="true" /> : <IconArrowsMaximize aria-hidden="true" />}
        </Button>
      </div>
      {body}
    </div>
  );
};
