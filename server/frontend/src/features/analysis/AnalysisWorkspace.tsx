import { AnimatePresence } from "motion/react";
import { SidebarNav } from "../../components/layout/SidebarNav";
import { TooltipProvider } from "../../components/ui/tooltip";
import { AnalysisInboxView } from "./components/AnalysisInboxView";
import { RealtimeAnomalyDetailView } from "./components/RealtimeAnomalyDetailView";
import { useAnalysisWorkspace } from "./hooks/useAnalysisWorkspace";
import { useRealtimeAnomalies } from "./hooks/useRealtimeAnomalies";

type AnalysisWorkspaceProps = {
  onLogout: () => void;
};

export const AnalysisWorkspace = ({ onLogout }: AnalysisWorkspaceProps) => {
  const realtime = useRealtimeAnomalies();
  const {
    activeCategory,
    activeRealtimeEvent,
    activeTheme,
    categoryCounts,
    filteredDetails,
    handleCategoryChange,
    handleToggleWide,
    isWide,
    latestDetectedAt,
    query,
    setActiveDetailId,
    setQuery,
    sidebarGroups,
  } = useAnalysisWorkspace(realtime.events);

  return (
    <TooltipProvider>
      <section className="analysis-workspace">
        <SidebarNav
          activeId={activeCategory}
          groups={sidebarGroups}
          onSelect={handleCategoryChange}
        />
        <main className="analysis-main">
          <section className="analysis-workspace-surface" aria-label="상세 분석 화면">
            <AnimatePresence mode="wait">
              {activeRealtimeEvent ? (
                <RealtimeAnomalyDetailView
                  key={activeRealtimeEvent.eventId}
                  event={activeRealtimeEvent}
                  theme={activeTheme}
                  onBack={() => setActiveDetailId(null)}
                  isWide={isWide}
                  onToggleWide={handleToggleWide}
                />
              ) : (
                <AnalysisInboxView
                  key={activeCategory}
                  activeCategory={activeCategory}
                  details={filteredDetails}
                  eventCount={realtime.events.length}
                  query={query}
                  onOpenDetail={setActiveDetailId}
                  onQueryChange={setQuery}
                  isWide={isWide}
                  onToggleWide={handleToggleWide}
                  latestDetectedAt={latestDetectedAt}
                  streamStatus={realtime.status}
                  streamError={realtime.error}
                  invalidCount={realtime.invalidCount}
                  requiresLogout={realtime.requiresLogout}
                  onRetry={realtime.retry}
                  onLogout={onLogout}
                />
              )}
            </AnimatePresence>
          </section>
        </main>
      </section>
    </TooltipProvider>
  );
};
