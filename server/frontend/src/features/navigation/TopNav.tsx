import { IconActivity, IconBrain, IconHome, IconSettings, type Icon } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { activeTopNav, visibleTopNavItems, type TopNavId } from "@/app/routes";
import type { UserRole } from "@/types/app";
import { NotificationsBell } from "@/features/notifications/NotificationsBell";
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
  "h-9 gap-1.5 rounded-full border border-transparent px-4 text-muted-foreground transition-[color,background-color,border-color] duration-180 ease-[ease] has-[>svg]:px-4 aria-[current=page]:border-border aria-[current=page]:bg-accent aria-[current=page]:text-foreground max-nav-compact:px-2.5 max-nav-compact:text-nav-compact max-nav-compact:has-[>svg]:px-2.5";
const pillIcon =
  "rounded-full text-muted-foreground transition-[color,background-color,border-color] duration-150 ease-[ease] [&_svg]:size-5";

/**
 * 상단 메뉴(origin/dev 935e5eb 모양 유지): 좌측 로고·제목 / 가운데 정렬 메뉴 / 우측 로그아웃·알림·설정.
 * 반투명 배경 + 흐림, 1100px 이하는 메뉴를 둘째 줄로, 700px 이하는 크기를 줄인다.
 */
export const TopNav = ({ pathname, role, onLogout, settingsPath }: TopNavProps) => {
  const active = activeTopNav(pathname);
  return (
    <header className="sticky top-0 z-(--z-header) grid min-h-(--top-nav-height) grid-cols-[minmax(0,1fr)_auto] items-center gap-6 border-b bg-card/92 px-6 py-3 backdrop-blur-lg nav-stack:grid-cols-(--top-nav-columns) max-nav-compact:gap-3 max-nav-compact:p-3">
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
          return (
            <Button asChild variant="ghost" key={item.id} className={pillItem}>
              <Link to={item.to} aria-current={active === item.id ? "page" : undefined}>
                <ItemIcon aria-hidden="true" />
                {item.label}
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
