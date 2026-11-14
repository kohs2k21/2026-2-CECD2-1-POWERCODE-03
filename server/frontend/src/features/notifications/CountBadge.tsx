import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/**
 * 숫자 배지(와이어프레임 .badge, 알림 버튼·상단 메뉴 공용): 부모 오른쪽 위에 겹친다(부모는 relative). 화면 읽기는 부모 이름이 맡는다(aria-hidden).
 * muted = 수를 확인할 수 없을 때(알림 버튼 `?`).
 */
export const CountBadge = ({ muted = false, className, ...props }: HTMLAttributes<HTMLSpanElement> & { muted?: boolean }) => (
  <span
    aria-hidden="true"
    {...props}
    className={cn(
      "absolute -top-0.5 -right-0.5 grid h-count-badge min-w-count-badge place-items-center rounded-full px-1 text-count-badge font-semibold",
      muted ? "bg-muted text-muted-foreground" : "bg-destructive text-primary-foreground",
      className,
    )}
  />
);
