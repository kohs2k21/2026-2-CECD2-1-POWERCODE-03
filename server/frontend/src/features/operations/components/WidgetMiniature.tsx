import { IconChevronRight } from "@tabler/icons-react";
import { cn } from "@/lib/utils";
import type { WidgetId } from "../data/widgetLayout";

/** 막대 한 칸(미리보기용 회색·강조 막대). */
const Bar = ({ className }: { className?: string }) => <span className={cn("block h-1.5 rounded-full bg-border", className)} />;

/**
 * 위젯 추가 목록·끌기 미리보기용 축소 모양(#100 이전 WidgetMiniatures 방식). 실제 값이 아닌 모양만 보여 준다.
 */
export const WidgetMiniature = ({ id, className }: { id: WidgetId; className?: string }) => (
  <div aria-hidden="true" className={cn("pointer-events-none grid h-full content-center gap-2 p-3", className)}>
    {id === "flow" && (
      <div className="flex flex-wrap items-center gap-0.5">
        {[0, 1, 2, 3, 4].map((index) => (
          <span key={index} className="flex items-center gap-0.5">
            {index > 0 && <IconChevronRight className="size-2.5 text-subtle" />}
            <span className="h-3 w-5 rounded-sm border bg-card" />
          </span>
        ))}
      </div>
    )}
    {id === "recent" &&
      [0, 1, 2, 3].map((index) => (
        <div key={index} className="flex items-center gap-1.5">
          <Bar className="w-6 bg-link/60" />
          <Bar className="flex-1" />
        </div>
      ))}
    {id === "trend" &&
      [0, 1, 2, 3].map((row) => (
        <div key={row} className="grid grid-cols-4 gap-1">
          {[0, 1, 2, 3].map((cell) => (
            <Bar key={cell} className={row === 0 ? "bg-subtle/60" : undefined} />
          ))}
        </div>
      ))}
    {id === "active" && (
      <>
        <div className="flex gap-1.5">
          <Bar className="w-4 bg-primary/70" />
          <Bar className="w-10" />
        </div>
        <div className="flex gap-1.5">
          <Bar className="w-10 bg-primary/70" />
          <Bar className="w-14" />
        </div>
        <Bar className="w-10 bg-link/60" />
      </>
    )}
    {id === "queue" &&
      [0, 1, 2].map((row) => (
        <div key={row} className="flex justify-between gap-3 border-b pb-1 last:border-b-0">
          <Bar className="w-14" />
          <Bar className="w-5 bg-primary/70" />
        </div>
      ))}
    {id === "collect" && (
      <>
        <Bar className="w-3/4 bg-primary/70" />
        <Bar className="w-full" />
        <Bar className="w-8 bg-link/60" />
      </>
    )}
    {id === "storage" && (
      <>
        <Bar className="w-2/3 bg-primary/70" />
        <span className="block h-2 overflow-hidden rounded-full bg-primary/20">
          <span className="block h-full w-3/5 rounded-full bg-primary" />
        </span>
        <Bar className="w-1/2" />
      </>
    )}
  </div>
);
