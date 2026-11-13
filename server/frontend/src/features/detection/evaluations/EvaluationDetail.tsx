import type { ReactNode } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { paths } from "@/app/routes";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { DASH, bytesText, msText, numberText } from "@/features/operations/format";
import { comparisonKey, type CompareVersion, type ComparisonDetail, type ComparisonItem, type EvaluationSet, type PairwiseAgreement, type Rate } from "./evaluationsApi";
import {
  comparisonStatus, costMetricText, countText, excludedText, fixedInfoText, labelOf, policyText, rateText, ruleConditionText, setKindLabel, setNote, setUnitLabel, truthRowLabels, valueOrReason,
} from "./evaluationFormat";
import { RecommendationBlock, RecommendationUnconnected } from "./RecommendationBlock";
import { useCompareVersions, useComparison, useEvaluationSet } from "./useEvaluations";

const FAILED_CELL = "실패 · 결과 없음";
const UNMEASURED_CELL = "미측정";

/** 판정 가능 기준 값은 서버가 줄 때만 붙인다(v1 비교 결과에는 없다). */
const decidable = (rate: Rate | undefined) => (rate ? ` · ${rateText(rate)} 판정 가능 기준` : "");
const NORMAL_LABELS_UNAVAILABLE = "verified_normal_labels_unavailable";

export type MatrixContext = { versions: CompareVersion[] | undefined; set: EvaluationSet | undefined };
type Context = MatrixContext;

const roleLabel = (item: ComparisonItem, versions: CompareVersion[] | undefined) => {
  if (item.role === "candidate") return "비교 모델";
  const active = versions?.find((version) => version.id === item.configVersionId)?.active;
  return active === undefined ? "기준" : active ? "현재 운영" : "평가 당시 운영";
};

/** 표 칸 하나: 실패 항목과 측정 없는 항목은 지표 대신 상태를 쓴다. */
const cellOf = (item: ComparisonItem, read: (ml: NonNullable<ComparisonItem["categoryResults"]>["ml"]) => ReactNode) => {
  if (item.status === "failed") return FAILED_CELL;
  const ml = item.categoryResults?.ml;
  return ml && ml.measurementStatus === "measured" ? read(ml) : UNMEASURED_CELL;
};
const compositeUnavailable = (item: ComparisonItem) => `${UNMEASURED_CELL} · ${item.reasonCodes?.find((code) => code.startsWith("composite_unavailable:")) ?? item.evaluationEnvelope?.rule_ml.reason ?? DASH}`;
const withComposite = (item: ComparisonItem, read: (composite: NonNullable<ComparisonItem["composite"]>) => ReactNode) => {
  if (item.status === "failed") return FAILED_CELL;
  return item.composite ? read(item.composite) : compositeUnavailable(item);
};
const ruleMlText = (item: ComparisonItem) => {
  if (item.status === "failed") return FAILED_CELL;
  const family = item.evaluationEnvelope?.rule_ml;
  if (!family) return UNMEASURED_CELL;
  if (family.status !== "measured" || !family.metrics) return `${UNMEASURED_CELL} · ${family.reason ?? DASH}`;
  const { hrBoth, hrRuleOnly, hrMlOnly, hrNeither } = family.metrics;
  return `${[hrBoth, hrRuleOnly, hrMlOnly, hrNeither].map((cell) => countText(cell?.numerator)).join(" / ")} · 비교 가능 ${countText(hrBoth?.denominator)}`;
};
const costText = (item: ComparisonItem, key: string, format: (value: number) => string) =>
  item.status === "failed" ? FAILED_CELL : costMetricText(item.evaluationEnvelope?.execution_cost, key, format);

type ListItem = { label: string; value: ReactNode };
/** 라벨 없이 보는 판정 결과·행당 처리 시간(11/14 필드). 예전 결과처럼 키가 없으면 그 줄을 만들지 않는다. */
const unlabeledItems = (item: ComparisonItem): ListItem[] => {
  const ml = item.categoryResults?.ml;
  const rows: ListItem[] = [];
  if (ml?.predictedAnomalyCount !== undefined) rows.push({ label: "이상 판정 건수", value: valueOrReason(ml.predictedAnomalyCount, ml.predictedAnomalyRate?.reason, (value) => `${numberText(value)}건`) });
  if (ml?.predictedAnomalyRate) rows.push({ label: "이상 판정 비율", value: rateText(ml.predictedAnomalyRate) });
  const quantiles = ml?.scoreQuantiles;
  if (quantiles) {
    const values = [quantiles.p50, quantiles.p95, quantiles.p99];
    rows.push({
      label: "이상 점수 중앙값 / 95분위 / 99분위",
      value: values.every((value) => value === null) ? `${UNMEASURED_CELL} · ${quantiles.reason ?? DASH}` : values.map((value) => valueOrReason(value, quantiles.reason, numberText)).join(" / "),
    });
  }
  if (item.executionCost) {
    rows.push({ label: "행당 처리 시간 중앙값", value: costMetricText(item.executionCost, "itemP50Ms", msText) });
    rows.push({ label: "행당 처리 시간 P95", value: costMetricText(item.executionCost, "itemP95Ms", msText) });
  }
  return rows;
};
/** 평가 전에 뺀 행. 판정 실패 행(`ml.unavailableCount`)과 다른 양이라 다른 줄로 보인다. */
const excludedItems = (item: ComparisonItem): ListItem[] => {
  const value = excludedText(item.counts);
  return value === null ? [] : [{ label: "평가 제외 행", value }];
};

const MetricTable = ({ items, versions, rows, caption }: { items: ComparisonItem[]; versions: CompareVersion[] | undefined; rows: [string, (item: ComparisonItem) => ReactNode][]; caption?: string }) => (
  <Table>
    {caption && <TableCaption>{caption}</TableCaption>}
    <TableHeader>
      <TableRow>
        <TableHead>지표</TableHead>
        {items.map((item) => <TableHead key={item.configVersionId}>{labelOf(versions, item.configVersionId)}{item.role === "active" ? " (기준)" : ""}</TableHead>)}
      </TableRow>
    </TableHeader>
    <TableBody>
      {rows.map(([label, read]) => (
        <TableRow key={label}>
          <TableCell className="whitespace-normal">{label}</TableCell>
          {items.map((item) => <TableCell key={item.configVersionId} className="whitespace-normal">{read(item)}</TableCell>)}
        </TableRow>
      ))}
    </TableBody>
  </Table>
);

/** 모델 하나의 혼동행렬(모델 단독). 정의되지 않는 칸은 서버의 null 그대로 `정의 불가`로 쓴다. 모델 교체 2단계는 교체 진입 버튼 없이 쓴다. */
export const MatrixCard = ({ item, context, showReplace = true }: { item: ComparisonItem; context: Context; showReplace?: boolean }) => {
  const location = useLocation();
  const { versions, set } = context;
  const version = versions?.find((entry) => entry.id === item.configVersionId);
  const head = (
    <span className="flex flex-wrap items-center gap-2">
      {labelOf(versions, item.configVersionId)}
      <Badge variant={item.role === "active" ? "success" : "secondary"}>{roleLabel(item, versions)}</Badge>
    </span>
  );
  if (item.status === "failed") {
    return <SectionCard title={head} headingLevel={3}><p className="text-sm text-destructive-text">평가 실패 · {item.errorCode ?? DASH} · 결과 없음</p></SectionCard>;
  }
  const ml = item.categoryResults?.ml;
  if (!ml || ml.measurementStatus !== "measured") {
    const rows = [...unlabeledItems(item), ...excludedItems(item)];
    return (
      <SectionCard title={head} headingLevel={3}>
        <p className="text-sm text-muted-foreground">{UNMEASURED_CELL} · {ml?.measurementStatus ?? DASH}</p>
        {rows.length > 0 && <DescriptionList className="sm:grid-cols-1 xl:grid-cols-1" items={rows} />}
      </SectionCard>
    );
  }
  const { tp, fp, tn, fn } = ml.confusion;
  const labels = truthRowLabels(set);
  const definedSum = (a: number | null, b: number | null) => (a === null || b === null ? "" : ` ${numberText(a + b)}`);
  return (
    <SectionCard title={head} headingLevel={3}>
      <Table>
        <TableCaption>완료 기준 혼동행렬 · 모델 단독</TableCaption>
        <TableHeader>
          <TableRow><TableHead>정답(행) · 판정(열)</TableHead><TableHead>이상</TableHead><TableHead>정상 확인</TableHead></TableRow>
        </TableHeader>
        <TableBody>
          <TableRow><TableCell>{labels.anomaly}{definedSum(tp, fn)}</TableCell><TableCell>TP <strong className="font-semibold">{countText(tp)}</strong></TableCell><TableCell>FN <strong className="font-semibold">{countText(fn)}</strong></TableCell></TableRow>
          <TableRow><TableCell>{labels.normal}{definedSum(fp, tn)}</TableCell><TableCell>FP {countText(fp)}</TableCell><TableCell>TN {countText(tn)}</TableCell></TableRow>
        </TableBody>
      </Table>
      <DescriptionList
        className="sm:grid-cols-1 xl:grid-cols-1"
        items={[
          { label: "재현율", value: `${rateText(ml.completeOnly.recall)} 완료 기준${decidable(ml.decidable?.recall)}` },
          // 정상 확정 라벨이 없어서 null일 때만 그 문구를 붙인다. 다른 사유는 rateText가 사유 코드를 그대로 보인다.
          { label: "정밀도", value: ml.completeOnly.precision.value === null ? `${rateText(ml.completeOnly.precision)}${ml.completeOnly.precision.reason === NORMAL_LABELS_UNAVAILABLE ? " · 정상 확정 라벨 없음" : ""}` : `${rateText(ml.completeOnly.precision)} 완료 기준${decidable(ml.decidable?.precision)}` },
          { label: "오탐률", value: ml.completeOnly.falsePositiveRate.value === null ? rateText(ml.completeOnly.falsePositiveRate) : `${rateText(ml.completeOnly.falsePositiveRate)} 완료 기준${decidable(ml.decidable?.falsePositiveRate)}` },
          ...unlabeledItems(item),
          // 평가한 행 중 모델 판정이 실패한 행. 평가 전에 뺀 행(평가 제외 행)과 합치지 않는다.
          { label: "판정 실패 행", value: `완료 기준 ${numberText(ml.unavailableCount)}행${ml.decidable ? ` · 판정 가능 기준 ${ml.decidable.unavailableCount === undefined ? DASH : `${numberText(ml.decidable.unavailableCount)}행`}` : ""}` },
          // 교체 버튼 바로 위에 둔다(제외 행이 있어도 적용 gate를 통과할 수 있어서).
          ...excludedItems(item),
        ]}
      />
      {showReplace && item.role === "candidate" && version?.executable && !version.active && (
        <div><Button asChild size="sm" variant="outline"><Link to={paths.detectionReplace} state={{ returnTo: location.pathname + location.search, targetVersionId: item.configVersionId }}>이 모델로 교체</Link></Button></div>
      )}
    </SectionCard>
  );
};

const AGREEMENT_NOTE = "정확도가 아니라 두 버전의 판정이 얼마나 같은지입니다";
/** 버전 쌍마다 판정 일치(서버 값 그대로). {A}/{B}는 8자 버전 ID. */
const AgreementTable = ({ pair }: { pair: PairwiseAgreement }) => {
  const a = pair.a.slice(0, 8);
  const b = pair.b.slice(0, 8);
  const cells: [string, ReactNode][] = [
    ["둘 다 이상", numberText(pair.both)],
    [`${a}만 이상`, numberText(pair.aOnly)],
    [`${b}만 이상`, numberText(pair.bOnly)],
    ["둘 다 정상", numberText(pair.neither)],
    ["판정 일치율", rateText(pair.agreementRate)],
  ];
  return (
    <Table data-agreement={`${pair.a}:${pair.b}`}>
      <TableHeader><TableRow>{cells.map(([label]) => <TableHead key={label}>{label}</TableHead>)}</TableRow></TableHeader>
      <TableBody><TableRow>{cells.map(([label, value]) => <TableCell key={label}>{value}</TableCell>)}</TableRow></TableBody>
    </Table>
  );
};

const Results = ({ detail, context }: { detail: ComparisonDetail; context: Context }) => {
  const { versions, set } = context;
  const items = detail.items ?? [];
  const note = setNote(set);
  // v1 서버는 룰+모델 종합·실행 비용을 내지 않는다(계약 v1 구현 범위). 그 값이 하나도 없으면 빈 표를 그리지 않는다.
  const withRules = items.some((item) => item.composite !== undefined || item.evaluationEnvelope);
  const withEnvelope = items.some((item) => item.evaluationEnvelope);
  // 판정 가능 기준도 v1에는 없다. 어느 항목에도 없으면 그 행을 그리지 않는다.
  const withDecidable = items.some((item) => item.categoryResults?.ml.decidable);
  type MlResult = NonNullable<ComparisonItem["categoryResults"]>["ml"];
  const decidableRow = (label: string, read: (ml: MlResult) => ReactNode): [string, (item: ComparisonItem) => ReactNode][] =>
    withDecidable ? [[label, (item) => cellOf(item, read)]] : [];
  return (
    <>
      <SectionCard title="머신러닝 모델별 혼동행렬 · 모델 단독" description={note}>
        <div className="grid gap-4 xl:grid-cols-2">{items.map((item) => <MatrixCard key={item.configVersionId} item={item} context={context} />)}</div>
      </SectionCard>
      <SectionCard title="지표 비교 · 모델 단독">
        <MetricTable
          items={items}
          versions={versions}
          caption={withDecidable ? "완료 기준 = 모델 판정이 끝난 행 · 판정 가능 기준 = 결과가 있는 행(정상 여부 미확인 제외)" : "완료 기준 = 모델 판정이 끝난 행"}
          rows={[
            ["평가 정책", (item) => (item.status === "failed" ? FAILED_CELL : policyText(item.evaluationPolicy))],
            ["재현율 · 완료 기준", (item) => cellOf(item, (ml) => rateText(ml.completeOnly.recall))],
            ...decidableRow("재현율 · 판정 가능 기준", (ml) => rateText(ml.decidable?.recall)),
            ["정밀도 · 완료 기준", (item) => cellOf(item, (ml) => rateText(ml.completeOnly.precision))],
            ...decidableRow("정밀도 · 판정 가능 기준", (ml) => rateText(ml.decidable?.precision)),
            ["오탐률 · 완료 기준", (item) => cellOf(item, (ml) => rateText(ml.completeOnly.falsePositiveRate))],
            ...decidableRow("오탐률 · 판정 가능 기준", (ml) => rateText(ml.decidable?.falsePositiveRate)),
            ["모델 미판정 행", (item) => cellOf(item, (ml) => `${numberText(ml.unavailableCount)}건`)],
          ]}
        />
      </SectionCard>
      {/* 예전 결과(키 없음)와 성공 항목 1개 이하([])는 그리지 않는다. */}
      {detail.pairwiseAgreement && detail.pairwiseAgreement.length > 0 && (
        <SectionCard title="버전 간 판정 일치" description={AGREEMENT_NOTE}>
          <div className="grid gap-4">{detail.pairwiseAgreement.map((pair) => <AgreementTable key={`${pair.a}:${pair.b}`} pair={pair} />)}</div>
        </SectionCard>
      )}
      {withRules && (
        <SectionCard title={`룰과 함께 판정 · ${ruleConditionText(detail.ruleSet?.revision)}`}>
          <MetricTable
            items={items}
            versions={versions}
            caption="모든 모델에 같은 룰 버전을 적용 · 점수로 합산하지 않습니다"
            rows={[
              ["재현율 · 룰+모델 · 완료 기준", (item) => withComposite(item, (composite) => rateText(composite.completeOnly.recall))],
              ["오탐률 · 룰+모델 · 완료 기준", (item) => withComposite(item, (composite) => rateText(composite.completeOnly.falsePositiveRate))],
              ["경보 · 룰+모델 · 완료 기준", (item) => withComposite(item, (composite) => `${numberText(composite.completeOnly.hitCount)}건`)],
              ["룰·머신러닝 모델 비교 · 둘 다 이상 / 룰만 / 모델만 / 둘 다 정상", ruleMlText],
            ]}
          />
        </SectionCard>
      )}
      {withEnvelope && (
        <SectionCard title="기타 지표">
          <MetricTable
            items={items}
            versions={versions}
            caption="상태: measured / unavailable / not_applicable / failed"
            rows={[
              ["실행 비용 · 판정 1건 p95", (item) => costText(item, "itemP95Ms", msText)],
              ["실행 비용 · 처리량", (item) => costText(item, "throughput", (value) => `${numberText(Math.round(value))} 행/s`)],
              ["실행 비용 · 워커 RSS(완료 시점 1회)", (item) => costText(item, "processRssBytes", bytesText)],
              ["운영 지표", (item) => (item.status === "failed" ? FAILED_CELL : "미측정 · 기간을 지정한 별도 운영 보고에서 조회")],
            ]}
          />
        </SectionCard>
      )}
    </>
  );
};

const Head = ({ detail, context }: { detail: ComparisonDetail; context: Context }) => {
  const location = useLocation();
  const { versions, set } = context;
  const status = comparisonStatus(detail.status, detail.items?.filter((item) => item.status === "failed").length ?? 0);
  const baselines = detail.baselines ?? [];
  const baselineText = baselines.length
    ? baselines.map(({ configVersionId }) => {
      const active = versions?.find((version) => version.id === configVersionId)?.active;
      return `${labelOf(versions, configVersionId)}${active === undefined ? "" : active ? " · 현재 운영" : " · 평가 당시 운영"}`;
    }).join(", ")
    : "없음";
  return (
    <SectionCard title={`비교 평가 ${comparisonKey(detail).slice(0, 8)}`} action={<Badge variant={status.variant}>{status.label}</Badge>}>
      <DescriptionList
        items={[
          { label: "평가 세트", value: set ? `${set.displayName} · ${setKindLabel(set)}${set.synthetic ? `(${setUnitLabel(set)})` : ""}` : DASH },
          { label: "행 수 · 이상 정답", value: detail.evaluationSet?.rowCount !== undefined ? `${numberText(detail.evaluationSet.rowCount)}행 · ${set?.generation ? `${numberText(set.generation.truthCount)}행` : DASH}` : DASH },
          // v1: 룰 세트 버전은 접수 때 기록만 하고 항목은 각자의 평가 정책으로 평가한다(v1·v2 = 구성 룰). 같은 룰이라고 적지 않는다.
          { label: "룰 조건", value: detail.ruleSet ? `${ruleConditionText(detail.ruleSet.revision)} · ${detail.items?.some((item) => item.evaluationPolicy) ? "항목별 평가 정책" : "모든 모델에 같은 룰"}` : DASH },
          { label: "기준 운영 모델", value: detail.baselines ? baselineText : DASH },
          { label: "비교 모델", value: detail.items?.map((item) => labelOf(versions, item.configVersionId)).join(", ") ?? DASH },
          { label: "작업", value: <Link className="text-link" to={paths.detectionJob(detail.jobId)} state={{ returnTo: location.pathname + location.search }}>{detail.jobId.slice(0, 8)}</Link> },
          { label: "고정 정보", value: set ? <span className="font-mono text-xs break-all">{fixedInfoText(set, true)}</span> : DASH },
        ]}
      />
    </SectionCard>
  );
};

const Body = ({ detail }: { detail: ComparisonDetail }) => {
  const versions = useCompareVersions();
  const set = useEvaluationSet(detail.evaluationSet?.id);
  const context = { versions: versions.data, set: set.data };
  const where = `${comparisonKey(detail).slice(0, 8)} · ${set.data?.displayName ?? DASH} · ${ruleConditionText(detail.ruleSet?.revision)}`;
  return (
    <>
      {versions.isError && <ErrorState title={`모델 버전 조회 실패 · ${errorCode(versions.error)}`} onRetry={() => void versions.refetch()} retryLabel="다시 조회" />}
      {set.isError && <ErrorState title={`평가 세트 조회 실패 · ${errorCode(set.error)}`} onRetry={() => void set.refetch()} retryLabel="다시 조회" />}
      <Head detail={detail} context={context} />
      {detail.replacementRecommendations?.map((rec) => <RecommendationBlock key={rec.modelKey} rec={rec} versions={versions.data} where={where} />)}
      {!detail.replacementRecommendations && detail.replacementRecommendationsReason && <RecommendationUnconnected reason={detail.replacementRecommendationsReason} />}
      {detail.status === "failed" && (
        <Alert variant="destructive">
          <AlertDescription>
            <p>비교 평가 실패 · {detail.errorCode ?? DASH} · 결과가 없으므로 지표를 표시하지 않습니다.</p>
            {/* 모든 항목 실패여도 항목별 사유는 서버 값 그대로 보인다. */}
            {detail.items?.filter((item) => item.status === "failed").map((item) => (
              <p key={item.configVersionId} data-item-failure>{labelOf(versions.data, item.configVersionId)} 실패 · {item.errorCode ?? DASH}</p>
            ))}
          </AlertDescription>
        </Alert>
      )}
      {detail.status === "succeeded" && <Results detail={detail} context={context} />}
    </>
  );
};

/** 평가 상세(주소 ID는 비교 평가 ID): 조회만 한다. 지표는 서버 값 그대로, 합산·순위 없음. */
export const EvaluationDetailPage = () => {
  const { evaluationId = "" } = useParams();
  const comparison = useComparison(evaluationId);
  return (
    <div className="grid gap-6">
      <ScreenTitle>평가 상세 {evaluationId}</ScreenTitle>
      {comparison.isPending && <LoadingState>평가 조회 중…</LoadingState>}
      {comparison.isError && <ErrorState title={`평가 조회 실패 · ${errorCode(comparison.error)}`} onRetry={() => void comparison.refetch()} retryLabel="다시 조회" />}
      {comparison.data && <Body detail={comparison.data} />}
    </div>
  );
};
