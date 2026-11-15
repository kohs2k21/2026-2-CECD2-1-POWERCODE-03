import { useLocation, useSearchParams } from "react-router-dom";
import { LinkTabs } from "@/components/layout/LinkTabs";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { urlOptions } from "@/app/routes";
import { stampText } from "@/features/operations/format";
import { useUrlOption } from "@/hooks/useUrlState";
import { protocolDraftsQueryName, useProtocolDrafts } from "../protocols/useProtocolDrafts";
import type { CandidateList } from "./candidateApi";
import { CandidateJobRequest, LatestCandidateJob } from "./CandidateJobRequest";
import { CANDIDATES_TITLE, RECOMMEND_DESCRIPTION, TAB_ANOMALY, TAB_FLOW, candidateErrorTitle, n, tabText } from "./candidateLabels";
import { AnomalyTab } from "./RecommendAnomalyTab";
import { FlowTab } from "./RecommendFlowTab";
import { anomalyCount, protocolCount, relationCount, sumCount, type CandidateView } from "./recommendations";
import { candidateListQueryName, useCandidateList } from "./useCandidates";

/** 후보 작업 접수 뒤 다시 읽을 조회: 규칙 추천 목록 + 같은 작업 결과의 관측 경로 초안(protocol-settings-ui.md 2절). */
const JOB_REFRESH = [candidateListQueryName, protocolDraftsQueryName] as const;

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

/**
 * 탐지 설정 › 룰 설정 › 규칙 추천(contracts/rule-candidates-ui.md 8절): 룰 설정 안의 추천을 한 곳에 모은 화면.
 * 머리(설명·[후보 작업 요청]·결과 머리) + 탭(주소 `tab`) 이상 징후 / 실행 흐름 규칙. 주소 `interface`로 인터페이스 선택, 행 → 후보 상세·프로토콜 상세.
 */
export const CandidateListPage = () => {
  const [search, setSearch] = useSearchParams();
  const location = useLocation();
  const tab = useUrlOption("tab", urlOptions.ruleCandidatesTab);
  const requested = search.get("interface");
  const query = useCandidateList(requested);
  const drafts = useProtocolDrafts(null);
  const data = query.data;
  const onInterface = (id: string) => {
    const next = new URLSearchParams(search);
    next.set("interface", id);
    setSearch(next, { state: location.state });
  };
  const returnTo = location.pathname + location.search;
  const view: CandidateView = { data, pending: query.isPending && !data, onInterface, detailState: { returnTo } };
  const counts = { anomaly: anomalyCount(data), flow: sumCount(relationCount(data), protocolCount(drafts.data)) };
  return (
    <div className="grid min-w-0 gap-6">
      <ScreenTitle>{CANDIDATES_TITLE}</ScreenTitle>
      <SectionCard
        title={CANDIDATES_TITLE}
        description={RECOMMEND_DESCRIPTION}
        action={data && <CandidateJobRequest latestJob={data.latestJob} currentSnapshotId={data.result?.snapshotId ?? null} refreshQueryNames={JOB_REFRESH} />}
      >
        {query.isPending && !data ? (
          <LoadingState />
        ) : !data ? (
          <ErrorState title={candidateErrorTitle(query.error, CANDIDATES_TITLE)} onRetry={() => void query.refetch()} />
        ) : (
          <>
            {query.isError && <ErrorState title={`${candidateErrorTitle(query.error, CANDIDATES_TITLE)} · 이전 결과 표시`} onRetry={() => void query.refetch()} />}
            <ResultHeader data={data} returnTo={returnTo} />
            {!data.result && <EmptyState>후보 작업 결과가 없습니다.</EmptyState>}
          </>
        )}
      </SectionCard>
      <LinkTabs
        label={CANDIDATES_TITLE}
        activeId={tab.value}
        tabs={[
          { id: "anomaly", label: tabText(TAB_ANOMALY, counts.anomaly), to: tab.hrefFor("anomaly") },
          { id: "flow", label: tabText(TAB_FLOW, counts.flow), to: tab.hrefFor("flow") },
        ]}
      />
      {tab.value === "anomaly" ? <AnomalyTab view={view} /> : <FlowTab view={view} drafts={drafts} />}
    </div>
  );
};
