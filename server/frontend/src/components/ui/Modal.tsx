import type { ReactNode } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "./dialog";

export type ModalSize = "sm" | "md" | "lg" | "xl" | "full";
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
      className={"ui-modal--" + size}
      showCloseButton={showCloseButton}
      {...(description ? {} : { "aria-describedby": undefined })}
    >
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        {description && <DialogDescription>{description}</DialogDescription>}
      </DialogHeader>
      <div className="ui-dialog-body">{children}</div>
    </DialogContent>
  </Dialog>
);
