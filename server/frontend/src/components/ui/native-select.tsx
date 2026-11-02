import { IconChevronDown } from "@tabler/icons-react";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/** shadcn native-select: 폼 입력용 기본 select(키보드·모바일 기본 동작 유지). */
export const NativeSelect = ({
  className,
  size = "default",
  ...props
}: Omit<ComponentProps<"select">, "size"> & { size?: "sm" | "default" }) => (
  <div
    data-slot="native-select-wrapper"
    className="group/native-select relative w-full has-[select:disabled]:opacity-50"
  >
    <select
      data-slot="native-select"
      data-size={size}
      className={cn(
        "h-9 w-full min-w-0 appearance-none rounded-md border border-input bg-card px-3 py-2 pr-9 text-sm shadow-xs transition-[color,box-shadow] outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed aria-invalid:border-destructive aria-invalid:ring-destructive/20 data-[size=sm]:h-8 data-[size=sm]:py-1",
        className,
      )}
      {...props}
    />
    <IconChevronDown
      data-slot="native-select-icon"
      aria-hidden="true"
      className="pointer-events-none absolute top-1/2 right-3.5 size-4 -translate-y-1/2 text-muted-foreground opacity-50 select-none"
    />
  </div>
);
export const NativeSelectOption = (props: ComponentProps<"option">) => (
  <option data-slot="native-select-option" {...props} />
);
