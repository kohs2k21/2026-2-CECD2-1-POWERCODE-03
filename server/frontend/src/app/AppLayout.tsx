import { useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { NavigationGuard } from "@/components/layout/NavigationGuard";
import { TooltipProvider } from "@/components/ui/tooltip";
import { TopNav } from "@/features/navigation/TopNav";
import { RealtimeAnomalyProvider } from "@/features/analysis/RealtimeAnomalyProvider";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { useDraftStore } from "@/stores/draftStore";
import { paths } from "./routes";

export type AppOutletContext = { requestLogout: () => void };

export const AppLayout = () => {
  const session = useAuthSession();
  const location = useLocation();
  const [logoutRequested, setLogoutRequested] = useState(false);
  const requestLogout = () => {
    if (Object.values(useDraftStore.getState().drafts).some((draft) => draft.dirty)) setLogoutRequested(true);
    else session.logout();
  };
  const settingsPath =
    location.pathname === paths.settings
      ? location.pathname + location.search
      : `${paths.settings}?returnTo=${encodeURIComponent(location.pathname + location.search)}`;
  return (
    <RealtimeAnomalyProvider key={`${session.user!.id}:${session.user!.userType}`}>
      <TooltipProvider>
        <AppShell
          topNav={
            <TopNav
              pathname={location.pathname}
              role={session.user!.userType}
              settingsPath={settingsPath}
              onLogout={requestLogout}
            />
          }
        >
          <Outlet context={{ requestLogout } satisfies AppOutletContext} />
          <NavigationGuard
            logoutRequested={logoutRequested}
            onCancelLogout={() => setLogoutRequested(false)}
            onLogout={session.logout}
          />
        </AppShell>
      </TooltipProvider>
    </RealtimeAnomalyProvider>
  );
};
