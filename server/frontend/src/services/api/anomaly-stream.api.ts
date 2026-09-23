import { getStoredToken } from "../auth/session";
import { isRealtimeAnomalyEvent, type RealtimeAnomalyEvent } from "../../types/realtime";
import { buildApiUrl } from "./client";

export type AnomalyStreamOptions = {
  signal: AbortSignal;
  onOpen?: () => void;
  onEvent: (event: RealtimeAnomalyEvent) => void;
  onInvalidEvent?: (reason: string) => void;
};

export class AnomalyStreamError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "AnomalyStreamError";
    this.status = status;
  }
}

const reportInvalidEvent = (
  onInvalidEvent: AnomalyStreamOptions["onInvalidEvent"],
  reason: string,
) => {
  onInvalidEvent?.(reason);
};

const createFrameDispatcher = ({
  onEvent,
  onInvalidEvent,
}: Pick<AnomalyStreamOptions, "onEvent" | "onInvalidEvent">) => {
  return (frame: string) => {
    let eventName = "";
    const dataLines: string[] = [];

    for (const line of frame.split("\n")) {
      if (line.startsWith(":")) {
        continue;
      }

      const separator = line.indexOf(":");
      const field = separator === -1 ? line : line.slice(0, separator);
      const value = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");

      if (field === "event") {
        eventName = value;
      } else if (field === "data") {
        dataLines.push(value);
      }
    }

    if (dataLines.length === 0) {
      return;
    }

    if (eventName !== "anomaly") {
      reportInvalidEvent(onInvalidEvent, "지원하지 않는 SSE 이벤트 형식입니다.");
      return;
    }

    let payload: unknown;
    try {
      payload = JSON.parse(dataLines.join("\n")) as unknown;
    } catch {
      reportInvalidEvent(onInvalidEvent, "SSE anomaly 이벤트의 JSON 형식이 올바르지 않습니다.");
      return;
    }

    if (!isRealtimeAnomalyEvent(payload)) {
      reportInvalidEvent(onInvalidEvent, "SSE anomaly 이벤트가 계약 검증을 통과하지 못했습니다.");
      return;
    }

    onEvent(payload);
  };
};

const isAbortError = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "AbortError";

export const consumeAnomalyStream = async ({
  signal,
  onOpen,
  onEvent,
  onInvalidEvent,
}: AnomalyStreamOptions): Promise<void> => {
  const token = getStoredToken();
  if (!token) {
    throw new AnomalyStreamError("로그인 토큰이 없어 실시간 스트림을 열 수 없습니다.", 401);
  }

  let response: Response;
  try {
    response = await fetch(buildApiUrl("/api/anomaly/realtime-stream"), {
      method: "GET",
      headers: {
        Accept: "text/event-stream",
        Authorization: "Bearer " + token,
      },
      signal,
    });
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }

    throw new AnomalyStreamError("실시간 anomaly 스트림에 연결하지 못했습니다.");
  }

  if (response.status !== 200) {
    throw new AnomalyStreamError(
      `실시간 anomaly 스트림 요청이 거부되었습니다 (${response.status}).`,
      response.status,
    );
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("text/event-stream")) {
    throw new AnomalyStreamError("실시간 anomaly 스트림 응답 형식이 올바르지 않습니다.");
  }

  if (!response.body) {
    throw new AnomalyStreamError("실시간 anomaly 스트림 본문을 읽을 수 없습니다.");
  }

  onOpen?.();

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const dispatchFrame = createFrameDispatcher({ onEvent, onInvalidEvent });
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");

      let delimiterIndex = buffer.indexOf("\n\n");
      while (delimiterIndex !== -1) {
        dispatchFrame(buffer.slice(0, delimiterIndex));
        buffer = buffer.slice(delimiterIndex + 2);
        delimiterIndex = buffer.indexOf("\n\n");
      }
    }

    buffer += decoder.decode();
    if (buffer.trim() !== "") {
      dispatchFrame(buffer.replace(/\r\n/g, "\n"));
    }
  } finally {
    reader.releaseLock();
  }
};
