import { Fragment, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { NavigationGuard } from "@/components/layout/NavigationGuard";
import { TooltipProvider } from "@/components/ui/tooltip";
import { TopNav } from "@/features/navigation/TopNav";
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
  const settingsPath = location.pathname === paths.settings ? location.pathname + location.search : paths.settings;
  return (
    // 계정·역할이 바뀌면 아래 전체를 다시 마운트한다(이전 계정 화면 상태·연결 정리). 알림은 서버 알림 API(NotificationsBell)만 쓴다.
    <Fragment key={`${session.user!.id}:${session.user!.userType}`}>
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
    </Fragment>
  );
};
