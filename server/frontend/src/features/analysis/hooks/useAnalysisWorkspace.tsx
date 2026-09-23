import { useMemo, useState } from "react";
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
): SidebarNavGroup<AnalysisCategory>[] =>
  ["이상 로그", "분류함"].map((title, groupIndex) => ({
    title,
    items: categoryOrder
      .filter(
        (category) =>
          (categoryThemeMap[category].group === "workflow") === (groupIndex === 1),
      )
      .map((category) => {
        const theme = categoryThemeMap[category];
        const Icon = theme.icon;

        return {
          id: category,
          label: theme.label,
          count: categoryCounts[category],
          icon: <Icon size={17} aria-hidden="true" />,
          className: theme.className,
        };
      }),
  }));

export const useAnalysisWorkspace = (events: RealtimeAnomalyEvent[]) => {
  const [isWide, setIsWide] = useState<boolean>(() =>
    getStored(storageKeys.layoutWide("analysis"), false),
  );
  const [activeCategory, setActiveCategory] = useState<AnalysisCategory>("All");
  const [activeDetailId, setActiveDetailId] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const inboxItems = useMemo(() => adaptRealtimeAnomalyEvents(events), [events]);
  const categoryCounts = useMemo(
    () => getRealtimeCategoryCounts(inboxItems),
    [inboxItems],
  );
  const filteredDetails = useMemo(
    () => filterRealtimeAnalysisItems(inboxItems, activeCategory, query),
    [activeCategory, inboxItems, query],
  );
  const activeRealtimeEvent = getRealtimeDetailByEventId(events, activeDetailId);
  const activeTheme: CategoryTheme = activeRealtimeEvent
    ? categoryThemeMap[activeRealtimeEvent.severity]
    : categoryThemeMap[activeCategory];
  const sidebarGroups = useMemo(
    () => getSidebarGroups(categoryCounts),
    [categoryCounts],
  );

  const handleToggleWide = (value: boolean) => {
    setIsWide(value);
    setStored(storageKeys.layoutWide("analysis"), value);
  };

  const handleCategoryChange = (category: AnalysisCategory) => {
    setActiveCategory(category);
    setActiveDetailId(null);
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
  };
};
