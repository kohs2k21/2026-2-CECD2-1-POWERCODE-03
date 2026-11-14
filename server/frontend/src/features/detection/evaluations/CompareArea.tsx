import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { paths, workStepCount } from "@/app/routes";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { SectionCard } from "@/components/layout/SectionCard";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { StepLinks } from "@/features/detection/components/StepLinks";
import { versionLabel } from "@/features/detection/models/VersionList";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { numberText } from "@/features/operations/format";
import { useUrlStep } from "@/hooks/useUrlState";
import { useDraftStore } from "@/stores/draftStore";
import { EMPTY_DRAFT, MAX_COMPARE, compareRequest, includedActive, pickedVersions, writeCompareDraft, type CompareDraft } from "./compareDraft";
import { evaluationCapabilities, requestComparison } from "./evaluationsApi";
import { ruleConditionText, setUnitLabel } from "./evaluationFormat";
import { SetStep } from "./SetStep";
import { TargetStep } from "./TargetStep";
import { useCompareVersions, useEvaluationSets, useRuleRevision } from "./useEvaluations";
import { detectionSections } from "../detectionToc";

const STEPS = ["평가 세트", "비교 모델", "요청"];

/** 새 비교 평가(사용자 9.8): 평가 목록 아래 같은 화면, 머리글 버튼 없음. 단계는 주소 `step`, 고른 값은 초안. */
export const CompareArea = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const sets = useEvaluationSets();
  const versions = useCompareVersions();
  const ruleRevision = useRuleRevision();
  const { value: step, hrefFor } = useUrlStep(workStepCount.compare);
  const draft = useDraftStore((state) => state.drafts[paths.detectionEvaluations]?.value as CompareDraft | undefined) ?? EMPTY_DRAFT;
  const [requesting, setRequesting] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const area = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const submitted = useRef(false);
  useEffect(() => () => {
    if (submitted.current) useDraftStore.getState().discard(paths.detectionEvaluations);
  }, []);
  // 모델 교체 2단계의 '+ 새 비교 평가'는 교체할 버전을 넘긴다. 진입 때 한 번 초안의 비교 모델로 넣는다(세트 기본 스냅샷도 그 버전의 학습 스냅샷이 된다).
  const incoming = (location.state as { compareVersionId?: string } | null)?.compareVersionId;
  useEffect(() => {
    if (!incoming) return;
    writeCompareDraft((current) => ({ ...current, versionIds: current.versionIds.includes(incoming) ? current.versionIds : [...current.versionIds, incoming], setFormOpen: current.setId ? current.setFormOpen : true }));
    navigate(location.pathname + location.search, { replace: true, state: null });
  }, [incoming, navigate, location.pathname, location.search]);

  const set = sets.data?.items.find((item) => item.id === draft.setId && item.status === "ready") ?? null;
  const picked = pickedVersions(versions.data ?? [], draft);
  const included = includedActive(versions.data ?? [], picked);
  const total = picked.length + included.length;
  const maxStep = !set ? 1 : picked.length === 0 || total > MAX_COMPARE ? 2 : 3;
  const shownStep = Math.min(step, maxStep);
  const settled = sets.isSuccess && versions.isSuccess;

  // 앞 단계가 끝나지 않은 주소는 가까운 유효 단계로 정규화한다. 조회 실패로 모르는 동안은 주소를 바꾸지 않는다.
  useEffect(() => {
    if (settled && step > maxStep) navigate(hrefFor(maxStep), { replace: true, state: location.state });
  }, [settled, step, maxStep, navigate, hrefFor, location.state]);
  // `step`이 주소에 있을 때만 작업 영역으로 스크롤한다(목록 첫 진입은 그대로).
  const hasStep = new URLSearchParams(location.search).has("step");
  useEffect(() => {
    if (hasStep) area.current?.scrollIntoView?.({ block: "start" });
  }, [hasStep, shownStep]);

  const toggle = (id: string, checked: boolean) =>
    writeCompareDraft((current) => ({ ...current, versionIds: checked ? [...current.versionIds, id] : current.versionIds.filter((item) => item !== id) }));
  const request = async () => {
    if (!set) return;
    setRequesting(true);
    setRequestError(null);
    try {
      const job = await requestComparison(compareRequest(set, picked));
      // 비교 묶음은 완료 때 생기므로 상세는 작업 ID로 연다(같은 상세 API가 받는다). 완료될 때까지 상태를 다시 읽는다.
      // 접수한 초안은 저장된 것으로 두고(이탈 확인 없음) 떠난 뒤 지운다. 먼저 지우면 단계 정규화가 이 화면 1단계 주소로 되돌린다.
      useDraftStore.getState().markSaved(paths.detectionEvaluations);
      submitted.current = true;
      navigate(paths.detectionEvaluation(job.id));
      await Promise.all(["comparisons", "recent-comparisons"].map((name) => queryClient.invalidateQueries({ queryKey: [name] })));
    } catch (error) {
      setRequestError(errorCode(error));
    } finally {
      setRequesting(false);
    }
  };

  const ready = sets.data && versions.data;
  const models = [...included.map((version) => `${versionLabel(version)}(현재 운영)`), ...picked.map(versionLabel)].join(", ");
  return (
    <div ref={area}>
      <SectionCard id={detectionSections.evaluationsNew.id} title={detectionSections.evaluationsNew.label}>
        <StepLinks steps={STEPS} step={shownStep} maxStep={maxStep} hrefFor={hrefFor} />
        {(sets.isPending || versions.isPending) && <LoadingState>평가 조건 조회 중…</LoadingState>}
        {sets.isError && <ErrorState title={`평가 세트 조회 실패 · ${errorCode(sets.error)}`} onRetry={() => void sets.refetch()} retryLabel="다시 조회" />}
        {versions.isError && <ErrorState title={`모델 버전 조회 실패 · ${errorCode(versions.error)}`} onRetry={() => void versions.refetch()} retryLabel="다시 조회" />}
        {ready && shownStep === 1 && (
          <>
            <SetStep sets={sets.data!.items} draft={draft} />
            <div className="flex justify-end">
              {set ? <Button asChild><Link to={hrefFor(2)}>다음 · 비교 모델</Link></Button> : <Button disabled>다음 · 비교 모델</Button>}
            </div>
          </>
        )}
        {ready && set && shownStep === 2 && (
          <>
            <TargetStep versions={versions.data!} picked={picked} included={included} setLabel={`${set.displayName} · ${setUnitLabel(set)}`} onToggle={toggle} />
            <div className="flex justify-between">
              <Button asChild variant="outline"><Link to={hrefFor(1)}>이전</Link></Button>
              {maxStep === 3 ? <Button asChild><Link to={hrefFor(3)}>다음 · 요청</Link></Button> : <Button disabled>다음 · 요청</Button>}
            </div>
          </>
        )}
        {ready && set && shownStep === 3 && (
          <>
            <h3 className="text-sm font-medium">요청 확인</h3>
            <DescriptionList
              items={[
                { label: "평가 세트", value: set.displayName },
                { label: "비교 모델", value: models },
                { label: "룰 조건", value: ruleConditionText(ruleRevision.data) },
                { label: "총", value: `${numberText(total)}개` },
              ]}
            />
            {ruleRevision.isError && <ErrorState title={`룰 버전 조회 실패 · ${errorCode(ruleRevision.error)}`} onRetry={() => void ruleRevision.refetch()} retryLabel="다시 조회" />}
            {requestError && <Alert variant="destructive" data-compare-error><AlertDescription>비교 평가 요청 실패 · {requestError}</AlertDescription></Alert>}
            <div className="flex justify-between">
              <Button asChild variant="outline"><Link to={hrefFor(2)}>이전</Link></Button>
              <Button disabled={!evaluationCapabilities.createComparison || requesting} onClick={() => void request()}>비교 평가 요청</Button>
            </div>
          </>
        )}
      </SectionCard>
    </div>
  );
};
