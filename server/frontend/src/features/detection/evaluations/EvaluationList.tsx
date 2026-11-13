import { Link, useLocation } from "react-router-dom";
import { paths, urlOptions } from "@/app/routes";
import { LinkTabs } from "@/components/layout/LinkTabs";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { DASH, numberText, stampText } from "@/features/operations/format";
import { useUrlOption } from "@/hooks/useUrlState";
import { comparisonKey, type CompareVersion, type ComparisonListItem, type EvaluationSet } from "./evaluationsApi";
import { REC_STATE_LABELS, comparisonStatus, labelOf } from "./evaluationFormat";
import { useCompareVersions, useComparisons, useEvaluationSets } from "./useEvaluations";

const COLUMNS = ["비교", "평가 세트", "비교 모델", "완료", "상태", "교체 추천"];

const RecommendationCell = ({ item, versions }: { item: ComparisonListItem; versions: CompareVersion[] | undefined }) => {
  // 추천이 없으면 완료 뒤 사유(v1 `not_implemented_v1`)를 미연결로 보인다. 완료 전은 빈칸.
  if (!item.recommendations) return <>{item.recommendationsReason ? `미연결 · ${item.recommendationsReason}` : DASH}</>;
  if (!item.recommendations.length) return <>{DASH}</>;
  const several = item.recommendations.length > 1;
  return (
    <span className="grid justify-items-start gap-1">
      {item.recommendations.map(({ modelKey, targetVersionId, state }) => {
        const prefix = several ? `${modelKey} · ` : "";
        if (state === "none") return <span key={modelKey}>{prefix}{REC_STATE_LABELS.none}</span>;
        return <Badge key={modelKey} variant={state === "current" ? "info" : "secondary"}>{prefix}추천 {labelOf(versions, targetVersionId)} · {REC_STATE_LABELS[state]}</Badge>;
      })}
    </span>
  );
};

const ComparisonRow = ({ item, sets, versions, returnTo }: { item: ComparisonListItem; sets: EvaluationSet[] | undefined; versions: CompareVersion[] | undefined; returnTo: string }) => {
  const status = comparisonStatus(item.status, item.failedCount);
  const baselines = item.baselines.map((baseline) => labelOf(versions, baseline.configVersionId)).join(", ");
  return (
    <TableRow>
      <TableCell><Link className="text-link underline-offset-2 hover:underline" to={paths.detectionEvaluation(comparisonKey(item))} state={{ returnTo }}>{comparisonKey(item).slice(0, 8)}</Link></TableCell>
      <TableCell>{sets?.find((set) => set.id === item.evaluationSetId)?.displayName ?? item.evaluationSetId.slice(0, 8)}</TableCell>
      <TableCell className="whitespace-normal">{numberText(item.itemCount)}개{baselines ? ` (기준 운영 ${baselines})` : ""}</TableCell>
      <TableCell>{stampText(item.completedAt)}</TableCell>
      <TableCell><Badge variant={status.variant}>{status.label}</Badge></TableCell>
      <TableCell><RecommendationCell item={item} versions={versions} /></TableCell>
    </TableRow>
  );
};

/** 비교 평가 목록: 탭 2개 + 자체 스크롤(사용자 9.8·9.9). 아래 새 비교 평가 영역은 같은 화면이다. */
export const ComparisonsCard = () => {
  const location = useLocation();
  const tab = useUrlOption("tab", urlOptions.evaluationsTab);
  const list = useComparisons(tab.value);
  const sets = useEvaluationSets();
  const versions = useCompareVersions();
  const { first, items } = list;
  return (
    <SectionCard title="비교 평가">
      <LinkTabs
        label="평가 보기"
        activeId={tab.value}
        tabs={[{ id: "current", label: "현재 운영 모델 기준", to: tab.hrefFor("current") }, { id: "all", label: "전체 평가", to: tab.hrefFor("all") }]}
      />
      {first.isPending && <LoadingState>평가 조회 중…</LoadingState>}
      {first.isError && <ErrorState title={`평가 조회 실패 · ${errorCode(first.error)}`} onRetry={() => void first.refetch()} retryLabel="다시 조회" />}
      {first.data && items.length === 0 && !list.cursor && !first.isError && (
        <EmptyState>{tab.value === "current" ? "현재 운영 모델 기준 평가가 없습니다." : "비교 평가가 없습니다."}</EmptyState>
      )}
      {items.length > 0 && (
        <div className="max-h-80 overflow-auto" tabIndex={0} aria-label="평가 목록">
          <Table>
            <TableHeader>
              <TableRow>{COLUMNS.map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => <ComparisonRow key={comparisonKey(item)} item={item} sets={sets.data?.items} versions={versions.data} returnTo={location.pathname + location.search} />)}
            </TableBody>
          </Table>
        </div>
      )}
      {list.cursor && <div><Button size="sm" variant="outline" disabled={list.loading} onClick={() => void list.loadMore()}>더 보기</Button></div>}
    </SectionCard>
  );
};
