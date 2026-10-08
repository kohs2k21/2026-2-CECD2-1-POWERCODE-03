import { Link, useSearchParams } from "react-router-dom";
import { Button } from "../../../components/ui/button";
import { EmptyState } from "../../../components/ui/feedback";
import {
  DetailMissing,
  jobStateLabels,
  valueText,
} from "../components/DetectionQueryBoundary";
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

const EvaluationDetails = ({ result }: { result: EvaluationResult }) => (
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
  const candidateResult = evaluationFor(candidate, data);
  const active = data.versions.find((item) => item.id === data.activeVersionId);
  const activeResult = evaluationFor(active, data);
  const matches = Boolean(
    candidate &&
    candidateResult?.state === "succeeded" &&
    conditionsMatch(candidateResult.condition, condition) &&
    evaluationMatchesConfiguration(candidateResult, candidate, data),
  );
  const comparable = Boolean(
    active &&
    candidate &&
    activeResult?.state === "succeeded" &&
    candidateResult?.state === "succeeded" &&
    conditionsMatch(activeResult.condition, candidateResult.condition) &&
    evaluationMatchesConfiguration(activeResult, active, data) &&
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
        <h3>기록된 운영 구성과 후보 비교</h3>
        <p className="detection-note" role="status">
          {comparable
            ? "같은 평가 조건이며 각 기록의 구성 내용이 확인되었습니다."
            : "비교 불가: 양쪽의 성공한 평가 결과, 동일한 평가 조건, 현재 구성과 일치하는 평가 근거가 필요합니다."}
        </p>
        <div className="detection-table-wrap">
          <table className="detection-table">
            <caption>기록된 평가 지표 · 미측정값은 —</caption>
            <thead>
              <tr>
                <th scope="col">지표</th>
                <th scope="col">현재 · {active?.name ?? "—"}</th>
                <th scope="col">후보 · {candidate?.name ?? "—"}</th>
              </tr>
            </thead>
            <tbody>
              {metricRows.map(({ key, label, unit }) => (
                <tr key={key}>
                  <th scope="row">{label}</th>
                  <td>{metricText(activeResult?.metrics[key], unit)}</td>
                  <td>{metricText(candidateResult?.metrics[key], unit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="detection-note">
          다음 평가 조건의 실행 결과가 아닙니다. 조건이나 구성의 일치가 확인되지
          않은 수치로 성능의 우열을 판단할 수 없습니다.
        </p>
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
