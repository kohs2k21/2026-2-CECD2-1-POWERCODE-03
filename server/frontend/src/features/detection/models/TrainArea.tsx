import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { paths, withSearchParam, workStepCount } from "@/app/routes";
import { SectionCard } from "@/components/layout/SectionCard";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { StepLinks } from "@/features/detection/components/StepLinks";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { useSourcesQuery } from "@/features/operations/data/inputs";
import { numberText } from "@/features/operations/format";
import { useUrlStep } from "@/hooks/useUrlState";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { forgetJobRequestKey, jobRequestKey } from "@/services/api/job-idempotency";
import { createPipelineJob, type PipelineJob } from "@/services/api/pipeline.api";
import { useDraftStore } from "@/stores/draftStore";
import { FeatureStep, useFeatureSets } from "./FeatureStep";
import { ModelStep } from "./ModelStep";
import { DAY_MINUTES, minuteDay } from "./period";
import { PeriodStep } from "./PeriodStep";
import { draftFingerprint, freshDraft, jobBlocksRequest, modelKeyOf, readTrainDraft, readinessIssues, selectedMethod, trainRequest, writeTrainDraft, type PeriodDraft, type TrainDraft } from "./trainDraft";
import { VERSIONS_QUERY, useCapabilities, useTrainJob } from "./useModels";
import { detectionSections } from "../detectionToc";

const STEPS = ["기간", "입력 변수", "모델·파라미터"];

const JOB_LABELS: Record<PipelineJob["status"], string> = { queued: "대기", running: "실행 중", succeeded: "완료", failed: "실패" };
const JOB_BADGES = { queued: "info", running: "info", succeeded: "success", failed: "critical" } as const;

/** 학습 작업 진행. 접수만으로 완료로 보이지 않게 작업 상태를 그대로 보여 준다. 재시도 대기는 `errorCode`가 있는 대기 상태다. */
const TrainJobStatus = ({ jobId, returnTo }: { jobId: string; returnTo: string }) => {
  const job = useTrainJob(jobId);
  if (job.isPending) return <LoadingState>학습 작업 조회 중…</LoadingState>;
  if (job.isError && !job.data) return <ErrorState title={`학습 작업 조회 실패 · ${errorCode(job.error)}`} onRetry={() => void job.refetch()} retryLabel="다시 조회" />;
  const { status, errorCode: code, attemptCount, maxAttempts, resultSummary } = job.data!;
  const version = status === "succeeded" ? resultSummary?.configVersionId : undefined;
  return (
    <>
      <Alert role="status" variant={status === "failed" ? "destructive" : "default"} data-job-status={status}>
        <AlertDescription className="flex flex-row flex-wrap items-center gap-2">
          <Badge variant={JOB_BADGES[status]}>{JOB_LABELS[status]}</Badge>
          <span>학습 작업 </span>
          <Link className="font-mono text-xs text-link" to={paths.detectionJob(jobId)} state={{ returnTo }}>{jobId.slice(0, 8)}</Link>
          {attemptCount !== undefined && maxAttempts !== undefined && <span>시도 {attemptCount}/{maxAttempts}</span>}
          {code && <span>{status === "failed" ? "실패" : "재시도 대기"} · {code}</span>}
          {version && <Button asChild size="sm" variant="outline"><Link to={paths.detectionModel(version)}>결과 보기 · 버전 {version.slice(0, 8)}</Link></Button>}
        </AlertDescription>
      </Alert>
      {job.isError && <ErrorState title={`학습 작업 조회 실패 · ${errorCode(job.error)}`} onRetry={() => void job.refetch()} retryLabel="다시 조회" />}
    </>
  );
};

const EMPTY_DRAFT = freshDraft();
const periodLabel = (period: PeriodDraft) =>
  period.query ? `${minuteDay(period.query[0])} ~ ${minuteDay(period.query[1] - DAY_MINUTES)} · 제외 ${period.excluded.length}구간` : "—";

/** 새 학습(사용자 9.6·9.8): 버전 목록 아래 같은 화면, 머리글 버튼 없음. 단계는 주소 `step`, 접수한 작업은 주소 `job`. */
export const TrainArea = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user } = useAuthSession();
  const capabilities = useCapabilities();
  const sources = useSourcesQuery();
  const { value: step, hrefFor } = useUrlStep(workStepCount.train);
  const draft = useDraftStore((state) => state.drafts[paths.detectionModels]?.value as TrainDraft | undefined) ?? EMPTY_DRAFT;
  const [requesting, setRequesting] = useState(false);
  const area = useRef<HTMLDivElement>(null);
  const params = new URLSearchParams(location.search);
  const urlJob = params.get("job");
  // 초안의 작업이 먼저다: 주소의 작업은 브라우저 뒤로 가기로 옛 값일 수 있다.
  const jobId = draft.jobId ?? urlJob;
  const job = useTrainJob(jobId);
  const requestBlocked = jobBlocksRequest(jobId, job);

  const sourceId = sources.data?.[0]?.id ?? "";
  const featureSets = useFeatureSets(sourceId);
  const methods = capabilities.data?.methodCatalog.methods ?? [];
  const method = selectedMethod(methods, draft.modelKey);
  const featureSet = featureSets.data?.items.find((item) => item.id === draft.featureSetId) ?? null;
  const exclusionSupported = capabilities.data?.training?.excludedIntervals === true;
  const issues = readinessIssues({ draft, method, featureSet, exclusionSupported });
  const maxStep = !draft.period.query ? 1 : !draft.featureSetId ? 2 : 3;
  const shownStep = Math.min(step, maxStep);

  // 앞 단계가 끝나지 않은 주소는 가까운 유효 단계로 정규화한다.
  useEffect(() => {
    if (step > maxStep) navigate(hrefFor(maxStep), { replace: true, state: location.state });
  }, [step, maxStep, navigate, hrefFor, location.state]);
  // `step`이 주소에 있을 때만 작업 영역으로 스크롤한다(목록 첫 진입은 그대로).
  const hasStep = params.has("step");
  useEffect(() => {
    if (hasStep) area.current?.scrollIntoView?.({ block: "start" });
  }, [hasStep, shownStep]);
  // 주소의 `job`은 초안의 작업을 따른다(새로고침해도 이어 보도록). 응답이 어느 인스턴스에 오든 지금 보이는 화면이 옮긴다.
  useEffect(() => {
    if (draft.jobId && urlJob !== draft.jobId) navigate(location.pathname + withSearchParam(location.search, "job", draft.jobId), { replace: true, state: location.state });
  }, [draft.jobId, urlJob, navigate, location]);
  // 성공한 작업: 목록은 한 번만 다시 읽는다. 초안의 작업을 지우면 결과 표시는 주소에 남으므로 주소가 맞은 뒤에 정리한다.
  // 순서가 바뀌면 빈 초안의 단계 정규화가 옛 주소로 다시 이동한다. 접수 뒤 고친 내용은 남긴다.
  const handledJob = useRef<string | null>(null);
  const succeeded = job.data?.status === "succeeded" ? job.data.id : null;
  useEffect(() => {
    if (!succeeded) return;
    if (handledJob.current !== succeeded) {
      handledJob.current = succeeded;
      void queryClient.invalidateQueries({ queryKey: [VERSIONS_QUERY] });
    }
    if (draft.jobId !== succeeded || urlJob !== succeeded) return;
    // 이 작업을 접수한 키만 지운다. 내용으로 찾아 지우면 같은 내용의 새 요청 키까지 사라져 다음 요청이 중복 학습이 된다.
    if (user && draft.requestFor) forgetJobRequestKey(user.id, JSON.parse(draft.requestFor), draft.jobKey);
    writeTrainDraft((current) => (current.submitted === draftFingerprint(current)
      ? freshDraft()
      : { ...current, jobId: null, jobKey: null, submitted: null, requestKey: null, requestFor: null }));
  }, [succeeded, urlJob, draft.jobId, draft.jobKey, draft.requestFor, queryClient, user]);

  const updatePeriod = (period: PeriodDraft) => writeTrainDraft((current) => ({ ...current, period }));
  const setParam = (key: string, value: string) => {
    if (!method) return;
    const modelKey = modelKeyOf(method);
    writeTrainDraft((current) => ({ ...current, params: { ...current.params, [modelKey]: { ...current.params[modelKey], [key]: value } } }));
  };
  const resetParams = () => {
    if (!method) return;
    writeTrainDraft((current) => {
      const { [modelKeyOf(method)]: _removed, ...params } = current.params;
      return { ...current, params };
    });
  };
  const request = async () => {
    if (!method || !capabilities.data || !user) return;
    const body = trainRequest({ sourceId, draft, method, timeBasis: capabilities.data.snapshot.timeBasis });
    // 같은 내용은 같은 키다: 응답을 놓친 화면이 다시 눌러도 서버가 같은 작업을 돌려준다(trainer-service.md). 새 키는 그 키의 작업이 실패 확정일 때만.
    // 초안의 작업이 그 키로 접수한 작업일 때만 판단한다. 주소에만 있는 옛 작업의 실패는 이 요청의 키와 무관하다.
    const failed = job.data?.status === "failed" && Boolean(draft.jobId) && draft.jobKey === draft.requestKey;
    let key = jobRequestKey(user.id, body, failed);
    const submitted = draftFingerprint(draft);
    writeTrainDraft((current) => ({ ...current, requestKey: key, requestFor: JSON.stringify(body), requestError: null }));
    setRequesting(true);
    try {
      let created = await createPipelineJob(body, key);
      if (created.status === "succeeded") {
        // 같은 키의 끝난 작업이 돌아왔다(서버 멱등). 새 학습이 아니므로 그 키를 버리고 한 번만 다시 보낸다. 새 키의 응답은 끝난 작업일 수 없다.
        const previous = key;
        if (readTrainDraft()?.requestKey !== previous) { forgetJobRequestKey(user.id, body, previous); return; }
        key = jobRequestKey(user.id, body, true);
        writeTrainDraft((current) => (current.requestKey === previous ? { ...current, requestKey: key } : current));
        created = await createPipelineJob(body, key);
      }
      const accepted = created.id, acceptedKey = key;
      writeTrainDraft((current) => (current.requestKey === acceptedKey ? { ...current, jobId: accepted, jobKey: acceptedKey, submitted } : current));
    } catch (error) {
      const failedKey = key, code = errorCode(error);
      writeTrainDraft((current) => (current.requestKey === failedKey ? { ...current, requestError: code } : current));
    } finally {
      setRequesting(false);
    }
  };

  const ready = capabilities.data && sources.data && sourceId;
  return (
    <div ref={area}>
      <SectionCard id={detectionSections.modelsTrain.id} title={detectionSections.modelsTrain.label}>
        {jobId && <TrainJobStatus jobId={jobId} returnTo={location.pathname + location.search} />}
        <StepLinks steps={STEPS} step={shownStep} maxStep={maxStep} hrefFor={hrefFor} />
        {(capabilities.isPending || sources.isPending) && <LoadingState>학습 조건 조회 중…</LoadingState>}
        {capabilities.isError && <ErrorState title={`방법 카탈로그 조회 실패 · ${errorCode(capabilities.error)}`} onRetry={() => void capabilities.refetch()} retryLabel="다시 조회" />}
        {sources.isError && <ErrorState title={`출처 조회 실패 · ${errorCode(sources.error)}`} onRetry={() => void sources.refetch()} retryLabel="다시 조회" />}
        {sources.data?.length === 0 && <EmptyState>저장된 데이터 출처가 없습니다.</EmptyState>}
        {ready && shownStep === 1 && (
          <>
            <PeriodStep period={draft.period} update={updatePeriod} sourceId={sourceId} timeBasis={capabilities.data!.snapshot.timeBasis} exclusionSupported={exclusionSupported} />
            <div className="flex justify-end">
              {draft.period.query ? <Button asChild><Link to={hrefFor(2)}>다음 · 입력 변수</Link></Button> : <Button disabled>다음 · 입력 변수</Button>}
            </div>
          </>
        )}
        {ready && shownStep === 2 && (
          <>
            <FeatureStep
              sourceId={sourceId}
              selected={draft.featureSetId}
              onSelect={(featureSetId) => writeTrainDraft((current) => ({ ...current, featureSetId }))}
              catalogVersion={capabilities.data!.methodCatalog.catalogVersion}
              periodText={periodLabel(draft.period)}
            />
            <div className="flex justify-between">
              <Button asChild variant="outline"><Link to={hrefFor(1)}>이전</Link></Button>
              {draft.featureSetId ? <Button asChild><Link to={hrefFor(3)}>다음 · 모델·파라미터</Link></Button> : <Button disabled>다음 · 모델·파라미터</Button>}
            </div>
          </>
        )}
        {ready && shownStep === 3 && (
          <>
            <ModelStep
              methods={methods}
              method={method}
              values={method ? draft.params[modelKeyOf(method)] : undefined}
              onSelect={(modelKey) => writeTrainDraft((current) => ({ ...current, modelKey }))}
              onChange={setParam}
              onReset={resetParams}
            />
            <div className="grid gap-1">
              <h3 className="text-sm font-medium">준비 검사</h3>
              <ul className="grid gap-1 text-sm">
                {issues.map((issue) => <li key={issue} className="text-warning-text">확인 필요 · {issue}</li>)}
                {issues.length === 0 && <li>통과 · {periodLabel(draft.period)} · 입력 변수 {numberText(featureSet?.featureNames.length ?? 0)}개 · {method?.displayName}</li>}
              </ul>
            </div>
            {draft.requestError && <Alert variant="destructive" data-train-error><AlertDescription>학습 요청 실패 · {draft.requestError}</AlertDescription></Alert>}
            <div className="flex justify-between">
              <Button asChild variant="outline"><Link to={hrefFor(2)}>이전</Link></Button>
              <Button disabled={issues.length > 0 || requesting || requestBlocked} onClick={() => void request()}>학습 요청</Button>
            </div>
          </>
        )}
      </SectionCard>
    </div>
  );
};
