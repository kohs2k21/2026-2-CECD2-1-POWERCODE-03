import { Outlet, useLocation } from "react-router-dom";
import { AppShell } from "../components/layout/AppShell";
import { TopNav } from "../features/navigation/TopNav";
import { useAuthSession } from "../services/auth/AuthSessionProvider";
import { getVisibleNavItems } from "./router";
import type { ViewId } from "../types/app";
import { useState } from "react";
import { NavigationGuard } from "../components/layout/NavigationGuard";
import { useDraftStore } from "../stores/draftStore";

export const AppLayout = () => {
  const session = useAuthSession();
  const location = useLocation();
  const [logoutRequested, setLogoutRequested] = useState(false);
  const requestLogout = () => {
    if (
      Object.values(useDraftStore.getState().drafts).some(
        (draft) => draft.dirty,
      )
    )
      setLogoutRequested(true);
    else session.logout();
  };
  const activeView: ViewId =
    location.pathname === "/settings"
      ? "settings"
      : location.pathname.startsWith("/analysis")
        ? "analysis"
        : location.pathname.startsWith("/detection")
          ? "model"
          : "home";
  return (
    <AppShell
      topNav={
        <TopNav
          activeView={activeView}
          role={session.user!.userType}
          navItems={getVisibleNavItems(session.user!.userType)}
          settingsPath={
            location.pathname === "/settings"
              ? location.pathname + location.search
              : "/settings?returnTo=" +
                encodeURIComponent(location.pathname + location.search)
          }
          onLogout={requestLogout}
        />
      }
    >
      <Outlet context={{ requestLogout }} />
      <NavigationGuard
        logoutRequested={logoutRequested}
        onCancelLogout={() => setLogoutRequested(false)}
        onLogout={session.logout}
      />
    </AppShell>
  );
};
