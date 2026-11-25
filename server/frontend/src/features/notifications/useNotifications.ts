import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { accountQueryKey, useAccountQuery } from "@/lib/query";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { getStoredToken } from "@/services/auth/session";
import { DECISION_STREAM_RETRY_MS, subscribeDecisionStream } from "@/services/api/decision-stream-hub";
import { createNotificationMonitor, initialNotificationLiveState, type NotificationMonitor, type NotificationLiveState } from "./notificationLive";
import { NotificationLiveContext } from "./NotificationLiveContext";
import {
  markNotificationsRead,
  readMySettings,
  readNotificationList,
  readUnreadCount,
  saveMySettings,
  type MySettings,
  type NotificationSettings,
  type NotificationView,
  type ReadRequest,
  type StoredLayout,
  type UnreadCount, keepProtocolAlert } from "./notificationsApi";

/** 읽음·이상 유지 응답의 배지 값(미확인 수 조회와 같은 모양). */
const countOf = ({ latestSeq, latestJobAt, unreadCount, unreadByType }: UnreadCount): UnreadCount => ({ latestSeq, latestJobAt, unreadCount, unreadByType });

export const notificationListQueryName = "notifications-list";
export const unreadCountQueryName = "notifications-unread";
export const mySettingsQueryName = "me-settings";
/** 패널 한 쪽 항목 수. */
export const NOTIFICATION_PAGE_SIZE = 20;
/** 새 판정 이벤트가 몰려도 알림 다시 조회는 이 간격에 한 번(이벤트가 있을 때만, 주기 조회 아님). */
export { NOTIFICATION_REFRESH_MS } from "./notificationLive";
export const NOTIFICATION_RETRY_MS = DECISION_STREAM_RETRY_MS;

/** 배지 숫자(서버 미확인 수). 화면 이동·패널 열기·새 판정 이벤트 때 다시 조회한다(주기 조회 없음). */
export const useUnreadCount = () =>
  useAccountQuery({ name: unreadCountQueryName, read: (signal) => readUnreadCount(signal), staleTime: 0 });

/** 패널 한 쪽(cursor null = 첫 쪽). 더 보기는 쪽마다 이 조회를 하나씩 더한다. */
export const useNotificationPage = (view: NotificationView, cursor: string | null, enabled: boolean) =>
  useAccountQuery({
    name: notificationListQueryName,
    params: [view, cursor],
    read: (signal) => readNotificationList({ view, cursor, limit: NOTIFICATION_PAGE_SIZE }, signal),
    enabled,
    staleTime: 0,
  });

export const useMySettings = () => useAccountQuery({ name: mySettingsQueryName, read: (signal) => readMySettings(signal) });

const useInvalidateNotifications = () => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  return () => {
    void cache.invalidateQueries({ queryKey: accountQueryKey(notificationListQueryName, user?.id) });
    void cache.invalidateQueries({ queryKey: accountQueryKey(unreadCountQueryName, user?.id) });
  };
};

/** [이상 유지](관리자). 성공하면 배지 수를 응답값으로 바꾸고 목록을 다시 조회한다. */
export const useKeepProtocolAlert = () => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  const invalidate = useInvalidateNotifications();
  return useMutation({
    mutationFn: keepProtocolAlert,
    onSuccess: (result) => {
      cache.setQueryData<UnreadCount>(accountQueryKey(unreadCountQueryName, user?.id), countOf(result));
      void cache.invalidateQueries({ queryKey: accountQueryKey(notificationListQueryName, user?.id) });
    },
    onError: invalidate,
  });
};

/** 읽음 표시(항목 또는 모두). 성공하면 배지 수를 응답값으로 바꾸고 목록을 다시 조회한다. 실패는 오류 그대로. */
export const useMarkNotificationsRead = () => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  const invalidate = useInvalidateNotifications();
  return useMutation({
    mutationFn: async (body: ReadRequest) => {
      const token = getStoredToken();
      return { result: await markNotificationsRead(body), token };
    },
    onSuccess: async ({ result, token }) => {
      if (token !== getStoredToken()) return;
      // 읽음 응답보다 먼저 시작한 배지 조회(이동·SSE로 시작)는 읽기 전 값을 돌려줄 수 있으므로 취소하고 응답값을 쓴다.
      await cache.cancelQueries({ queryKey: accountQueryKey(unreadCountQueryName, user?.id) });
      if (token !== getStoredToken()) return;
      cache.setQueryData<UnreadCount>(accountQueryKey(unreadCountQueryName, user?.id), countOf(result));
      void cache.invalidateQueries({ queryKey: accountQueryKey(notificationListQueryName, user?.id) });
    },
    onError: invalidate,
  });
};

/** 설정 저장(보낸 항목만). 성공하면 설정 캐시를 응답값으로 바꾸고, 알림 설정이면 알림 목록·배지를 다시 조회한다. */
export const useSaveMySettings = () => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  const invalidate = useInvalidateNotifications();
  const live = useContext(NotificationLiveContext);
  return useMutation({
    mutationFn: async (body: { notifications?: NotificationSettings; widgetLayout?: StoredLayout }) => {
      const token = getStoredToken();
      return { saved: await saveMySettings(body), token, notifications: Boolean(body.notifications) };
    },
    onSuccess: ({ saved, token, notifications }) => {
      if (token !== getStoredToken()) return;
      cache.setQueryData<MySettings>(accountQueryKey(mySettingsQueryName, user?.id), saved);
      if (notifications) {
        invalidate();
        live?.reset();
      }
    },
  });
};

/**
 * 알림 연결 1개의 소유자. SSE는 분석 화면과 공유한다. 화면·네트워크 복귀와 연결 복구 때 다시 조회한다.
 * 배너 후보는 서버 필터가 적용된 미확인 첫 50개에서 확인한다. 전체 신규 알림 개수로 표시하지 않는다.
 */
export const useNotificationLiveUpdates = (latestSeq: string | undefined) => {
  const { user } = useAuthSession();
  const invalidate = useInvalidateNotifications();
  const invalidateRef = useRef(invalidate);
  invalidateRef.current = invalidate;
  const ready = latestSeq !== undefined;
  const userId = user?.id;
  const token = getStoredToken();
  const owner = useMemo(() => ({}), [userId, user?.userType, token]);
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const monitorRef = useRef<NotificationMonitor | null>(null);
  const [result, setResult] = useState<{ owner: object; state: NotificationLiveState } | null>(null);
  const refresh = useCallback(() => monitorRef.current?.refresh(), []);
  const reset = useCallback(() => monitorRef.current?.reset(), []);
  const dismiss = useCallback(() => monitorRef.current?.dismiss(), []);
  useEffect(() => {
    if (!userId || !ready) return;
    const visible = () => typeof document === "undefined" || document.visibilityState !== "hidden";
    const online = () => typeof navigator === "undefined" || navigator.onLine !== false;
    const monitor = createNotificationMonitor({
      after: latestSeq!,
      current: () => ownerRef.current === owner && token === getStoredToken(),
      visible,
      online,
      read: (signal) => readNotificationList({ view: "unread", cursor: null, limit: 50 }, signal),
      invalidate: () => invalidateRef.current(),
      subscribe: (subscriber) => subscribeDecisionStream(subscriber),
      publish: (state) => setResult({ owner, state }),
    });
    monitorRef.current = monitor;
    const resume = () => monitor.resume();
    window.addEventListener("online", resume);
    window.addEventListener("offline", resume);
    window.addEventListener("focus", resume);
    window.addEventListener("pageshow", resume);
    if (typeof document !== "undefined") document.addEventListener?.("visibilitychange", resume);
    monitor.start();
    return () => {
      window.removeEventListener("online", resume);
      window.removeEventListener("offline", resume);
      window.removeEventListener("focus", resume);
      window.removeEventListener("pageshow", resume);
      if (typeof document !== "undefined") document.removeEventListener?.("visibilitychange", resume);
      monitor.stop();
      if (monitorRef.current === monitor) monitorRef.current = null;
    };
  }, [ready, owner]);
  return { state: result?.owner === owner ? result.state : initialNotificationLiveState(), refresh, reset, dismiss };
};

/** 작업 상태를 담은 조회(작업 목록·작업 상세·학습 진행·비교 평가 목록·상세). 행마다 [작업 ID, 상태]를 꺼낸다. */
const JOB_STATUS_QUERIES: Record<string, (data: unknown) => [string, string][]> = {
  jobs: (data) => rowsOf(data, "id"),
  job: (data) => rowsOf([data], "id"),
  "train-job": (data) => rowsOf([data], "id"),
  comparisons: (data) => rowsOf(data, "jobId"),
  comparison: (data) => rowsOf([data], "jobId"),
};
function rowsOf(data: unknown, idKey: string): [string, string][] {
  const items = Array.isArray(data) ? data : data && typeof data === "object" && Array.isArray((data as { items?: unknown }).items) ? (data as { items: unknown[] }).items : [];
  return items.flatMap((item) => {
    const row = item as Record<string, unknown> | null;
    return row && typeof row[idKey] === "string" && typeof row.status === "string" ? [[row[idKey] as string, row.status as string] as [string, string]] : [];
  });
}
const IN_PROGRESS = new Set(["queued", "running"]);
const FINISHED = new Set(["succeeded", "failed"]);

/**
 * 작업 알림 반영(새 연결 없음): 화면이 이미 다시 읽는 작업 조회(진행 중이면 5초 주기)에서 대기·실행 중이던 작업이 완료·실패로 바뀌면
 * 알림 배지·목록을 다시 조회한다. 그 밖에는 화면 이동·패널 열기 때 반영된다.
 */
export const useRefreshOnJobFinish = (onFinish?: () => void) => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  const userId = user?.id;
  const onFinishRef = useRef(onFinish);
  onFinishRef.current = onFinish;
  useEffect(() => {
    if (!userId) return;
    const seen = new Map<string, string>();
    let pending = false;
    const unsubscribe = cache.getQueryCache().subscribe((event) => {
      if (event.type !== "updated" || event.action.type !== "success") return;
      const [name, account] = event.query.queryKey as unknown[];
      const read = typeof name === "string" ? JOB_STATUS_QUERIES[name] : undefined;
      if (!read || account !== userId) return;
      let finished = false;
      for (const [id, status] of read(event.query.state.data)) {
        if (FINISHED.has(status) && IN_PROGRESS.has(seen.get(id) ?? "")) finished = true;
        seen.set(id, status);
      }
      if (!finished || pending) return;
      // 같은 갱신 안에서 여러 조회가 끝나도 한 번만 다시 읽는다.
      pending = true;
      queueMicrotask(() => {
        pending = false;
        void cache.invalidateQueries({ queryKey: accountQueryKey(unreadCountQueryName, userId) });
        void cache.invalidateQueries({ queryKey: accountQueryKey(notificationListQueryName, userId) });
        onFinishRef.current?.();
      });
    });
    return unsubscribe;
  }, [cache, userId]);
};

/** 화면 이동 때 배지를 다시 조회한다(첫 마운트 제외). */
export const useRefreshOnNavigate = (locationKey: string, onNavigate?: () => void) => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  const first = useRef(true);
  const onNavigateRef = useRef(onNavigate);
  onNavigateRef.current = onNavigate;
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    void cache.invalidateQueries({ queryKey: accountQueryKey(unreadCountQueryName, user?.id) });
    onNavigateRef.current?.();
  }, [cache, locationKey, user?.id]);
};
