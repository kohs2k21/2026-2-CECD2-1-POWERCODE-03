import { httpClient, HttpError } from "@/services/api/client";
import { fetchSources } from "@/services/api/pipeline.api";
import { useAccountQuery } from "@/lib/query";

export type InputMode = "api" | "csv" | "paused";
export type InputState = {
  storedCounts: Record<string, number>;
  csvResume: null | { settings: { replayId: string; rate: number; repeat: number }; position: number; status: string };
  enabled: boolean;
  sourceId: string;
  control: null | {
    mode: InputMode;
    revision: number;
    runId: string | null;
    configVersionId: string | null;
    status: string | null;
    settings: { rate?: number; repeat?: number; replayId?: string; pollSeconds?: number } | null;
    position: number | null;
    accepted: number | null;
    duplicates: number | null;
    unidentified: number | null;
    lastError: string | null;
    workerHeartbeatAt: string | null;
  };
  replays: { id: string; replayKey: string; recordCount: number; layerCounts: Record<string, number> }[];
  configurations: { id: string; mode: string | null; createdAt: string }[];
};
export type InputModeRequest = {
  mode: InputMode;
  expectedRevision: number;
  pollSeconds?: number;
  replayId?: string;
  rate?: number;
  repeat?: number;
  restart?: boolean;
  configVersionId?: string | null;
};

const messages: Record<string, string> = {
  input_revision_conflict: "다른 화면에서 입력 설정이 변경되었습니다. 최신 상태를 불러온 뒤 다시 선택하세요.",
  input_settings_changed_restart_required:
    "이 재생의 속도·횟수가 이전 실행과 다릅니다. 이전 설정으로 재개하거나 새 재생을 선택하세요.",
  csv_replay_not_ready: "먼저 서버에서 CSV를 등록하세요.",
  input_config_source_mismatch: "이 데이터 원천에 속하는 탐지 구성을 선택하세요.",
};
export const inputErrorText = (error: unknown) =>
  error instanceof HttpError
    ? (messages[error.code ?? ""] ?? error.code ?? error.message)
    : error instanceof Error
      ? error.message
      : "입력 상태를 확인하지 못했습니다.";

export const readInputState = (sourceId: string, signal?: AbortSignal) =>
  httpClient.get<InputState>("/api/inputs", { params: { sourceId }, signal });
export const requestInputMode = (sourceId: string, body: InputModeRequest) =>
  httpClient.post<unknown>(`/api/inputs/${encodeURIComponent(sourceId)}/mode`, body);

/** 입력 실행이 진행 중(running)일 때만 5초 주기 조회. */
export const inputRunning = (state: InputState | undefined) => state?.control?.status === "running";

export const useSourcesQuery = () => useAccountQuery({ name: "pipeline-sources", read: fetchSources });
export const useInputStateQuery = (sourceId: string) =>
  useAccountQuery({
    name: "input-state",
    params: [sourceId],
    read: (signal) => readInputState(sourceId, signal),
    enabled: Boolean(sourceId),
    adminOnly: true,
    staleTime: 0,
    isRunning: inputRunning,
  });
