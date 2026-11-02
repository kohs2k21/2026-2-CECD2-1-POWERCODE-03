import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** 페이지 머리글: 제목 28/36(ui.md) + 설명 + 오른쪽 명령 버튼. 제목 아래 내부 탐색과 32px 간격은 사용하는 쪽 gap으로 둔다. */
export const PageHeader = ({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) => (
  <header data-slot="page-header" className={cn("flex flex-wrap items-start justify-between gap-4", className)}>
    <div className="grid min-w-0 gap-1">
      <h1 className="text-page-title">{title}</h1>
      {description && <p className="text-sm text-muted-foreground">{description}</p>}
    </div>
    {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
  </header>
);

/**
 * 화면 제목(시각적으로 숨김): 운영 현황·이상 분석·탐지 설정은 상단 메뉴·사이드바의 현재 표시로 위치를 알 수 있어
 * 제목 줄을 두지 않는다(사용자 11/03). 화면 낭독기·문서 구조용 h1만 남긴다.
 */
export const ScreenTitle = ({ children }: { children: ReactNode }) => <h1 className="sr-only">{children}</h1>;
