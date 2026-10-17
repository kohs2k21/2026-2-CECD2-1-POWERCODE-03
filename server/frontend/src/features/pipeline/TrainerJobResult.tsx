import type { PipelineJob } from "../../services/api/pipeline.api";

const families = { rule_ml: "Rule·ML 비교", synthetic: "합성 이상", execution_cost: "실행 비용", operational: "운영 지표" };
const statuses = { measured: "측정됨", unavailable: "미측정", not_applicable: "해당 없음", failed: "측정 실패" };
const number = (value: unknown): string => typeof value === "number" && Number.isFinite(value) ? String(value) : "미측정";

export function TrainerJobResult({ job }: { job: PipelineJob }) {
  const result = job.resultSummary;
  if (!result) return <p>저장된 결과가 없습니다.</p>;
  const manifest = result.modelManifest;
  const metrics = result.metrics;
  const count = (key: string): number | null => {
    const value = metrics?.[key];
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  };
  const ratio = (key: string, first: string, second: string): string => {
    const a = count(first), b = count(second);
    return a !== null && b !== null && a + b > 0 ? number(metrics?.[key]) : "미측정";
  };
  return <div>
    {result.configVersionId && <p>후보 구성: <code>{result.configVersionId}</code></p>}
    {result.evaluationId && <p>평가 ID: <code>{result.evaluationId}</code></p>}
    {result.applicability && <p>라벨 평가: {result.applicability === "valid" ? "평가 가능" : "미측정"}
      {result.reasonCodes?.length ? ` · ${result.reasonCodes.join(", ")}` : ""}</p>}
    {result.populationCount !== undefined && <p>대상 {number(result.populationCount)} · 판정 {number(result.scoredCount)} ·
      판정 불가 {number(result.unavailableCount)} · 라벨 없음 {number(result.unlabeledCount)}</p>}
    {metrics && result.applicability === "valid" && <details><summary>라벨 평가 결과</summary>
      <p>TP {number(count("tp"))} · FP {number(count("fp"))} · TN {number(count("tn"))} · FN {number(count("fn"))}</p>
      <p>Precision: {ratio("precision", "tp", "fp")} · Recall: {ratio("recall", "tp", "fn")} ·
        FPR: {ratio("falsePositiveRate", "fp", "tn")}</p>
    </details>}
    <details><summary>실행 정보</summary>
      {result.snapshotId && <p>스냅샷 ID: <code>{result.snapshotId}</code></p>}
      {result.dataContract && <p>스냅샷 해시: <code>{String(result.dataContract.snapshotHash ?? "미제공")}</code></p>}
      {manifest && <>
        <p>모델: {String(manifest.modelKey ?? "미제공")} · <code>{String(manifest.modelArtifactId ?? "미제공")}</code></p>
        <p>모델 해시: <code>{String(manifest.modelHash ?? "미제공")}</code></p>
        <p>전처리 해시: <code>{String(manifest.fitHash ?? "미제공")}</code></p>
      </>}
    </details>
    <dl className="pipeline-evaluation-status">
      {Object.entries(families).map(([key, label]) => {
        const item = result.evaluationEnvelope?.[key];
        return <div key={key}>
          <dt>{label}</dt>
          <dd>{item ? statuses[item.status] ?? "상태 확인 불가" : "미측정"}{item?.reason ? ` · ${item.reason}` : ""}
            {item?.status === "measured" && item.metrics && Object.entries(item.metrics).map(([name, value]) =>
              <span key={name}> · {name}: {number(value.value)} {value.unit}{value.reason ? ` · ${value.reason}` : ""}
                {` (분자 ${number(value.numerator)} / 분모 ${number(value.denominator)}, 커버리지 ${number(value.coverage)})`}</span>)}
          </dd>
        </div>;
      })}
    </dl>
  </div>;
}
