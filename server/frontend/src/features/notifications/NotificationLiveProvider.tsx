import { useCallback, useEffect, useMemo, useState, type PropsWithChildren } from "react";
import { useLocation } from "react-router-dom";
import { NotificationLiveContext } from "./NotificationLiveContext";
import { useNotificationLiveUpdates, useRefreshOnJobFinish, useRefreshOnNavigate, useUnreadCount } from "./useNotifications";

const readEnvironment = () => ({
  online: typeof navigator === "undefined" || navigator.onLine !== false,
  visible: typeof document === "undefined" || document.visibilityState !== "hidden",
});

/** 앱 하나에 알림 연결 소유자 하나만 둔다. 종 버튼·알림 페이지가 같은 상태를 읽는다. */
export const NotificationLiveProvider = ({ children }: PropsWithChildren) => {
  const location = useLocation();
  const unread = useUnreadCount();
  const live = useNotificationLiveUpdates(unread.data?.latestSeq);
  const ready = unread.data !== undefined;
  const [environment, setEnvironment] = useState(readEnvironment);
  const refresh = useCallback(() => {
    if (ready) live.refresh();
    else void unread.refetch();
  }, [ready, live.refresh, unread.refetch]);
  // 최초 배지 조회 실패 시에도 화면·네트워크 복귀와 수동 확인으로 연결을 시작할 수 있다.
  useEffect(() => {
    if (ready) return;
    setEnvironment(readEnvironment());
    const resume = () => {
      const next = readEnvironment();
      setEnvironment(next);
      if (next.online && next.visible) void unread.refetch();
    };
    window.addEventListener("online", resume);
    window.addEventListener("offline", resume);
    window.addEventListener("focus", resume);
    window.addEventListener("pageshow", resume);
    if (typeof document !== "undefined") document.addEventListener?.("visibilitychange", resume);
    return () => {
      window.removeEventListener("online", resume);
      window.removeEventListener("offline", resume);
      window.removeEventListener("focus", resume);
      window.removeEventListener("pageshow", resume);
      if (typeof document !== "undefined") document.removeEventListener?.("visibilitychange", resume);
    };
  }, [ready, unread.refetch]);
  useRefreshOnJobFinish(refresh);
  useRefreshOnNavigate(location.key, refresh);
  const state = ready ? live.state : {
    ...live.state,
    connection: !environment.online ? "offline" as const : !environment.visible ? "paused" as const : "connecting" as const,
    refreshing: unread.isFetching,
    refreshFailed: unread.isError,
  };
  const value = useMemo(() => ({ state, refresh, reset: live.reset, dismiss: live.dismiss }), [state, refresh, live.reset, live.dismiss]);
  return <NotificationLiveContext.Provider value={value}>{children}</NotificationLiveContext.Provider>;
};
