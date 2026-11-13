import * as DialogPrimitive from "@radix-ui/react-dialog";
import { IconX } from "@tabler/icons-react";
import { useRef, type ComponentProps } from "react";
import { motionClasses } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { Button } from "./button";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;
export const DialogPortal = DialogPrimitive.Portal;

export const DialogOverlay = ({ className, ...props }: ComponentProps<typeof DialogPrimitive.Overlay>) => (
  <DialogPrimitive.Overlay
    data-slot="dialog-overlay"
    className={cn(
      "fixed inset-0 z-(--z-dialog-overlay) bg-overlay",
      motionClasses.overlay,
      className,
    )}
    {...props}
  />
);

type DialogContentProps = ComponentProps<typeof DialogPrimitive.Content> & { showCloseButton?: boolean };

/** 닫힌 뒤 연 요소로 포커스를 돌려 준다(ui.md). Escape·외부 클릭은 onOpenChange(false). */
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
        data-slot="dialog-content"
        className={cn(
          "fixed top-1/2 left-1/2 z-(--z-dialog) grid max-h-[calc(100%-2rem)] w-full max-w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 gap-4 overflow-y-auto rounded-lg border bg-card p-6 text-card-foreground shadow-dialog sm:max-w-lg",
          motionClasses.dialog,
          className,
        )}
        onOpenAutoFocus={(event) => {
          returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
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
            <Button variant="ghost" size="icon-sm" className="absolute top-3 right-3" aria-label="닫기">
              <IconX aria-hidden="true" />
            </Button>
          </DialogClose>
        )}
      </DialogPrimitive.Content>
    </DialogPortal>
  );
};
export const DialogHeader = ({ className, ...props }: ComponentProps<"div">) => (
  <div data-slot="dialog-header" className={cn("flex flex-col gap-2 pr-8 text-left", className)} {...props} />
);
/** 확인·취소 버튼 영역: 본문과 24px, 상단 16px 패딩·구분선, 버튼 사이 12px(ui.md). */
export const DialogFooter = ({ className, ...props }: ComponentProps<"div">) => (
  <div
    data-slot="dialog-footer"
    className={cn("mt-2 flex flex-col-reverse gap-3 border-t pt-4 sm:flex-row sm:justify-end", className)}
    {...props}
  />
);
export const DialogTitle = ({ className, ...props }: ComponentProps<typeof DialogPrimitive.Title>) => (
  <DialogPrimitive.Title
    data-slot="dialog-title"
    className={cn("text-lg leading-none font-semibold", className)}
    {...props}
  />
);
export const DialogDescription = ({ className, ...props }: ComponentProps<typeof DialogPrimitive.Description>) => (
  <DialogPrimitive.Description
    data-slot="dialog-description"
    className={cn("text-sm text-muted-foreground", className)}
    {...props}
  />
);
