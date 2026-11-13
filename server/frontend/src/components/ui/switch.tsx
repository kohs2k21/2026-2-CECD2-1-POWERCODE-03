import * as SwitchPrimitive from "@radix-ui/react-switch";
import type { ComponentProps } from "react";
import { motionClasses } from "@/lib/motion";
import { cn } from "@/lib/utils";

export const Switch = ({ className, ...props }: ComponentProps<typeof SwitchPrimitive.Root>) => (
  <SwitchPrimitive.Root
    data-slot="switch"
    className={cn(
      "peer inline-flex h-[1.15rem] w-8 shrink-0 items-center rounded-full border border-transparent shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary data-[state=unchecked]:bg-input",
      motionClasses.toggle,
      className,
    )}
    {...props}
  >
    <SwitchPrimitive.Thumb
      data-slot="switch-thumb"
      className={cn(
        "pointer-events-none block size-4 rounded-full bg-card ring-0 data-[state=checked]:translate-x-[calc(100%-2px)] data-[state=unchecked]:translate-x-0",
        motionClasses.toggle,
      )}
    />
  </SwitchPrimitive.Root>
);
