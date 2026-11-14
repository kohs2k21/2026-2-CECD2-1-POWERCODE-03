import { useEffect, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { accountQueryKey, useAccountQuery } from "@/lib/query";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { getStoredToken } from "@/services/auth/session";
import { DECISION_STREAM_RETRY_MS, subscribeDecisionStream } from "@/services/api/decision-stream-hub";
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
const countOf = ({ latestSeq, unreadCount, unreadByType }: UnreadCount): UnreadCount => ({ latestSeq, unreadCount, unreadByType });

export const notificationListQueryName = "notifications-list";
export const unreadCountQueryName = "notifications-unread";
export const mySettingsQueryName = "me-settings";
/** 패널 한 쪽 항목 수. */
export const NOTIFICATION_PAGE_SIZE = 20;
/** 새 판정 이벤트가 몰려도 알림 다시 조회는 이 간격에 한 번(이벤트가 있을 때만, 주기 조회 아님). */
export const NOTIFICATION_REFRESH_MS = 10_000;
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
  return useMutation({
    mutationFn: async (body: { notifications?: NotificationSettings; widgetLayout?: StoredLayout }) => {
      const token = getStoredToken();
      return { saved: await saveMySettings(body), token, notifications: Boolean(body.notifications) };
    },
    onSuccess: ({ saved, token, notifications }) => {
      if (token !== getStoredToken()) return;
      cache.setQueryData<MySettings>(accountQueryKey(mySettingsQueryName, user?.id), saved);
      if (notifications) invalidate();
    },
  });
};

/**
 * 새 판정·중간 판정 이벤트가 오면 알림 목록·배지를 다시 조회한다. 기존 SSE(/api/pipeline/events?include=interims)를
 * 배지 응답의 latestSeq 다음부터 받는다(과거 재생 없음). 끊기면 3초 뒤 같은 커서로 재연결, 세션이 바뀌면 멈춘다.
 * 사건 묶음(Detector 주기 작업)은 이벤트가 없으므로 다음 판정 이벤트·화면 이동·패널 열기 때 반영된다.
 */
export const useNotificationLiveUpdates = (latestSeq: string | undefined) => {
  const { user } = useAuthSession();
  const invalidate = useInvalidateNotifications();
  const invalidateRef = useRef(invalidate);
  invalidateRef.current = invalidate;
  const startRef = useRef<string | null>(null);
  startRef.current = startRef.current ?? latestSeq ?? null;
  const ready = startRef.current !== null;
  const userId = user?.id;
  useEffect(() => {
    if (!userId || !ready) return;
    const token = getStoredToken();
    let stopped = false;
    let refresh: ReturnType<typeof setTimeout> | undefined;
    // 이상 분석 목록과 같은 연결을 나눠 쓴다(decision-stream-hub). 연결 실패는 배지·목록 조회 결과로 드러난다(연결 쪽이 3초 뒤 다시 연결만 한다).
    const unsubscribe = subscribeDecisionStream({
      after: startRef.current!,
      onEvent: () => {
        if (token !== getStoredToken()) return;
        refresh ??= setTimeout(() => {
          refresh = undefined;
          if (!stopped && token === getStoredToken()) invalidateRef.current();
        }, NOTIFICATION_REFRESH_MS);
      },
    });
    return () => {
      stopped = true;
      unsubscribe();
      if (refresh) clearTimeout(refresh);
    };
  }, [ready, userId]);
};

/** 화면 이동 때 배지를 다시 조회한다(첫 마운트 제외). */
export const useRefreshOnNavigate = (locationKey: string) => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    void cache.invalidateQueries({ queryKey: accountQueryKey(unreadCountQueryName, user?.id) });
  }, [cache, locationKey, user?.id]);
};
