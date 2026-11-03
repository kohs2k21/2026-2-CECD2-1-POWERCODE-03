import type { ReactNode } from "react";
import { Card, CardAction, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** 섹션 카드: 제목(15/22) + 설명 + 오른쪽 명령 + 본문. 제목 수준은 화면 구조에 맞게 고른다. */
export const SectionCard = ({
  title,
  description,
  action,
  children,
  className,
  headingLevel = 2,
  "aria-label": ariaLabel,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
  headingLevel?: 2 | 3;
  "aria-label"?: string;
}) => {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <Card data-slot="section-card" className={cn("gap-4", className)} aria-label={ariaLabel}>
      <CardHeader>
        <Heading className="text-section-title">{title}</Heading>
        {description && <CardDescription>{description}</CardDescription>}
        {action && <CardAction className="flex flex-wrap items-center gap-2">{action}</CardAction>}
      </CardHeader>
      {children !== undefined && <CardContent className="grid gap-4">{children}</CardContent>}
    </Card>
  );
};
