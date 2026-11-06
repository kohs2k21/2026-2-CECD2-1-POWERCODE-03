import { useEffect, useRef, useState } from "react";
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
import { NOT_CONNECTED, evaluationCapabilities, requestComparison } from "./evaluationsApi";
import { ruleConditionText, setUnitLabel } from "./evaluationFormat";
import { SetStep } from "./SetStep";
import { TargetStep } from "./TargetStep";
import { useCompareVersions, useEvaluationSets, useRuleRevision } from "./useEvaluations";

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
      useDraftStore.getState().discard(paths.detectionEvaluations);
      navigate(paths.detectionJob(job.id));
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
      <SectionCard title="새 비교 평가">
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
            {!evaluationCapabilities.createComparison && <p className="text-sm text-warning-text">확인 필요 · 비교 평가 요청: {NOT_CONNECTED}</p>}
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
