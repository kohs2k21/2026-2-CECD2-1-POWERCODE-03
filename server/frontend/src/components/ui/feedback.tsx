import type { ReactNode } from "react";
import { IconAlertCircle, IconLoader2 } from "@tabler/icons-react";
import { cn } from "@/lib/utils";
import { motionClasses } from "@/lib/motion";
import { Button } from "./button";

/** 조회 상태 부품(ui.md): 최초 로딩 / 빈 응답·검색 결과 없음 / 실패를 구분해 인라인으로 표시한다. */
export const LoadingState = ({
  children = "불러오는 중...",
  className,
}: {
  children?: ReactNode;
  className?: string;
}) => (
  <div
    data-slot="loading-state"
    role="status"
    className={cn("flex items-center gap-2 py-6 text-sm text-muted-foreground", motionClasses.enter, className)}
  >
    <IconLoader2 className="size-4 animate-spin" aria-hidden="true" />
    <span>{children}</span>
  </div>
);

export const ErrorState = ({
  title,
  children,
  onRetry,
  retryLabel = "다시 시도",
  className,
}: {
  title: string;
  children?: ReactNode;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
}) => (
  <div
    data-slot="error-state"
    role="alert"
    className={cn(
      "flex flex-wrap items-start justify-between gap-3 rounded-lg border border-destructive-soft bg-card px-4 py-3 text-sm",
      motionClasses.enter,
      className,
    )}
  >
    <div className="flex min-w-0 items-start gap-2">
      <IconAlertCircle className="mt-0.5 size-4 shrink-0 text-destructive-text" aria-hidden="true" />
      <div className="grid gap-1">
        <strong className="font-medium text-destructive-text">{title}</strong>
        {children && <div className="text-muted-foreground">{children}</div>}
      </div>
    </div>
    {onRetry && (
      <Button variant="outline" size="sm" onClick={onRetry}>
        {retryLabel}
      </Button>
    )}
  </div>
);

export const EmptyState = ({ children, className }: { children: ReactNode; className?: string }) => (
  <div
    data-slot="empty-state"
    role="status"
    className={cn(
      "grid gap-1 rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground",
      motionClasses.enter,
      className,
    )}
  >
    {children}
  </div>
);
