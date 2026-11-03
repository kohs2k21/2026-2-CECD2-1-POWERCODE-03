import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SideNav, type SideNavGroup } from "@/components/layout/SideNav";
import { paths } from "@/app/routes";
import { getStored, setStored, storageKeys } from "@/lib/storage";
import { categories, listSearch, parseListSearch, type DecisionList, type ListParams } from "./data/analysisApi";
import { useDecisionDetail, useDecisionInterims, useDecisionList, useDecisionLiveUpdates } from "./data/useAnalysis";
import { DecisionDetailView } from "./components/DecisionDetailView";
import { DecisionListView } from "./components/DecisionListView";
import { categoryLabels } from "./labels";

/** 사이드바 판정 결과별 분류(건수 = 서버 전체 기준, 검색·필터와 무관). 다른 조건은 유지하고 페이지만 1로. */
export const sidebarGroups = (params: ListParams, counts: DecisionList["counts"] | undefined): SideNavGroup[] => [
  {
    title: "판정",
    items: categories.map((category) => ({
      id: category,
      label: categoryLabels[category],
      count: counts?.[category] ?? undefined,
      to: paths.analysis + listSearch({ ...params, category, page: 1 }),
    })),
  },
];

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
  const list = useDecisionList(params);
  const live = useDecisionLiveUpdates(list.data?.latestEventId ?? null);
  const detail = useDecisionDetail(decisionId);
  const interims = useDecisionInterims(decisionId);

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
  const groups = useMemo(() => sidebarGroups(params, list.data?.counts), [params, list.data?.counts]);
  const search = listSearch(params, location.search);

  return (
    <div className="grid gap-6">
      <ScreenTitle>이상 분석</ScreenTitle>
      <div className="flex flex-col gap-6 md:flex-row md:items-start">
        {!isWide && <SideNav label="판정 분류" menuId="analysis-categories" activeId={decisionId ? null : params.category} groups={groups} />}
        <section className="min-w-0 flex-1" aria-label={decisionId ? "판정 상세" : "판정 목록"}>
          {decisionId ? (
            <DecisionDetailView
              key={decisionId}
              id={decisionId}
              query={detail}
              interims={interims}
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
