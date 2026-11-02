import { Link, useOutletContext } from "react-router-dom";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SideNav } from "@/components/layout/SideNav";
import { Button } from "@/components/ui/button";
import { paths } from "@/app/routes";
import type { AppOutletContext } from "@/app/AppLayout";
import { cn } from "@/lib/utils";
import { AnalysisInboxView } from "./components/AnalysisInboxView";
import { RealtimeAnomalyDetailView } from "./components/RealtimeAnomalyDetailView";
import { useAnalysisWorkspace } from "./hooks/useAnalysisWorkspace";
import { useSharedRealtimeAnomalies } from "./RealtimeAnomalyProvider";

/**
 * 이상 분석: 분류 사이드바 + 목록/상세(기존 배치 유지, 와이어프레임 #93 analysis).
 * 현재 연결: 실시간 SSE 이벤트(세션당 최근 50건)·판정 근거 조회·피드백 저장. DB 판정 기준 전환은 하위 Issue(이상 분석).
 */
export const AnalysisPage = () => {
  const { requestLogout } = useOutletContext<AppOutletContext>();
  const realtime = useSharedRealtimeAnomalies();
  const workspace = useAnalysisWorkspace(realtime.events);
  return (
    <div className="grid gap-6">
      <ScreenTitle>이상 분석</ScreenTitle>
      <div className={cn("flex flex-col gap-6 md:flex-row md:items-start")}>
        {!workspace.isWide && (
          <SideNav
            label="판정 분류"
            menuId="analysis-categories"
            activeId={workspace.activeRealtimeEvent ? null : workspace.activeCategory}
            groups={workspace.sidebarGroups}
          />
        )}
        <section className="min-w-0 flex-1" aria-label="상세 분석 화면">
          {workspace.activeRealtimeEvent ? (
            <RealtimeAnomalyDetailView
              key={workspace.activeRealtimeEvent.eventId}
              event={workspace.activeRealtimeEvent}
              theme={workspace.activeTheme}
              onBack={() => workspace.setActiveDetailId(null)}
              isWide={workspace.isWide}
              onToggleWide={workspace.handleToggleWide}
            />
          ) : (
            <AnalysisInboxView
              key={workspace.activeCategory}
              activeCategory={workspace.activeCategory}
              details={workspace.filteredDetails}
              eventCount={realtime.events.length}
              workflowKnownCount={workspace.workflowKnownCount}
              query={workspace.query}
              onOpenDetail={workspace.setActiveDetailId}
              onQueryChange={workspace.setQuery}
              onCategoryChange={workspace.handleCategoryChange}
              isWide={workspace.isWide}
              onToggleWide={workspace.handleToggleWide}
              latestDetectedAt={workspace.latestDetectedAt}
              streamStatus={realtime.status}
              streamError={realtime.error}
              invalidCount={realtime.invalidCount}
              requiresLogout={realtime.requiresLogout}
              onRetry={realtime.retry}
              onLogout={requestLogout}
              headerAction={
                <Button asChild variant="outline" size="sm">
                  <Link to={paths.savedDecisions}>저장된 탐지 판정·근거 설명</Link>
                </Button>
              }
            />
          )}
        </section>
      </div>
    </div>
  );
};
