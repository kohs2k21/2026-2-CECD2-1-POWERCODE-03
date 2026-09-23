import { useEffect, useState } from "react";
import {
  AnomalyStreamError,
  consumeAnomalyStream,
} from "../../../services/api/anomaly-stream.api";
import { getStoredToken } from "../../../services/auth/session";
import type { RealtimeAnomalyEvent } from "../../../types/realtime";

export type RealtimeStreamStatus =
  | "idle"
  | "connecting"
  | "open"
  | "retrying"
  | "closed"
  | "error";

export type RealtimeAnomaliesState = {
  events: RealtimeAnomalyEvent[];
  status: RealtimeStreamStatus;
  error: string | null;
  invalidCount: number;
};

const initialState: RealtimeAnomaliesState = {
  events: [],
  status: "idle",
  error: null,
  invalidCount: 0,
};

const maxEvents = 50;
const maxSeenEventIds = 256;
const maxReconnectAttempts = 3;
const reconnectDelays = [500, 1000, 2000] as const;

const isAbortError = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "AbortError";

const getErrorMessage = (error: unknown): string =>
  error instanceof Error ? error.message : "실시간 anomaly 스트림을 사용할 수 없습니다.";

export const useRealtimeAnomalies = (
  enabled = true,
): RealtimeAnomaliesState => {
  const token = getStoredToken();
  const [state, setState] = useState<RealtimeAnomaliesState>(initialState);

  useEffect(() => {
    if (!enabled || !token) {
      setState(initialState);
      return;
    }

    let stopped = false;
    let retryTimer: number | null = null;
    let reconnectAttempts = 0;
    const controller = new AbortController();
    const seenEventIds = new Set<string>();
    const seenEventOrder: string[] = [];

    setState({ ...initialState, status: "connecting" });

    const clearRetryTimer = () => {
      if (retryTimer !== null) {
        window.clearTimeout(retryTimer);
        retryTimer = null;
      }
    };

    const scheduleReconnect = () => {
      if (stopped) {
        return;
      }

      if (reconnectAttempts >= maxReconnectAttempts) {
        setState((current) => ({ ...current, status: "closed" }));
        return;
      }

      const delay = reconnectDelays[reconnectAttempts];
      reconnectAttempts += 1;
      setState((current) => ({
        ...current,
        status: "retrying",
        error: current.error ?? "실시간 연결이 종료되어 재연결을 시도합니다.",
      }));
      retryTimer = window.setTimeout(() => {
        retryTimer = null;
        void connect();
      }, delay);
    };

    const connect = async (): Promise<void> => {
      if (stopped) {
        return;
      }

      setState((current) => ({
        ...current,
        status: "connecting",
      }));

      try {
        await consumeAnomalyStream({
          signal: controller.signal,
          onEvent: (event) => {
            if (stopped) {
              return;
            }

            setState((current) => ({ ...current, status: "open" }));
            if (seenEventIds.has(event.eventId)) {
              return;
            }

            seenEventIds.add(event.eventId);
            seenEventOrder.push(event.eventId);
            if (seenEventOrder.length > maxSeenEventIds) {
              const oldestEventId = seenEventOrder.shift();
              if (oldestEventId) {
                seenEventIds.delete(oldestEventId);
              }
            }

            setState((current) => ({
              ...current,
              events: [...current.events, event].slice(-maxEvents),
              status: "open",
              error: null,
            }));
          },
          onInvalidEvent: (reason) => {
            if (stopped) {
              return;
            }

            setState((current) => ({
              ...current,
              invalidCount: current.invalidCount + 1,
              error: reason,
            }));
          },
        });

        if (!stopped) {
          scheduleReconnect();
        }
      } catch (error) {
        if (stopped || controller.signal.aborted || isAbortError(error)) {
          return;
        }

        if (
          error instanceof AnomalyStreamError &&
          (error.status === 401 || error.status === 403)
        ) {
          setState((current) => ({
            ...current,
            status: "error",
            error: "실시간 이벤트 인증이 만료되었거나 권한이 없습니다. 다시 로그인해 주세요.",
          }));
          return;
        }

        setState((current) => ({
          ...current,
          status: "error",
          error: getErrorMessage(error),
        }));
        scheduleReconnect();
      }
    };

    void connect();

    return () => {
      stopped = true;
      clearRetryTimer();
      controller.abort();
    };
  }, [enabled, token]);

  return state;
};
