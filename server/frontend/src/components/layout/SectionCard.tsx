import type { CSSProperties, ReactNode } from "react";
import { Card, CardAction, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { motionClasses } from "@/lib/motion";

/**
 * 섹션 카드: 제목(15/22) + 설명 + 오른쪽 명령 + 본문. 제목 수준은 화면 구조에 맞게 고른다.
 * icon(선택): 제목 앞 장식 아이콘(Tabler). 장식이므로 aria-hidden으로 감싸 제목 이름에는 들어가지 않는다.
 * id(선택): 영역 이동 대상(주소 해시·사이드바 목차). 고정 머리글 높이는 문서 scroll-padding이 피한다.
 * 휴대폰 폭(768px 미만)은 좌우 안쪽 여백을 24px → 16px로 줄여 본문 폭을 넓힌다(PC는 그대로).
 * 제목 줄 오른쪽 명령(action): 설명이 없으면 제목과 세로 가운데, 있으면 제목·설명 두 줄 옆 위쪽.
 * fill(선택): 카드가 늘어난 높이(같은 높이 두 열 등)만큼 본문이 세로로 채운다(본문은 세로 flex, 자식이 flex-1로 남은 높이를 가질 수 있음).
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
  "aria-busy": ariaBusy,
  fill = false,
  style,
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
  /** 내용이 아직 채워지는 중(예: 근거 요약 흘러나오기). 보조 기술이 조각마다 읽지 않게 한다. */
  "aria-busy"?: boolean;
  fill?: boolean;
  /** 카드 상자 인라인 스타일(예: 펼칠 때 이전 높이를 최소 높이로 잇기). */
  style?: CSSProperties;
}) => {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <Card id={id} data-slot="section-card" className={cn("gap-4", className)} style={style} aria-label={ariaLabel} aria-busy={ariaBusy || undefined}>
      {/*
        설명 줄이 없고 오른쪽 명령(버튼·배지)이 있으면 한 줄(빈 둘째 줄·간격 없음)에 세로 가운데로 맞춘다(사용자 11/28).
        최소 높이 28px는 이 변경 전 머리 높이(제목 22 + 간격 6)와 같다 — 배지 카드는 그대로, 버튼(32px) 카드는 버튼 높이.
      */}
      <CardHeader className={cn(motionClasses.enter, "max-md:px-4", !description && action && "min-h-7 grid-rows-[auto] items-center")}>
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
        {action && <CardAction className={cn("flex flex-wrap items-center gap-2", !description && "row-span-1 self-center")}>{action}</CardAction>}
      </CardHeader>
      {children !== undefined && (
        <CardContent className={cn(fill ? "flex min-h-0 flex-1 flex-col gap-4" : "grid gap-4", "max-md:px-4")}>{children}</CardContent>
      )}
    </Card>
  );
};
