import * as DialogPrimitive from "@radix-ui/react-dialog";
import { IconX } from "@tabler/icons-react";
import { useRef, type ComponentProps } from "react";
import { cn } from "../../lib/utils";
import { Button } from "./button";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;
export const DialogPortal = DialogPrimitive.Portal;

export const DialogOverlay = ({
  className,
  ...props
}: ComponentProps<typeof DialogPrimitive.Overlay>) => (
  <DialogPrimitive.Overlay
    className={cn("ui-dialog-overlay", className)}
    {...props}
  />
);

type DialogContentProps = ComponentProps<typeof DialogPrimitive.Content> & {
  showCloseButton?: boolean;
};
export const DialogContent = ({
  children,
  className,
  showCloseButton = true,
  onOpenAutoFocus,
  onCloseAutoFocus,
  ...props
}: DialogContentProps) => {
  const returnFocus = useRef<HTMLElement | null>(null);
  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Content
        className={cn("ui-dialog-content", className)}
        onOpenAutoFocus={(event) => {
          returnFocus.current =
            document.activeElement instanceof HTMLElement
              ? document.activeElement
              : null;
          onOpenAutoFocus?.(event);
        }}
        onCloseAutoFocus={(event) => {
          onCloseAutoFocus?.(event);
          if (!event.defaultPrevented && returnFocus.current?.isConnected) {
            event.preventDefault();
            returnFocus.current.focus();
          }
        }}
        {...props}
      >
        {children}
        {showCloseButton && (
          <DialogClose asChild>
            <Button
              variant="ghost"
              size="icon"
              className="ui-dialog-close"
              aria-label="닫기"
            >
              <IconX size={16} aria-hidden="true" />
            </Button>
          </DialogClose>
        )}
      </DialogPrimitive.Content>
    </DialogPortal>
  );
};
export const DialogHeader = ({
  className,
  ...props
}: ComponentProps<"div">) => (
  <div className={cn("ui-dialog-header", className)} {...props} />
);
export const DialogFooter = ({
  className,
  ...props
}: ComponentProps<"div">) => (
  <div className={cn("ui-dialog-footer", className)} {...props} />
);
export const DialogTitle = ({
  className,
  ...props
}: ComponentProps<typeof DialogPrimitive.Title>) => (
  <DialogPrimitive.Title
    className={cn("ui-dialog-title", className)}
    {...props}
  />
);
export const DialogDescription = ({
  className,
  ...props
}: ComponentProps<typeof DialogPrimitive.Description>) => (
  <DialogPrimitive.Description
    className={cn("ui-dialog-description", className)}
    {...props}
  />
);
