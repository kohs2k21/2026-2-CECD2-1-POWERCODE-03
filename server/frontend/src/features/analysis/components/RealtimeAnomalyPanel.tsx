import { useRealtimeAnomalies } from "../hooks/useRealtimeAnomalies";
import type { RealtimeStreamStatus } from "../hooks/useRealtimeAnomalies";
import type { RealtimeAnomalyEvent } from "../../../types/realtime";

const statusLabels: Record<RealtimeStreamStatus, string> = {
  idle: "대기 중",
  connecting: "연결 중",
  open: "연결됨",
  retrying: "재연결 대기",
  closed: "연결 종료",
  error: "오류",
};

const displayOptional = (value: string | undefined): string =>
  value === undefined || value === "" ? "미제공" : value;

const EventCard = ({ event }: { event: RealtimeAnomalyEvent }) => (
  <article className="realtime-anomaly-event">
    <header className="realtime-anomaly-event__header">
      <div>
        <strong>{event.severity}</strong>
        <span>위험도 {event.riskLevel} · {event.riskScore}</span>
      </div>
      <code>{event.eventId}</code>
    </header>
    <dl className="realtime-anomaly-event__fields">
      <div>
        <dt>responseCode</dt>
        <dd>{event.responseCode}</dd>
      </div>
      <div>
        <dt>anomalyScore</dt>
        <dd>{event.anomalyScore}</dd>
      </div>
      <div>
        <dt>processTimeMs</dt>
        <dd>{event.processTimeMs}</dd>
      </div>
      <div>
        <dt>detectedAt</dt>
        <dd>{displayOptional(event.detectedAt)}</dd>
      </div>
      <div>
        <dt>processName</dt>
        <dd>{displayOptional(event.processName)}</dd>
      </div>
      <div>
        <dt>channelName</dt>
        <dd>{displayOptional(event.channelName)}</dd>
      </div>
      <div>
        <dt>transactionId</dt>
        <dd>{displayOptional(event.transactionId)}</dd>
      </div>
      <div>
        <dt>status</dt>
        <dd>{displayOptional(event.status)}</dd>
      </div>
    </dl>
  </article>
);

export const RealtimeAnomalyPanel = () => {
  const { error, events, invalidCount, status } = useRealtimeAnomalies();

  return (
    <section className="realtime-anomaly-panel" aria-labelledby="realtime-anomaly-title">
      <header className="realtime-anomaly-panel__header">
        <div>
          <p className="realtime-anomaly-panel__eyebrow">REAL API</p>
          <h2 id="realtime-anomaly-title">실시간 anomaly 이벤트</h2>
          <p>
            백엔드 ingest 이벤트의 계약 필드만 표시합니다. 서버가 제공하지 않은 상세 분석은
            생성하지 않습니다.
          </p>
        </div>
        <span className={`realtime-anomaly-status realtime-anomaly-status--${status}`}>
          {statusLabels[status]}
        </span>
      </header>

      {error ? <p className="realtime-anomaly-panel__message">{error}</p> : null}
      {invalidCount > 0 ? (
        <p className="realtime-anomaly-panel__message">
          계약 검증에 실패한 이벤트 {invalidCount}건은 표시하지 않았습니다.
        </p>
      ) : null}

      {events.length > 0 ? (
        <div className="realtime-anomaly-events">
          {events.map((event) => (
            <EventCard key={event.eventId} event={event} />
          ))}
        </div>
      ) : (
        <p className="realtime-anomaly-panel__empty">
          수신된 유효한 실시간 이벤트가 없습니다. 연결 상태와 백엔드 계약을 확인해 주세요.
        </p>
      )}
    </section>
  );
};
