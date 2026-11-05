import { paths } from "@/app/routes";
import { HttpError } from "@/services/api/client";
import type { FeatureSet, PipelineJob } from "@/services/api/pipeline.api";
import type { CatalogMethod, ParameterRule, TimeBasis } from "./modelsApi";
import { useDraftStore } from "@/stores/draftStore";
import { toUtc, type Range } from "./period";

/** 새 학습 초안과 방법 카탈로그 기준 파라미터 표시·검사. 최종 판단은 서버·워커가 같은 카탈로그로 다시 한다. */
export type PeriodDraft = {
  fromDate: string;
  toDate: string;
  /** 조회한 범위. 날짜 입력칸을 고쳐도 다시 조회할 때까지 이 값을 쓴다. */
  query: Range | null;
  view: Range | null;
  excluded: Range[];
  /** 두 번 클릭 선택의 첫 막대. */
  pending: Range | null;
};
export type TrainDraft = {
  period: PeriodDraft;
  featureSetId: string | null;
  modelKey: string | null;
  /** 모델별 입력칸 값(문자열 그대로). */
  params: Record<string, Record<string, string>>;
  /** 이 초안으로 접수한 작업. 작업 성공 전까지 초안과 함께 보관해 나갔다 와도 이어 본다. */
  jobId: string | null;
  /** `jobId`를 접수한 멱등키. 실패 확정된 그 키로 다시 보내면 같은 작업만 돌아오므로 새 키가 필요한지 판단한다. */
  jobKey: string | null;
  /** 접수 때 내용(`draftFingerprint`). 작업이 성공해도 그 뒤 고친 초안은 비우지 않는다. */
  submitted: string | null;
  /** 마지막으로 보낸 요청의 멱등키이자 응답을 받을 토큰이다. 응답은 이 값이 같은 초안에만 적는다(버린 초안·새 초안에는 적히지 않는다). */
  requestKey: string | null;
  /** 그 요청 본문(JSON). 초안의 작업이 성공하면 그 작업을 접수한 키를 지울 때 쓴다. */
  requestFor: string | null;
  /** 요청 실패 코드. 응답을 받은 화면과 지금 화면이 달라도 보이도록 초안에 둔다. */
  requestError: string | null;
};
export const freshDraft = (): TrainDraft => ({
  period: { fromDate: "", toDate: "", query: null, view: null, excluded: [], pending: null },
  featureSetId: null,
  modelKey: null,
  params: {},
  jobId: null,
  jobKey: null,
  submitted: null,
  requestKey: null,
  requestFor: null,
  requestError: null,
});
/** 사용자가 입력한 내용(표시 범위·접수 기록 제외). */
export const draftFingerprint = ({ period, featureSetId, modelKey, params }: TrainDraft) =>
  JSON.stringify([period.fromDate, period.toDate, period.query, period.excluded, featureSetId, modelKey, params]);

/**
 * 초안의 유일한 저장소는 `draftStore`다. 화면 인스턴스가 값을 따로 들고 있으면 응답을 받은 옛 인스턴스와 복귀한 화면이 어긋난다.
 * 앱 안 이동 확인은 접수 뒤 고친 내용이 있을 때만 필요하다(`leaveFree`). 새로고침·로그아웃 확인은 `dirty`를 따른다.
 */
export const readTrainDraft = () => (useDraftStore.getState().drafts[paths.detectionModels]?.value as TrainDraft | undefined) ?? null;
export const writeTrainDraft = (update: (current: TrainDraft) => TrainDraft) => {
  const current = readTrainDraft() ?? freshDraft();
  const next = update(current);
  if (next === current) return;
  const store = useDraftStore.getState();
  if (draftDirty(next)) store.edit(paths.detectionModels, next, { leaveFree: next.submitted === draftFingerprint(next) });
  else store.discard(paths.detectionModels);
};
/**
 * 접수한 작업의 상태를 모르면 새 요청을 막는다(조회 실패 뒤 다시 누르면 새 키로 중복 학습이 된다). 대기·실행 중도 막는다.
 * 없는 작업·잘못된 ID(404·400)는 기다릴 작업이 없으므로 막지 않는다.
 */
export const jobBlocksRequest = (jobId: string | null, job: { data?: PipelineJob; isError: boolean; error: unknown }) => {
  if (!jobId) return false;
  if (job.data) return job.data.status === "queued" || job.data.status === "running";
  if (job.isError) return !(job.error instanceof HttpError && (job.error.status === 404 || job.error.status === 400));
  return true;
};
export const draftDirty = ({ period, featureSetId, modelKey, params }: TrainDraft) =>
  Boolean(period.fromDate || period.toDate || period.query || featureSetId || modelKey || Object.keys(params).length);

export const modelKeyOf = (method: Pick<CatalogMethod, "methodId">) => method.methodId.slice(method.methodId.indexOf(":") + 1);
/** 실행 가능한 첫 모델이 기본 선택이다. 선택값이 실행 불가가 되면 쓰지 않는다. */
export const selectedMethod = (methods: CatalogMethod[], modelKey: string | null) =>
  methods.find((method) => modelKeyOf(method) === modelKey && method.executable.process) ?? methods.find((method) => method.executable.process) ?? null;

/** 화면 이름·뜻은 카탈로그에 없어 와이어프레임 값을 쓴다. 없는 키는 키 그대로 표시한다. */
const PARAMETER_TEXT: Record<string, { label: string; meaning: string }> = {
  n_estimators: { label: "트리 수", meaning: "앙상블 트리 개수" },
  max_samples: { label: "트리당 표본", meaning: "트리 하나가 보는 행 수" },
  contamination: { label: "contamination", meaning: "이상 비율 가정" },
  random_state: { label: "seed", meaning: "재현용 난수 seed" },
  thresholdPolicy: { label: "판정선 기준", meaning: "train 분할 내부 기준 P99 초과" },
  k: { label: "군집 수 K", meaning: "군집 중심 개수" },
  batch_size: { label: "배치 크기", meaning: "미니배치 행 수" },
  max_iter: { label: "최대 반복", meaning: "학습 반복 상한" },
  n_init: { label: "초기화 횟수", meaning: "중심 초기화 반복" },
  quantile: { label: "판정 분위수", meaning: "최근접 중심 거리의 분위수 초과" },
  ntrees: { label: "트리 수", meaning: "트리 개수" },
  sample_size: { label: "트리당 표본", meaning: "트리 하나가 보는 행 수" },
  extension_level: { label: "확장 수준", meaning: "분할 초평면 확장 차수" },
};
export const METHOD_DESCRIPTIONS: Record<string, string> = {
  "ml:iforest": "무작위 분할 트리를 여러 개 만들어, 적은 분할로 쉽게 고립되는 관측을 이상으로 봅니다.",
  "ml:minibatch_kmeans": "정상 관측을 K개 군집으로 묶고, 가장 가까운 군집 중심에서 멀리 떨어진 관측을 이상으로 봅니다.",
  "ml:eif": "Isolation Forest의 축 방향 분할 대신 기울어진 초평면으로 분할해 축 방향 편향을 줄인 변형입니다.",
};
export const parameterLabel = (key: string) => PARAMETER_TEXT[key]?.label ?? key;

export type ParameterRow = { key: string; label: string; meaning: string; rule: ParameterRule; fixed: boolean; defaultValue: string | null; rangeLabel: string };
const rangeLabel = ({ minimum, maximum, exclusiveMinimum, exclusiveMaximum }: ParameterRule) => {
  const low = minimum ?? exclusiveMinimum, high = maximum ?? exclusiveMaximum;
  if (low !== undefined && high !== undefined) return `${low} ~ ${high}`;
  if (low !== undefined) return `${exclusiveMinimum !== undefined ? ">" : "≥"} ${low}`;
  if (high !== undefined) return `${exclusiveMaximum !== undefined ? "<" : "≤"} ${high}`;
  return "—";
};
export const parameterRows = (method: CatalogMethod): ParameterRow[] =>
  Object.entries(method.parameterSchema.properties).map(([key, rule]) => {
    const fixed = "const" in rule;
    const value = fixed ? rule.const : method.defaults?.[key];
    return {
      key, rule, fixed,
      label: parameterLabel(key),
      meaning: PARAMETER_TEXT[key]?.meaning ?? "",
      defaultValue: value === undefined || value === null ? null : String(value),
      rangeLabel: fixed ? "고정" : rangeLabel(rule),
    };
  });
export const parameterValue = (row: ParameterRow, values: Record<string, string> | undefined) => values?.[row.key] ?? row.defaultValue ?? "";

const NUMBER_TEXT = /^-?\d+(\.\d+)?$/;
const invalidValue = (raw: string, { type, minimum, maximum, exclusiveMinimum, exclusiveMaximum }: ParameterRule) => {
  if (!NUMBER_TEXT.test(raw.trim())) return true;
  const value = Number(raw);
  if (type === "integer" && !Number.isSafeInteger(value)) return true;
  return (minimum !== undefined && value < minimum) || (maximum !== undefined && value > maximum) ||
    (exclusiveMinimum !== undefined && value <= exclusiveMinimum) || (exclusiveMaximum !== undefined && value >= exclusiveMaximum);
};
/** 입력칸 검사(parameterSchema). 기본값이 없는 선택 키는 비워도 된다. */
export const parameterIssues = (method: CatalogMethod, values: Record<string, string> | undefined) =>
  parameterRows(method).filter((row) => {
    if (row.fixed) return false;
    const raw = parameterValue(row, values);
    if (raw.trim() === "" && row.defaultValue === null) return false;
    return invalidValue(raw, row.rule);
  }).map((row) => `${row.label}: 허용 범위 밖 또는 빈 값`);
/** 요청 `parameters`: 조정 가능한 키만 숫자로 보낸다. 고정 값은 워커가 기본값으로 채운다. */
export const parameterPayload = (method: CatalogMethod, values: Record<string, string> | undefined) =>
  Object.fromEntries(parameterRows(method).filter((row) => !row.fixed && parameterValue(row, values).trim() !== "").map((row) => [row.key, Number(parameterValue(row, values))]));

/**
 * 판정 정책은 화면에 없고(사용자 11/02), 학습 요청에서도 빠질 값이다(model-version.md v3). 지금 서버는 v1 형식 요청만 받아
 * 모델 단독 값을 고정해 보낸다. v3 산출(#113)이 열리면 이 키를 뺀다.
 */
const LEGACY_POLICY = { mode: "ml_only", deadlineMs: 3000, combination: "selected_path" } as const;

export const trainRequest = ({ sourceId, draft, method, timeBasis }: { sourceId: string; draft: TrainDraft; method: CatalogMethod; timeBasis: TimeBasis }) => {
  const [from, until] = draft.period.query!;
  const modelKey = modelKeyOf(method);
  return {
    kind: "train", sourceId, from: toUtc(from), until: toUtc(until), featureSetId: draft.featureSetId, modelKey,
    parameters: parameterPayload(method, draft.params[modelKey]), detectionPolicy: LEGACY_POLICY,
    // 키 생략이 서버 기본(persisted_at)이다. 구간이 없으면 키도 없다.
    ...(timeBasis === "persisted_at" ? {} : { timeBasis }),
    ...(draft.period.excluded.length ? { excludedIntervals: draft.period.excluded.map(([start, end]) => ({ from: toUtc(start), until: toUtc(end) })) } : {}),
  };
};

/** 준비 검사(와이어프레임): 요청을 막는 항목. */
export const EXCLUSION_UNSUPPORTED = "config_schema_not_supported_yet";
export const readinessIssues = ({ draft, method, featureSet, exclusionSupported }: { draft: TrainDraft; method: CatalogMethod | null; featureSet: FeatureSet | null; exclusionSupported: boolean }) => {
  const issues: string[] = [];
  if (!draft.period.query) issues.push("학습 기간 조회 필요");
  if (draft.period.excluded.length > 0 && !exclusionSupported) issues.push(`제외 구간: ${EXCLUSION_UNSUPPORTED}`);
  if (!featureSet) issues.push("입력 변수 선택 필요");
  if (!method) return [...issues, "실행 가능한 모델 없음"];
  if (featureSet && !featureSet.supported) issues.push(`입력 변수: ${featureSet.reason ?? "사용 불가"}`);
  const max = method.inputRequirements.maxFeatures;
  if (featureSet && max !== undefined && featureSet.featureNames.length > max) issues.push(`입력 변수 ${featureSet.featureNames.length}개 · 최대 ${max}개`);
  return [...issues, ...parameterIssues(method, draft.params[modelKeyOf(method)])];
};
