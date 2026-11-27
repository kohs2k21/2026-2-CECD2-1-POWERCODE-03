import { useEffect, useRef, useState, type ReactNode } from "react";
import { LoadingState } from "@/components/ui/feedback";
import { Skeleton } from "@/components/ui/skeleton";
import { PENDING_MS, motionClasses } from "@/lib/motion";
import { cn } from "@/lib/utils";

/**
 * 조회 대기 표시(ui.md "움직임" 로딩 표시) — 이전 결과를 들고 있는 조회(keepPrevious)가 새 조건으로 다시 조회하는 동안.
 * - 조건(분류·필터·검색·기간·인터페이스)이 바뀌면 이전 행은 다른 뜻이라 행 자리 뼈대(skeleton)
 * - 같은 조건의 쪽 넘김이면 이전 행을 흐리게(투명도 50%·누름 막음) + 목록 위 진행 막대(dim)
 * - 깜박임 방지: 대기가 PENDING_MS.delay보다 짧으면 아무 표시 없이 바뀌고, 한번 보이면 최소 PENDING_MS.min 유지
 * - 새 문구 없음: 화면 낭독용 상태는 기존 "불러오는 중..."(LoadingState 기본 문구)
 * 최초 로딩(이전 결과 없음)은 지금처럼 LoadingState.
 * data-pending: 보이는 동안 표시(탐지 설정 영역 이동은 대상 위 대기 표시가 사라질 때까지 기다린다 — useDetectionScroll).
 */
export const LOADING_TEXT = "불러오는 중...";

export const useDelayedPending = (pending: boolean, { delay, min }: { delay: number; min: number } = PENDING_MS) => {
  const [show, setShow] = useState(false);
  const shownAt = useRef(0);
  useEffect(() => {
    if (pending) {
      if (show) return;
      const timer = setTimeout(() => {
        shownAt.current = Date.now();
        setShow(true);
      }, delay);
      return () => clearTimeout(timer);
    }
    if (!show) return;
    const left = min - (Date.now() - shownAt.current);
    if (left <= 0) {
      setShow(false);
      return;
    }
    const timer = setTimeout(() => setShow(false), left);
    return () => clearTimeout(timer);
  }, [pending, show, delay, min]);
  return show;
};

export type PendingKind = "skeleton" | "dim";

/**
 * 목록 대기 표시 종류: 마지막으로 결과가 온 조건(condition)과 지금 조건이 다르면 skeleton, 같고 쪽(page)만 다르면 dim.
 * 최소 유지 시간 동안 응답이 와도 종류가 바뀌지 않게 대기 중에 정한 값을 기억한다.
 */
export const useListPending = ({ pending, condition }: { pending: boolean; condition: string }) => {
  const settled = useRef(condition);
  const kind = useRef<PendingKind>("skeleton");
  if (!pending) settled.current = condition;
  else kind.current = settled.current === condition ? "dim" : "skeleton";
  const show = useDelayedPending(pending);
  return { show, kind: kind.current, pending };
};

/** 기본 행 뼈대 한 줄(테두리 칸 + 두 줄 글자 자리). 목록마다 행 모양에 맞춘 뼈대를 skeleton으로 넘길 수 있다. */
export const RowSkeleton = () => (
  <div className="grid gap-2 rounded-lg border bg-card px-4 py-3">
    <Skeleton className="h-4 w-1/3" />
    <Skeleton className="h-3 w-2/3" />
  </div>
);

/** 판정 목록 행(DecisionRow)과 같은 테두리·여백·두 칸 배치의 뼈대 한 줄. */
export const DecisionRowSkeleton = () => (
  <div className="grid gap-3 rounded-lg border bg-card px-4 py-3 sm:grid-cols-[minmax(0,1fr)_auto]">
    <div className="grid content-start gap-2">
      <Skeleton className="h-4 w-44" />
      <Skeleton className="h-4 w-72 max-w-full" />
      <Skeleton className="h-3 w-56 max-w-full" />
    </div>
    <div className="grid content-start gap-2 sm:justify-items-end">
      <Skeleton className="h-5 w-12 rounded-full" />
      <Skeleton className="h-3 w-28" />
      <Skeleton className="h-3 w-36" />
    </div>
  </div>
);

/**
 * 대기 표시 영역: show·kind·pending은 useListPending. 보이지 않을 때는 children 그대로(이전 결과 또는 새 결과).
 * aria-busy는 조회 중 내내(표시가 보이기 전 지연 시간 포함, pending).
 */
export const PendingRegion = ({ show, kind, pending = show, rows = 6, skeleton = <RowSkeleton />, children, className, label }: {
  show: boolean;
  pending?: boolean;
  kind: PendingKind;
  rows?: number;
  skeleton?: ReactNode;
  children: ReactNode;
  className?: string;
  label?: string;
}) => {
  if (show && kind === "skeleton")
    return (
      <section aria-label={label} aria-busy="true" data-pending="skeleton" className={cn("grid gap-2", motionClasses.fadeIn, className)}>
        <span role="status" className="sr-only">
          {LOADING_TEXT}
        </span>
        {Array.from({ length: rows }, (_, index) => (
          <div key={index}>{skeleton}</div>
        ))}
      </section>
    );
  return (
    <section aria-label={label} aria-busy={pending || show || undefined} data-pending={show ? "dim" : undefined} className={className}>
      {show && (
        <span role="status" className="sr-only">
          {LOADING_TEXT}
        </span>
      )}
      <div
        className={cn(
          "grid gap-[inherit] transition-opacity duration-(--motion-duration-base) ease-motion-out",
          show && "pointer-events-none opacity-50",
        )}
      >
        {children}
      </div>
    </section>
  );
};

/**
 * 목록 위 진행 막대(dim과 함께, 기존 --animate-progress-slide 토큰 — 프로토콜 설명 진행 막대와 같은 모양). 부모는 relative.
 * 동작 줄이기면 멈춘 전체 막대.
 */
export const TopProgress = ({ show }: { show: boolean }) =>
  show ? (
    <div aria-hidden="true" data-top-progress="" className={cn("absolute inset-x-0 top-0 h-0.5 overflow-hidden rounded-t-lg bg-info-soft", motionClasses.fadeIn)}>
      <span className="block h-full w-1/3 animate-progress-slide bg-info-text motion-reduce:w-full motion-reduce:animate-none" />
    </div>
  ) : null;

/**
 * 화면 조각을 받는 동안(Suspense fallback) 대체 표시: PENDING_MS.delay 전에는 빈 자리(대기 표시만 data-slot으로 남김), 지나면 기존 LoadingState.
 */
export const DelayedLoadingState = ({ children, className }: { children?: ReactNode; className?: string }) => {
  const [show, setShow] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setShow(true), PENDING_MS.delay);
    return () => clearTimeout(timer);
  }, []);
  return show ? <LoadingState className={className}>{children}</LoadingState> : <div data-slot="loading-state" aria-busy="true" className={cn("py-6", className)} />;
};
