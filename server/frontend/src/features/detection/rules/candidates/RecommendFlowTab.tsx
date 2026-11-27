import { lazy, Suspense, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { DelayedLoadingState } from "@/components/ui/pending";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { paths } from "@/app/routes";
import { FlowViewBox } from "@/features/analysis/components/ProcessFlowCard";
import { DASH, stampText } from "@/features/operations/format";
import { PROTOCOL_ITEM_TERM, errorCode } from "../ruleDraft";
import type { RuleSetState } from "../ruleSetApi";
import { count, draftUnavailableText } from "../protocols/protocolLabels";
import type { useProtocolDrafts } from "../protocols/useProtocolDrafts";
import { useRowApproval, type RowApproval } from "./approvalFlow";
import type { Approval, CandidateList, RelationCandidate } from "./candidateApi";
import {
  APPROVE, PAIR_LISTED, PATH_SECTION, RELATION_SECTION, STEP_SECTION, adapterText, kindLabels, n, pathText, rateText, relationStatusBadge, relationStatusLabels,
} from "./candidateLabels";
import { InterfacePicker } from "./InterfacePicker";
import { recommendSections, type CandidateView } from "./recommendations";

const PatternGraphView = lazy(() => import("./PatternGraphView"));
type Drafts = ReturnType<typeof useProtocolDrafts>;
type Result = NonNullable<CandidateList["result"]>;

/**
 * 경로(정상 프로토콜): 관측 경로 초안의 인터페이스 요약(protocol-settings-ui.md 2절 GET /api/rule-set/protocol-drafts) + 등록 여부.
 * 행 → 그 인터페이스 프로토콜 상세(초안 경로 선택·승인 저장·LLM 경로 설명은 상세의 기존 흐름 그대로).
 */
const PathsCard = ({ drafts, state, detailState }: { drafts: Drafts; state: RuleSetState | undefined; detailState: CandidateView["detailState"] }) => {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const available = drafts.data?.state === "available" ? drafts.data : null;
  const entryOf = (interfaceId: string) => state?.protocolRules?.find((rule) => rule.interfaceId === interfaceId) ?? null;
  const rows = available?.interfaces ?? [];
  const needle = search.trim().toLowerCase();
  const shown = needle ? rows.filter((row) => row.interfaceId.toLowerCase().includes(needle)) : rows;
  return (
    <SectionCard
      id={recommendSections.paths}
      title={PATH_SECTION}
      aria-label={PATH_SECTION}
      description={
        <span className="flex flex-wrap gap-x-4 gap-y-1">
          {drafts.isPending && <span>관측 경로 초안 조회 중…</span>}
          {drafts.isError && <span data-drafts-error>관측 경로 초안 조회 실패 · {errorCode(drafts.error)}</span>}
          {drafts.data?.state === "protocol_drafts_unavailable" && <span data-drafts-unavailable>{draftUnavailableText(drafts.data)}</span>}
          {available && <span data-drafts-head>관측 경로 초안 · 후보 작업 완료 {stampText(available.job.finishedAt)} · 기준 기간 {stampText(available.period.from)} ~ {stampText(available.period.until)}</span>}
        </span>
      }
    >
      {available && (
        <div className="max-w-sm">
          <Input type="search" aria-label="인터페이스 검색" placeholder="인터페이스 ID 검색" value={search} onChange={(event) => setSearch(event.target.value)} />
        </div>
      )}
      {available && (shown.length === 0 ? (
        <EmptyState>{rows.length ? "검색 결과가 없습니다." : `${PROTOCOL_ITEM_TERM}: 표시할 항목이 없습니다.`}</EmptyState>
      ) : (
        <Table aria-label={PATH_SECTION}>
          <TableHeader>
            <TableRow>{["인터페이스", "허용 경로", "상태", "학습 기간 관측"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((row) => {
              const entry = entryOf(row.interfaceId);
              const to = paths.detectionRuleProtocol(row.interfaceId);
              return (
                <TableRow key={row.interfaceId} data-path-row={row.interfaceId} className="cursor-pointer" onClick={() => navigate(to, { state: detailState })}>
                  <TableCell className="font-mono text-sm whitespace-pre">
                    <Link to={to} state={detailState} className="text-link hover:underline" onClick={(event) => event.stopPropagation()}>{row.interfaceId}</Link>
                  </TableCell>
                  <TableCell>{entry ? `${count(entry.paths.length)}개` : DASH}</TableCell>
                  <TableCell>
                    {!state ? DASH : entry
                      ? <Badge variant={entry.enabled ? "success" : "outline"}>{entry.enabled ? "켜짐" : "꺼짐"}</Badge>
                      : <Badge variant="warning" data-unregistered>미등록</Badge>}
                  </TableCell>
                  <TableCell>{`경로 ${count(row.distinctPaths)}개 · 거래 ${count(row.pathTransactions)}건${row.status === "insufficient_evidence" ? " · 근거 부족" : ""}`}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      ))}
    </SectionCard>
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

/** 관계 규칙 후보 표(규칙 추천 목록의 표 그대로) + 행 [승인](후보 상세와 같은 흐름). */
const RelationTable = ({ items, available, approval, detailState }: {
  items: RelationCandidate[]; available: Approval; approval: RowApproval; detailState: CandidateView["detailState"];
}) =>
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
          <TableHead />
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
            <TableCell>
              <span className="grid justify-items-start gap-1">
                <Button size="sm" disabled={!available.available || !item.approvable || !approval.state || approval.busy} onClick={() => approval.start(item.candidateId)}>{APPROVE}</Button>
                {approval.noteFor(item.candidateId)}
              </span>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  ) : (
    <EmptyState>이 인터페이스의 관계 규칙 후보가 없습니다.</EmptyState>
  );

/** 단계 조건(관계 규칙): 인터페이스 선택 → 정상 패턴 그래프 + 관계 규칙 후보 표(필수 단계·선행·메시지 인계)와 [승인]. */
const RelationsCard = ({ view, approval }: { view: CandidateView; approval: RowApproval }) => {
  const result: Result | null = view.data?.result ?? null;
  const selected = result?.selectedInterfaceId ?? null;
  const graph = useMemo(() => result?.graph ?? null, [result]);
  return (
    <SectionCard id={recommendSections.relations} title={STEP_SECTION} aria-label={STEP_SECTION}>
      {approval.stateError && <ErrorState title={`현재 운영 룰 조회 실패 · ${errorCode(approval.rules.error)}`} onRetry={() => void approval.rules.refetch()} retryLabel="다시 조회" />}
      {approval.banner}
      {view.pending ? (
        <LoadingState />
      ) : !result ? (
        view.data ? <EmptyState>후보 작업 결과가 없습니다.</EmptyState> : <span className="text-sm text-muted-foreground">{DASH}</span>
      ) : (
        <>
          <InterfacePicker result={result} onInterface={view.onInterface} />
          {graph && graph.nodes.length ? (
            <Suspense fallback={<FlowViewBox><DelayedLoadingState /></FlowViewBox>}>
              <PatternGraphView key={selected} nodes={graph.nodes} edges={graph.edges} label={`${selected} 정상 패턴`} />
            </Suspense>
          ) : (
            <EmptyState>패턴 단계가 없습니다.</EmptyState>
          )}
          <RelationTable items={result.relationCandidates} available={view.data!.approval} approval={approval} detailState={view.detailState} />
        </>
      )}
      {approval.dialogs}
    </SectionCard>
  );
};

/** 실행 흐름 규칙 탭: 경로(정상 프로토콜) + 단계 조건(관계 규칙). */
export const FlowTab = ({ view, drafts }: { view: CandidateView; drafts: Drafts }) => {
  const approval = useRowApproval();
  return (
    <>
      <PathsCard drafts={drafts} state={approval.state} detailState={view.detailState} />
      <RelationsCard view={view} approval={approval} />
    </>
  );
};
