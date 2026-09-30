import type { RealtimeAnomalyEvent } from "../../../types/realtime";
import type { AnalysisCategory } from "../types";

export type AnalysisInboxItem = {
  log: {
    logId: string;
    severity: RealtimeAnomalyEvent["severity"];
    status?: string;
    detectedAt?: string;
    processName?: string;
    channelName?: string;
    transactionId?: string;
    responseCode: string;
    anomalyScore: number;
    processTimeMs: number;
    riskScore: number;
    riskLevel: RealtimeAnomalyEvent["riskLevel"];
  };
};

export const adaptRealtimeAnomalyEvent = (
  event: RealtimeAnomalyEvent,
): AnalysisInboxItem => ({
  log: {
    logId: event.eventId,
    severity: event.severity,
    status: event.status,
    detectedAt: event.detectedAt,
    processName: event.processName,
    channelName: event.channelName,
    transactionId: event.transactionId,
    responseCode: event.responseCode,
    anomalyScore: event.anomalyScore,
    processTimeMs: event.processTimeMs,
    riskScore: event.riskScore,
    riskLevel: event.riskLevel,
  },
});

export const adaptRealtimeAnomalyEvents = (
  events: RealtimeAnomalyEvent[],
): AnalysisInboxItem[] => events.map(adaptRealtimeAnomalyEvent);

export const getRealtimeDetailByEventId = (
  events: RealtimeAnomalyEvent[],
  eventId: string | null,
): RealtimeAnomalyEvent | null =>
  eventId
    ? events.find((event) => event.eventId === eventId) ?? null
    : null;

export const matchesRealtimeCategory = (
  item: AnalysisInboxItem,
  category: AnalysisCategory,
): boolean => {
  if (category === "All") {
    return true;
  }

  if (category === "Open" || category === "Resolved") {
    return false;
  }

  return item.log.severity === category;
};

export const matchesRealtimeQuery = (
  item: AnalysisInboxItem,
  query: string,
): boolean => {
  const searchable = [
    item.log.logId,
    item.log.severity,
    item.log.status,
    item.log.detectedAt,
    item.log.processName,
    item.log.channelName,
    item.log.transactionId,
    item.log.responseCode,
    item.log.anomalyScore,
    item.log.processTimeMs,
    item.log.riskScore,
    item.log.riskLevel,
  ]
    .filter((value) => value !== undefined)
    .join(" ")
    .toLowerCase();

  return searchable.includes(query.trim().toLowerCase());
};

export const getRealtimeCategoryCounts = (
  items: AnalysisInboxItem[],
): Record<AnalysisCategory, number> =>
  items.reduce<Record<AnalysisCategory, number>>(
    (counts, item) => {
      if (matchesRealtimeCategory(item, "All")) {
        counts.All += 1;
      }
      if (matchesRealtimeCategory(item, "Critical")) {
        counts.Critical += 1;
      }
      if (matchesRealtimeCategory(item, "Warning")) {
        counts.Warning += 1;
      }
      if (matchesRealtimeCategory(item, "Info")) {
        counts.Info += 1;
      }
      if (matchesRealtimeCategory(item, "Open")) {
        counts.Open += 1;
      }
      if (matchesRealtimeCategory(item, "Resolved")) {
        counts.Resolved += 1;
      }
      return counts;
    },
    { All: 0, Critical: 0, Warning: 0, Info: 0, Open: 0, Resolved: 0 },
  );

export const filterRealtimeAnalysisItems = (
  items: AnalysisInboxItem[],
  category: AnalysisCategory,
  query: string,
): AnalysisInboxItem[] =>
  items.filter(
    (item) =>
      matchesRealtimeCategory(item, category) &&
      matchesRealtimeQuery(item, query),
  );

export const getLatestRealtimeDetectionAt = (
  items: AnalysisInboxItem[],
): string | null => {
  let latest: { raw: string; timestamp: number } | null = null;

  for (const item of items) {
    const raw = item.log.detectedAt;
    if (!raw) {
      continue;
    }

    const timestamp = Date.parse(raw);
    if (!Number.isFinite(timestamp)) {
      continue;
    }

    if (!latest || timestamp > latest.timestamp) {
      latest = { raw, timestamp };
    }
  }

  return latest?.raw ?? null;
};
