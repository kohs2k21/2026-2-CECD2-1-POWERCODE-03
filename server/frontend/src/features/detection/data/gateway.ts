import { HttpError } from "../../../services/api/client";
import type { DetectionData, DetectionGateway } from "./types";
const cancelled = (signal?: AbortSignal) => {
  if (signal?.aborted) throw new DOMException("취소된 조회", "AbortError");
};
export const createDevelopmentGateway = (
  data: DetectionData,
): DetectionGateway => ({
  async read(signal) {
    cancelled(signal);
    await Promise.resolve();
    cancelled(signal);
    return structuredClone(data);
  },
  async request() {
    throw new HttpError(
      "작업 서비스에 연결되지 않아 요청할 수 없습니다. 편집 내용은 유지됩니다.",
      503,
      "service_unavailable",
    );
  },
});
export const unavailableGateway: DetectionGateway = {
  async read() {
    throw new HttpError(
      "탐지 관리 조회 서비스에 연결되지 않았습니다.",
      503,
      "service_unavailable",
    );
  },
  async request() {
    throw new HttpError(
      "작업 서비스에 연결되지 않아 요청할 수 없습니다. 편집 내용은 유지됩니다.",
      503,
      "service_unavailable",
    );
  },
};
export const getDefaultGateway = async (): Promise<DetectionGateway> => {
  if ((import.meta as ImportMeta & { env?: { DEV?: boolean } }).env?.DEV) {
    const { developmentDetectionData } = await import("./fixtures");
    return createDevelopmentGateway(developmentDetectionData);
  }
  return unavailableGateway;
};
