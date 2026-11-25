import type { NotificationItem, NotificationList } from "./notificationsApi";

export const NOTIFICATION_REFRESH_MS = 10_000;
export const NOTIFICATION_RESUME_MS = 250;
export type NotificationConnection = "connecting" | "connected" | "reconnecting" | "offline" | "paused";
export type NotificationBanner = { riskRaised: boolean };
export type NotificationLiveState = {
  connection: NotificationConnection;
  lastCheckedAt: number | null;
  refreshing: boolean;
  refreshFailed: boolean;
  banner: NotificationBanner | null;
};
export const initialNotificationLiveState = (): NotificationLiveState => ({
  connection: "connecting", lastCheckedAt: null, refreshing: false, refreshFailed: false, banner: null,
});

type Subscription = {
  after: string;
  onEvent(event: unknown, id: string): void;
  onOpen(): void;
  onDisconnect(error: string | null): void;
};
type TimerHandle = ReturnType<typeof setTimeout> | number;
type Options = {
  after: string;
  read(signal: AbortSignal): Promise<NotificationList>;
  invalidate(): void;
  subscribe(subscriber: Subscription): () => void;
  publish(state: NotificationLiveState): void;
  current(): boolean;
  visible(): boolean;
  online(): boolean;
  now?: () => number;
  setTimer?: (call: () => void, delay: number) => TimerHandle;
  clearTimer?: (timer: TimerHandle) => void;
};

type Boundary = { seq: bigint; jobAt: string | null; settings: string };
const boundaryOf = (snapshot: NotificationList): Boundary => ({
  seq: BigInt(snapshot.latestSeq), jobAt: snapshot.latestJobAt, settings: JSON.stringify(snapshot.settings),
});

/** 초기 이력·설정 변경·근거 추가는 새 알림으로 표시하지 않는다. 개수 대신 서버 알림 경계를 비교한다. */
export const isNewNotification = (item: NotificationItem, previous: Boundary) => {
  if (!item.unread) return false;
  if (item.type === "job") return previous.jobAt === null || item.notifiedAt > previous.jobAt;
  if (BigInt(item.seq) <= previous.seq) return false;
  if (item.type === "protocol") return item.protocol.resolution === null;
  return item.reason === "opened" || item.reason === "risk_raised";
};

/** 기존 SSE를 재사용하고 초기·복귀·이벤트 때만 조회한다. 숨김·오프라인·이전 계정의 응답은 채택하지 않는다. */
export function createNotificationMonitor(options: Options) {
  const now = options.now ?? Date.now;
  const setTimer: NonNullable<Options["setTimer"]> = options.setTimer ?? ((call, delay) => setTimeout(call, delay));
  const clearTimer: NonNullable<Options["clearTimer"]> = options.clearTimer ?? ((handle) => clearTimeout(handle));
  let state = initialNotificationLiveState();
  let stopped = false;
  let cursor = options.after;
  let boundary: Boundary | null = null;
  let unsubscribe: (() => void) | undefined;
  let streamGeneration = 0;
  let hasOpened = false;
  let timer: TimerHandle | undefined;
  let due = Infinity;
  let request: AbortController | null = null;
  let pending = false;
  const valid = () => !stopped && options.current();
  const active = () => valid() && options.online() && options.visible();
  const update = (patch: Partial<NotificationLiveState>) => {
    if (!valid()) return;
    state = { ...state, ...patch };
    options.publish(state);
  };
  const cancelTimer = () => {
    if (timer !== undefined) clearTimer(timer);
    timer = undefined;
    due = Infinity;
  };
  const cancelRead = () => {
    const old = request;
    request = null;
    old?.abort();
  };
  const schedule = (delay: number) => {
    if (!active() || (timer !== undefined && due <= now() + delay)) return;
    cancelTimer();
    due = now() + delay;
    timer = setTimer(() => {
      timer = undefined;
      due = Infinity;
      refresh();
    }, delay);
  };
  const refresh = () => {
    if (!active()) { pending = true; return; }
    cancelTimer();
    if (request) { pending = true; return; }
    pending = false;
    const controller = new AbortController();
    request = controller;
    update({ refreshing: true });
    const accepted = () => active() && request === controller && !controller.signal.aborted;
    void Promise.resolve().then(() => {
      if (!accepted()) return null;
      return options.read(controller.signal);
    }).then((snapshot) => {
      if (!snapshot || !accepted()) return;
      const next = boundaryOf(snapshot);
      // 이전 응답으로 경계가 되돌아가 같은 알림이 다시 표시되지 않게 한다.
      if (boundary && next.seq < boundary.seq) throw new Error("notification_snapshot_behind");
      const policyChanged = boundary !== null && next.settings !== boundary.settings;
      const candidates = boundary && !policyChanged
        ? snapshot.items.filter((item) => isNewNotification(item, boundary!)) : [];
      boundary = next;
      const banner = candidates.length ? {
        riskRaised: candidates.some((item) => item.type !== "job" && item.type !== "protocol" && item.reason === "risk_raised"),
      } : policyChanged || snapshot.unreadCount === 0 ? null : state.banner;
      update({ lastCheckedAt: now(), refreshFailed: false, banner });
      options.invalidate();
    }).catch(() => {
      // 외부 오류 원문·접속 주소를 표시하지 않는다. 실패해도 마지막 성공 시각은 유지한다.
      if (accepted()) update({ refreshFailed: true });
    }).finally(() => {
      if (request !== controller) return;
      request = null;
      update({ refreshing: false });
      if (pending && active()) schedule(NOTIFICATION_RESUME_MS);
    });
  };
  const detach = () => {
    streamGeneration += 1;
    const stop = unsubscribe;
    unsubscribe = undefined;
    stop?.();
  };
  const attach = () => {
    if (!active() || unsubscribe) return;
    update({ connection: "connecting" });
    const generation = ++streamGeneration;
    const live = () => active() && generation === streamGeneration;
    unsubscribe = options.subscribe({
      after: cursor,
      onEvent: (_event, id) => {
        if (!live()) return;
        if (BigInt(id) > BigInt(cursor)) cursor = id;
        schedule(NOTIFICATION_REFRESH_MS);
      },
      onOpen: () => {
        if (!live()) return;
        update({ connection: "connected" });
        if (hasOpened) schedule(NOTIFICATION_RESUME_MS);
        hasOpened = true;
      },
      onDisconnect: () => {
        if (live()) update({ connection: "reconnecting" });
      },
    });
  };
  const resume = () => {
    if (!valid()) return;
    if (!options.online() || !options.visible()) {
      cancelTimer();
      cancelRead();
      detach();
      pending = true;
      update({ connection: options.online() ? "paused" : "offline", refreshing: false });
      return;
    }
    attach();
    schedule(NOTIFICATION_RESUME_MS);
  };
  return {
    start() { resume(); },
    resume,
    refresh,
    /** 설정 변경 후 새 목록을 조용히 기준으로 고정하고, 과거 이력을 다시 알리지 않는다. */
    reset() {
      boundary = null;
      cancelRead();
      update({ banner: null, refreshing: false });
      refresh();
    },
    dismiss() { update({ banner: null }); },
    stop() {
      stopped = true;
      cancelTimer();
      cancelRead();
      detach();
    },
  };
}

export type NotificationMonitor = ReturnType<typeof createNotificationMonitor>;
