import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** 항목·값 목록(dl). 값이 없을 때 표시는 호출하는 쪽에서 정한다(추정값 금지). */
export const DescriptionList = ({
  items,
  className,
}: {
  items: { label: ReactNode; value: ReactNode; key?: string }[];
  className?: string;
}) => (
  <dl className={cn("grid gap-x-6 gap-y-3 sm:grid-cols-2 xl:grid-cols-3", className)}>
    {items.map((item, index) => (
      <div key={item.key ?? index} className="grid min-w-0 gap-0.5">
        <dt className="text-xs text-muted-foreground">{item.label}</dt>
        <dd className="text-sm break-words">{item.value}</dd>
      </div>
    ))}
  </dl>
);
