import { useContext } from "react";
import { Link } from "react-router-dom";
import { IconBell, IconX } from "@tabler/icons-react";
import { paths } from "@/app/routes";
import { Button } from "@/components/ui/button";
import { NotificationLiveContext } from "./NotificationLiveContext";
import type { NotificationLiveState } from "./notificationLive";

export const notificationConnectionText = (state: NotificationLiveState) => {
  if (state.connection === "offline") return "네트워크 연결 끊김";
  if (state.connection === "paused") return "화면 복귀 시 알림을 다시 확인합니다";
  if (state.refreshFailed) return "알림을 새로 확인하지 못했습니다";
  if (state.connection === "reconnecting") return "알림 연결 재시도 중";
  if (state.connection === "connecting") return "알림 연결 확인 중";
  return "알림 연결됨";
};

/** 마지막 확인은 목록 조회가 성공한 시각이다. SSE 연결만 열렸다고 시각을 갱신하지 않는다. */
export const NotificationConnectionStatus = () => {
  const live = useContext(NotificationLiveContext);
  if (!live) return null;
  const { state } = live;
  const checked = state.lastCheckedAt === null ? "확인 이력 없음"
    : `마지막 확인 ${new Date(state.lastCheckedAt).toLocaleTimeString("ko-KR", { hour12: false })}`;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2 text-xs" data-notification-connection={state.connection}>
      <div role="status" className="grid gap-0.5">
        <span>{notificationConnectionText(state)}</span>
        <span className="text-muted-foreground">{checked}</span>
      </div>
      <Button variant="outline" size="sm" disabled={state.refreshing || state.connection === "offline"} onClick={live.refresh}>
        {state.refreshing ? "확인 중…" : "새로 확인"}
      </Button>
    </div>
  );
};

/** 웹 화면 안의 알림이다. 표시·닫기는 서버의 읽음 상태를 바꾸지 않는다. */
export const NotificationLiveFeedback = () => {
  const live = useContext(NotificationLiveContext);
  if (!live) return null;
  const { state } = live;
  const problem = state.connection === "offline" || state.connection === "reconnecting" || state.refreshFailed;
  if (!problem && !state.banner) return null;
  return (
    <div className="mb-4 grid gap-2" data-notification-live-feedback="">
      {problem && <p role="status" className="rounded-md border bg-muted px-3 py-2 text-sm">
        {notificationConnectionText(state)}. 표시된 알림이 최신 상태가 아닐 수 있습니다.
      </p>}
      {state.banner && (
        <div role="status" className="flex flex-wrap items-center gap-3 rounded-lg border bg-accent px-4 py-3" data-new-notification-banner="">
          <IconBell className="size-5 shrink-0" aria-hidden="true" />
          <div className="min-w-0 flex-1 text-sm">
            <strong>새 알림이 있습니다</strong>
            {state.banner.riskRaised && <p>위험도가 상승한 알림이 포함되어 있습니다.</p>}
          </div>
          <Button asChild size="sm" variant="outline"><Link to={`${paths.notifications}?view=unread`} onClick={live.dismiss}>알림 보기</Link></Button>
          <Button size="icon" variant="ghost" aria-label="새 알림 배너 닫기" onClick={live.dismiss}><IconX aria-hidden="true" /></Button>
        </div>
      )}
    </div>
  );
};
