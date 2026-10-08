import { IconBell, IconSettings } from "@tabler/icons-react";
import type { NavItem, UserRole, ViewId } from "../../types/app";
import inzentLogo from "./inzent_logo.svg";
type TopNavProps = {
  activeView: ViewId;
  navItems: NavItem[];
  role: UserRole;
  onLogout: () => void;
  onSelectView: (viewId: ViewId) => void;
  onOpenSettings: () => void;
};
export const TopNav = ({
  activeView,
  navItems,
  role,
  onLogout,
  onSelectView,
  onOpenSettings,
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
        <button
          key={id}
          type="button"
          className={
            activeView === id ? "tab-item tab-item--active" : "tab-item"
          }
          aria-current={activeView === id ? "page" : undefined}
          onClick={() => onSelectView(id)}
        >
          <Icon size={16} aria-hidden="true" />
          {label}
        </button>
      ))}
    </nav>
    <div className="top-nav-actions">
      <button type="button" className="ghost-button" onClick={onLogout}>
        로그아웃
      </button>
      <span className="top-nav-icon-btn notification-icon" aria-label="알림">
        <IconBell size={20} aria-hidden="true" />
      </span>
      <button
        type="button"
        className={
          activeView === "settings"
            ? "ghost-button settings-button is-active"
            : "ghost-button settings-button"
        }
        aria-label="설정"
        aria-current={activeView === "settings" ? "page" : undefined}
        onClick={onOpenSettings}
      >
        <IconSettings size={20} aria-hidden="true" />
      </button>
    </div>
  </header>
);
