import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export const TooltipProvider = ({
  delayDuration = 0,
  ...props
}: ComponentProps<typeof TooltipPrimitive.Provider>) => (
  <TooltipPrimitive.Provider delayDuration={delayDuration} {...props} />
);
export const Tooltip = (props: ComponentProps<typeof TooltipPrimitive.Root>) => (
  <TooltipPrimitive.Root data-slot="tooltip" {...props} />
);
export const TooltipTrigger = (props: ComponentProps<typeof TooltipPrimitive.Trigger>) => (
  <TooltipPrimitive.Trigger data-slot="tooltip-trigger" {...props} />
);
export const TooltipContent = ({
  className,
  sideOffset = 6,
  children,
  ...props
}: ComponentProps<typeof TooltipPrimitive.Content>) => (
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content
      data-slot="tooltip-content"
      sideOffset={sideOffset}
      className={cn(
        "z-(--z-tooltip) w-fit origin-(--radix-tooltip-content-transform-origin) text-balance rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
        className,
      )}
      {...props}
    >
      {children}
      <TooltipPrimitive.Arrow className="z-(--z-tooltip) size-2.5 translate-y-[calc(-50%_-_2px)] rotate-45 rounded-xs bg-primary fill-primary" />
    </TooltipPrimitive.Content>
  </TooltipPrimitive.Portal>
);
