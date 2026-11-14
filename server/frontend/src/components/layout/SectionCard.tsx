import type { ReactNode } from "react";
import { Card, CardAction, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { motionClasses } from "@/lib/motion";

/**
 * 섹션 카드: 제목(15/22) + 설명 + 오른쪽 명령 + 본문. 제목 수준은 화면 구조에 맞게 고른다.
 * icon(선택): 제목 앞 장식 아이콘(Tabler). 장식이므로 aria-hidden으로 감싸 제목 이름에는 들어가지 않는다.
 * id(선택): 영역 이동 대상(주소 해시·사이드바 목차). 고정 머리글 높이는 문서 scroll-padding이 피한다.
 */
export const SectionCard = ({
  id,
  title,
  icon,
  description,
  action,
  children,
  className,
  headingLevel = 2,
  "aria-label": ariaLabel,
}: {
  id?: string;
  title: ReactNode;
  icon?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
  headingLevel?: 2 | 3;
  "aria-label"?: string;
}) => {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <Card id={id} data-slot="section-card" className={cn("gap-4", className)} aria-label={ariaLabel}>
      <CardHeader className={motionClasses.enter}>
        {icon ? (
          <Heading className="flex min-w-0 items-center gap-2 text-section-title">
            <span aria-hidden="true" className="flex shrink-0 [&_svg]:size-4.5">
              {icon}
            </span>
            {title}
          </Heading>
        ) : (
          <Heading className="text-section-title">{title}</Heading>
        )}
        {description && <CardDescription>{description}</CardDescription>}
        {action && <CardAction className="flex flex-wrap items-center gap-2">{action}</CardAction>}
      </CardHeader>
      {children !== undefined && <CardContent className="grid gap-4">{children}</CardContent>}
    </Card>
  );
};
