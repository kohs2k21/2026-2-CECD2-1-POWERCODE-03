import { Link, useLocation } from "react-router-dom";
import { paths } from "@/app/routes";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { msText, stampText } from "@/features/operations/format";
import type { CompareVersion, Recommendation } from "./evaluationsApi";
import { decisionLabel, isReplaceRecommended, labelOf } from "./evaluationFormat";

/** 교체 추천을 만들지 않는 비교(v1 `not_implemented_v1`): 빈 추천을 성공처럼 보이지 않고 미연결과 사유 코드를 그대로 보인다. */
export const RecommendationUnconnected = ({ reason }: { reason: string }) => (
  <Alert role="status" data-rec-state="unconnected">
    <AlertDescription><p>모델 교체 추천 미연결 · {reason}</p></AlertDescription>
  </Alert>
);

/**
 * 비교 모델별 결과(`candidates[]`, 11/15~): 제외 후보는 서버 사유 코드(`excludedReason`), 그 밖은 `outcome` 코드 그대로. 계약에 사유 표시 이름이 없어 코드를 옮긴다.
 * 예전 결과(키 없음)·후보 없음은 그리지 않는다.
 */
const Candidates = ({ rec, versions }: { rec: Recommendation; versions: CompareVersion[] | undefined }) =>
  rec.candidates?.length ? (
    <ul className="mt-1 grid gap-0.5 text-muted-foreground" data-rec-candidates>
      {rec.candidates.map((candidate) => (
        <li key={candidate.configVersionId} data-outcome={candidate.outcome}>비교 모델 {labelOf(versions, candidate.configVersionId)} · {candidate.excludedReason ?? candidate.outcome}</li>
      ))}
    </ul>
  ) : null;

/** 추천 대상의 실행 비용 값만(한도 비교 결과는 summary의 서버 문장에 있다). 값이 없으면 미측정. */
const Costs = ({ rec }: { rec: Recommendation }) =>
  rec.costs?.length ? (
    <>
      {rec.costs.map((cost) => (
        <p key={cost.metric} className="text-muted-foreground" data-rec-cost={cost.outcome}>
          행당 처리 시간 P95 {cost.target === null || cost.target === undefined ? "미측정" : msText(cost.target)}
        </p>
      ))}
    </>
  ) : null;

/**
 * 모델 교체 추천 한 건(modelKey별). 문구는 와이어프레임, 사유 문장은 서버 `summary`(화면에서 다시 만들지 않음). 분류(`decision`)가 있으면 그 표시 이름을 보인다.
 * 수락은 추천이 있을 때(`recommend_replace`, 예전 모양은 대상 있음)만이고, 자동 교체는 없으며 수락해도 교체 화면으로 갈 뿐이다.
 */
export const RecommendationBlock = ({ rec, versions, where }: { rec: Recommendation; versions: CompareVersion[] | undefined; where: string }) => {
  const location = useLocation();
  const target = labelOf(versions, rec.targetVersionId);
  const generated = `${where} · 생성 ${stampText(rec.generatedAt)}`;
  const state = rec.recommendationState;
  const decision = rec.decision ?? undefined;
  if (state === "none" || !isReplaceRecommended(rec)) {
    return (
      <Alert role="status" data-rec-state="none" data-decision={decision}>
        <AlertDescription>
          <p><strong className="font-semibold text-foreground">{decision ? `모델 교체 추천 · ${decisionLabel(decision)} · ` : "모델 교체 추천 없음 · "}</strong><span className="text-foreground">{rec.summary}</span> · {generated}</p>
          <Candidates rec={rec} versions={versions} />
        </AlertDescription>
      </Alert>
    );
  }
  const returnTo = location.pathname + location.search;
  if (state === "current") {
    return (
      <Alert role="region" aria-label="모델 교체 추천" data-rec-state="current" data-decision={decision}>
        <AlertTitle>모델 교체 추천 {labelOf(versions, rec.baselineConfigVersionId)} → {target} · 시스템 추천 · 자동 교체 안 함</AlertTitle>
        <AlertDescription>
          <p className="text-foreground">{rec.summary}</p>
          <Costs rec={rec} />
          <p className="text-muted-foreground">{generated}</p>
          <Candidates rec={rec} versions={versions} />
          <Button asChild size="sm" className="mt-2"><Link to={paths.detectionReplace} state={{ returnTo, acceptedRecommendation: { comparisonJobId: rec.comparisonJobId, modelKey: rec.modelKey, targetVersionId: rec.targetVersionId } }}>수락하고 교체</Link></Button>
        </AlertDescription>
      </Alert>
    );
  }
  if (state === "applied") {
    return (
      <Alert role="region" aria-label="모델 교체 추천" data-rec-state="applied" data-decision={decision}>
        <AlertTitle>모델 교체 추천 {target} · 이미 적용됨</AlertTitle>
        <AlertDescription>
          <p className="text-foreground">이 추천은 적용되었습니다.</p>
          <p className="text-muted-foreground">{rec.summary} · {generated}</p>
          <Costs rec={rec} />
          <Candidates rec={rec} versions={versions} />
        </AlertDescription>
      </Alert>
    );
  }
  return (
    <Alert role="region" aria-label="모델 교체 추천" data-rec-state="stale" data-decision={decision}>
      <AlertTitle>모델 교체 추천 {target} · 지난 추천</AlertTitle>
      <AlertDescription>
        <p className="text-foreground">운영 모델이 바뀌어 더 이상 유효하지 않은 추천입니다. 새 비교 평가를 실행하세요.</p>
        <p className="text-muted-foreground">{rec.summary} · {generated}</p>
        <Costs rec={rec} />
        <Candidates rec={rec} versions={versions} />
        <div className="mt-2 flex gap-2">
          <Button size="sm" disabled>수락하고 교체</Button>
          <Button asChild size="sm" variant="outline"><Link to={`${paths.detectionEvaluations}?step=1`}>+ 새 비교 평가</Link></Button>
        </div>
      </AlertDescription>
    </Alert>
  );
};
