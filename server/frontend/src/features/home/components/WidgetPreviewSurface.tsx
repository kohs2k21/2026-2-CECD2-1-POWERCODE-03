import type { MockWidget } from "../../../types/mock";
export const WidgetPreviewSurface = ({ widget }: { widget: MockWidget; fallbackLabel: string }) => (
  <div style={{ padding: "8px", display: "grid", placeItems: "center", height: "100%", textAlign: "center", pointerEvents: "none" }}>
    <span>{widget.title}<br />배치 미리보기</span>
  </div>
);
