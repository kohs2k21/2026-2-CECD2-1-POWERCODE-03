import * as ProgressPrimitive from "@radix-ui/react-progress";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/** shadcn new-york Progress(Radix). value는 0~max(기본 100). max를 주면 aria-valuemax도 그 값이다. */
export const Progress = ({ className, value, max = 100, ...props }: ComponentProps<typeof ProgressPrimitive.Root>) => (
  <ProgressPrimitive.Root
    data-slot="progress"
    className={cn("relative h-2 w-full overflow-hidden rounded-full bg-primary/20", className)}
    value={value}
    max={max}
    {...props}
  >
    <ProgressPrimitive.Indicator
      data-slot="progress-indicator"
      className="h-full w-full flex-1 bg-primary transition-all"
      style={{ transform: `translateX(-${100 - ((value || 0) / max) * 100}%)` }}
    />
  </ProgressPrimitive.Root>
);
