import { paths, urlOptions } from "@/app/routes";
import type { CatalogMethod } from "@/features/detection/models/modelsApi";
import { DASH, dayText } from "@/features/operations/format";
import type { JobListItem, JobResultLinks, JobStatus } from "./jobsApi";

/**
 * 작업 화면 문구(와이어프레임 #93 작업 목록·작업 상세, operations-ui.md '작업 목록·작업 상세').
 * 종류 이름은 각 화면 이름과 같다(머신러닝 모델 학습, 평가 결과의 평가 세트·비교 평가, 룰 설정의 AI 규칙 추천·규칙 추천).
 */
export const JOB_KINDS = urlOptions.jobsKind.filter((kind) => kind !== "all");
export const JOB_KIND_LABELS: Record<string, string> = {
  train: "학습", evaluate: "평가", evaluation_set: "평가 세트", compare: "비교 평가",
  recommend_thresholds: "AI 규칙 추천", recommend_relations: "규칙 추천",
};
export const kindLabel = (kind: string) => JOB_KIND_LABELS[kind] ?? kind;

/** 상태 탭(와이어프레임 전체·진행 중·완료·실패). 진행 중 = 대기 + 실행 중. blocked는 전체에만 보인다. */
export type JobTab = (typeof urlOptions.jobsTab)[number];
export const JOB_TAB_LABELS: Record<JobTab, string> = { all: "전체", running: "진행 중", done: "완료", failed: "실패" };
export const JOB_TAB_STATUSES: Record<JobTab, JobStatus[] | null> = { all: null, running: ["queued", "running"], done: ["succeeded"], failed: ["failed"] };
export const JOB_EMPTY: Record<JobTab, string> = {
  all: "작업이 없습니다.", running: "이 상태의 작업이 없습니다.", done: "이 상태의 작업이 없습니다.", failed: "이 상태의 작업이 없습니다.",
};

export const jobInProgress = (status: JobStatus | undefined) => status === "queued" || status === "running";

type BadgeVariant = "info" | "success" | "warning" | "critical" | "outline";
/**
 * 상태 배지. 재시도 대기는 오류 코드가 있는 대기 상태다(trainer-service.md). 비교 평가는 일부 항목 실패를 `완료 · 일부 실패`로 보인다(평가 결과 화면과 같다).
 * 그 밖의 상태 값(blocked 등)은 코드 그대로 보인다.
 */
export const jobStatusBadge = (job: Pick<JobListItem, "status" | "errorCode" | "kind">, failedCount?: number | null): { label: string; variant: BadgeVariant } => {
  switch (job.status) {
    case "queued":
      return { label: job.errorCode ? "재시도 대기" : "대기", variant: job.errorCode ? "warning" : "info" };
    case "running":
      return { label: "진행 중", variant: "info" };
    case "succeeded":
      return job.kind === "compare" && (failedCount ?? 0) > 0 ? { label: "완료 · 일부 실패", variant: "warning" } : { label: "완료", variant: "success" };
    case "failed":
      return { label: "실패", variant: "critical" };
    default:
      return { label: String(job.status), variant: "outline" };
  }
};

const text = (value: unknown) => (typeof value === "string" && value ? value : null);
const short = (value: unknown) => text(value)?.slice(0, 8) ?? DASH;
const day = (value: unknown) => (text(value) ? dayText(String(value).slice(0, 10)) : DASH);
const period = (input: Record<string, unknown>) => (text(input.from) || text(input.until) ? `${day(input.from)} ~ ${day(input.until)}` : null);

/** 모델 키 → 방법 카탈로그 표시 이름(없으면 키 그대로). 키 생략 학습은 기본 IF다(trainer-service.md). */
export const modelName = (methods: CatalogMethod[] | undefined, key: unknown) => {
  const modelKey = text(key) ?? "iforest";
  return methods?.find((method) => method.methodId === `ml:${modelKey}`)?.displayName ?? modelKey;
};

export type JobNames = {
  methods?: CatalogMethod[];
  /** 평가 세트 id → 이름. 모르면 id 앞 8자리. */
  setName?: (id: string) => string | null;
  /** 모델 버전 id → 표시(`id8 · 이름`). */
  versionLabel?: (id: string) => string;
};

/** 목록 `대상` 칸: 작업 입력에서 무엇을 대상으로 했는지 한 줄(입력 값 그대로, 없는 값은 비움). */
export const jobTarget = (job: Pick<JobListItem, "kind" | "inputSpec">, names: JobNames = {}): string => {
  const input = job.inputSpec ?? {};
  const set = () => (text(input.evaluationSetId) ? (names.setName?.(String(input.evaluationSetId)) ?? short(input.evaluationSetId)) : DASH);
  const version = (id: unknown) => (text(id) ? (names.versionLabel?.(String(id)) ?? short(id)) : DASH);
  switch (job.kind) {
    case "train":
      return [modelName(names.methods, input.modelKey), period(input) ?? (text(input.snapshotId) ? `스냅샷 ${short(input.snapshotId)}` : null)].filter(Boolean).join(" · ");
    case "evaluate":
      return `${version(input.configVersionId)} · ${text(input.purpose) ?? DASH}`;
    case "evaluation_set":
      return `${set()} · ${text(input.purpose) ?? DASH}`;
    case "compare": {
      const count = Array.isArray(input.configVersionIds) ? input.configVersionIds.length : null;
      return `${set()}${count !== null ? ` · 모델 ${count}개` : ""}`;
    }
    case "recommend_thresholds": {
      const revision = typeof input.ruleSetRevision === "number" ? `룰 버전 ${input.ruleSetRevision}` : DASH;
      const data = input.data && typeof input.data === "object" ? period(input.data as Record<string, unknown>) : null;
      return data ? `${revision} · ${data}` : revision;
    }
    case "recommend_relations":
      return `스냅샷 ${short(input.snapshotId)}`;
    default:
      return DASH;
  }
};

/** 결과 화면 연결(완료 뒤). 비교 평가는 완료 전·실패도 평가 상세가 진행·실패 항목을 보이므로 항상 연결한다. */
export const jobResultLink = (job: Pick<JobListItem, "id" | "kind" | "status" | "resultConfigVersionId" | "inputSpec">, result: Partial<JobResultLinks> | null | undefined, names: JobNames = {}): { to: string; label: string } | null => {
  const versionText = (version: string) => names.versionLabel?.(version) ?? version.slice(0, 8);
  if (job.kind === "compare") return { to: paths.detectionEvaluation(result?.comparisonId ?? job.id), label: `비교 ${(result?.comparisonId ?? job.id).slice(0, 8)}` };
  if (job.status !== "succeeded") return null;
  switch (job.kind) {
    case "train": {
      const version = result?.configVersionId ?? job.resultConfigVersionId;
      return version ? { to: paths.detectionModel(version), label: `버전 ${versionText(version)}` } : null;
    }
    case "evaluate": {
      // 평가 결과는 평가한 모델 버전 상세(최근 평가)에서 본다.
      const version = text(job.inputSpec?.configVersionId);
      return version ? { to: paths.detectionModel(version), label: `버전 ${versionText(version)}` } : null;
    }
    case "evaluation_set":
      return { to: paths.detectionEvaluations, label: "평가 결과" };
    case "recommend_thresholds":
      return { to: paths.detectionRules, label: "룰 설정" };
    case "recommend_relations":
      return { to: paths.detectionRuleCandidates, label: "규칙 추천" };
    default:
      return null;
  }
};
