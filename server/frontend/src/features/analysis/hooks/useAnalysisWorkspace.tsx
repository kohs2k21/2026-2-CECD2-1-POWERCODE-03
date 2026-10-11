import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { SidebarNavGroup } from "../../../components/layout/SidebarNav";
import { getStored, setStored, storageKeys } from "../../../lib/storage";
import type { RealtimeAnomalyEvent } from "../../../types/realtime";
import { categoryOrder, categoryThemeMap } from "../constants";
import type { AnalysisCategory, CategoryTheme } from "../types";
import {
  adaptRealtimeAnomalyEvents,
  filterRealtimeAnalysisItems,
  getLatestRealtimeDetectionAt,
  getRealtimeCategoryCounts,
  getRealtimeDetailByEventId,
} from "../utils/realtimeAdapter";

const getSidebarGroups = (
  categoryCounts: Record<AnalysisCategory, number>,
  workflowKnownCount: number,
): SidebarNavGroup<AnalysisCategory>[] =>
  [
    "이상 로그",
    workflowKnownCount
      ? `분류함 (상태 제공 ${workflowKnownCount}건)`
      : "분류함 (확인 불가)",
  ].map((title, groupIndex) => ({
    title,
    items: categoryOrder
      .filter(
        (category) =>
          (categoryThemeMap[category].group === "workflow") ===
          (groupIndex === 1),
      )
      .map((category) => {
        const theme = categoryThemeMap[category];
        const Icon = theme.icon;

        return {
          id: category,
          label: theme.label,
          count:
            theme.group === "workflow" && !workflowKnownCount
              ? undefined
              : categoryCounts[category],
          icon: <Icon size={17} aria-hidden="true" />,
          className: theme.className,
        };
      }),
  }));

export const useAnalysisWorkspace = (events: RealtimeAnomalyEvent[]) => {
  const [isWide, setIsWide] = useState<boolean>(() =>
    getStored(storageKeys.layoutWide("analysis"), false),
  );
  const [params, setParams] = useSearchParams();
  const category = params.get("category");
  const activeCategory: AnalysisCategory = categoryOrder.includes(
    category as AnalysisCategory,
  )
    ? (category as AnalysisCategory)
    : "All";
  const activeDetailId = params.get("event");
  const query = params.get("q") ?? "";
  const setActiveDetailId = (id: string | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set("event", id);
    else next.delete("event");
    setParams(next);
  };
  const setQuery = (value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set("q", value);
    else next.delete("q");
    setParams(next, { replace: true });
  };

  const inboxItems = useMemo(
    () => adaptRealtimeAnomalyEvents(events),
    [events],
  );
  const categoryCounts = useMemo(
    () => getRealtimeCategoryCounts(inboxItems),
    [inboxItems],
  );
  const filteredDetails = useMemo(
    () => filterRealtimeAnalysisItems(inboxItems, activeCategory, query),
    [activeCategory, inboxItems, query],
  );
  const activeRealtimeEvent = getRealtimeDetailByEventId(
    events,
    activeDetailId,
  );
  const activeTheme: CategoryTheme = activeRealtimeEvent
    ? categoryThemeMap[activeRealtimeEvent.severity]
    : categoryThemeMap[activeCategory];
  const workflowKnownCount = inboxItems.filter(
    (item) => item.log.workflowStatus !== undefined,
  ).length;
  const sidebarGroups = useMemo(
    () =>
      getSidebarGroups(categoryCounts, workflowKnownCount).map((group) => ({
        ...group,
        items: group.items.map((item) => {
          const next = new URLSearchParams(params);
          next.set("category", item.id);
          next.delete("event");
          return { ...item, href: "/analysis?" + next.toString() };
        }),
      })),
    [categoryCounts, params, workflowKnownCount],
  );

  const handleToggleWide = (value: boolean) => {
    setIsWide(value);
    setStored(storageKeys.layoutWide("analysis"), value);
  };

  const handleCategoryChange = (category: AnalysisCategory) => {
    const next = new URLSearchParams(params);
    next.set("category", category);
    next.delete("event");
    setParams(next);
  };

  return {
    activeCategory,
    activeRealtimeEvent,
    activeTheme,
    categoryCounts,
    filteredDetails,
    handleCategoryChange,
    handleToggleWide,
    isWide,
    latestDetectedAt: getLatestRealtimeDetectionAt(inboxItems),
    query,
    setActiveDetailId,
    setQuery,
    sidebarGroups,
    workflowKnownCount,
  };
};
