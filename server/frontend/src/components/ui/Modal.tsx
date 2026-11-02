import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "./dialog";

export type ModalSize = "sm" | "md" | "lg" | "xl" | "full";
const sizeClass: Record<ModalSize, string> = {
  sm: "sm:max-w-sm",
  md: "sm:max-w-lg",
  lg: "sm:max-w-2xl",
  xl: "sm:max-w-4xl",
  full: "sm:max-w-[calc(100%-4rem)]",
};
export interface ModalProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  size?: ModalSize;
  children: ReactNode;
  trigger?: ReactNode;
  showCloseButton?: boolean;
}
export const Modal = ({
  isOpen,
  onOpenChange,
  title,
  description,
  size = "md",
  children,
  trigger,
  showCloseButton = true,
}: ModalProps) => (
  <Dialog open={isOpen} onOpenChange={onOpenChange}>
    {trigger && <DialogTrigger asChild>{trigger}</DialogTrigger>}
    <DialogContent
      className={cn(sizeClass[size])}
      showCloseButton={showCloseButton}
      {...(description ? {} : { "aria-describedby": undefined })}
    >
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        {description && <DialogDescription>{description}</DialogDescription>}
      </DialogHeader>
      <div className="grid gap-4">{children}</div>
    </DialogContent>
  </Dialog>
);
