import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { paths } from "@/app/routes";
import { RecommendationBlock, RecommendationUnconnected } from "@/features/detection/evaluations/RecommendationBlock";
import { comparisonKey } from "@/features/detection/evaluations/evaluationsApi";
import { ruleConditionText } from "@/features/detection/evaluations/evaluationFormat";
import { useCompareVersions } from "@/features/detection/evaluations/useEvaluations";
import { VERSIONS_QUERY, useCapabilities } from "@/features/detection/models/useModels";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { useRuleSet, useSoftRuleFields } from "@/features/detection/rules/useRuleSet";
import { HttpError } from "@/services/api/client";
import type { DetectionReturnState } from "../DetectionLayout";
import { ActiveHistoryCard, RestoreDialog, RetireDialog, restoreCandidates } from "../operations/ActiveSetDialogs";
import { ActiveSetBannerView, changedBanner, failedBanner, type ActiveSetBanner } from "../operations/activeSetResult";
import { OperationsOverview } from "../operations/OperationsOverview";
import { requestRestore, requestRetire, type ActiveHistoryItem, type ActiveModel } from "../operations/operationsApi";
import { ACTIVE_HISTORY_QUERY, METHOD_STATUS_QUERY, OPERATIONAL_STATE_QUERY, useActiveHistory, useOperationalState, useRecentComparisons } from "../operations/useOperations";

/** 운영 모델이 바뀐 뒤 다시 읽을 조회(운영 상태·작동 상태·이력·모델 버전 운영 표시). */
export const ACTIVE_SET_QUERIES = [OPERATIONAL_STATE_QUERY, METHOD_STATUS_QUERY, ACTIVE_HISTORY_QUERY, VERSIONS_QUERY, "model-version", "recent-comparisons"];
export type OperationsState = DetectionReturnState & { activeSetBanner?: ActiveSetBanner };

/** 교체 추천 카드(와이어프레임 opsRec): 가장 최근 완료 비교 평가의 modelKey별 추천. 비교 평가 조회가 연결되지 않았으면 미연결로 보인다. */
const Recommendations = () => {
  const recent = useRecentComparisons();
  const versions = useCompareVersions();
  if (recent.isPending) return <LoadingState>모델 교체 추천 조회 중…</LoadingState>;
  if (recent.isError) {
    const unconnected = recent.error instanceof HttpError && (recent.error.status === 404 || recent.error.status === 503);
    return <ErrorState title={unconnected ? `모델 교체 추천 미연결 · ${errorCode(recent.error)}` : `모델 교체 추천 조회 실패 · ${errorCode(recent.error)}`} onRetry={() => void recent.refetch()} retryLabel="다시 조회" />;
  }
  const latest = recent.data?.find((detail) => detail.replacementRecommendations?.length);
  if (!latest) {
    // 최근 완료 비교가 추천을 만들지 않았으면(v1 `not_implemented_v1`) 미연결로 보인다. 비교가 없으면 아무것도 없다.
    const reason = recent.data?.find((detail) => !detail.replacementRecommendations && detail.replacementRecommendationsReason)?.replacementRecommendationsReason;
    return reason ? <RecommendationUnconnected reason={reason} /> : null;
  }
  const where = `${comparisonKey(latest).slice(0, 8)} · ${ruleConditionText(latest.ruleSet?.revision)}`;
  return <div className="grid gap-2">{latest.replacementRecommendations!.map((rec) => <RecommendationBlock key={rec.modelKey} rec={rec} versions={versions.data} where={where} />)}</div>;
};

/**
 * 탐지 설정 › 운영 설정(사용자 9.6 운영 버전 overview): 지금 운영 중인 룰·모델과 방법별 작동 상태, 그 위 명령(모델 교체·복원·운영에서 제외).
 * 모델 변경은 활성 모델 집합 API(model-version.md)로 바로 적용된다. 룰은 룰 설정에서 바꾼다. 판정 정책은 보이지 않는다(Q-M10).
 */
export const OperationsSettingsPage = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const state = useOperationalState();
  const rules = useRuleSet();
  const fields = useSoftRuleFields();
  const capabilities = useCapabilities();
  const history = useActiveHistory(state.data?.activeModelSet.revision);
  const passed = (location.state as OperationsState | null)?.activeSetBanner ?? null;
  const [banner, setBanner] = useState<ActiveSetBanner | null>(passed);
  const [retiring, setRetiring] = useState<ActiveModel | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [busy, setBusy] = useState(false);
  const methods = capabilities.data?.methodCatalog.methods;
  const returnState = { returnTo: location.pathname + location.search } satisfies DetectionReturnState;

  // 넘겨받은 안내는 한 번만 보인다(새로고침·뒤로 가기에서 다시 뜨지 않게 주소 state를 비운다).
  useEffect(() => {
    if (passed) navigate(location.pathname + location.search, { replace: true, state: null });
  }, [passed, navigate, location.pathname, location.search]);

  const refresh = () => Promise.all(ACTIVE_SET_QUERIES.map((name) => queryClient.invalidateQueries({ queryKey: [name] })));
  const run = async (operation: "retire" | "restore", call: (revision: number) => ReturnType<typeof requestRetire>) => {
    const current = state.data!.activeModelSet;
    setBusy(true);
    try {
      setBanner(changedBanner(await call(current.revision), current.models));
    } catch (error) {
      setBanner(failedBanner(operation, error, current.models));
    } finally {
      setBusy(false);
      setRetiring(null);
      setRestoring(false);
      await refresh();
    }
  };
  const retire = (model: ActiveModel) => run("retire", (revision) => requestRetire(model.configVersionId, revision));
  const restore = (item: ActiveHistoryItem) => run("restore", (revision) => requestRestore(item.revision, revision, true));

  const loading = state.isPending || rules.isPending;
  const failed = state.isError ? `운영 상태 조회 실패 · ${errorCode(state.error)}` : rules.isError ? `현재 운영 룰 조회 실패 · ${errorCode(rules.error)}` : null;
  return (
    <div className="grid gap-6">
      <ScreenTitle>운영 설정</ScreenTitle>
      {banner && <ActiveSetBannerView banner={banner} />}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-section-title">운영</h2>
        <div className="flex flex-wrap gap-2">
          <Button asChild><Link to={paths.detectionReplace} state={returnState}>모델 교체</Link></Button>
          <Button variant="outline" disabled={!state.data || !history.first.data} onClick={() => setRestoring(true)}>복원</Button>
        </div>
      </div>
      {loading && <LoadingState>운영 상태 조회 중…</LoadingState>}
      {failed && <ErrorState title={failed} onRetry={() => { void state.refetch(); void rules.refetch(); }} retryLabel="다시 조회" />}
      {state.data && rules.data && (
        <OperationsOverview state={state.data} rules={rules.data} fields={fields.data ?? []} methods={methods} onRetire={setRetiring} />
      )}
      <Recommendations />
      <ActiveHistoryCard history={history} methods={methods} />
      <div role="separator" aria-label="운영과 별도로 실행" className="flex items-center gap-3 text-xs text-muted-foreground">
        <span aria-hidden="true" className="h-px flex-1 bg-border" />운영과 별도로 실행<span aria-hidden="true" className="h-px flex-1 bg-border" />
      </div>
      <SectionCard title="작업" description="학습·평가·비교 평가·AI 규칙 추천·규칙 추천 작업의 진행과 실패 사유"
        action={<Button asChild size="sm" variant="outline"><Link to={paths.detectionJobs} state={returnState}>작업 목록</Link></Button>} />
      {retiring && state.data && (
        <RetireDialog model={retiring} methods={methods} last={state.data.activeModelSet.models.length === 1} busy={busy}
          onCancel={() => setRetiring(null)} onConfirm={() => void retire(retiring)} />
      )}
      {restoring && state.data && (
        <RestoreDialog candidates={restoreCandidates(history.items, state.data.activeModelSet)} current={state.data.activeModelSet.models} methods={methods} busy={busy}
          onCancel={() => setRestoring(false)} onConfirm={(item) => void restore(item)} />
      )}
    </div>
  );
};
