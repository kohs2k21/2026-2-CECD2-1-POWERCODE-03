import type { ReactNode } from "react";
import { IconLoader2 } from "@tabler/icons-react";
import { cn } from "../../lib/utils";
import { Button } from "./button";

export const LoadingState = ({
  children = "불러오는 중...",
  className,
}: {
  children?: ReactNode;
  className?: string;
}) => (
  <div
    className={cn("ui-feedback ui-feedback--loading", className)}
    role="status"
  >
    <IconLoader2 className="ui-spinner" size={18} aria-hidden="true" />
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
  <div className={cn("ui-feedback ui-feedback--error", className)} role="alert">
    <div>
      <strong>{title}</strong>
      {children && <div className="ui-feedback-description">{children}</div>}
    </div>
    {onRetry && (
      <Button variant="outline" size="sm" onClick={onRetry}>
        {retryLabel}
      </Button>
    )}
  </div>
);
export const EmptyState = ({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) => (
  <div
    className={cn("ui-feedback ui-feedback--empty", className)}
    role="status"
  >
    {children}
  </div>
);
