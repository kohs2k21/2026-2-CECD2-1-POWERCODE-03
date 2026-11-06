import { motion } from "motion/react";
import { IconPlus } from "@tabler/icons-react";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/button";
import { widgetLabels, type WidgetId } from "../data/widgetLayout";
import { WidgetMiniature } from "./WidgetMiniature";

/** 끌기 시작 문턱(px): 이만큼 움직였을 때만 끌기로 본다. */
export const DRAG_START_THRESHOLD = 5;
const startAfterThreshold = (x: number, y: number, start: (point: { x: number; y: number }) => void) => {
  const move = (event: PointerEvent) => {
    if (Math.hypot(event.clientX - x, event.clientY - y) < DRAG_START_THRESHOLD) return;
    stop();
    start({ x: event.clientX, y: event.clientY });
  };
  const stop = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", stop);
    window.removeEventListener("pointercancel", stop);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop);
  window.addEventListener("pointercancel", stop);
};

/**
 * 위젯 추가 목록(#100 이전 WidgetCatalogOverlay 방식): 축소 모양을 끌어 격자의 원하는 칸에 놓거나,
 * `추가` 버튼으로 위에서부터 처음 비어 있는 칸에 넣는다. 축소 모양은 5px 이상 움직여야 끌기를 시작하고(단순 클릭은 동작 없음),
 * 끌기를 시작하면 목록을 닫고 격자 위에 미리보기를 띄운다(OverviewTab).
 */
export const WidgetCatalog = ({
  open,
  widgets,
  onOpenChange,
  onAdd,
  onDragStart,
}: {
  open: boolean;
  widgets: readonly WidgetId[];
  onOpenChange: (open: boolean) => void;
  onAdd: (id: WidgetId) => void;
  onDragStart: (id: WidgetId, point: { x: number; y: number }) => void;
}) => (
  <Modal isOpen={open} onOpenChange={onOpenChange} size="lg" title="위젯 추가">
    {widgets.length ? (
      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {widgets.map((id) => (
          <motion.li
            key={id}
            layout
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ type: "spring", stiffness: 420, damping: 30 }}
            className="grid gap-2"
          >
            <motion.div
              whileHover={{ y: -2 }}
              data-widget-source={id}
              onPointerDown={(event) => {
                if (event.button !== 0) return;
                event.preventDefault();
                startAfterThreshold(event.clientX, event.clientY, (point) => onDragStart(id, point));
              }}
              className="h-widget-preview cursor-grab touch-none rounded-lg border bg-muted shadow-xs transition-shadow hover:shadow-widget-edit active:cursor-grabbing"
            >
              <WidgetMiniature id={id} />
            </motion.div>
            <div className="flex items-center justify-between gap-2">
              <strong className="text-sm font-medium">{widgetLabels[id]}</strong>
              <Button size="sm" variant="outline" aria-label={`${widgetLabels[id]} 추가`} onClick={() => onAdd(id)}>
                <IconPlus aria-hidden="true" />
                추가
              </Button>
            </div>
          </motion.li>
        ))}
      </ul>
    ) : (
      <p className="text-sm text-muted-foreground">추가할 수 있는 위젯을 모두 배치했습니다.</p>
    )}
  </Modal>
);
