import { paths } from "@/app/routes";
import { useDraftStore } from "@/stores/draftStore";
import type { CompareVersion, ComparisonRequest, EvaluationSet, EvaluationSetRequest, EvaluationSnapshot } from "./evaluationsApi";

/** 세트 생성 방식. `original` = 합성 없는 원본 분할만(v1 서버가 받는 유일한 방식), 나머지는 합성 프로필(v1 미지원). */
export type ProfileKey = "original" | "esb" | "adbench";
/** 새 평가 세트 입력값(입력칸 문자열 그대로). `snapshotId`가 null이면 기본 스냅샷(고른 버전의 학습 스냅샷)이다. */
export type SetForm = {
  name: string;
  profile: ProfileKey;
  snapshotId: string | null;
  scenarios: string[];
  severity: string;
  repetitions: string;
  targetRows: string;
  seed: string;
};
export const FRESH_SET_FORM: SetForm = { name: "평가 세트 초안", profile: "original", snapshotId: null, scenarios: ["P02"], severity: "1", repetitions: "2", targetRows: "50", seed: "42" };

/** 새 비교 평가 초안: 고른 세트·비교 모델·세트 입력값(주소 밖, 경로별 `draftStore`). 단계 이동으로 화면이 바뀌어도 여기 남는다. */
export type CompareDraft = { setId: string | null; versionIds: string[]; setForm: SetForm; setFormOpen: boolean };
export const EMPTY_DRAFT: CompareDraft = { setId: null, versionIds: [], setForm: FRESH_SET_FORM, setFormOpen: false };
/** 활성 포함 총 상한(comparison_too_many_configs). */
export const MAX_COMPARE = 8;

const hasInput = ({ setId, versionIds, setForm }: CompareDraft) =>
  Boolean(setId) || versionIds.length > 0 || JSON.stringify(setForm) !== JSON.stringify(FRESH_SET_FORM);

/** 입력이 있으면 이탈 확인 대상이다. 입력 없이 양식만 연 상태는 보관하되 확인을 띄우지 않는다. */
export const writeCompareDraft = (update: (current: CompareDraft) => CompareDraft) => {
  const store = useDraftStore.getState();
  const path = paths.detectionEvaluations;
  const next = update((store.drafts[path]?.value as CompareDraft | undefined) ?? EMPTY_DRAFT);
  if (!hasInput(next) && !next.setFormOpen) {
    store.discard(path);
    return;
  }
  store.edit(path, next);
  if (!hasInput(next)) store.markSaved(path);
};

/** 고를 수 있는 버전 중 초안에 있는 것. 활성·실행 불가 버전은 요청에 넣지 않는다. */
export const pickedVersions = (versions: CompareVersion[], draft: Pick<CompareDraft, "versionIds">) =>
  versions.filter((version) => draft.versionIds.includes(version.id) && !version.active && version.executable);
/** 서버가 자동으로 넣는 활성 버전: 고른 후보와 같은 `modelKey`만(config-comparison-evaluation.md 요청 절). */
export const includedActive = (versions: CompareVersion[], picked: CompareVersion[]) =>
  versions.filter((version) => version.active && picked.some((item) => item.modelKey === version.modelKey));
export const compareRequest = (set: EvaluationSet, picked: CompareVersion[]): ComparisonRequest => ({
  kind: "compare", sourceId: set.sourceId, evaluationSetId: set.id, configVersionIds: picked.map((version) => version.id),
});

/**
 * 세트 기준 스냅샷 기본값(고른 버전의 학습 스냅샷 검증 분할). 입력값 → 고른 후보의 학습 스냅샷 → 현재 운영 버전의 학습 스냅샷 → 목록 첫 항목.
 */
export const defaultSnapshot = (snapshots: EvaluationSnapshot[], form: Pick<SetForm, "snapshotId">, versionIds: string[], versions: CompareVersion[]) => {
  const trainedOn = (id: string) => snapshots.find((snapshot) => snapshot.configVersionIds.includes(id));
  const chosen = form.snapshotId ? snapshots.find((snapshot) => snapshot.id === form.snapshotId) : undefined;
  return chosen ?? versionIds.map(trainedOn).find(Boolean) ?? versions.filter((version) => version.active).map((version) => trainedOn(version.id)).find(Boolean) ?? snapshots[0] ?? null;
};
/** `POST /api/evaluation-sets` 본문: 원본 검증 분할만. */
export const setRequest = (form: Pick<SetForm, "name">, snapshot: EvaluationSnapshot): EvaluationSetRequest => ({
  sourceId: snapshot.sourceId, snapshotId: snapshot.id, purpose: "validation", displayName: form.name.trim(), synthetic: null,
});
