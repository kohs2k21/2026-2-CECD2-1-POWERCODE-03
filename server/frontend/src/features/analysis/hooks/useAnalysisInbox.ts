import { useEffect, useMemo, useState } from "react";
import type { AnalysisInboxItem } from "../utils/realtimeAdapter";
import type { AnalysisCategory, SortOption } from "../types";

const severityRank: Record<string, number> = {
  Critical: 3,
  Warning: 2,
  Info: 1,
};

const getDefaultSortMode = (category: AnalysisCategory): SortOption =>
  category === "All" ? "severity_desc" : "time_desc";

const compareDetectedAt = (
  left: AnalysisInboxItem,
  right: AnalysisInboxItem,
  direction: 1 | -1,
): number => {
  const leftTime = left.log.detectedAt ? Date.parse(left.log.detectedAt) : NaN;
  const rightTime = right.log.detectedAt ? Date.parse(right.log.detectedAt) : NaN;
  const leftValid = Number.isFinite(leftTime);
  const rightValid = Number.isFinite(rightTime);

  if (leftValid !== rightValid) {
    return leftValid ? -1 : 1;
  }
  if (!leftValid || !rightValid) {
    return 0;
  }
  return direction * (leftTime - rightTime);
};

export const useAnalysisInbox = (
  activeCategory: AnalysisCategory,
  items: AnalysisInboxItem[],
) => {
  const [sortMode, setSortMode] = useState<SortOption>(
    getDefaultSortMode(activeCategory),
  );
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [isFilterOpen, setIsFilterOpen] = useState(false);

  useEffect(() => {
    setSortMode(getDefaultSortMode(activeCategory));
    setCurrentPage(1);
  }, [activeCategory]);

  const sortedDetails = useMemo(
    () =>
      [...items].sort((left, right) => {
        if (sortMode === "severity_desc" || sortMode === "severity_asc") {
          const rankLeft = severityRank[left.log.severity] || 0;
          const rankRight = severityRank[right.log.severity] || 0;
          if (rankLeft !== rankRight) {
            return sortMode === "severity_desc"
              ? rankRight - rankLeft
              : rankLeft - rankRight;
          }
          return compareDetectedAt(left, right, -1);
        }

        return compareDetectedAt(
          left,
          right,
          sortMode === "time_asc" ? 1 : -1,
        );
      }),
    [items, sortMode],
  );

  const totalPages = Math.max(1, Math.ceil(sortedDetails.length / pageSize));
  const safePage = Math.min(currentPage, totalPages);
  const startIndex = (safePage - 1) * pageSize;
  const paginatedDetails = sortedDetails.slice(startIndex, startIndex + pageSize);

  const handleSortModeChange = (nextSortMode: SortOption) => {
    setSortMode(nextSortMode);
    setCurrentPage(1);
  };

  const handlePageSizeChange = (nextPageSize: number) => {
    setPageSize(nextPageSize);
    setCurrentPage(1);
  };

  const goToPreviousPage = () => {
    setCurrentPage((page) => Math.max(1, page - 1));
  };

  const goToNextPage = () => {
    setCurrentPage((page) => Math.min(totalPages, page + 1));
  };

  return {
    goToNextPage,
    goToPreviousPage,
    handlePageSizeChange,
    handleSortModeChange,
    isFilterOpen,
    pageSize,
    paginatedDetails,
    safePage,
    setCurrentPage,
    setIsFilterOpen,
    sortMode,
    totalPages,
  };
};
