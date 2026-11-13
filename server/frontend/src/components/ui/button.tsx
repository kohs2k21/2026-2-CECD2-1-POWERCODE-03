import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { motionClasses } from "@/lib/motion";
import { cn } from "@/lib/utils";

const buttonVariants = cva([
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium outline-none disabled:pointer-events-none disabled:opacity-50 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  motionClasses.press,
], {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow-xs hover:bg-primary/90",
        destructive:
          "bg-destructive text-primary-foreground shadow-xs hover:bg-destructive/90 focus-visible:ring-destructive/20",
        outline:
          "border bg-card shadow-xs hover:bg-accent hover:text-accent-foreground aria-[current=page]:bg-accent",
        secondary: "bg-secondary text-secondary-foreground shadow-xs hover:bg-secondary/80",
        ghost: "hover:bg-accent hover:text-accent-foreground aria-[current=page]:bg-accent",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2 has-[>svg]:px-3",
        sm: "h-8 gap-1.5 rounded-md px-3 has-[>svg]:px-2.5",
        lg: "h-10 rounded-md px-6 has-[>svg]:px-4",
        icon: "size-9",
        "icon-sm": "size-8",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

type ButtonProps = ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean };

/** 기본 type="button"(ui.md). 제출 버튼은 type="submit" 명시. URL 이동은 asChild + Link. */
export const Button = ({ asChild = false, className, size, variant, ...props }: ButtonProps) => {
  const Component = asChild ? Slot : "button";
  return (
    <Component
      data-slot="button"
      type={asChild ? undefined : "button"}
      className={cn(buttonVariants({ size, variant }), className)}
      {...props}
    />
  );
};
export { buttonVariants };
