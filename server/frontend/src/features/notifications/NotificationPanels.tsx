import { Link } from "react-router-dom";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { paths } from "@/app/routes";
import type { RealtimeAnomalyEvent } from "@/types/realtime";
import { useSharedRealtimeAnomalies } from "@/features/analysis/RealtimeAnomalyProvider";

const streamLabels = {
  idle: "연결 대기",
  connecting: "연결 중",
  open: "연결됨",
  retrying: "재연결 중",
  closed: "연결 끊김",
  error: "연결 오류",
} as const;
const severityLabels = { Info: "정보", Warning: "주의", Critical: "심각" } as const;
const observedTime = (value: string | null | undefined) => value || "시각 미확인";

export const operatingEventHref = (event: RealtimeAnomalyEvent) =>
  `${paths.analysis}?${new URLSearchParams({ category: event.severity, event: event.eventId }).toString()}`;

/**
 * 운영 알림: 앱 공용 SSE 연결(RealtimeAnomalyProvider) 하나에서 최근 이벤트를 보여 준다. 이벤트마다 토스트 없음(ui.md).
 * 읽음 상태·필터·작업 알림은 하위 Issue(알림·설정 + 사용자별 저장 API).
 */
export const NotificationPanels = ({ limit, onNavigate }: { limit?: number; onNavigate?: () => void }) => {
  const realtime = useSharedRealtimeAnomalies();
  const events = [...realtime.events].reverse().slice(0, limit);
  return (
    <section className="grid gap-3" aria-label="운영 알림">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-section-title">운영 알림</h3>
        <p className="text-xs text-muted-foreground" role="status">
          실시간 연결: {streamLabels[realtime.status]}
        </p>
      </div>
      {realtime.error && (
        <ErrorState
          title="운영 알림 연결을 확인해 주세요"
          onRetry={realtime.requiresLogout ? undefined : realtime.retry}
          retryLabel="다시 연결"
        >
          {realtime.error}
        </ErrorState>
      )}
      {!realtime.error && realtime.status === "closed" && (
        <ErrorState title="실시간 연결이 끊겼습니다" onRetry={realtime.retry} retryLabel="다시 연결" />
      )}
      {(realtime.status === "connecting" || realtime.status === "retrying") && (
        <LoadingState className="py-2">운영 알림에 연결하는 중...</LoadingState>
      )}
      {realtime.invalidCount > 0 && (
        <p className="text-xs text-destructive-text">형식을 확인할 수 없는 이벤트 {realtime.invalidCount}건</p>
      )}
      {events.length ? (
        <ul className="grid gap-1">
          {events.map((event) => (
            <li key={event.eventId}>
              <Link
                to={operatingEventHref(event)}
                onClick={onNavigate}
                className="grid gap-0.5 rounded-md px-2 py-2 text-sm transition-colors outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <span className="font-medium">
                  {severityLabels[event.severity]} · {event.processName || "프로세스 미확인"}
                </span>
                <span className="text-muted-foreground">응답 코드 {event.responseCode}</span>
                <span className="text-xs text-muted-foreground">{observedTime(event.detectedAt)}</span>
              </Link>
            </li>
          ))}
        </ul>
      ) : realtime.status === "open" ? (
        <EmptyState className="py-4">수신한 운영 알림이 없습니다.</EmptyState>
      ) : (
        <EmptyState className="py-4">수신한 운영 알림이 없습니다. 연결 상태를 확인해 주세요.</EmptyState>
      )}
    </section>
  );
};
