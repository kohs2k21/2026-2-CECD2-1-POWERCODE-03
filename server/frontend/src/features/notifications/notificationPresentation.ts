import type { UserRole } from "../../types/app";
import type { RealtimeAnomalyEvent } from "../../types/realtime";
import type { DetectionData, JobState } from "../detection/data/types";
import { taskDetailHref } from "../detection/data/actionReceipt";

export const notificationFilters = [
  { id: "all", label: "전체" },
  { id: "operations", label: "운영" },
  { id: "tasks", label: "작업" },
] as const;
export type NotificationFilter = (typeof notificationFilters)[number]["id"];
export const resolveNotificationFilter = (
  requested: string | null,
  role: UserRole,
): NotificationFilter =>
  requested === "tasks" && role === "admin"
    ? "tasks"
    : requested === "operations" || (requested === "tasks" && role === "user")
      ? "operations"
      : "all";

export const notificationFilterHref = (
  params: URLSearchParams,
  filter: NotificationFilter,
) => {
  const next = new URLSearchParams(params);
  next.set("filter", filter);
  return `?${next.toString()}`;
};

export const operatingEventHref = (event: RealtimeAnomalyEvent) =>
  "/analysis?" +
  new URLSearchParams({
    category: event.severity,
    event: event.eventId,
  }).toString();

export type TaskNotification = {
  id: string;
  kind: "training" | "evaluation";
  name: string;
  state: JobState;
  requestedAt: string | null;
  progress: number | null;
  failure: string | null;
  href: string;
};

export const pendingTask = (state: JobState) =>
  state === "accepted" || state === "queued" || state === "running";

export const measuredProgress = (progress: unknown): number | null =>
  typeof progress === "number" &&
  Number.isFinite(progress) &&
  progress >= 0 &&
  progress <= 100
    ? progress
    : null;

export const taskNotifications = (data: DetectionData): TaskNotification[] =>
  [
    ...data.trainingJobs.map((job) => ({
      id: job.id,
      kind: "training" as const,
      name: job.name,
      state: job.state,
      requestedAt: job.requestedAt,
      progress: measuredProgress(job.progress),
      failure: job.failure,
      href: taskDetailHref({ type: "training", id: job.id }),
    })),
    ...data.evaluations.map((result) => ({
      id: result.id,
      kind: "evaluation" as const,
      name:
        data.versions.find((item) => item.id === result.candidateId)?.name ??
        result.candidateId,
      state: result.state,
      requestedAt: result.requestedAt,
      progress: null,
      failure: result.failure,
      href: taskDetailHref({
        type: "evaluation",
        id: result.id,
        candidateId: result.candidateId,
        resultId: result.id,
      }),
    })),
  ].sort((left, right) => {
    const pendingOrder =
      Number(pendingTask(right.state)) - Number(pendingTask(left.state));
    if (pendingOrder) return pendingOrder;
    const time = (value: string | null) => {
      const parsed = value ? Date.parse(value) : NaN;
      return Number.isFinite(parsed) ? parsed : -Infinity;
    };
    return (
      time(right.requestedAt) - time(left.requestedAt) ||
      left.id.localeCompare(right.id)
    );
  });
