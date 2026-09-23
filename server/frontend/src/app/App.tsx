import { useEffect, useState } from "react";
import { AppShell } from "../components/layout/AppShell";
import { AnalysisWorkspace } from "../features/analysis/AnalysisWorkspace";
import { AdminModelPlaceholder } from "../features/admin_model/AdminModelPlaceholder";
import { AdminSystemPlaceholder } from "../features/admin_system/AdminSystemPlaceholder";
import { AuthPage } from "../features/auth/AuthPage";
import { HomeMock } from "../features/home/HomeMock";
import { TopNav } from "../features/navigation/TopNav";
import { SettingsPlaceholder } from "../features/settings/SettingsPlaceholder";
import { fetchCurrentUser } from "../services/api/auth.api";
import { HttpError } from "../services/api/client";
import {
  clearStoredToken,
  getStoredToken,
  storeToken,
} from "../services/auth/session";
import { createInitialAppState } from "../stores/appStore";
import type { AuthSession, AuthUser } from "../types/auth";
import type { ViewId } from "../types/app";
import { getVisibleNavItems } from "./router";

type AuthStatus = "checking" | "signedOut" | "signedIn" | "verificationFailed";

export const App = () => {
  const [authStatus, setAuthStatus] = useState<AuthStatus>("checking");
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [authCheckAttempt, setAuthCheckAttempt] = useState(0);
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
        setAuthError(null);
        setAuthStatus("signedIn");
      })
      .catch((error: unknown) => {
        if (!isMounted) {
          return;
        }

        setCurrentUser(null);

        if (
          error instanceof HttpError &&
          (error.status === 401 || error.status === 403 || error.status === 404)
        ) {
          clearStoredToken();
          setAuthError(null);
          setAuthStatus("signedOut");
          return;
        }

        setAuthError(
          "로그인 상태를 확인하지 못했습니다. 다시 확인하거나 다른 계정으로 로그인해 주세요.",
        );
        setAuthStatus("verificationFailed");
      });

    return () => {
      isMounted = false;
    };
  }, [authCheckAttempt]);

  const handleRetryAuthCheck = () => {
    setAuthError(null);
    setAuthStatus("checking");
    setAuthCheckAttempt((current) => current + 1);
  };

  const handleLoginSuccess = (session: AuthSession) => {
    storeToken(session.token);
    setCurrentUser(session.user);
    setAuthError(null);
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
    return (
      <AuthPage
        onLoginSuccess={handleLoginSuccess}
        authError={authStatus === "verificationFailed" ? authError : null}
        onRetryAuth={handleRetryAuthCheck}
      />
    );
  }

  const visibleNavItems = getVisibleNavItems(currentUser.userType);

  return (
    <AppShell
      topNav={
        <TopNav
          activeView={activeView}
          navItems={visibleNavItems}
          role={currentUser.userType}
          onLogout={handleLogout}
          onSelectView={setActiveView}
        />
      }
    >
      <ViewPanel
        activeView={activeView}
        currentUser={currentUser}
        role={currentUser.userType}
        onSelectView={setActiveView}
        onLogout={handleLogout}
      />
    </AppShell>
  );
};

type ViewPanelProps = {
  activeView: ViewId;
  currentUser: AuthUser;
  role: AuthUser["userType"];
  onSelectView: (view: ViewId) => void;
  onLogout: () => void;
};

const ViewPanel = ({ activeView, currentUser, role, onSelectView, onLogout }: ViewPanelProps) => {
  if (activeView === "home") {
    return <HomeMock role={role} onSelectView={onSelectView} />;
  }

  if (activeView === "analysis") {
    return <AnalysisWorkspace onLogout={onLogout} />;
  }

  if (activeView === "settings") {
    return <SettingsPlaceholder currentUser={currentUser} />;
  }

  if (activeView === "system") {
    return <AdminSystemPlaceholder />;
  }

  if (activeView === "model") {
    return <AdminModelPlaceholder />;
  }

  return <AnalysisWorkspace onLogout={onLogout} />;
};
