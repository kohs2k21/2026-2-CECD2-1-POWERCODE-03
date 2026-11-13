import { useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { paths, withSearchParam, workStepCount } from "@/app/routes";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StepLinks } from "@/features/detection/components/StepLinks";
import { MatrixCard } from "@/features/detection/evaluations/EvaluationDetail";
import type { ComparisonDetail, ComparisonItem } from "@/features/detection/evaluations/evaluationsApi";
import { rateText, ruleConditionText } from "@/features/detection/evaluations/evaluationFormat";
import { useCompareVersions, useEvaluationSet } from "@/features/detection/evaluations/useEvaluations";
import { methodName, parameterSummary, periodText, thresholdSummary, versionLabel } from "@/features/detection/models/VersionList";
import type { CatalogMethod, ModelVersion } from "@/features/detection/models/modelsApi";
import { modelKeyOf } from "@/features/detection/models/trainDraft";
import { useCapabilities, useVersions } from "@/features/detection/models/useModels";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { DASH, msText, numberText, stampText } from "@/features/operations/format";
import { useUrlStep } from "@/hooks/useUrlState";
import { cn } from "@/lib/utils";
import { HttpError } from "@/services/api/client";
import { useDraftStore } from "@/stores/draftStore";
import { ActiveSetBannerView, changedBanner, failedBanner, type ActiveSetBanner } from "../operations/activeSetResult";
import { requestReplace, type AcceptedRecommendation, type ActiveModel } from "../operations/operationsApi";
import { evidenceFor, useOperationalState, useRecentComparisons } from "../operations/useOperations";
import { ACTIVE_SET_QUERIES, type OperationsState } from "./OperationsSettingsPage";

const STEPS = ["버전 선택", "비교 확인", "승인·요청"];
const ML_TERM = "머신러닝 모델";
type ReplaceDraft = { target: string | null; accepted: AcceptedRecommendation | null };
type Incoming = { targetVersionId?: string; acceptedRecommendation?: AcceptedRecommendation; returnTo?: string };
const EMPTY: ReplaceDraft = { target: null, accepted: null };

/** 교체 가능: 실행 가능·지금 운영 중 아님·운영 적용 gate 평가 있음(model-version.md applicationEligibility). */
const eligible = (version: ModelVersion) => version.executable && !version.active && version.applicationEligibility?.eligible === true;
const measuredItem = (detail: ComparisonDetail, id: string): ComparisonItem | undefined =>
  detail.items?.find((item) => item.configVersionId === id && item.status === "succeeded" && item.categoryResults?.ml.measurementStatus === "measured");
const label = (id: string | null | undefined, versions: ModelVersion[], active: ActiveModel[] = []) => {
  if (!id) return DASH;
  const found = versions.find((version) => version.id === id) ?? active.map((model) => ({ id: model.configVersionId, displayName: model.displayName })).find((model) => model.id === id);
  return versionLabel(found ?? { id, displayName: null });
};

const Changed = ({ values }: { values: ReactNode[] }) => {
  const changed = values.length === 2 && String(values[0]) !== String(values[1]);
  return <>{values.map((value, index) => <TableCell key={index} className={cn("whitespace-normal", changed && "bg-warning-soft")} data-changed={changed || undefined}>{value}</TableCell>)}</>;
};

/** 2단계 비교 확인(와이어프레임 replaceCompare): 모델 정보·혼동행렬·지표. 같은 평가 세트 결과만 쓰고 바뀐 칸을 강조한다. */
const Evidence = ({ detail, ids, base, versions, methods }: { detail: ComparisonDetail; ids: string[]; base: string | null; versions: ModelVersion[]; methods: CatalogMethod[] | undefined }) => {
  const compareVersions = useCompareVersions();
  const set = useEvaluationSet(detail.evaluationSet?.id);
  const head = ids.map((id) => `${id === base ? "현재" : "선택"} ${label(id, versions)}`);
  const version = (id: string) => versions.find((item) => item.id === id);
  const method = (id: string) => methods?.find((item) => modelKeyOf(item) === version(id)?.modelKey);
  const info: [string, (id: string) => ReactNode][] = [
    ["모델", (id) => methodName(methods, version(id)?.modelKey ?? null)],
    ["버전 이름", (id) => version(id)?.displayName ?? DASH],
    ["생성", (id) => stampText(version(id)?.createdAt)],
    ["학습 기간", (id) => periodText(version(id)?.trainingSelection.trainingPeriod ?? null)],
    ["입력 변수", (id) => (version(id)?.modelSummary?.featureCount == null ? "없음" : `${numberText(version(id)!.modelSummary!.featureCount!)}개`)],
    ["학습 파라미터", (id) => parameterSummary(method(id), version(id)?.modelSummary?.parameters)],
    ["판정선", (id) => thresholdSummary(version(id)?.modelSummary?.thresholdValue)],
  ];
  const ml = (id: string) => measuredItem(detail, id)?.categoryResults?.ml;
  const cost = (id: string) => measuredItem(detail, id)?.evaluationEnvelope?.execution_cost.metrics?.itemP95Ms?.value;
  const metrics: [string, (id: string) => ReactNode][] = [
    ["재현율(완료 기준)", (id) => rateText(ml(id)?.completeOnly.recall)],
    ["정밀도(완료 기준)", (id) => rateText(ml(id)?.completeOnly.precision)],
    ["오탐률(완료 기준)", (id) => rateText(ml(id)?.completeOnly.falsePositiveRate)],
    ["제외 행(완료 기준)", (id) => (ml(id) ? `${numberText(ml(id)!.unavailableCount)}행` : DASH)],
    ["처리 시간 p95", (id) => (typeof cost(id) === "number" ? msText(cost(id) as number) : DASH)],
  ];
  const table = (rows: [string, (id: string) => ReactNode][], name: string) => (
    <div className="overflow-x-auto">
      <Table aria-label={name}>
        <TableHeader><TableRow><TableHead>항목</TableHead>{head.map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow></TableHeader>
        <TableBody>{rows.map(([rowLabel, read]) => <TableRow key={rowLabel}><TableCell>{rowLabel}</TableCell><Changed values={ids.map(read)} /></TableRow>)}</TableBody>
      </Table>
    </div>
  );
  return (
    <div className="grid gap-4">
      <p className="text-sm">
        평가 세트 {set.data?.displayName ?? DASH} · {detail.comparisonId.slice(0, 8)} · {ruleConditionText(detail.ruleSet?.revision)}
        {base ? " · 바뀐 칸 강조" : " · 같은 종류의 운영 모델 없음(추가 운영)"}
      </p>
      <h3 className="text-sm font-semibold">{ML_TERM} 정보</h3>
      {table(info, `${ML_TERM} 정보`)}
      <h3 className="text-sm font-semibold">혼동행렬</h3>
      <div className="grid gap-4 lg:grid-cols-2">
        {ids.map((id) => { const item = measuredItem(detail, id); return item ? <MatrixCard key={id} item={item} context={{ versions: compareVersions.data, set: set.data }} showReplace={false} /> : null; })}
      </div>
      <h3 className="text-sm font-semibold">지표</h3>
      {table(metrics, "지표")}
    </div>
  );
};

const Approve = ({ disabled, onRequest, back }: { disabled: boolean; onRequest: () => void; back: ReactNode }) => {
  const [checked, setChecked] = useState(false);
  return (
    <>
      <div className="flex items-start gap-2">
        <Checkbox id="replace-approve" checked={checked} onCheckedChange={(value) => setChecked(value === true)} />
        <Label htmlFor="replace-approve">모델 차이와 평가 결과를 확인했고 교체를 승인합니다</Label>
      </div>
      <p className="text-sm">실패하면 기존 모델을 유지합니다.</p>
      <div className="flex justify-between gap-2" data-actions>
        {back}
        <Button disabled={disabled || !checked} onClick={onRequest}>모델 교체</Button>
      </div>
    </>
  );
};

/**
 * 모델 교체(전체 페이지 3단계, 와이어프레임 cmd-replace): 1 버전 선택 → 2 비교 확인 → 3 승인·요청. 같은 종류의 운영 모델을 교체하고
 * 없으면 추가 운영한다. 룰은 바뀌지 않는다. 선택은 화면 초안(새로고침하면 1단계), 승인은 단계를 떠나면 다시 확인한다.
 */
export const ReplacePage = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { value: step, hrefFor } = useUrlStep(workStepCount.replace);
  const state = useOperationalState();
  const versions = useVersions("all");
  const capabilities = useCapabilities();
  const comparisons = useRecentComparisons();
  const stored = useDraftStore((store) => store.drafts[paths.detectionReplace]?.value as ReplaceDraft | undefined);
  const draft = stored ?? EMPTY;
  const [banner, setBanner] = useState<ActiveSetBanner | null>(null);
  const [stale, setStale] = useState(false);
  const [busy, setBusy] = useState(false);
  const methods = capabilities.data?.methodCatalog.methods;
  const setDraft = (next: ReplaceDraft) => useDraftStore.getState().edit(paths.detectionReplace, next, { leaveFree: true });

  // 넘겨받은 선택(추천 수락·평가 상세 '이 모델로 교체')은 진입 때 한 번 초안에 넣는다.
  const incoming = location.state as Incoming | null;
  useEffect(() => {
    const target = incoming?.acceptedRecommendation?.targetVersionId ?? incoming?.targetVersionId;
    if (!target) return;
    setDraft({ target, accepted: incoming?.acceptedRecommendation ?? null });
    navigate(location.pathname + location.search, { replace: true, state: incoming?.returnTo ? { returnTo: incoming.returnTo } : null });
  }, [incoming, navigate, location.pathname, location.search]);
  // 떠날 때 선택을 지운다(재진입 시 다시 고른다).
  useEffect(() => () => useDraftStore.getState().discard(paths.detectionReplace), []);

  const active = state.data?.activeModelSet.models ?? [];
  const items = versions.items;
  const target = items.find((version) => version.id === draft.target) ?? null;
  const targetUsable = target !== null && eligible(target);
  const base = target ? (active.find((model) => model.modelKey === target.modelKey)?.configVersionId ?? null) : null;
  const evidence = targetUsable ? evidenceFor(comparisons.data, base, target!.id) : null;
  const accepted = draft.accepted && draft.accepted.targetVersionId === draft.target ? draft.accepted : null;
  const acceptedRec = accepted ? comparisons.data?.flatMap((detail) => detail.replacementRecommendations ?? []).find((rec) => rec.comparisonJobId === accepted.comparisonJobId && rec.modelKey === accepted.modelKey) : undefined;
  const acceptedRun = accepted ? comparisons.data?.find((detail) => detail.jobId === accepted.comparisonJobId) : undefined;
  const evaluationId = accepted ? (acceptedRec?.targetEvaluationId ?? null) : (target?.applicationEligibility?.evaluationId ?? null);
  const maxStep = !targetUsable ? 1 : evidence ? 3 : 2;
  const ready = !state.isPending && !versions.first.isPending && !comparisons.isPending;

  // 앞 단계가 끝나지 않은 단계 주소는 가까운 유효 단계로(navigation.md). 조회가 끝난 뒤에만 판단한다.
  useEffect(() => {
    if (ready && step > maxStep) navigate({ pathname: location.pathname, search: withSearchParam(location.search, "step", String(maxStep)) }, { replace: true, state: location.state });
  }, [ready, step, maxStep, navigate, location.pathname, location.search, location.state]);

  const submit = async () => {
    if (!target || !state.data || !evaluationId) return;
    setBusy(true);
    setStale(false);
    setBanner(null);
    try {
      const result = await requestReplace(target.id, { evaluationId, expectedRevision: state.data.activeModelSet.revision, baseline: base, acceptedRecommendation: accepted });
      // 먼저 떠난다(선택 초안은 화면을 떠날 때 지운다). 초안을 먼저 지우면 단계 정규화가 이 화면 주소로 되돌린다.
      navigate(paths.detectionOperations, { state: { activeSetBanner: changedBanner(result, active) } satisfies OperationsState });
      await Promise.all(ACTIVE_SET_QUERIES.map((name) => queryClient.invalidateQueries({ queryKey: [name] })));
    } catch (error) {
      if (error instanceof HttpError && error.code === "accepted_recommendation_stale") setStale(true);
      else setBanner(failedBanner("replace", error, active));
      await Promise.all(ACTIVE_SET_QUERIES.map((name) => queryClient.invalidateQueries({ queryKey: [name] })));
    } finally {
      setBusy(false);
    }
  };

  const recall = (id: string) => {
    const detail = comparisons.data?.find((item) => measuredItem(item, id));
    const ml = detail && measuredItem(detail, id)?.categoryResults?.ml;
    return detail && ml ? <Badge>{detail.comparisonId.slice(0, 8)} · {rateText(ml.completeOnly.recall)}</Badge> : DASH;
  };
  const activeText = active.length ? active.map((model) => `${label(model.configVersionId, items, active)} · ${methodName(methods, model.modelKey)}`).join(", ") : "없음";

  return (
    <div className="grid gap-6">
      <ScreenTitle>모델 교체</ScreenTitle>
      <StepLinks steps={STEPS} step={step} maxStep={maxStep} hrefFor={hrefFor} />
      {accepted && <Alert role="status"><AlertDescription>모델 교체 추천 수락 · {(acceptedRun?.comparisonId ?? accepted.comparisonJobId).slice(0, 8)} 추천 {label(accepted.targetVersionId, items)}</AlertDescription></Alert>}
      {stale && (
        <Alert variant="destructive" role="alert" data-banner="stale">
          <AlertDescription>교체 거절 · 400 accepted_recommendation_stale · 운영 모델이 바뀌어 더 이상 유효하지 않은 추천입니다. 새 비교 평가를 실행하세요.</AlertDescription>
        </Alert>
      )}
      {banner && <ActiveSetBannerView banner={banner} />}
      {state.isError && <ErrorState title={`운영 상태 조회 실패 · ${errorCode(state.error)}`} onRetry={() => void state.refetch()} retryLabel="다시 조회" />}

      {step === 1 && (
        <SectionCard title="교체할 모델 버전">
          <p className="text-sm">현재 운영: <strong className="font-semibold">{state.data ? activeText : DASH}</strong></p>
          {versions.first.isPending && <LoadingState>버전 조회 중…</LoadingState>}
          {versions.first.isError && <ErrorState title={`버전 조회 실패 · ${errorCode(versions.first.error)}`} onRetry={() => void versions.first.refetch()} retryLabel="다시 조회" />}
          {versions.first.data && items.length === 0 && <EmptyState>학습된 버전이 없습니다.</EmptyState>}
          {items.length > 0 && (
            <RadioGroup value={draft.target ?? ""} onValueChange={(value) => setDraft({ target: value, accepted: draft.accepted })} aria-label="교체할 모델 버전">
              <div className="max-h-96 overflow-auto" tabIndex={0}>
                <Table>
                  <TableHeader><TableRow>{["선택", "버전", "모델", "운영 변화", "최근 평가", "재현율(완료 기준)"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow></TableHeader>
                  <TableBody>
                    {items.map((version) => {
                      const ok = eligible(version);
                      const slot = active.find((model) => model.modelKey === version.modelKey)?.configVersionId;
                      return (
                        <TableRow key={version.id} className={cn(!ok && "text-muted-foreground", version.id === draft.target && "bg-accent")} data-version={version.id}
                          title={ok ? undefined : (version.applicationEligibility?.reason ?? (version.active ? undefined : version.executable ? undefined : "not_executable"))}>
                          <TableCell><RadioGroupItem value={version.id} disabled={!ok} aria-label={`${versionLabel(version)} 선택`} /></TableCell>
                          <TableCell>{versionLabel(version)}</TableCell>
                          <TableCell>{methodName(methods, version.modelKey)}</TableCell>
                          <TableCell>{version.active ? <Badge variant="success">운영 중</Badge> : slot ? `${label(slot, items, active)} → ${versionLabel(version)}` : "추가 운영"}</TableCell>
                          <TableCell>{version.latestEvaluation ? <Badge variant="success">평가 완료 · {stampText(version.latestEvaluation.completedAt)}</Badge> : <Badge>평가 전</Badge>}</TableCell>
                          <TableCell>{recall(version.id)}</TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            </RadioGroup>
          )}
          {versions.cursor && <div><Button size="sm" variant="outline" disabled={versions.loading} onClick={() => void versions.loadMore()}>더 보기</Button></div>}
          <div className="flex justify-end">
            {targetUsable ? <Button asChild><Link to={hrefFor(2)}>다음 · 비교 확인</Link></Button> : <Button disabled>다음 · 비교 확인</Button>}
          </div>
        </SectionCard>
      )}

      {step === 2 && (
        <SectionCard title="비교 확인">
          {!targetUsable && <p className="text-sm">버전을 먼저 선택해 주세요. <Link className="text-link underline-offset-2 hover:underline" to={hrefFor(1)}>버전 선택</Link></p>}
          {targetUsable && comparisons.isPending && <LoadingState>비교 결과 조회 중…</LoadingState>}
          {targetUsable && comparisons.isError && (
            <ErrorState
              title={comparisons.error instanceof HttpError && (comparisons.error.status === 404 || comparisons.error.status === 503) ? `비교 결과 미연결 · ${errorCode(comparisons.error)}` : `비교 결과 조회 실패 · ${errorCode(comparisons.error)}`}
              onRetry={() => void comparisons.refetch()} retryLabel="다시 조회"
            />
          )}
          {targetUsable && comparisons.data && !evidence && (
            <Alert role="status">
              <AlertDescription>
                <p>현재 운영 모델과 같은 평가 세트로 비교한 결과가 없습니다. <Link className="text-link underline-offset-2 hover:underline" to={`${paths.detectionEvaluations}?step=1`}>+ 새 비교 평가</Link></p>
              </AlertDescription>
            </Alert>
          )}
          {evidence && target && <Evidence detail={evidence} ids={base ? [base, target.id] : [target.id]} base={base} versions={items} methods={methods} />}
          <div className="flex justify-between">
            <Button asChild variant="outline"><Link to={hrefFor(1)}>이전</Link></Button>
            {evidence ? <Button asChild><Link to={hrefFor(3)}>다음 · 승인·요청</Link></Button> : <Button disabled>다음 · 승인·요청</Button>}
          </div>
        </SectionCard>
      )}

      {step === 3 && target && evidence && (
        <SectionCard title="승인·요청">
          <DescriptionList
            items={[
              { label: "현재 운영 모델", value: base ? label(base, items, active) : "없음 · 추가 운영" },
              { label: "교체할 버전", value: versionLabel(target) },
              { label: "평가 근거", value: `${evidence.comparisonId.slice(0, 8)} · ${ruleConditionText(evidence.ruleSet?.revision)}` },
              { label: "추천 수락", value: accepted ? `${(acceptedRun?.comparisonId ?? accepted.comparisonJobId).slice(0, 8)} 추천 ${label(accepted.targetVersionId, items)}` : "없음 · 직접 선택" },
              { label: "룰", value: state.data ? `바뀌지 않음 · 운영 룰 버전 ${state.data.ruleSet.revision}` : DASH },
            ]}
          />
          {!evaluationId && <p className="text-sm text-destructive-text">운영 적용 평가 없음 · {target.applicationEligibility?.reason ?? DASH}</p>}
          <Approve key={`${target.id}:${accepted?.comparisonJobId ?? ""}`} disabled={busy || !evaluationId || !state.data} onRequest={() => void submit()}
            back={<Button asChild variant="outline"><Link to={hrefFor(2)}>이전</Link></Button>} />
        </SectionCard>
      )}
    </div>
  );
};
