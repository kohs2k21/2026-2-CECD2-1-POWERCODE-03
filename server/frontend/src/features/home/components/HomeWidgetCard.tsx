import { IconMinus } from "@tabler/icons-react";
import type { ReactNode } from "react";
import type { MockWidget } from "../../../types/mock";
import { Button } from "../../../components/ui/button";

type HomeWidgetCardProps = {
  widget: MockWidget;
  isEditing: boolean;
  onRemove: (event: React.MouseEvent<HTMLButtonElement>) => void;
  children: ReactNode;
};

export const HomeWidgetCard = ({
  widget,
  isEditing,
  onRemove,
  children,
}: HomeWidgetCardProps) => (
  <article
    className={isEditing ? "widget-card widget-card--editing" : "widget-card"}
  >
    {isEditing && (
      <Button
        variant="ghost"
        size="icon-sm"
        className="widget-remove-button"
        onClick={onRemove}
      >
        <IconMinus size={12} aria-hidden="true" />
        <span className="sr-only">{widget.title} 위젯 숨기기</span>
      </Button>
    )}
    <div className="widget-card__header">
      <h2>{widget.title}</h2>
    </div>
    {children}
  </article>
);
