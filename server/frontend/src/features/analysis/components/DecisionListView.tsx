import { useEffect, useRef, useState, type ReactNode } from "react";
import { IconArrowsMaximize, IconArrowsMinimize, IconChevronLeft, IconChevronRight, IconSearch } from "@tabler/icons-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { analysisErrorTitle, sorts, type DecisionList, type ListParams, type Sort } from "../data/analysisApi";
import type { LiveStatus } from "../data/useAnalysis";
import { categoryIcons } from "../icons";
import { categoryLabels, sortLabels, stampText } from "../labels";
import { DecisionFilterDialog } from "./DecisionFilterDialog";
import { DecisionRow } from "./DecisionRow";

export const SEARCH_DEBOUNCE_MS = 300;
const pageSizeOptions = [5, 10, 20];
const liveLabels: Record<LiveStatus, string> = { idle: "연결 대기", connecting: "연결 중", open: "실시간 연결됨", retrying: "재연결 대기" };
const liveVariant: Record<LiveStatus, "success" | "secondary" | "warning"> = {
  idle: "secondary",
  connecting: "secondary",
  open: "success",
  retrying: "warning",
};

/** 페이지 번호 창: 7쪽 이하면 모두, 그 이상이면 첫·끝·현재 앞뒤 1쪽과 생략(null). */
export const pageWindow = (current: number, total: number): (number | null)[] => {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const pages = new Set([1, total, current - 1, current, current + 1].filter((page) => page >= 1 && page <= total));
  const sorted = [...pages].sort((a, b) => a - b);
  return sorted.flatMap((page, index) => (index > 0 && page - sorted[index - 1] > 1 ? [null, page] : [page]));
};

export const DecisionListView = ({
  params,
  query,
  detailHref,
  onChange,
  isWide,
  onToggleWide,
  live,
}: {
  params: ListParams;
  query: {
    data: DecisionList | undefined;
    isPending: boolean;
    isError: boolean;
    isPlaceholderData: boolean;
    error: unknown;
    refetch: () => unknown;
  };
  detailHref: (id: string) => string;
  onChange: (next: Partial<ListParams>, replace?: boolean) => void;
  isWide: boolean;
  onToggleWide: (value: boolean) => void;
  live: { status: LiveStatus; error: string; received: number };
}) => {
  const data = query.data;
  // 검색어는 입력 중 지역 상태, 잠시 멈추면 주소(replace)에 반영 → 서버 검색.
  const [draft, setDraft] = useState(params.q);
  const committed = useRef(params.q);
  useEffect(() => {
    if (params.q !== committed.current) {
      committed.current = params.q;
      setDraft(params.q);
    }
  }, [params.q]);
  useEffect(() => {
    if (draft.trim() === params.q.trim()) return;
    const timer = setTimeout(() => {
      committed.current = draft.trim();
      onChange({ q: draft.trim(), page: 1 }, true);
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, onChange, params.q]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / params.pageSize)) : 1;
  // 적용 중인 필터는 검색 필터 대화상자 안의 선택값으로만 보인다(와이어프레임 요약 줄에 필터 요약 없음).
  // 목록 제목 아이콘(#100 이전 목록 제목의 분류 아이콘). 이전 화면에 대응 분류가 없으면 아이콘 없음.
  const CategoryIcon = categoryIcons[params.category];
  const narrowed = Boolean(params.q.trim()) || params.signal !== "all" || params.completeness !== "all" || params.category !== "all";

  let body: ReactNode;
  if (query.isPending && !data) body = <LoadingState />;
  else if (!data) body = <ErrorState title={analysisErrorTitle(query.error, "판정 목록")} onRetry={() => void query.refetch()} />;
  else if (data.items.length)
    body = data.items.map((item) => <DecisionRow key={item.id} item={item} href={detailHref(item.id)} />);
  else body = <EmptyState>{narrowed ? "검색 또는 분류 조건에 맞는 판정이 없습니다." : "저장된 판정이 없습니다."}</EmptyState>;

  return (
    <Card className="gap-4">
      <CardContent className="grid gap-4">
        <header className="flex items-center justify-between gap-3">
          <h2 className="flex min-w-0 items-center gap-2 text-section-title">
            <CategoryIcon aria-hidden="true" className="size-4.5 shrink-0" />
            {categoryLabels[params.category]} 판정
          </h2>
          <Button
            variant="outline"
            size="icon"
            onClick={() => onToggleWide(!isWide)}
            aria-label={isWide ? "콤팩트 화면으로 보기" : "넓은 화면으로 보기"}
          >
            {isWide ? <IconArrowsMinimize aria-hidden="true" /> : <IconArrowsMaximize aria-hidden="true" />}
          </Button>
        </header>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-0 flex-1 basis-60">
            <IconSearch
              className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              type="search"
              className="pl-9"
              value={draft}
              maxLength={100}
              aria-label="판정 검색"
              placeholder="판정 ID, 거래 ID, 프로세스, 인터페이스 검색"
              onChange={(event) => setDraft(event.target.value)}
            />
          </div>
          <DecisionFilterDialog
            signal={params.signal}
            completeness={params.completeness}
            onChange={(next) => onChange({ ...next, page: 1 })}
          />
          <span className="text-sm text-muted-foreground" id="analysis-sort-label">
            정렬
          </span>
          <Select value={params.sort} onValueChange={(value) => onChange({ sort: value as Sort, page: 1 })}>
            <SelectTrigger size="sm" aria-labelledby="analysis-sort-label">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {sorts.map((value) => (
                <SelectItem key={value} value={value}>
                  {sortLabels[value]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground" aria-live="polite">
          <strong className="text-foreground">{data ? `검색결과 ${data.total.toLocaleString("ko-KR")}건` : "검색결과 —"}</strong>
          <span>최근 감지: {data?.latestDecidedAt ? stampText(data.latestDecidedAt) : "정보 없음"}</span>
          <Badge variant={liveVariant[live.status]}>{liveLabels[live.status]}</Badge>
        </div>
        {live.error && live.status === "retrying" && (
          <p className="text-sm text-muted-foreground" role="status">
            {live.error}
          </p>
        )}
        {query.isError && data && (
          <ErrorState
            title={`${analysisErrorTitle(query.error, "판정 목록")} · 이전 관측값 표시`}
            onRetry={() => void query.refetch()}
          />
        )}
        <section className="grid gap-2" aria-label="판정 목록" aria-busy={query.isPlaceholderData || undefined}>
          {body}
        </section>
        <footer className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
          <span>
            전체 {data ? data.total.toLocaleString("ko-KR") : "—"}건 · 이번 세션 실시간 수신 {live.received}건
          </span>
          <nav aria-label="페이지" className="flex flex-wrap items-center gap-1">
            <Button
              variant="ghost"
              size="icon-sm"
              disabled={params.page <= 1}
              aria-label="이전 페이지"
              onClick={() => onChange({ page: Math.min(params.page - 1, totalPages) })}
            >
              <IconChevronLeft aria-hidden="true" />
            </Button>
            {pageWindow(Math.min(params.page, totalPages), totalPages).map((page, index) =>
              page === null ? (
                <span key={`gap-${index}`} aria-hidden="true" className="px-1">
                  …
                </span>
              ) : (
                <Button
                  key={page}
                  variant={params.page === page ? "outline" : "ghost"}
                  size="sm"
                  aria-current={params.page === page ? "page" : undefined}
                  onClick={() => onChange({ page })}
                >
                  {page}
                </Button>
              ),
            )}
            <Button
              variant="ghost"
              size="icon-sm"
              disabled={params.page >= totalPages}
              aria-label="다음 페이지"
              onClick={() => onChange({ page: params.page + 1 })}
            >
              <IconChevronRight aria-hidden="true" />
            </Button>
          </nav>
          <div className="flex items-center gap-2">
            <span id="analysis-page-size-label">페이지당</span>
            <Select value={String(params.pageSize)} onValueChange={(value) => onChange({ pageSize: Number(value), page: 1 })}>
              <SelectTrigger size="sm" aria-labelledby="analysis-page-size-label">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {pageSizeOptions.map((size) => (
                  <SelectItem key={size} value={String(size)}>
                    {size}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </footer>
      </CardContent>
    </Card>
  );
};
