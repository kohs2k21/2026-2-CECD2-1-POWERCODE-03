import { useCallback, useEffect, useMemo, useState } from "react";
import { IconTimelineEvent } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SideNav, type SideNavGroup } from "@/components/layout/SideNav";
import { paths } from "@/app/routes";
import { PageFade } from "@/components/layout/PageFade";
import { cn } from "@/lib/utils";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import {
  isOutcomeCategory,
  listSearch,
  outcomeCategories,
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
import { categoryIcons } from "./icons";
import { categoryLabels } from "./labels";
import { incidentSearch, parseIncidentSearch, periodFrom, type IncidentPeriod } from "./incidents/incidentApi";
import { IncidentDetailView } from "./incidents/IncidentDetailView";
import { incidentNavGroupTitle, incidentNavLabel } from "./incidents/incidentLabels";
import { IncidentListView } from "./incidents/IncidentListView";
import { useIncidentDetail, useIncidentList } from "./incidents/useIncidents";

export type AnalysisView = "risk" | "result";
const isRisk = (category: Category) => (riskCategories as readonly string[]).includes(category);
const isResult = (category: Category) => (resultCategories as readonly string[]).includes(category);

/**
 * 사이드바(와이어프레임 analysisSide): 보기(위험도별 / 판정 결과별)에 따라 `위험도` 또는 `판정` 분류 + 아래 별도 구역 `분류함`(보류·완료).
 * 건수 = 서버 전체 기준(검색·필터와 무관). 다른 조건은 유지하고 페이지만 1로.
 * 분류 아이콘은 모든 그룹에 둔다(`icons.ts`, 사용자 11/05).
 * activeId = SideNav 현재 항목(상세·사건 화면이면 null — 하위 항목도 현재 표시 없음).
 * 관리자(admin=true)는 분류함 `완료` 아래에 확인 결과별 하위 항목(실제 이상·정상·판단 불가, 사용자 11/15)과 건수가 붙는다. 조사·조치 기록이 관리자 전용이라 user에게는 없다.
 */
export const sidebarGroups = (params: ListParams, counts: DecisionList["counts"] | undefined, view: AnalysisView, admin = false,
  activeId: string | null = params.category): SideNavGroup[] => {
  const item = (category: Category) => {
    const Icon = categoryIcons[category];
    return {
      id: category,
      label: categoryLabels[category],
      icon: <Icon aria-hidden="true" />,
      count: counts?.[category] ?? undefined,
      to: paths.analysis + listSearch({ ...params, category, page: 1 }),
      // 와이어프레임 riskSide: 위험도 미정은 약하게(muted, 13px)
      muted: category === "risk_unrated" || undefined,
    };
  };
  const outcomeSub = outcomeCategories.map((category) => ({
    id: category,
    label: categoryLabels[category],
    count: counts?.[category] ?? undefined,
    to: paths.analysis + listSearch({ ...params, category, page: 1 }),
    current: activeId === category,
    currentType: "page" as const,
  }));
  const workflowItems = workflowCategories.map((category) =>
    // 확인 결과별 하위 항목은 늘 펼친 채(메뉴를 바꿔도 접히지 않음, 사용자 11/28).
    admin && category === "workflow_resolved" ? { ...item(category), sub: outcomeSub, subFixed: true } : item(category),
  );
  return [
    view === "risk" ? { title: "위험도", items: riskCategories.map(item) } : { title: "판정", items: resultCategories.map(item) },
    { title: "분류함", items: workflowItems, separated: true },
  ];
};

/** 사이드바 별도 구역 "사건"(사건 보기, contracts/incidents-ui.md 5절). 판정 분류 그룹(sidebarGroups) 아래에 붙인다. */
export const incidentNavGroup = (): SideNavGroup => ({
  title: incidentNavGroupTitle,
  separated: true,
  items: [{ id: "incidents", label: incidentNavLabel, icon: <IconTimelineEvent aria-hidden="true" />, to: paths.analysisIncidents }],
});
const returnToOf = (state: unknown) => {
  const value = state && typeof state === "object" ? (state as { returnTo?: unknown }).returnTo : undefined;
  return typeof value === "string" ? value : null;
};
/** 알림 페이지 주소(`/notifications`, 쿼리 포함)만. `/notifications-x` 같은 다른 주소는 아니다. */
const isNotificationsPath = (value: string) => value === paths.notifications || value.startsWith(paths.notifications + "?");
/** 알림 페이지에서 연 상세의 돌아가기(state.returnTo, 이름표 "알림으로"). 판정·사건 상세 공통. */
export const NOTIFICATIONS_BACK_LABEL = "알림으로";
const notificationsReturn = (state: unknown) => {
  const value = returnToOf(state);
  return value && isNotificationsPath(value) ? value : null;
};
/**
 * 판정 상세의 돌아가기: 사건 화면에서 왔으면 그 주소("사건으로"), 알림 페이지에서 왔으면 그 주소("알림으로"),
 * 그 밖은 판정 목록("목록"). 넘겨받은 주소가 이 두 곳이 아니면 무시한다.
 */
const decisionReturn = (state: unknown): { href: string; label: string } | null => {
  const value = returnToOf(state);
  if (value?.startsWith(paths.analysisIncidents)) return { href: value, label: "사건으로" };
  if (value && isNotificationsPath(value)) return { href: value, label: NOTIFICATIONS_BACK_LABEL };
  return null;
};

/**
 * 이상 분석: 판정 결과별 분류 사이드바 + 판정 목록(`/analysis`) / PROCESS 판정 상세(`/analysis/d/:decisionId`)(와이어프레임 #93 analysis).
 * 목록 조건은 주소 쿼리(category·q·signal·completeness·sort·page·pageSize), 상세 주소도 같은 쿼리를 유지해 목록으로 돌아간다.
 * 새 판정은 SSE(/api/pipeline/events)로 받아 목록·건수를 다시 조회한다(주기 조회 없음).
 */
export const AnalysisPage = () => {
  const { decisionId = "", incidentId = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  // 사건 보기(/analysis/incidents, /analysis/incidents/:incidentId): 주소 쿼리 period·cursor.
  const incidentsMode = location.pathname === paths.analysisIncidents || location.pathname.startsWith(paths.analysisIncidents + "/");
  const incidentQuery = parseIncidentSearch(location.search);
  const incidentFrom = useMemo(() => periodFrom(incidentQuery.period), [incidentQuery.period]);
  const incidentList = useIncidentList(incidentFrom, incidentQuery.cursor, incidentQuery.all, incidentsMode && !incidentId);
  const incidentDetail = useIncidentDetail(incidentsMode ? incidentId : "");
  const { user } = useAuthSession();
  const isAdmin = user?.userType === "admin";
  const parsed = parseListSearch(location.search);
  // 확인 결과별 완료(관리자 전용) 주소를 user가 열면 완료로 replace(서버는 403).
  const userOutcome = Boolean(user) && !isAdmin && isOutcomeCategory(parsed.params.category);
  const params: ListParams = userOutcome ? { ...parsed.params, category: "workflow_resolved" } : parsed.params;
  const invalid = parsed.invalid || userOutcome;
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
  useEffect(() => {
    if (incidentsMode && incidentQuery.invalid)
      navigate({ pathname: location.pathname, search: incidentSearch(incidentQuery) }, { replace: true });
  }, [incidentQuery, incidentsMode, location.pathname, navigate]);
  // 병합된 사건·뒤 구간 id로 연 상세는 흐름 id(첫 구간) 주소로 바꾼다.
  const resolvedIncident = incidentDetail.data?.id;
  useEffect(() => {
    if (incidentId && resolvedIncident && resolvedIncident !== incidentId)
      navigate({ pathname: paths.analysisIncident(resolvedIncident), search: location.search }, { replace: true, state: location.state });
  }, [incidentId, location.search, location.state, navigate, resolvedIncident]);
  const onIncidentChange = useCallback(
    (next: { period?: IncidentPeriod; cursor?: string | null; all?: boolean }) => {
      const current = parseIncidentSearch(location.search);
      navigate({
        pathname: paths.analysisIncidents,
        search: incidentSearch({
          period: next.period ?? current.period,
          cursor: next.cursor === undefined ? current.cursor : next.cursor,
          all: next.all ?? current.all,
        }),
      });
    },
    [location.search, navigate],
  );
  // 작업 ID로 연 상세에 최종 판정이 생기면 판정 ID 주소로 바꾼다(돌아가기 state 유지).
  const resolvedId = detail.data?.id;
  useEffect(() => {
    if (decisionId && resolvedId && resolvedId !== decisionId)
      navigate({ pathname: paths.analysisDecision(resolvedId), search: location.search }, { replace: true, state: location.state });
  }, [decisionId, location.search, location.state, navigate, resolvedId]);

  const onChange = useCallback(
    (next: Partial<ListParams>, replace = false) => {
      const current = parseListSearch(location.search).params;
      navigate({ pathname: paths.analysis, search: listSearch({ ...current, ...next }, location.search) }, { replace });
    },
    [location.search, navigate],
  );
  const activeId = incidentsMode ? "incidents" : decisionId ? null : params.category;
  const groups = useMemo(
    () => [...sidebarGroups(params, list.data?.counts, view, isAdmin, activeId), incidentNavGroup()],
    [params, list.data?.counts, view, isAdmin, activeId],
  );
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
  const back = decisionReturn(location.state);
  const incidentBack = notificationsReturn(location.state);

  return (
    <div className="grid gap-6">
      <ScreenTitle>이상 분석</ScreenTitle>
      <div className="flex flex-col gap-6 md:flex-row md:items-start">
        {/* 넓게 보기는 앱 공통(상단 메뉴 오른쪽 버튼, 본문 폭 제한만 푼다) — 분류 메뉴는 늘 그대로. 휴대폰 폭의 상세(판정·사건)는 분류 메뉴 버튼을 숨긴다. */}
        <div className={cn("shrink-0", (decisionId || incidentId) && "max-md:hidden")}>
          <SideNav label="판정 분류" menuId="analysis-categories" activeId={activeId} groups={groups} header={viewSwitch} />
        </div>
        <section
          className="min-w-0 flex-1"
          aria-label={incidentsMode ? (incidentId ? "사건 상세" : "사건 목록") : decisionId ? "판정 상세" : "판정 목록"}
        >
          {/* 화면 전환(ui.md "움직임" 화면 전환): 목록·상세·사건 사이 경로가 바뀔 때만 사라지기 → 나타나기. 분류·쪽·검색(쿼리)은 전환 없음. */}
          <PageFade transitionKey={location.pathname}>
          {incidentsMode ? (
            incidentId ? (
              <IncidentDetailView
                key={incidentId}
                query={incidentDetail}
                backHref={incidentBack ?? paths.analysisIncidents + incidentSearch(incidentQuery)}
                backLabel={incidentBack ? NOTIFICATIONS_BACK_LABEL : undefined}
              />
            ) : (
              <IncidentListView
                period={incidentQuery.period}
                cursor={incidentQuery.cursor}
                all={incidentQuery.all}
                query={incidentList}
                detailHref={(id) => paths.analysisIncident(id) + location.search}
                onChange={onIncidentChange}
              />
            )
          ) : decisionId ? (
            <DecisionDetailView
              key={decisionId}
              id={decisionId}
              query={detail}
              interims={interims}
              flow={flow}
              backHref={back?.href ?? paths.analysis + search}
              backLabel={back?.label ?? "목록"}
            />
          ) : (
            <DecisionListView
              params={params}
              query={list}
              detailHref={(id) => paths.analysisDecision(id) + search}
              onChange={onChange}
              live={live}
            />
          )}
          </PageFade>
        </section>
      </div>
    </div>
  );
};
