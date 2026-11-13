import { lazy, Suspense, useMemo } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Combobox } from "@/components/ui/combobox";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { paths } from "@/app/routes";
import { FlowViewBox } from "@/features/analysis/components/ProcessFlowCard";
import { stampText } from "@/features/operations/format";
import type { DetectionReturnState } from "../../DetectionLayout";
import type { CandidateList, RelationCandidate, ThresholdCandidate } from "./candidateApi";
import { CandidateJobRequest, LatestCandidateJob } from "./CandidateJobRequest";
import {
  CANDIDATES_TITLE, INTERFACE_SEARCH, INTERFACE_SEARCH_EMPTY, PAIR_LISTED, RELATION_SECTION, THRESHOLD_BASIS, THRESHOLD_SECTION, adapterText, candidateErrorTitle, kindLabels, limitationTexts, n, pathText,
  rateText, relationStatusBadge, relationStatusLabels, thresholdStatusLabels, thresholdText,
} from "./candidateLabels";
import { useCandidateList } from "./useCandidates";

const PatternGraphView = lazy(() => import("./PatternGraphView"));
type Result = NonNullable<CandidateList["result"]>;

/** 결과 머리: 작업 완료 시각·학습 스냅샷 기간 + 결과 작업과 다른 최근 작업 상태(대기·실패 등, 작업 상세로 링크). */
const ResultHeader = ({ data, returnTo }: { data: CandidateList; returnTo: string }) => {
  const result = data.result;
  const newer = data.latestJob && data.latestJob.id !== result?.job.id ? data.latestJob : null;
  return (
    <div data-candidate-job className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
      {result && (
        <>
          <span>후보 작업 완료 {stampText(result.job.finishedAt)}</span>
          <span>기준 기간 {stampText(result.period.from)} ~ {stampText(result.period.until)}</span>
          <span>
            관계 후보 {n(result.totals.relationCandidates)}건(승인 가능 {n(result.totals.approvable)}) · 처리 시간 후보 {n(result.totals.thresholdCandidates)}건(후보 {n(result.totals.thresholdApprovable)})
          </span>
        </>
      )}
      {newer && <LatestCandidateJob job={newer} returnTo={returnTo} />}
    </div>
  );
};

const CountCells = ({ item }: { item: RelationCandidate }) => (
  <>
    <TableCell className="text-right tabular-nums">{n(item.counts.applicable)}</TableCell>
    <TableCell className="text-right tabular-nums">{n(item.counts.satisfied)}</TableCell>
    <TableCell className="text-right tabular-nums">{item.counts.violated ? n(item.counts.violated) : "—"}</TableCell>
    <TableCell className="text-right tabular-nums">{item.counts.undetermined ? n(item.counts.undetermined) : "—"}</TableCell>
  </>
);

const RelationTable = ({ items, detailState }: { items: RelationCandidate[]; detailState: DetectionReturnState }) =>
  items.length ? (
    <Table aria-label={`${RELATION_SECTION} 표`}>
      <TableHeader>
        <TableRow>
          <TableHead>종류</TableHead>
          <TableHead>경로·단계</TableHead>
          <TableHead>어댑터</TableHead>
          <TableHead className="text-right">적용</TableHead>
          <TableHead className="text-right">충족</TableHead>
          <TableHead className="text-right">위반</TableHead>
          <TableHead className="text-right">보류</TableHead>
          <TableHead className="text-right">충족률</TableHead>
          <TableHead>상태</TableHead>
          <TableHead>승인 가능</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => (
          <TableRow key={item.candidateId} data-candidate-row={item.candidateId}>
            <TableCell>{kindLabels[item.kind]}</TableCell>
            <TableCell>
              <Link to={paths.detectionRuleCandidate(item.candidateId)} state={detailState} className="font-mono text-sm text-link hover:underline">
                {pathText(item)}
              </Link>
            </TableCell>
            <TableCell>{adapterText(item)}</TableCell>
            <CountCells item={item} />
            <TableCell className="text-right tabular-nums">{rateText(item.satisfactionRate)}</TableCell>
            <TableCell>
              <span className="flex flex-wrap gap-1">
                <Badge variant={relationStatusBadge[item.status]}>{relationStatusLabels[item.status]}</Badge>
                {item.listedForPairing && <Badge variant="outline">{PAIR_LISTED}</Badge>}
              </span>
            </TableCell>
            <TableCell>{item.approvable ? "가능" : "불가"}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  ) : (
    <EmptyState>이 인터페이스의 관계 규칙 후보가 없습니다.</EmptyState>
  );

const ThresholdTable = ({ items, detailState }: { items: ThresholdCandidate[]; detailState: DetectionReturnState }) =>
  items.length ? (
    <Table aria-label={`${THRESHOLD_SECTION} 표`}>
      <TableHeader>
        <TableRow>
          <TableHead>단계</TableHead>
          <TableHead>어댑터</TableHead>
          <TableHead>제안 임계값</TableHead>
          <TableHead className="text-right">근거 건수</TableHead>
          <TableHead className="text-right">END_TIME 없음</TableHead>
          <TableHead>상태</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => (
          <TableRow key={item.candidateId} data-threshold-row={item.candidateId}>
            <TableCell>
              <Link to={paths.detectionRuleCandidate(item.candidateId)} state={detailState} className="font-mono text-sm text-link hover:underline">
                {item.scope.processId}
              </Link>
              {item.interfaceIds.length > 1 && <div className="text-xs text-muted-foreground">인터페이스 {item.interfaceIds.length}곳 공통</div>}
            </TableCell>
            <TableCell>{item.scope.adapterType ?? "모든 어댑터"}</TableCell>
            <TableCell>
              <span className="font-mono">{thresholdText(item)}</span>
              {item.threshold !== null && <div className="text-xs text-muted-foreground">{THRESHOLD_BASIS}</div>}
            </TableCell>
            <TableCell className="text-right tabular-nums">{n(item.evidence.n)}</TableCell>
            <TableCell className="text-right tabular-nums">{item.evidence.endTimeMissingReferenceRows ? n(item.evidence.endTimeMissingReferenceRows) : "—"}</TableCell>
            <TableCell>
              <Badge variant={item.status === "candidate" ? "success" : "outline"}>{thresholdStatusLabels[item.status]}</Badge>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  ) : (
    <EmptyState>이 인터페이스의 처리 시간 후보가 없습니다.</EmptyState>
  );

const Sections = ({ result, onInterface, detailState }: { result: Result; onInterface: (id: string) => void; detailState: DetectionReturnState }) => {
  const selected = result.selectedInterfaceId;
  const summary = result.interfaces.find((item) => item.interfaceId === selected);
  const options = useMemo(() => result.interfaces.map((item) => ({ value: item.interfaceId, label: item.interfaceId, detail: `승인 가능 ${n(item.approvable)}` })),
    [result.interfaces]);
  return (
    <>
      <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        <span id="candidate-interface-label">인터페이스</span>
        <Combobox
          size="sm"
          aria-labelledby="candidate-interface-label"
          className="min-w-56"
          value={selected}
          onValueChange={onInterface}
          options={options}
          valueText={summary ? `${summary.interfaceId} · 승인 가능 ${n(summary.approvable)}` : undefined}
          searchLabel={INTERFACE_SEARCH}
          emptyText={INTERFACE_SEARCH_EMPTY}
        />
        {summary && <span data-interface-summary>거래 {n(summary.transactions)} · 관계 후보 {n(summary.relationCandidates)} · 처리 시간 후보 {n(summary.thresholdCandidates)}</span>}
      </div>
      <SectionCard title={RELATION_SECTION} aria-label={RELATION_SECTION}>
        {result.graph && result.graph.nodes.length ? (
          <Suspense fallback={<FlowViewBox><LoadingState /></FlowViewBox>}>
            <PatternGraphView key={selected} nodes={result.graph.nodes} edges={result.graph.edges} label={`${selected} 정상 패턴`} />
          </Suspense>
        ) : (
          <EmptyState>패턴 단계가 없습니다.</EmptyState>
        )}
        <RelationTable items={result.relationCandidates} detailState={detailState} />
      </SectionCard>
      <SectionCard title={THRESHOLD_SECTION} aria-label={THRESHOLD_SECTION}>
        <ThresholdTable items={result.thresholdCandidates} detailState={detailState} />
        {limitationTexts(result.thresholdLimitations).map((text) => (
          <p key={text} data-threshold-limitation className="text-sm text-muted-foreground">한계(Trainer) · {text}</p>
        ))}
        {result.thresholdsWithoutInterface > 0 && (
          <p data-threshold-unassigned className="text-sm text-muted-foreground">인터페이스를 알 수 없는 단계 후보 {n(result.thresholdsWithoutInterface)}건은 이 표에 없습니다.</p>
        )}
      </SectionCard>
    </>
  );
};

/** 탐지 설정 › 룰 설정 › 규칙 추천 목록(contracts/rule-candidates-ui.md 8절). 주소 `interface`로 인터페이스 선택, 행 → 후보 상세. */
export const CandidateListPage = () => {
  const [search, setSearch] = useSearchParams();
  const location = useLocation();
  const requested = search.get("interface");
  const query = useCandidateList(requested);
  const data = query.data;
  const onInterface = (id: string) => {
    const next = new URLSearchParams(search);
    next.set("interface", id);
    setSearch(next);
  };
  const detailState: DetectionReturnState = { returnTo: location.pathname + location.search };
  return (
    <div className="grid min-w-0 gap-6">
      <ScreenTitle>{CANDIDATES_TITLE}</ScreenTitle>
      <SectionCard
        title={CANDIDATES_TITLE}
        description="Trainer 후보 작업 결과 · 관계 규칙(이상)·처리 시간(이상 징후)"
        action={data && <CandidateJobRequest latestJob={data.latestJob} currentSnapshotId={data.result?.snapshotId ?? null} />}
      >
        {query.isPending && !data ? (
          <LoadingState />
        ) : !data ? (
          <ErrorState title={candidateErrorTitle(query.error, CANDIDATES_TITLE)} onRetry={() => void query.refetch()} />
        ) : (
          <>
            {query.isError && <ErrorState title={`${candidateErrorTitle(query.error, CANDIDATES_TITLE)} · 이전 결과 표시`} onRetry={() => void query.refetch()} />}
            <ResultHeader data={data} returnTo={location.pathname + location.search} />
            {!data.result && <EmptyState>후보 작업 결과가 없습니다.</EmptyState>}
          </>
        )}
      </SectionCard>
      {data?.result && <Sections result={data.result} onInterface={onInterface} detailState={detailState} />}
    </div>
  );
};
