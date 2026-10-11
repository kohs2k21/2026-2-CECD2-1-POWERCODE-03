import { Link } from "react-router-dom";
import { Button } from "../../components/ui/button";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "../../components/ui/feedback";
import { useAuthSession } from "../../services/auth/AuthSessionProvider";
import { useSharedRealtimeAnomalies } from "../analysis/RealtimeAnomalyProvider";
import { jobStateLabels } from "../detection/components/DetectionQueryBoundary";
import { useNotificationJobs } from "./NotificationsProvider";
import {
  operatingEventHref,
  pendingTask,
  taskNotifications,
  type NotificationFilter,
} from "./notificationPresentation";

const streamLabels = {
  idle: "연결 대기",
  connecting: "연결 중",
  open: "연결됨",
  retrying: "재연결 중",
  closed: "연결 끊김",
  error: "연결 오류",
} as const;
const severityLabels = {
  Info: "정보",
  Warning: "주의",
  Critical: "심각",
} as const;
const observedTime = (value: string | null | undefined) =>
  value || "시각 미확인";

export const NotificationPanels = ({
  filter = "all",
  limit,
  onNavigate,
}: {
  filter?: NotificationFilter;
  limit?: number;
  onNavigate?: () => void;
}) => {
  const { user } = useAuthSession();
  const realtime = useSharedRealtimeAnomalies();
  const query = useNotificationJobs();
  const events = [...realtime.events].reverse().slice(0, limit);
  const tasks = query.data ? taskNotifications(query.data).slice(0, limit) : [];
  return (
    <div className="notification-panels">
      {filter !== "tasks" && (
        <section className="notification-section" aria-label="운영 알림">
          <h2>운영 알림</h2>
          <p className="notification-meta" role="status">
            실시간 연결: {streamLabels[realtime.status]}
          </p>
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
            <ErrorState
              title="실시간 연결이 끊겼습니다"
              onRetry={realtime.retry}
              retryLabel="다시 연결"
            />
          )}
          {(realtime.status === "connecting" ||
            realtime.status === "retrying") && (
            <LoadingState>운영 알림에 연결하는 중...</LoadingState>
          )}
          {realtime.invalidCount > 0 && (
            <p className="notification-error">
              형식을 확인할 수 없는 이벤트 {realtime.invalidCount}건
            </p>
          )}
          {events.length ? (
            <ul className="notification-list">
              {events.map((event) => (
                <li key={event.eventId}>
                  <Link to={operatingEventHref(event)} onClick={onNavigate}>
                    <span className="notification-row-title">
                      {severityLabels[event.severity]} ·{" "}
                      {event.processName || "프로세스 미확인"}
                    </span>
                    <span>응답 코드 {event.responseCode}</span>
                    <span className="notification-meta">
                      {observedTime(event.detectedAt)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : realtime.status === "open" ? (
            <EmptyState>수신한 운영 알림이 없습니다.</EmptyState>
          ) : (
            <EmptyState>
              수신한 운영 알림이 없습니다. 연결 상태를 확인해 주세요.
            </EmptyState>
          )}
        </section>
      )}
      {user?.userType === "admin" && filter !== "operations" && (
        <section className="notification-section" aria-label="진행 작업">
          <div className="notification-section-heading">
            <h2>진행 작업</h2>
            <Button
              variant="outline"
              size="sm"
              disabled={query.isFetching}
              onClick={() => void query.refetch()}
            >
              다시 조회
            </Button>
          </div>
          {query.isPending && (
            <LoadingState>학습·평가 작업을 불러오는 중...</LoadingState>
          )}
          {query.isError && (
            <ErrorState
              title="작업 상태를 조회하지 못했습니다"
              onRetry={() => void query.refetch()}
            >
              {query.error.message}
              {query.data && <p>표시된 작업은 마지막 조회 결과입니다.</p>}
            </ErrorState>
          )}
          {query.data && (
            <>
              <p className="notification-meta" role="status">
                마지막 조회:{" "}
                {new Date(query.dataUpdatedAt).toLocaleString("ko-KR")}
                {query.isFetching ? " · 갱신 중" : ""}
              </p>
              {tasks.length ? (
                <ul className="notification-list">
                  {tasks.map((task) => (
                    <li key={`${task.kind}:${task.id}`}>
                      <Link to={task.href} onClick={onNavigate}>
                        <span className="notification-row-title">
                          {task.kind === "training" ? "학습" : "평가"} ·{" "}
                          {task.name}
                        </span>
                        <span>{jobStateLabels[task.state]}</span>
                        {task.progress !== null ? (
                          <span className="notification-progress">
                            <progress
                              value={task.progress}
                              max={100}
                              aria-label={`${task.name} 진행률`}
                            />
                            {task.progress}%
                          </span>
                        ) : pendingTask(task.state) ? (
                          <span className="notification-meta">
                            진행률 미확인
                          </span>
                        ) : null}
                        <span className="notification-meta">
                          요청: {observedTime(task.requestedAt)}
                        </span>
                        {task.failure && (
                          <span className="notification-error">
                            {task.failure}
                          </span>
                        )}
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState>조회된 학습·평가 작업이 없습니다.</EmptyState>
              )}
            </>
          )}
        </section>
      )}
    </div>
  );
};
