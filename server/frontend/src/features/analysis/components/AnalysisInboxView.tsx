import type { ReactNode } from "react";
import {
  IconArrowsMaximize,
  IconArrowsMinimize,
  IconChevronLeft,
  IconChevronRight,
  IconSearch,
} from "@tabler/icons-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { RealtimeStreamStatus } from "../hooks/useRealtimeAnomalies";
import type { AnalysisInboxItem } from "../utils/realtimeAdapter";
import { categoryThemeMap } from "../constants";
import { useAnalysisInbox } from "../hooks/useAnalysisInbox";
import type { AnalysisCategory, SortOption } from "../types";
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
const statusVariant: Record<RealtimeStreamStatus, "success" | "warning" | "critical" | "secondary"> = {
  idle: "secondary",
  connecting: "secondary",
  open: "success",
  retrying: "warning",
  closed: "critical",
  error: "critical",
};
const sortOptions: { value: SortOption; label: string }[] = [
  { value: "severity_desc", label: "위험도 높은순" },
  { value: "severity_asc", label: "위험도 낮은순" },
  { value: "time_desc", label: "최신순" },
  { value: "time_asc", label: "과거순" },
];
const pageSizes = [10, 20, 50];

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
  headerAction,
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
  /** 목록 머리 오른쪽 추가 명령(이상 분석 화면의 저장 판정 링크) */
  headerAction?: ReactNode;
}) => {
  const workflowCategory = activeCategory === "Open" || activeCategory === "Resolved";
  const workflowUnavailable = workflowCategory && workflowKnownCount === 0;
  const theme = categoryThemeMap[activeCategory];
  const Icon = theme.icon;
  const inbox = useAnalysisInbox(activeCategory, details);
  return (
    <Card className="gap-4">
      <CardContent className="grid gap-4">
        <header className="flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-section-title">
            <Icon className="size-5 text-muted-foreground" aria-hidden="true" />
            {theme.label} 이상 로그
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            {headerAction}
            <Button
              variant="outline"
              size="icon"
              onClick={() => onToggleWide(!isWide)}
              aria-label={isWide ? "콤팩트 화면으로 보기" : "넓은 화면으로 보기"}
            >
              {isWide ? <IconArrowsMinimize aria-hidden="true" /> : <IconArrowsMaximize aria-hidden="true" />}
            </Button>
          </div>
        </header>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-60 flex-1">
            <IconSearch
              className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              className="pl-9"
              value={query}
              aria-label="이상 로그 검색"
              placeholder="이벤트 ID, Transaction ID, Process, Channel, 응답코드 검색"
              onChange={(event) => onQueryChange(event.target.value)}
            />
          </div>
          <AnalysisFilterDialog
            isOpen={inbox.isFilterOpen}
            onOpenChange={inbox.setIsFilterOpen}
            activeCategory={activeCategory}
            onCategoryChange={onCategoryChange}
          />
          <span className="text-sm text-muted-foreground" id="analysis-sort-label">
            정렬
          </span>
          <Select value={inbox.sortMode} onValueChange={(value) => inbox.handleSortModeChange(value as SortOption)}>
            <SelectTrigger size="sm" aria-labelledby="analysis-sort-label">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {sortOptions.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground" aria-live="polite">
          <strong className="text-foreground">
            {workflowUnavailable ? "업무 분류 확인 불가" : `검색결과 ${details.length}건`}
          </strong>
          {workflowCategory && workflowKnownCount > 0 && <span>상태 제공 {workflowKnownCount}건 기준</span>}
          <span>최근 감지: {latestDetectedAt ?? "정보 없음"}</span>
          <Badge variant={statusVariant[streamStatus]}>{statusLabels[streamStatus]}</Badge>
          {!requiresLogout && (streamStatus === "closed" || streamStatus === "error") && (
            <Button variant="ghost" size="sm" onClick={onRetry}>
              다시 연결
            </Button>
          )}
          {requiresLogout && (
            <Button variant="ghost" size="sm" onClick={onLogout}>
              로그아웃 후 다시 로그인
            </Button>
          )}
        </div>
        {streamError &&
          (streamStatus === "error" || streamStatus === "closed" ? (
            <ErrorState title={streamError} />
          ) : (
            <p className="text-sm text-muted-foreground" role="status">
              {streamError}
            </p>
          ))}
        {invalidCount > 0 && (
          <p className="text-sm text-muted-foreground" role="status">
            계약 검증에 실패한 이벤트 {invalidCount}건은 표시하지 않았습니다.
          </p>
        )}
        <section className="grid gap-2" aria-label="실시간 이상 로그 목록">
          {inbox.paginatedDetails.length > 0 ? (
            inbox.paginatedDetails.map((detail) => (
              <AnalysisInboxRow key={detail.log.logId} detail={detail} onOpen={() => onOpenDetail(detail.log.logId)} />
            ))
          ) : eventCount === 0 && (streamStatus === "connecting" || streamStatus === "retrying") && !workflowCategory ? (
            <LoadingState>
              {streamStatus === "retrying" ? "실시간 연결을 다시 기다리는 중..." : "실시간 이상 이벤트에 연결하는 중..."}
            </LoadingState>
          ) : (
            <EmptyState>
              {workflowUnavailable ? (
                <p>업무 분류 상태가 제공되지 않아 보류·완료 건수를 확인할 수 없습니다.</p>
              ) : eventCount === 0 ? (
                <>
                  <p>현재 세션에서 수신한 실시간 이상 이벤트가 없습니다.</p>
                  <p>
                    목록은 현재 세션에서 받은 최근 50건만 보관합니다. 새로고침하면 초기화되며 과거 이벤트는 조회할 수
                    없습니다.
                  </p>
                </>
              ) : (
                <p>검색 또는 분류 조건에 맞는 실시간 이상 로그가 없습니다.</p>
              )}
            </EmptyState>
          )}
        </section>
        <footer className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
          <span>
            {workflowUnavailable ? "업무 분류 확인 불가" : `전체 ${details.length}건`} · 수신 {eventCount}건
          </span>
          <nav aria-label="페이지" className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon-sm"
              disabled={inbox.safePage <= 1}
              aria-label="이전 페이지"
              onClick={inbox.goToPreviousPage}
            >
              <IconChevronLeft aria-hidden="true" />
            </Button>
            {Array.from({ length: inbox.totalPages }).map((_, index) => {
              const page = index + 1;
              return (
                <Button
                  key={page}
                  variant={inbox.safePage === page ? "outline" : "ghost"}
                  size="sm"
                  aria-current={inbox.safePage === page ? "page" : undefined}
                  onClick={() => inbox.setCurrentPage(page)}
                >
                  {page}
                </Button>
              );
            })}
            <Button
              variant="ghost"
              size="icon-sm"
              disabled={inbox.safePage >= inbox.totalPages}
              aria-label="다음 페이지"
              onClick={inbox.goToNextPage}
            >
              <IconChevronRight aria-hidden="true" />
            </Button>
          </nav>
          <Select value={String(inbox.pageSize)} onValueChange={(value) => inbox.handlePageSizeChange(Number(value))}>
            <SelectTrigger size="sm" aria-label="페이지당 항목 수">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {pageSizes.map((size) => (
                <SelectItem key={size} value={String(size)}>
                  {size} / 페이지
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </footer>
      </CardContent>
    </Card>
  );
};
