import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "../../lib/utils";

const buttonVariants = cva("ui-button", {
  variants: {
    variant: {
      default: "ui-button--primary",
      outline: "ui-button--outline",
      ghost: "ui-button--ghost",
    },
    size: {
      default: "ui-button--default",
      sm: "ui-button--sm",
      icon: "ui-button--icon",
      "icon-sm": "ui-button--icon-sm",
    },
  },
  defaultVariants: { variant: "default", size: "default" },
});
type ButtonProps = ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean };
export const Button = ({
  asChild = false,
  className,
  size,
  variant,
  ...props
}: ButtonProps) => {
  const Component = asChild ? Slot : "button";
  return (
    <Component
      type={asChild ? undefined : "button"}
      className={cn(buttonVariants({ size, variant }), className)}
      {...props}
    />
  );
};
export { buttonVariants };
