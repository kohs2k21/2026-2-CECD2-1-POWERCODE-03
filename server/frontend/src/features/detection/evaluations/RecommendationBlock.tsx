import { Link, useLocation } from "react-router-dom";
import { paths } from "@/app/routes";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { stampText } from "@/features/operations/format";
import type { CompareVersion, Recommendation } from "./evaluationsApi";
import { labelOf } from "./evaluationFormat";

/** 모델 교체 추천 한 건(modelKey별). 문구는 와이어프레임, 사유 문장은 서버 `summary`. 자동 교체는 없고 수락해도 교체 화면으로 갈 뿐이다. */
export const RecommendationBlock = ({ rec, versions, where }: { rec: Recommendation; versions: CompareVersion[] | undefined; where: string }) => {
  const location = useLocation();
  const target = labelOf(versions, rec.targetVersionId);
  const generated = `${where} · 생성 ${stampText(rec.generatedAt)}`;
  const state = rec.recommendationState;
  if (state === "none") {
    return (
      <Alert role="status" data-rec-state="none">
        <AlertDescription><p><strong className="font-semibold text-foreground">모델 교체 추천 없음 · </strong><span className="text-foreground">{rec.summary}</span> · {generated}</p></AlertDescription>
      </Alert>
    );
  }
  const returnTo = location.pathname + location.search;
  if (state === "current") {
    return (
      <Alert role="region" aria-label="모델 교체 추천" data-rec-state="current">
        <AlertTitle>모델 교체 추천 {labelOf(versions, rec.baselineConfigVersionId)} → {target} · 시스템 추천 · 자동 교체 안 함</AlertTitle>
        <AlertDescription>
          <p className="text-foreground">{rec.summary}</p>
          <p className="text-muted-foreground">{generated}</p>
          <Button asChild size="sm" className="mt-2"><Link to={paths.detectionReplace} state={{ returnTo, acceptedRecommendation: { comparisonJobId: rec.comparisonJobId, modelKey: rec.modelKey, targetVersionId: rec.targetVersionId } }}>수락하고 교체</Link></Button>
        </AlertDescription>
      </Alert>
    );
  }
  if (state === "applied") {
    return (
      <Alert role="region" aria-label="모델 교체 추천" data-rec-state="applied">
        <AlertTitle>모델 교체 추천 {target} · 이미 적용됨</AlertTitle>
        <AlertDescription>
          <p className="text-foreground">이 추천은 적용되었습니다.</p>
          <p className="text-muted-foreground">{rec.summary} · {generated}</p>
        </AlertDescription>
      </Alert>
    );
  }
  return (
    <Alert role="region" aria-label="모델 교체 추천" data-rec-state="stale">
      <AlertTitle>모델 교체 추천 {target} · 지난 추천</AlertTitle>
      <AlertDescription>
        <p className="text-foreground">운영 모델이 바뀌어 더 이상 유효하지 않은 추천입니다. 새 비교 평가를 실행하세요.</p>
        <p className="text-muted-foreground">{rec.summary} · {generated}</p>
        <div className="mt-2 flex gap-2">
          <Button size="sm" disabled>수락하고 교체</Button>
          <Button asChild size="sm" variant="outline"><Link to={`${paths.detectionEvaluations}?step=1`}>+ 새 비교 평가</Link></Button>
        </div>
      </AlertDescription>
    </Alert>
  );
};
