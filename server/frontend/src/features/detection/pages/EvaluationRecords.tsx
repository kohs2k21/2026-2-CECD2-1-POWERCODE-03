import { Link, useSearchParams } from "react-router-dom";
import { Button } from "../../../components/ui/button";
import { EmptyState } from "../../../components/ui/feedback";
import {
  DetailMissing,
  jobStateLabels,
  valueText,
} from "../components/DetectionQueryBoundary";
import { comparisonResult } from "../data/evaluationComparison";
import { evaluationMatchesConfiguration } from "../data/configuration";
import type {
  DetectionData,
  EvaluationCondition,
  EvaluationResult,
  VersionBundle,
} from "../data/types";
import {
  conditionsMatch,
  evaluationFor,
  metricRows,
  metricText,
} from "./operationPresentation";

export const conditionLabels = {
  snapshotId: "데이터셋",
  protocolId: "평가 프로토콜",
  splitVersion: "분할 버전",
  scenario: "시나리오",
  purpose: "평가 목적",
  evaluationSetId: "고정 평가 세트",
  evaluationSetRevision: "평가 세트 revision",
} as const;
export const ConditionDetails = ({
  condition,
}: {
  condition: EvaluationCondition;
}) => (
  <dl className="detection-summary">
    {Object.entries(condition).map(([key, value]) => (
      <div key={key}>
        <dt>{conditionLabels[key as keyof EvaluationCondition]}</dt>
        <dd>{value}</dd>
      </div>
    ))}
  </dl>
);

export const EvaluationDetails = ({ result }: { result: EvaluationResult }) => (
  <section className="detection-card" aria-labelledby="evaluation-detail-title">
    <h2 id="evaluation-detail-title">기록된 평가 결과 상세</h2>
    <dl className="detection-summary">
      <div>
        <dt>결과 ID</dt>
        <dd>{result.id}</dd>
      </div>
      <div>
        <dt>기록된 처리 상태</dt>
        <dd>{jobStateLabels[result.state]}</dd>
      </div>
      <div>
        <dt>평가한 후보</dt>
        <dd>{result.candidateId}</dd>
      </div>
      <div>
        <dt>요청 시각</dt>
        <dd>{valueText(result.requestedAt)}</dd>
      </div>
      <div>
        <dt>기록된 완료 시각</dt>
        <dd>{valueText(result.completedAt)}</dd>
      </div>
      {metricRows.map(({ key, label, unit }) => (
        <div key={key}>
          <dt>{label}</dt>
          <dd>{metricText(result.metrics[key], unit)}</dd>
        </div>
      ))}
    </dl>
    <h3>기록된 평가 조건</h3>
    <ConditionDetails condition={result.condition} />
    <div className="detection-table-wrap">
      <table className="detection-table">
        <caption>시나리오별 판정 결과 · 미제공값은 —</caption>
        <thead>
          <tr>
            <th>시나리오</th>
            <th>주입</th>
            <th>탐지</th>
            <th>미탐</th>
            <th>판정 불가</th>
            <th>제외</th>
            <th>라벨 출처</th>
            <th>사유</th>
          </tr>
        </thead>
        <tbody>
          {result.scenarioOutcomes?.length ? (
            result.scenarioOutcomes.map((row) => (
              <tr key={row.kind}>
                <th scope="row">{row.kind}</th>
                {(
                  [
                    "injected",
                    "detected",
                    "missed",
                    "unavailable",
                    "excluded",
                  ] as const
                ).map((key) => (
                  <td key={key}>
                    {typeof row[key] === "number" &&
                    Number.isSafeInteger(row[key]) &&
                    row[key]! >= 0
                      ? row[key]
                      : "—"}
                  </td>
                ))}
                <td>{valueText(row.labelSource)}</td>
                <td>{valueText(row.reason)}</td>
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={8}>시나리오별 판정 내역이 제공되지 않았습니다.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
    {result.failure && (
      <p className="detection-error" role="alert">
        {result.failure}
      </p>
    )}
    <p className="detection-note">
      이 결과는 기록된 구성과 조건으로 실행한 평가입니다. 다음 평가 조건의 입력
      변경은 과거 이력을 바꾸지 않습니다.
    </p>
  </section>
);

export const EvaluationRecords = ({
  data,
  candidate,
  condition,
}: {
  data: DetectionData;
  candidate: VersionBundle | undefined;
  condition: EvaluationCondition;
}) => {
  const [params] = useSearchParams();
  const href = (result: string | null) => {
    const next = new URLSearchParams(params);
    if (result) next.set("result", result);
    else next.delete("result");
    return `?${next.toString()}`;
  };
  const set = data.evaluationSets?.find(
    (item) =>
      item.id === condition.evaluationSetId &&
      item.revision === condition.evaluationSetRevision,
  );
  const candidateResult =
    candidate && set
      ? comparisonResult(candidate, set, data)
      : evaluationFor(candidate, data);
  const matches = Boolean(
    candidate &&
    candidateResult?.state === "succeeded" &&
    conditionsMatch(candidateResult.condition, condition) &&
    evaluationMatchesConfiguration(candidateResult, candidate, data),
  );
  const resultId = params.get("result");
  const detail = data.evaluations.find((item) => item.id === resultId);
  const results = data.evaluations.filter(
    (item) => item.candidateId === candidate?.id,
  );
  return (
    <>
      <section
        className="detection-card"
        aria-labelledby="evaluation-recorded-title"
      >
        <h2 id="evaluation-recorded-title">기록된 평가 결과·조건</h2>
        <p
          className={matches ? "detection-status" : "detection-note"}
          role="status"
        >
          {matches
            ? "현재 입력 조건과 구성에 대응하는 기록된 평가 결과입니다."
            : "현재 입력 조건의 결과 없음 · 다음 조건 또는 구성이 달라지면 새 평가가 필요합니다."}
        </p>
        {candidateResult && (
          <>
            <p>
              기록된 처리 상태: {jobStateLabels[candidateResult.state]} · 완료
              시각: {valueText(candidateResult.completedAt)}
            </p>
            <ConditionDetails condition={candidateResult.condition} />
            {candidate &&
              !evaluationMatchesConfiguration(
                candidateResult,
                candidate,
                data,
              ) && (
                <p className="detection-error">
                  재평가 필요: 기록된 평가 구성과 현재 모델·룰 구성 불일치
                </p>
              )}
          </>
        )}
      </section>
      <section
        className="detection-card"
        aria-labelledby="evaluation-history-title"
      >
        <h2 id="evaluation-history-title">평가 이력</h2>
        {results.length ? (
          <ul className="detection-check-list">
            {results.map((result) => (
              <li key={result.id}>
                <Button
                  asChild
                  variant="ghost"
                  className="detection-list-button"
                >
                  <Link
                    to={href(result.id)}
                    aria-current={resultId === result.id ? "true" : undefined}
                  >
                    <span>{result.id}</span>
                    <span>
                      {jobStateLabels[result.state]} ·{" "}
                      {valueText(result.completedAt)}
                    </span>
                  </Link>
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>선택한 후보의 평가 이력이 없습니다.</EmptyState>
        )}
      </section>
      {resultId && (
        <>
          {detail ? <EvaluationDetails result={detail} /> : <DetailMissing />}
          <div className="detection-actions">
            <Button asChild variant="outline">
              <Link to={href(null)}>평가 목록으로 돌아가기</Link>
            </Button>
          </div>
        </>
      )}
    </>
  );
};
