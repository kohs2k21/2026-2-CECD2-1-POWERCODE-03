import { Fragment, useState } from "react";
import { useLocation, useOutlet } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { PageFade } from "@/components/layout/PageFade";
import { getStored, setStored, storageKeys } from "@/lib/storage";
import { NavigationGuard } from "@/components/layout/NavigationGuard";
import { TooltipProvider } from "@/components/ui/tooltip";
import { TopNav } from "@/features/navigation/TopNav";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { useDraftStore } from "@/stores/draftStore";
import { activeTopNav, paths } from "./routes";
import { NotificationLiveProvider } from "@/features/notifications/NotificationLiveProvider";
import { NotificationLiveFeedback } from "@/features/notifications/NotificationLiveFeedback";

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
  // 넓게 보기(ui.md "넓게 보기"): 모든 화면 공통 한 값(브라우저 저장). 켜면 본문 1600px 폭 제한을 풀어 좌우를 채운다.
  const [wide, setWide] = useState<boolean>(() => getStored(storageKeys.layoutWide, false));
  const toggleWide = () => {
    setWide(!wide);
    setStored(storageKeys.layoutWide, !wide);
  };
  const outlet = useOutlet({ requestLogout } satisfies AppOutletContext);
  // 화면 전환: 주요 화면(상단 메뉴 단위)이 바뀔 때. 그 안의 경로 변경은 각 화면이 본문에만 건다(사이드바는 그대로).
  const section = activeTopNav(location.pathname) ?? location.pathname;
  return (
    // 계정·역할이 바뀌면 아래 전체를 다시 마운트한다(이전 계정 화면 상태·연결 정리). 알림은 서버 알림 API와 공통 연결 소유자만 쓴다.
    <Fragment key={`${session.user!.id}:${session.user!.userType}`}>
      <NotificationLiveProvider>
        <TooltipProvider>
          <AppShell
            wide={wide}
            topNav={
              <TopNav
                pathname={location.pathname}
                role={session.user!.userType}
                settingsPath={settingsPath}
                onLogout={requestLogout}
                wide={wide}
                onToggleWide={toggleWide}
              />
            }
          >
            <NotificationLiveFeedback />
            <PageFade transitionKey={section}>{outlet}</PageFade>
            <NavigationGuard
              logoutRequested={logoutRequested}
              onCancelLogout={() => setLogoutRequested(false)}
              onLogout={session.logout}
            />
          </AppShell>
        </TooltipProvider>
      </NotificationLiveProvider>
    </Fragment>
  );
};
