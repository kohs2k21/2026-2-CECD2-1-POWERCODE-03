import { IconBell, IconSettings } from "@tabler/icons-react";
import type { NavItem, UserRole, ViewId } from "../../types/app";
import inzentLogo from "./inzent_logo.svg";
import { Link } from "react-router-dom";
import { Button } from "../../components/ui/button";
import { viewPaths } from "../../app/routePaths";
type TopNavProps = {
  activeView: ViewId;
  navItems: NavItem[];
  role: UserRole;
  onLogout: () => void;
  settingsPath: string;
};
export const TopNav = ({
  activeView,
  navItems,
  role,
  onLogout,
  settingsPath,
}: TopNavProps) => (
  <header className="top-nav">
    <a className="skip-link" href="#workspace-content">
      본문으로 이동
    </a>
    <div className="brand">
      <img src={inzentLogo} alt="INZENT" className="brand-logo" />
      <div>
        <strong>ESB 이상 징후 탐지 대시보드</strong>
        <span>{role === "admin" ? "관리자" : "일반 사용자"} 모드</span>
      </div>
    </div>
    <nav className="tab-list" aria-label="주요 화면">
      {navItems.map(({ id, label, Icon }) => (
        <Button
          asChild
          variant="ghost"
          key={id}
          className={
            activeView === id ? "tab-item tab-item--active" : "tab-item"
          }
        >
          <Link
            to={viewPaths[id]}
            aria-current={activeView === id ? "page" : undefined}
          >
            <Icon size={16} aria-hidden="true" />
            {label}
          </Link>
        </Button>
      ))}
    </nav>
    <div className="top-nav-actions">
      <Button variant="outline" onClick={onLogout}>
        로그아웃
      </Button>
      <span className="top-nav-icon-btn notification-icon" aria-label="알림">
        <IconBell size={20} aria-hidden="true" />
      </span>
      <Button
        asChild
        variant="ghost"
        size="icon"
        className={
          activeView === "settings"
            ? "settings-button is-active"
            : "settings-button"
        }
      >
        <Link
          to={settingsPath}
          aria-label="설정"
          aria-current={activeView === "settings" ? "page" : undefined}
        >
          <IconSettings size={20} aria-hidden="true" />
        </Link>
      </Button>
    </div>
  </header>
);
