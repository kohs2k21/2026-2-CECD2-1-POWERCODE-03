import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Button } from "../../../../components/ui/button";
import type { ViewId } from "../../../../types/app";
import { useSharedRealtimeAnomalies } from "../../../analysis/RealtimeAnomalyProvider";
import { useOperationsQuery } from "../../data/useOperationsQuery";
import { useObservationFreshness } from "../OperationsObservation";

const LiveFeedWidget = () => {
  const realtime = useSharedRealtimeAnomalies();
  const labels = { idle: "이벤트 연결 대기", connecting: "이벤트 연결 중", open: "이벤트 연결됨", retrying: "이벤트 재연결 중", closed: "이벤트 연결 종료", error: "이벤트 연결 오류" };
  return <div className="live-feed-widget operations-live-feed">
    <span role="status">{labels[realtime.status]}</span>
    {realtime.error && <p>{realtime.error}</p>}
    {(realtime.status === "closed" || realtime.status === "error") && <Button variant="ghost" size="sm" onClick={realtime.retry}>다시 연결</Button>}
    <div className="operations-live-feed__items">{[...realtime.events].reverse().map(event => <Link key={event.eventId} to={`/analysis?event=${encodeURIComponent(event.eventId)}`} className="live-feed-item">
      <strong>{event.processName ?? event.eventId}</strong><span>{event.detectedAt ?? "감지 시각 확인 불가"}</span>
      <p>응답 {event.responseCode} · 위험 점수 {event.riskScore}</p>
    </Link>)}</div>
    {realtime.events.length === 0 && <p>현재 세션에서 수신한 이상 이벤트가 없습니다.</p>}
  </div>;
};

const ObservedMetricWidget = ({ fields }: { fields: readonly ("collectorEps" | "cpuPercent" | "memoryPercent" | "diskPercent" | "alertDeliverySuccessPercent")[] }) => {
  const query = useOperationsQuery();
  const freshness = useObservationFreshness(query.data);
  const labels = { collectorEps: "수집 속도 EPS", cpuPercent: "CPU 사용률", memoryPercent: "메모리 사용률", diskPercent: "저장소 사용률", alertDeliverySuccessPercent: "알림 발송 성공률" };
  return <div className="operations-widget-metrics"><dl>{fields.map(field => <div key={field}><dt>{labels[field]}</dt><dd>{query.data?.[field] === undefined ? "— 확인 불가" : `${query.data[field]}${field.endsWith("Percent") ? "%" : ""}`}</dd></div>)}</dl>
    <p>{query.isError ? "관측 조회 실패" : freshness === "stale" ? "오래된 관측값" : freshness === "unknown" ? "운영 관측 확인 불가" : "최근 관측"} · {query.data?.observedAt ?? "—"}</p>
  </div>;
};
const AlertDeliveryWidget = () => <ObservedMetricWidget fields={["alertDeliverySuccessPercent"]} />;
const CollectorStatusWidget = () => <ObservedMetricWidget fields={["collectorEps"]} />;
const SystemStatusWidget = () => <ObservedMetricWidget fields={["cpuPercent", "memoryPercent", "diskPercent"]} />;
const RecentAlertsWidget = () => <p className="operations-widget-metrics">최근 알림 발송 기록: — 확인 불가</p>;

const UnavailableStatistics = () => <p className="operations-widget-metrics">통계: — 확인 불가</p>;

export const renderFullWidget = (
  widgetId: string,
  _onSelectView?: (view: ViewId) => void,
): ReactNode | null => {
  const chartById: Record<string, ReactNode> = {
    "system-status": <SystemStatusWidget />,
    "severity-trend": <UnavailableStatistics />,
    "response-code-change": <UnavailableStatistics />,
    "major-risk-events": <UnavailableStatistics />,
    "recent-anomaly-logs": <LiveFeedWidget />,
    "recent-alerts": <RecentAlertsWidget />,
    "channel-risk-rank": <UnavailableStatistics />,
    "alert-delivery": <AlertDeliveryWidget />,
    "collector-status": <CollectorStatusWidget />,
  };

  return chartById[widgetId] ?? null;
};
