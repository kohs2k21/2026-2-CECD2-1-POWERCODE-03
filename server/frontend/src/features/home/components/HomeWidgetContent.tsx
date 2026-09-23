import type { ViewId } from "../../../types/app";
import type { MockWidget } from "../../../types/mock";
import { renderFullWidget } from "./home-widgets/FullWidgetRenderer";

type HomeWidgetContentProps = {
  widget: MockWidget;
  isEditing: boolean;
  onSelectView?: (view: ViewId) => void;
};

export const HomeWidgetContent = ({ widget, isEditing, onSelectView }: HomeWidgetContentProps) => {
  const chartWrapperStyle: React.CSSProperties = {
    width: "100%",
    height: "calc(100% - 44px)",
    pointerEvents: isEditing ? "none" : "auto",
    marginTop: "8px",
  };
  const chart = renderFullWidget(widget.widgetId, onSelectView);

  if (chart) {
    return <div style={chartWrapperStyle}>{chart}</div>;
  }

  return (
    <>
      <strong>{widget.value}</strong>
      <p>{widget.meta}</p>
      <p className="widget-card__description">{widget.description}</p>
      <ul className="widget-card__supporting-list">
        {widget.supportingItems.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </>
  );
};
