import { useCallback, useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SideNav, type SideNavGroup } from "@/components/layout/SideNav";
import { paths } from "@/app/routes";
import { getStored, setStored, storageKeys } from "@/lib/storage";
import {
  listSearch,
  parseListSearch,
  resultCategories,
  riskCategories,
  workflowCategories,
  type Category,
  type DecisionList,
  type ListParams,
} from "./data/analysisApi";
import { useDecisionDetail, useDecisionFlow, useDecisionInterims, useDecisionList, useDecisionLiveUpdates } from "./data/useAnalysis";
import { DecisionDetailView } from "./components/DecisionDetailView";
import { DecisionListView } from "./components/DecisionListView";
import { categoryLabels } from "./labels";

export type AnalysisView = "risk" | "result";
const isRisk = (category: Category) => (riskCategories as readonly string[]).includes(category);
const isResult = (category: Category) => (resultCategories as readonly string[]).includes(category);

/**
 * 사이드바(와이어프레임 analysisSide): 보기(위험도별 / 판정 결과별)에 따라 `위험도` 또는 `판정` 분류 + 아래 별도 구역 `분류함`(보류·완료).
 * 건수 = 서버 전체 기준(검색·필터와 무관). 다른 조건은 유지하고 페이지만 1로.
 */
export const sidebarGroups = (params: ListParams, counts: DecisionList["counts"] | undefined, view: AnalysisView): SideNavGroup[] => {
  const item = (category: Category) => ({
    id: category,
    label: categoryLabels[category],
    count: counts?.[category] ?? undefined,
    to: paths.analysis + listSearch({ ...params, category, page: 1 }),
    // 와이어프레임 riskSide: 위험도 미정은 약하게(muted, 13px)
    muted: category === "risk_unrated" || undefined,
  });
  return [
    view === "risk" ? { title: "위험도", items: riskCategories.map(item) } : { title: "판정", items: resultCategories.map(item) },
    { title: "분류함", items: workflowCategories.map(item), separated: true },
  ];
};

/**
 * 이상 분석: 판정 결과별 분류 사이드바 + 판정 목록(`/analysis`) / PROCESS 판정 상세(`/analysis/d/:decisionId`)(와이어프레임 #93 analysis).
 * 목록 조건은 주소 쿼리(category·q·signal·completeness·sort·page·pageSize), 상세 주소도 같은 쿼리를 유지해 목록으로 돌아간다.
 * 새 판정은 SSE(/api/pipeline/events)로 받아 목록·건수를 다시 조회한다(주기 조회 없음).
 */
export const AnalysisPage = () => {
  const { decisionId = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { params, invalid } = parseListSearch(location.search);
  const [isWide, setIsWide] = useState<boolean>(() => getStored(storageKeys.layoutWide("analysis"), false));
  // 보기: 위험도 분류면 위험도별, 판정 결과 분류면 판정 결과별, 분류함은 직전 보기를 유지한다.
  // 분류함 주소로 바로 들어오면 와이어프레임 기본 보기(위험도별, analysisView='risk')
  const [lastView, setLastView] = useState<AnalysisView>(() => (isResult(params.category) ? "result" : "risk"));
  const view: AnalysisView = isRisk(params.category) ? "risk" : isResult(params.category) ? "result" : lastView;
  useEffect(() => setLastView(view), [view]);
  const list = useDecisionList(params);
  const live = useDecisionLiveUpdates(list.data?.latestEventId ?? null);
  const detail = useDecisionDetail(decisionId);
  const interims = useDecisionInterims(decisionId);
  const flow = useDecisionFlow(decisionId);

  // 잘못된 조건은 기본값으로 replace 정규화(기타 쿼리·state 보존).
  useEffect(() => {
    if (invalid) navigate({ pathname: location.pathname, search: listSearch(params, location.search) }, { replace: true, state: location.state });
  }, [invalid, location, navigate, params]);
  // 페이지가 범위를 넘으면(건수가 줄었거나 주소 직접 입력) 마지막 페이지로 replace.
  const lastPage = list.data && !list.isPlaceholderData ? Math.max(1, Math.ceil(list.data.total / params.pageSize)) : null;
  useEffect(() => {
    if (!decisionId && lastPage !== null && params.page > lastPage)
      navigate({ pathname: location.pathname, search: listSearch({ ...params, page: lastPage }, location.search) }, { replace: true });
  }, [decisionId, lastPage, location, navigate, params]);
  // 작업 ID로 연 상세에 최종 판정이 생기면 판정 ID 주소로 바꾼다.
  const resolvedId = detail.data?.id;
  useEffect(() => {
    if (decisionId && resolvedId && resolvedId !== decisionId)
      navigate({ pathname: paths.analysisDecision(resolvedId), search: location.search }, { replace: true });
  }, [decisionId, location.search, navigate, resolvedId]);

  const onChange = useCallback(
    (next: Partial<ListParams>, replace = false) => {
      const current = parseListSearch(location.search).params;
      navigate({ pathname: paths.analysis, search: listSearch({ ...current, ...next }, location.search) }, { replace });
    },
    [location.search, navigate],
  );
  const onToggleWide = (value: boolean) => {
    setIsWide(value);
    setStored(storageKeys.layoutWide("analysis"), value);
  };
  const groups = useMemo(() => sidebarGroups(params, list.data?.counts, view), [params, list.data?.counts, view]);
  // 와이어프레임 analysis-view: 보기를 바꾸면 그 보기의 전체 분류(위험도별 = 위험도 전체, 판정 결과별 = 전체)로, 1쪽.
  const viewSwitch = (
    <div role="group" aria-label="분류 보기" className="grid grid-cols-2 gap-1 px-1">
      {(
        [
          ["risk", "위험도별", "risk_all"],
          ["result", "판정 결과별", "all"],
        ] as const
      ).map(([mode, label, category]) => (
        <Button
          key={mode}
          size="sm"
          variant={view === mode ? "default" : "outline"}
          aria-pressed={view === mode}
          onClick={() => {
            setLastView(mode);
            navigate({ pathname: paths.analysis, search: listSearch({ ...params, category, page: 1 }, location.search) });
          }}
        >
          {label}
        </Button>
      ))}
    </div>
  );
  const search = listSearch(params, location.search);

  return (
    <div className="grid gap-6">
      <ScreenTitle>이상 분석</ScreenTitle>
      <div className="flex flex-col gap-6 md:flex-row md:items-start">
        {!isWide && (
          <SideNav label="판정 분류" menuId="analysis-categories" activeId={decisionId ? null : params.category} groups={groups} header={viewSwitch} />
        )}
        <section className="min-w-0 flex-1" aria-label={decisionId ? "판정 상세" : "판정 목록"}>
          {decisionId ? (
            <DecisionDetailView
              key={decisionId}
              id={decisionId}
              query={detail}
              interims={interims}
              flow={flow}
              backHref={paths.analysis + search}
              isWide={isWide}
              onToggleWide={onToggleWide}
            />
          ) : (
            <DecisionListView
              params={params}
              query={list}
              detailHref={(id) => paths.analysisDecision(id) + search}
              onChange={onChange}
              isWide={isWide}
              onToggleWide={onToggleWide}
              live={live}
            />
          )}
        </section>
      </div>
    </div>
  );
};
