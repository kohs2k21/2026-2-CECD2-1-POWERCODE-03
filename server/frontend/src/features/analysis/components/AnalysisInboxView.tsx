import {
  IconArrowsMaximize,
  IconArrowsMinimize,
  IconChevronLeft,
  IconChevronRight,
  IconSearch,
} from "@tabler/icons-react";
import { AnimatedPanel } from "../../../components/layout/AnimatedPanel";
import { Button } from "../../../components/ui/button";
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
  query,
  onOpenDetail,
  onQueryChange,
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
  query: string;
  onOpenDetail: (logId: string) => void;
  onQueryChange: (query: string) => void;
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
    <AnimatedPanel className={`analysis-inbox ${isWide ? "analysis-inbox--wide" : ""}`}>
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
            {isWide ? <IconArrowsMinimize size={16} /> : <IconArrowsMaximize size={16} />}
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
          />
          <div className="analysis-sort-group">
            <span className="analysis-sort-label">정렬</span>
            <CustomSortSelect value={sortMode} onChange={handleSortModeChange} />
          </div>
        </div>
      </div>

      <div className="analysis-simple-summary" aria-live="polite">
        <strong>검색결과 {details.length}건</strong>
        <span>최근 감지: {latestDetectedAt ?? "정보 없음"}</span>
        <span
          className={`realtime-anomaly-status realtime-anomaly-status--${streamStatus}`}
        >
          {statusLabels[streamStatus]}
        </span>
        {!requiresLogout && (streamStatus === "closed" || streamStatus === "error") ? (
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
        <p className="realtime-anomaly-panel__message" role="status">
          {streamError}
        </p>
      ) : null}
      {invalidCount > 0 ? (
        <p className="realtime-anomaly-panel__message" role="status">
          계약 검증에 실패한 이벤트 {invalidCount}건은 표시하지 않았습니다.
        </p>
      ) : null}

      <section className="analysis-inbox-list" aria-label="실시간 이상 로그 목록">
        {paginatedDetails.length > 0 ? (
          paginatedDetails.map((detail) => (
            <AnalysisInboxRow
              key={detail.log.logId}
              detail={detail}
              onOpen={() => onOpenDetail(detail.log.logId)}
            />
          ))
        ) : (
          <div className="analysis-empty-text">
            {activeCategory === "Open" || activeCategory === "Resolved" ? (
              <p>
                백엔드 이벤트에는 원천 상태만 있어 업무 보류·완료 분류를 지원하지 않습니다.
              </p>
            ) : eventCount === 0 ? (
              <>
                <p>현재 세션에서 수신한 실시간 이상 이벤트가 없습니다.</p>
                <p>
                  목록은 현재 세션에서 받은 최근 50건만 보관합니다. 새로고침하면 초기화되며
                  과거 이벤트는 조회할 수 없습니다.
                </p>
              </>
            ) : (
              <p>검색 또는 분류 조건에 맞는 실시간 이상 로그가 없습니다.</p>
            )}
          </div>
        )}
      </section>
      <footer className="analysis-inbox-footer">
        <div className="analysis-inbox-footer__left">
          <span className="analysis-inbox-footer__total">
            전체 {details.length}건 · 수신 {eventCount}건
          </span>
        </div>
        <div className="analysis-pagination-controls">
          <Button
            variant="ghost"
            size="icon"
            disabled={safePage <= 1}
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
            onClick={goToNextPage}
          >
            <IconChevronRight size={16} aria-hidden="true" />
          </Button>
        </div>
        <div className="analysis-inbox-footer__right">
          <CustomPageSizeSelect value={pageSize} onChange={handlePageSizeChange} />
        </div>
      </footer>
    </AnimatedPanel>
  );
};
