import type { InputModeName } from "../data/operationsReads";

/** 입력 모드 이름(와이어프레임 MODE_LABEL). 입력은 하나만 받는다. */
export const inputModeLabel: Record<InputModeName, string> = {
  api: "실서버 API(실제)",
  csv: "CSV 재생(시뮬레이션)",
  paused: "일시정지(입력 중단)",
};
/** 입력 실행 상태(ops_input_runs.status). */
export const inputRunStatusLabel: Record<string, string> = {
  running: "실행 중",
  paused: "일시정지",
  completed: "완료",
  failed: "실패",
};
