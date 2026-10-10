import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import type { ComponentProps } from "react";
import { cn } from "../../lib/utils";

export const TooltipProvider = TooltipPrimitive.Provider;
export const Tooltip = TooltipPrimitive.Root;
export const TooltipTrigger = TooltipPrimitive.Trigger;

type TooltipContentProps = ComponentProps<typeof TooltipPrimitive.Content>;

export const TooltipContent = ({
  className,
  sideOffset = 6,
  ...props
}: TooltipContentProps) => {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        className={cn("ui-tooltip", className)}
        sideOffset={sideOffset}
        {...props}
      />
    </TooltipPrimitive.Portal>
  );
};
