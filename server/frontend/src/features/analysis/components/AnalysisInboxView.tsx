import {
  IconArrowsMaximize,
  IconArrowsMinimize,
  IconChevronLeft,
  IconChevronRight,
  IconSearch,
} from "@tabler/icons-react";
import { AnimatedPanel } from "../../../components/layout/AnimatedPanel";
import { Button } from "../../../components/ui/button";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "../../../components/ui/feedback";
import type { RealtimeStreamStatus } from "../hooks/useRealtimeAnomalies";
import type { AnalysisInboxItem } from "../utils/realtimeAdapter";
import { CustomPageSizeSelect, CustomSortSelect } from "./AnalysisSelects";
import { categoryThemeMap } from "../constants";
import { useAnalysisInbox } from "../hooks/useAnalysisInbox";
import type { AnalysisCategory } from "../types";
import { AnalysisFilterDialog } from "./AnalysisFilterDialog";
import { AnalysisInboxRow } from "./AnalysisInboxRow";

const statusLabels: Record<RealtimeStreamStatus, string> = {
  idle: "대기 중",
  connecting: "연결 중",
  open: "연결됨",
  retrying: "재연결 대기",
  closed: "연결 종료",
  error: "오류",
};

export const AnalysisInboxView = ({
  activeCategory,
  details,
  eventCount,
  workflowKnownCount,
  query,
  onOpenDetail,
  onQueryChange,
  onCategoryChange,
  isWide,
  onToggleWide,
  latestDetectedAt,
  streamStatus,
  streamError,
  invalidCount,
  requiresLogout,
  onRetry,
  onLogout,
}: {
  activeCategory: AnalysisCategory;
  details: AnalysisInboxItem[];
  eventCount: number;
  workflowKnownCount: number;
  query: string;
  onOpenDetail: (logId: string) => void;
  onQueryChange: (query: string) => void;
  onCategoryChange: (category: AnalysisCategory) => void;
  isWide: boolean;
  onToggleWide: (val: boolean) => void;
  latestDetectedAt: string | null;
  streamStatus: RealtimeStreamStatus;
  streamError: string | null;
  invalidCount: number;
  requiresLogout: boolean;
  onRetry: () => void;
  onLogout: () => void;
}) => {
  const workflowCategory =
    activeCategory === "Open" || activeCategory === "Resolved";
  const workflowUnavailable = workflowCategory && workflowKnownCount === 0;
  const theme = categoryThemeMap[activeCategory];
  const Icon = theme.icon;
  const {
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
  } = useAnalysisInbox(activeCategory, details);

  return (
    <AnimatedPanel
      className={`analysis-inbox ${isWide ? "analysis-inbox--wide" : ""}`}
    >
      <header className="analysis-inbox__header">
        <div>
          <h2>
            <span className={`analysis-heading-icon ${theme.className}`}>
              <Icon size={20} aria-hidden="true" />
            </span>
            {theme.label} 이상 로그
          </h2>
        </div>
        <div className="analysis-toolbar__actions">
          <Button
            variant="outline"
            size="icon"
            onClick={() => onToggleWide(!isWide)}
            aria-label={isWide ? "콤팩트 화면으로 보기" : "넓은 화면으로 보기"}
          >
            {isWide ? (
              <IconArrowsMinimize size={16} />
            ) : (
              <IconArrowsMaximize size={16} />
            )}
          </Button>
        </div>
      </header>

      <div className="analysis-search-row">
        <div className="analysis-search analysis-search--wide">
          <IconSearch size={16} aria-hidden="true" />
          <input
            value={query}
            placeholder="이벤트 ID, Transaction ID, Process, Channel, 응답코드 검색"
            onChange={(event) => onQueryChange(event.target.value)}
          />
        </div>
        <div className="analysis-chip-row">
          <AnalysisFilterDialog
            isOpen={isFilterOpen}
            onOpenChange={setIsFilterOpen}
            activeCategory={activeCategory}
            onCategoryChange={onCategoryChange}
          />
          <div className="analysis-sort-group">
            <span className="analysis-sort-label">정렬</span>
            <CustomSortSelect
              value={sortMode}
              onChange={handleSortModeChange}
            />
          </div>
        </div>
      </div>

      <div className="analysis-simple-summary" aria-live="polite">
        <strong>
          {workflowUnavailable
            ? "업무 분류 확인 불가"
            : `검색결과 ${details.length}건`}
        </strong>
        {workflowCategory && workflowKnownCount > 0 && (
          <span>상태 제공 {workflowKnownCount}건 기준</span>
        )}
        <span>최근 감지: {latestDetectedAt ?? "정보 없음"}</span>
        <span
          className={`realtime-anomaly-status realtime-anomaly-status--${streamStatus}`}
        >
          {statusLabels[streamStatus]}
        </span>
        {!requiresLogout &&
        (streamStatus === "closed" || streamStatus === "error") ? (
          <Button variant="ghost" size="sm" onClick={onRetry}>
            다시 연결
          </Button>
        ) : null}
        {requiresLogout ? (
          <Button variant="ghost" size="sm" onClick={onLogout}>
            로그아웃 후 다시 로그인
          </Button>
        ) : null}
      </div>
      {streamError ? (
        streamStatus === "error" || streamStatus === "closed" ? (
          <ErrorState title={streamError} />
        ) : (
          <p className="realtime-anomaly-panel__message" role="status">
            {streamError}
          </p>
        )
      ) : null}
      {invalidCount > 0 ? (
        <p className="realtime-anomaly-panel__message" role="status">
          계약 검증에 실패한 이벤트 {invalidCount}건은 표시하지 않았습니다.
        </p>
      ) : null}

      <section
        className="analysis-inbox-list"
        aria-label="실시간 이상 로그 목록"
      >
        {paginatedDetails.length > 0 ? (
          paginatedDetails.map((detail) => (
            <AnalysisInboxRow
              key={detail.log.logId}
              detail={detail}
              onOpen={() => onOpenDetail(detail.log.logId)}
            />
          ))
        ) : eventCount === 0 &&
          (streamStatus === "connecting" || streamStatus === "retrying") &&
          activeCategory !== "Open" &&
          activeCategory !== "Resolved" ? (
          <LoadingState>
            {streamStatus === "retrying"
              ? "실시간 연결을 다시 기다리는 중..."
              : "실시간 이상 이벤트에 연결하는 중..."}
          </LoadingState>
        ) : (
          <EmptyState className="analysis-empty-text">
            {workflowUnavailable ? (
              <p>
                업무 분류 상태가 제공되지 않아 보류·완료 건수를 확인할 수
                없습니다.
              </p>
            ) : eventCount === 0 ? (
              <>
                <p>현재 세션에서 수신한 실시간 이상 이벤트가 없습니다.</p>
                <p>
                  목록은 현재 세션에서 받은 최근 50건만 보관합니다. 새로고침하면
                  초기화되며 과거 이벤트는 조회할 수 없습니다.
                </p>
              </>
            ) : (
              <p>검색 또는 분류 조건에 맞는 실시간 이상 로그가 없습니다.</p>
            )}
          </EmptyState>
        )}
      </section>
      <footer className="analysis-inbox-footer">
        <div className="analysis-inbox-footer__left">
          <span className="analysis-inbox-footer__total">
            {workflowUnavailable
              ? "업무 분류 확인 불가"
              : `전체 ${details.length}건`}{" "}
            · 수신 {eventCount}건
          </span>
        </div>
        <div className="analysis-pagination-controls">
          <Button
            variant="ghost"
            size="icon"
            disabled={safePage <= 1}
            aria-label="이전 페이지"
            onClick={goToPreviousPage}
          >
            <IconChevronLeft size={16} aria-hidden="true" />
          </Button>
          {Array.from({ length: totalPages }).map((_, idx) => {
            const pageNum = idx + 1;
            return (
              <Button
                key={pageNum}
                variant={safePage === pageNum ? "outline" : "ghost"}
                size="sm"
                className={safePage === pageNum ? "active" : ""}
                onClick={() => setCurrentPage(pageNum)}
              >
                {pageNum}
              </Button>
            );
          })}
          <Button
            variant="ghost"
            size="icon"
            disabled={safePage >= totalPages}
            aria-label="다음 페이지"
            onClick={goToNextPage}
          >
            <IconChevronRight size={16} aria-hidden="true" />
          </Button>
        </div>
        <div className="analysis-inbox-footer__right">
          <CustomPageSizeSelect
            value={pageSize}
            onChange={handlePageSizeChange}
          />
        </div>
      </footer>
    </AnimatedPanel>
  );
};
