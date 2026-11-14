import { useEffect, useRef } from "react";
import { IconActivity, IconBrain, IconHome, IconSettings, type Icon } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { observeHeight } from "@/lib/observeHeight";
import { cn } from "@/lib/utils";
import { activeTopNav, visibleTopNavItems, type TopNavId } from "@/app/routes";
import type { UserRole } from "@/types/app";
import { NotificationsBell } from "@/features/notifications/NotificationsBell";
import { CountBadge } from "@/features/notifications/CountBadge";
import { badgeText } from "@/features/notifications/notificationLabels";
import { analysisUnread, detectionUnread } from "@/features/notifications/notificationsApi";
import { useUnreadCount } from "@/features/notifications/useNotifications";
import inzentLogo from "./inzent_logo.svg";

type TopNavProps = {
  pathname: string;
  role: UserRole;
  onLogout: () => void;
  settingsPath: string;
};

const navIcons: Record<TopNavId, Icon> = { operations: IconHome, analysis: IconActivity, detection: IconBrain };

/** 상단 메뉴 알약형 항목(935e5eb tab-item): 36px·좌우 16px·완전 둥근 모서리, 색·배경·테두리 0.18초 전환, 현재 항목은 테두리+옅은 배경. */
const pillItem =
  "h-9 gap-1.5 rounded-full border border-transparent px-4 text-muted-foreground transition-[color,background-color,border-color,scale] duration-(--motion-duration-base) ease-motion-standard has-[>svg]:px-4 aria-[current=page]:border-border aria-[current=page]:bg-accent aria-[current=page]:text-foreground max-nav-compact:px-2.5 max-nav-compact:text-nav-compact max-nav-compact:has-[>svg]:px-2.5";
const pillIcon =
  "rounded-full text-muted-foreground transition-[color,background-color,border-color,scale] duration-(--motion-duration-base) ease-motion-standard [&_svg]:size-5";

/**
 * 상단 메뉴(origin/dev 935e5eb 모양 유지): 좌측 로고·제목 / 가운데 정렬 메뉴 / 우측 로그아웃·알림·설정.
 * 반투명 배경 + 흐림, 1100px 이하는 메뉴를 둘째 줄로, 700px 이하는 크기를 줄인다.
 */
export const TopNav = ({ pathname, role, onLogout, settingsPath }: TopNavProps) => {
  const active = activeTopNav(pathname);
  const header = useRef<HTMLElement>(null);
  // 메뉴 배지(와이어프레임 badgeAnalysis·badgeDetect): 알림 버튼과 같은 미확인 수 조회(같은 Query 캐시)의 판정·사건 수, 작업 알림 수(관리자만 — 탐지 설정 메뉴도 관리자만).
  // 다시 조회는 알림 버튼 흐름(이동·패널·판정 이벤트·작업 종료)을 따른다. 0·불러오기 전·조회 실패는 숨김(실패 표시는 알림 버튼 `?`가 맡는다).
  const unread = useUnreadCount();
  const counted = unread.data && !unread.isError ? unread.data : null;
  const badges: Partial<Record<TopNavId, number>> = { analysis: counted ? analysisUnread(counted) : 0, detection: counted ? detectionUnread(counted) : 0 };
  // 고정 영역(사이드바·룰 설정 저장 줄)이 머리글 아래에 붙도록 실제 높이를 알린다. 좁은 화면에서는 메뉴가 둘째 줄로 내려가 높이가 달라진다.
  useEffect(() => observeHeight(header.current, "--top-nav-offset"), []);
  return (
    <header ref={header} className="sticky top-0 z-(--z-header) grid min-h-(--top-nav-height) grid-cols-[minmax(0,1fr)_auto] items-center gap-6 border-b bg-card/92 px-6 py-3 backdrop-blur-lg nav-stack:grid-cols-(--top-nav-columns) max-nav-compact:gap-3 max-nav-compact:p-3">
      <a
        href="#workspace-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:rounded-md focus:bg-card focus:px-3 focus:py-2 focus:shadow-md"
      >
        본문으로 이동
      </a>
      <div className="flex min-w-0 items-center gap-3 justify-self-start">
        <img src={inzentLogo} alt="INZENT" className="block h-brand-logo w-auto object-contain max-nav-compact:h-brand-logo-compact" />
        <div className="min-w-0">
          <strong className="block text-sm leading-5 max-nav-compact:max-w-brand-title-compact max-nav-compact:text-brand-compact max-nav-compact:leading-(--text-brand-compact--line-height)">
            ESB 이상 징후 탐지 대시보드
          </strong>
          <span className="block text-xs leading-4 text-subtle max-nav-compact:text-brand-compact max-nav-compact:leading-4">
            {role === "admin" ? "관리자" : "일반 사용자"} 모드
          </span>
        </div>
      </div>
      <nav
        aria-label="주요 화면"
        className="flex items-center justify-center gap-2 justify-self-center max-nav-stack:col-span-full max-nav-stack:row-start-2 max-nav-stack:flex-wrap max-nav-stack:justify-self-start max-nav-compact:w-full max-nav-compact:justify-start max-nav-compact:gap-1"
      >
        {visibleTopNavItems(role).map((item) => {
          const ItemIcon = navIcons[item.id];
          const badge = badges[item.id] ?? 0;
          return (
            <Button asChild variant="ghost" key={item.id} className={cn(pillItem, "relative")}>
              <Link
                to={item.to}
                aria-current={active === item.id ? "page" : undefined}
                aria-label={badge ? `${item.label} · 미확인 ${badge}건` : undefined}
              >
                <ItemIcon aria-hidden="true" />
                {item.label}
                {badge > 0 && <CountBadge data-nav-badge={item.id}>{badgeText(badge)}</CountBadge>}
              </Link>
            </Button>
          );
        })}
      </nav>
      <div className="flex items-center gap-2 justify-self-end max-nav-stack:col-start-2 max-nav-stack:row-start-1 max-nav-compact:gap-0">
        <Button variant="outline" onClick={onLogout} className="h-9 rounded-full px-4 max-nav-compact:text-xs">
          로그아웃
        </Button>
        <NotificationsBell triggerClassName={pillIcon} />
        <Button asChild variant="ghost" size="icon" className={pillIcon}>
          <Link to={settingsPath} aria-label="설정" aria-current={active === "settings" ? "page" : undefined}>
            <IconSettings aria-hidden="true" />
          </Link>
        </Button>
      </div>
    </header>
  );
};
