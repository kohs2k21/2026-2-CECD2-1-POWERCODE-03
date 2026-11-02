import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export const Table = ({ className, ...props }: ComponentProps<"table">) => (
  <div data-slot="table-container" className="relative w-full overflow-x-auto">
    <table data-slot="table" className={cn("w-full caption-bottom text-sm", className)} {...props} />
  </div>
);
export const TableHeader = ({ className, ...props }: ComponentProps<"thead">) => (
  <thead data-slot="table-header" className={cn("[&_tr]:border-b", className)} {...props} />
);
export const TableBody = ({ className, ...props }: ComponentProps<"tbody">) => (
  <tbody data-slot="table-body" className={cn("[&_tr:last-child]:border-0", className)} {...props} />
);
export const TableFooter = ({ className, ...props }: ComponentProps<"tfoot">) => (
  <tfoot
    data-slot="table-footer"
    className={cn("border-t bg-muted/50 font-medium [&>tr]:last:border-b-0", className)}
    {...props}
  />
);
export const TableRow = ({ className, ...props }: ComponentProps<"tr">) => (
  <tr
    data-slot="table-row"
    className={cn("border-b transition-colors hover:bg-muted/50 data-[state=selected]:bg-muted", className)}
    {...props}
  />
);
export const TableHead = ({ className, ...props }: ComponentProps<"th">) => (
  <th
    data-slot="table-head"
    className={cn(
      "h-10 whitespace-nowrap px-2 text-left align-middle font-medium text-foreground [&:has([role=checkbox])]:pr-0",
      className,
    )}
    {...props}
  />
);
export const TableCell = ({ className, ...props }: ComponentProps<"td">) => (
  <td
    data-slot="table-cell"
    className={cn("p-2 align-middle [&:has([role=checkbox])]:pr-0", className)}
    {...props}
  />
);
export const TableCaption = ({ className, ...props }: ComponentProps<"caption">) => (
  <caption data-slot="table-caption" className={cn("mt-4 text-sm text-muted-foreground", className)} {...props} />
);
