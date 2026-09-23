import { useEffect, useState } from "react";
import { AppShell } from "../components/layout/AppShell";
import { AnalysisMock } from "../features/analysis/AnalysisMock";
import { AdminModelPlaceholder } from "../features/admin_model/AdminModelPlaceholder";
import { AdminSystemPlaceholder } from "../features/admin_system/AdminSystemPlaceholder";
import { AuthPage } from "../features/auth/AuthPage";
import { HomeMock } from "../features/home/HomeMock";
import { TopNav } from "../features/navigation/TopNav";
import { SettingsPlaceholder } from "../features/settings/SettingsPlaceholder";
import { fetchCurrentUser } from "../services/api/auth.api";
import {
  clearStoredToken,
  getStoredToken,
  storeToken,
} from "../services/auth/session";
import { createInitialAppState } from "../stores/appStore";
import type { AuthSession, AuthUser } from "../types/auth";
import type { ViewId } from "../types/app";
import { getVisibleNavItems } from "./router";

type AuthStatus = "checking" | "signedOut" | "signedIn";

export const App = () => {
  const [authStatus, setAuthStatus] = useState<AuthStatus>("checking");
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [activeView, setActiveView] = useState<ViewId>(
    () => createInitialAppState().activeView,
  );

  useEffect(() => {
    let isMounted = true;
    const token = getStoredToken();

    if (!token) {
      setAuthStatus("signedOut");
      return () => {
        isMounted = false;
      };
    }

    fetchCurrentUser()
      .then((user) => {
        if (!isMounted) {
          return;
        }

        setCurrentUser(user);
        setAuthStatus("signedIn");
      })
      .catch(() => {
        if (!isMounted) {
          return;
        }

        clearStoredToken();
        setCurrentUser(null);
        setAuthStatus("signedOut");
      });

    return () => {
      isMounted = false;
    };
  }, []);

  const handleLoginSuccess = (session: AuthSession) => {
    storeToken(session.token);
    setCurrentUser(session.user);
    setActiveView("home");
    setAuthStatus("signedIn");
  };

  const handleLogout = () => {
    clearStoredToken();
    setCurrentUser(null);
    setActiveView("home");
    setAuthStatus("signedOut");
  };

  if (authStatus === "checking") {
    return (
      <main className="auth-loading" aria-live="polite">
        <span className="auth-loading__spinner" aria-hidden="true" />
        <p>로그인 상태를 확인하는 중...</p>
      </main>
    );
  }

  if (!currentUser || authStatus !== "signedIn") {
    return <AuthPage onLoginSuccess={handleLoginSuccess} />;
  }

  const visibleNavItems = getVisibleNavItems(currentUser.userType);

  return (
    <AppShell
      topNav={
        <TopNav
          activeView={activeView}
          navItems={visibleNavItems}
          role={currentUser.userType}
          onChangeRole={handleLogout}
          onSelectView={setActiveView}
        />
      }
    >
      <ViewPanel
        activeView={activeView}
        role={currentUser.userType}
        onSelectView={setActiveView}
      />
    </AppShell>
  );
};

type ViewPanelProps = {
  activeView: ViewId;
  role: AuthUser["userType"];
  onSelectView: (view: ViewId) => void;
};

const ViewPanel = ({ activeView, role, onSelectView }: ViewPanelProps) => {
  if (activeView === "home") {
    return <HomeMock role={role} onSelectView={onSelectView} />;
  }

  if (activeView === "analysis") {
    return <AnalysisMock />;
  }

  if (activeView === "settings") {
    return <SettingsPlaceholder />;
  }

  if (activeView === "system") {
    return <AdminSystemPlaceholder />;
  }

  if (activeView === "model") {
    return <AdminModelPlaceholder />;
  }

  return <AnalysisMock />;
};
