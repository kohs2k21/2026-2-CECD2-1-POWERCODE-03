import { motion, useReducedMotion } from "motion/react";
import type { MockWidget } from "../../../types/mock";
import { WidgetPreviewSurface } from "./WidgetPreviewSurface";

type WidgetDragPreviewProps = {
  widget: MockWidget;
  x: number;
  y: number;
};

export const WidgetDragPreview = ({ widget, x, y }: WidgetDragPreviewProps) => {
  const reduceMotion = useReducedMotion();
  return (
    <motion.div
      animate={{ opacity: 1, scale: 1 }}
      className={`widget-drag-preview widget-drag-preview--${widget.size}`}
      exit={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.96 }}
      initial={reduceMotion ? false : { opacity: 0, scale: 0.96 }}
      transition={reduceMotion ? { duration: 0 } : undefined}
      style={{ left: x, top: y }}
    >
      <WidgetPreviewSurface widget={widget} fallbackLabel={widget.title} />
    </motion.div>
  );
};
