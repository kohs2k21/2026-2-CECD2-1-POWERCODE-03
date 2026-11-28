import type { CatalogMethod } from "./modelsApi";

/**
 * 머신러닝 모델의 두 틀(models-ui.md 틀 절, ui.md 용어): 행 단위 모델(PROCESS 한 행) / 시계열 모델(시간 구간 집계).
 * 틀은 방법 카탈로그 `inputRequirements.unit`으로만 정한다(`process_row` → row, `aggregated_window` → trend). 화면이 틀을 지어내지 않는다.
 * `executable.window`는 예전 시간창 경로 표시용이라 틀 판단에 쓰지 않는다(method-catalog.md).
 * 틀 이름·틀 화면 문구는 이 파일 한 곳에 둔다. 서버 응답의 `frame` 값(analysis-ui.md·operations-ui.md 추가 필드)도 같은 두 값이다.
 */
export const modelFrames = ["row", "trend"] as const;
export type ModelFrame = (typeof modelFrames)[number];

export const frameLabels: Record<ModelFrame, string> = { row: "행 단위 모델", trend: "시계열 모델" };
/** 틀 한 줄 설명(머신러닝 모델 화면 구역 설명). */
export const frameNotes: Record<ModelFrame, string> = {
  row: "PROCESS 한 행을 보고 바로 판정",
  trend: "시간 구간으로 모아 추세를 보고 판정",
};
/** 틀 화면 문구(models-ui.md·operations-ui.md). */
export const frameText = {
  current: "현재 운영",
  none: "없음",
  newTrain: "새 학습",
  trainFrame: "학습할 틀",
  switchTo: (frame: ModelFrame) => `${frameLabels[frame]}로 바꾸기`,
  algorithms: (total: number, trainable: number) => `알고리즘 ${total}개 · 학습 가능 ${trainable}개`,
  research: "연구 중",
  notTrainable: "새 학습 불가",
  noAlgorithm: "이 틀의 알고리즘이 카탈로그에 없음",
  blocked: (frame: ModelFrame) => `${frameLabels[frame]}은 지금 학습할 수 없습니다 · 서버에서 학습할 수 있는 알고리즘이 없습니다.`,
  noVersions: (frame: ModelFrame) => `${frameLabels[frame]}의 학습된 버전이 없습니다.`,
  slot: "자리",
  /** 운영 설정 운영 모델 표 설명: 틀로 묶어 보이지만 교체·추가 운영 규칙은 그대로다. */
  slotCaption: "틀마다 자리로 묶어 보임 · 같은 알고리즘이면 교체, 다른 알고리즘은 추가 운영(틀 안 교체는 아직 없음)",
  slotModel: "자리 · 모델",
  emptySlot: (frame: ModelFrame) => `운영 중인 ${frameLabels[frame]}이 없습니다`,
  noTrainable: "학습할 수 있는 알고리즘 없음",
  unknownFrame: "틀 미확인",
} as const;

export const frameOfUnit = (unit: unknown): ModelFrame | null => (unit === "process_row" ? "row" : unit === "aggregated_window" ? "trend" : null);
export const frameOfMethod = (method: Pick<CatalogMethod, "inputRequirements"> | undefined | null) => frameOfUnit(method?.inputRequirements?.unit);
export const methodsOfFrame = (methods: CatalogMethod[] | undefined, frame: ModelFrame) => (methods ?? []).filter((method) => frameOfMethod(method) === frame);
const keyOf = (method: Pick<CatalogMethod, "methodId">) => method.methodId.slice(method.methodId.indexOf(":") + 1);
export const frameOfModelKey = (methods: CatalogMethod[] | undefined, modelKey: string | null | undefined) =>
  modelKey ? frameOfMethod(methods?.find((method) => keyOf(method) === modelKey)) : null;
/**
 * 학습할 수 있는(서버에서 실행 가능한) 방법인가 — 화면의 유일한 판단(구역 `학습 가능 m개`·[새 학습]·3단계 선택 가능·기본 선택이 모두 이것을 쓴다).
 * 지금은 카탈로그 `executable.process`. 시계열 틀 전용 실행 표시가 생기면 이 함수만 바꾼다.
 */
export const methodTrainable = (method: Pick<CatalogMethod, "executable">) => method.executable.process;
/** 학습할 수 있는 알고리즘이 있는 틀인가. */
export const frameTrainable = (methods: CatalogMethod[]) => methods.some(methodTrainable);
/** 학습 불가 사유: 카탈로그 `executableReason` 코드 그대로(없으면 상태). */
export const notTrainableReason = (method: CatalogMethod) => method.executableReason?.process ?? method.status;
/** 모델 이름표 `행 단위 모델 · Isolation Forest`(틀 이름 · 알고리즘 표시 이름). 틀이나 이름을 모르면 null(호출부가 지금 표시를 유지). */
export const modelLabelText = (frame: ModelFrame | null | undefined, methodName: string | null | undefined) =>
  frame && methodName ? `${frameLabels[frame]} · ${methodName}` : null;

/** 서버 응답의 모델 이름표 추가 필드(analysis-ui.md `mlModels`, operations-ui.md `models[]`): 틀·이름을 모르면 null. */
export type ModelLabel = { modelKey: string; methodName: string | null; frame: ModelFrame | null };
export const parseModelLabel = (value: unknown): ModelLabel | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  if (typeof item.modelKey !== "string" || !item.modelKey) return null;
  return {
    modelKey: item.modelKey,
    methodName: typeof item.methodName === "string" && item.methodName ? item.methodName : null,
    frame: item.frame === "row" || item.frame === "trend" ? item.frame : null,
  };
};
/** 모델 키 → 이름표(`행 단위 모델 · Isolation Forest`). 응답에 없거나 틀·이름을 모르면 null. */
export const labelOfModelKey = (labels: readonly ModelLabel[] | undefined, modelKey: string | null | undefined) => {
  // map(methodLabel)처럼 두 번째 인자로 다른 값이 와도 무시한다.
  const label = modelKey && Array.isArray(labels) ? labels.find((item) => item.modelKey === modelKey) : undefined;
  return label ? modelLabelText(label.frame, label.methodName) : null;
};
