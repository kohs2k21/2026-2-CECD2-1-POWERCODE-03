import { useMemo } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { accountQueryKey, useAccountQuery } from "@/lib/query";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { getStoredToken } from "@/services/auth/session";
import {
  observeProcessing,
  readEmailAlertSettings,
  readRecentDeliveries,
  saveEmailAlertSettings,
  sendTestEmail,
  type EmailAlertSettings,
  type EmailAlertSettingsUpdate,
  type ProcessingObservation,
} from "./emailAlertsApi";

export const emailSettingsQueryName = "email-alert-settings";
export const emailDeliveriesQueryName = "email-alert-deliveries";

/** 메일 알림 설정(관리자만 조회, 계정별 key). 주기 조회 없음 — 저장·시험 발송 뒤 다시 조회한다. */
export const useEmailAlertSettings = () =>
  useAccountQuery({ name: emailSettingsQueryName, read: (signal) => readEmailAlertSettings(signal), adminOnly: true });
/** 최근 발송 20건(관리자만). */
export const useRecentDeliveries = () =>
  useAccountQuery({ name: emailDeliveriesQueryName, read: (signal) => readRecentDeliveries(signal), adminOnly: true, staleTime: 0 });

const useRefreshEmail = () => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  return () => {
    void cache.invalidateQueries({ queryKey: accountQueryKey(emailSettingsQueryName, user?.id) });
    void cache.invalidateQueries({ queryKey: accountQueryKey(emailDeliveriesQueryName, user?.id) });
  };
};

/** 저장: 성공하면 설정 캐시를 응답값으로 바꾸고 발송 목록을 다시 조회한다. 실패하면 캐시(서버 값)를 그대로 둔다. */
export const useSaveEmailAlertSettings = () => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (body: EmailAlertSettingsUpdate) => {
      const token = getStoredToken();
      return { saved: await saveEmailAlertSettings(body), token };
    },
    onSuccess: ({ saved, token }) => {
      if (token !== getStoredToken()) return;
      cache.setQueryData<EmailAlertSettings>(accountQueryKey(emailSettingsQueryName, user?.id), saved.settings);
      void cache.invalidateQueries({ queryKey: accountQueryKey(emailDeliveriesQueryName, user?.id) });
    },
  });
};

/** 시험 발송(받는 사람 1명). 결과와 상관없이 끝나면 설정(전송 상태)·발송 목록을 다시 조회한다. */
export const useSendTestEmail = () => {
  const refresh = useRefreshEmail();
  return useMutation({ mutationFn: (to: string) => sendTestEmail(to), onSettled: refresh });
};

/** 계정별 처리 경계 관찰(화면을 다시 열어도 이어서 잰다). 계정이 바뀌면 key가 달라 섞이지 않는다. */
const observations = new Map<string, ProcessingObservation>();
export const resetProcessingObservations = () => observations.clear();
export const useProcessingLag = (userId: string | undefined, settings: EmailAlertSettings | undefined, observedAt: number) =>
  useMemo(() => {
    if (!userId || !settings) return false;
    const { observation, lagging } = observeProcessing(observations.get(userId), settings, observedAt);
    if (observation) observations.set(userId, observation);
    else observations.delete(userId);
    return lagging;
  }, [userId, settings, observedAt]);
