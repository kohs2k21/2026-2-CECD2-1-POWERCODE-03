import { Link, useSearchParams } from "react-router-dom";
import { Button } from "../../../components/ui/button";
import { EmptyState } from "../../../components/ui/feedback";
import {
  DetectionQueryBoundary,
  DetailMissing,
  jobStateLabels,
  valueText,
} from "../components/DetectionQueryBoundary";
import { ServiceAction } from "../components/ServiceAction";
import type {
  DetectionData,
  EvaluationCondition,
  EvaluationResult,
} from "../data/types";
import { useDetectionQuery } from "../data/useDetectionQuery";
import {
  conditionsMatch,
  evaluationErrors,
  evaluationFor,
  metricRows,
  metricText,
  versionLabels,
} from "./operationPresentation";
import { useEvaluationConditions } from "./useEvaluationConditions";

const conditionLabels = {
  snapshotId: "데이터셋",
  protocolId: "평가 프로토콜",
  splitVersion: "분할 버전",
  scenario: "시나리오",
  purpose: "평가 목적",
} as const;

const EvaluationDetails = ({ result }: { result: EvaluationResult }) => (
  <section className="detection-card" aria-labelledby="evaluation-detail-title">
    <h2 id="evaluation-detail-title">평가 결과 상세</h2>
    <dl className="detection-summary">
      <div>
        <dt>결과 ID</dt>
        <dd>{result.id}</dd>
      </div>
      <div>
        <dt>처리 상태</dt>
        <dd>{jobStateLabels[result.state]}</dd>
      </div>
      <div>
        <dt>후보</dt>
        <dd>{result.candidateId}</dd>
      </div>
      <div>
        <dt>데이터셋</dt>
        <dd>{result.condition.snapshotId}</dd>
      </div>
      <div>
        <dt>프로토콜</dt>
        <dd>{result.condition.protocolId}</dd>
      </div>
      <div>
        <dt>분할</dt>
        <dd>{result.condition.splitVersion}</dd>
      </div>
      <div>
        <dt>시나리오</dt>
        <dd>{result.condition.scenario}</dd>
      </div>
      <div>
        <dt>평가 목적</dt>
        <dd>{result.condition.purpose}</dd>
      </div>
      <div>
        <dt>요청 시각</dt>
        <dd>{valueText(result.requestedAt)}</dd>
      </div>
      <div>
        <dt>완료 시각</dt>
        <dd>{valueText(result.completedAt)}</dd>
      </div>
      {metricRows.map(({ key, label, unit }) => (
        <div key={key}>
          <dt>{label}</dt>
          <dd>{metricText(result.metrics[key], unit)}</dd>
        </div>
      ))}
    </dl>
    {result.failure && (
      <p className="detection-error" role="alert">
        {result.failure}
      </p>
    )}
    {metricRows.some(
      ({ key, unit }) => metricText(result.metrics[key], unit) === "—",
    ) && (
      <p className="detection-note">
        측정되지 않은 지표는 —로 표시됩니다. 처리 성공 여부와 지표의 가용성은
        별도로 확인해야 합니다.
      </p>
    )}
  </section>
);

const EvaluationWorkbench = ({ data }: { data: DetectionData }) => {
  const [params, setParams] = useSearchParams();
  const patch = (values: Record<string, string | null>) => {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      Object.entries(values).forEach(([key, value]) =>
        value ? next.set(key, value) : next.delete(key),
      );
      return next;
    });
  };
  const href = (values: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) =>
      value ? next.set(key, value) : next.delete(key),
    );
    return `?${next.toString()}`;
  };
  const candidates = data.versions.filter(
    (item) => item.state === "candidate" || item.state === "draft",
  );
  const candidateId = params.get("candidate") ?? candidates[0]?.id ?? "";
  const candidate = candidates.find((item) => item.id === candidateId);
  const search = params.get("q") ?? "";
  const state = params.get("state") ?? "all";
  const visible = candidates.filter(
    (item) =>
      (state === "all" || item.state === state) &&
      `${item.name} ${item.id}`
        .toLocaleLowerCase()
        .includes(search.toLocaleLowerCase()),
  );
  const candidateResult = evaluationFor(candidate, data);
  const active = data.versions.find((item) => item.id === data.activeVersionId);
  const activeResult = evaluationFor(active, data);
  const defaultSnapshot =
    data.snapshots.find((item) => item.state === "ready") ?? data.snapshots[0];
  const initial: EvaluationCondition = candidateResult?.condition ?? {
    snapshotId: candidate?.snapshotId ?? defaultSnapshot?.id ?? "",
    protocolId: "",
    splitVersion: defaultSnapshot?.splitVersion ?? "",
    scenario: "",
    purpose: "",
  };
  const { condition, change } = useEvaluationConditions(candidateId, initial);
  const errors = evaluationErrors(condition, data);
  const comparable = Boolean(
    activeResult &&
    candidateResult &&
    activeResult.state === "succeeded" &&
    candidateResult.state === "succeeded" &&
    conditionsMatch(activeResult.condition, candidateResult.condition),
  );
  const resultId = params.get("result");
  const detail = data.evaluations.find((item) => item.id === resultId);
  const results = data.evaluations.filter(
    (item) => item.candidateId === candidateId,
  );

  return (
    <>
      <div className="detection-grid">
        <section
          className="detection-card"
          aria-labelledby="evaluation-candidate-title"
        >
          <h2 id="evaluation-candidate-title">평가 후보</h2>
          <div className="detection-form-grid">
            <label className="detection-field">
              후보 검색
              <input
                value={search}
                onChange={(event) => patch({ q: event.target.value })}
                placeholder="이름·ID"
              />
            </label>
            <label className="detection-field">
              후보 상태
              <select
                value={state}
                onChange={(event) => patch({ state: event.target.value })}
              >
                <option value="all">전체</option>
                <option value="candidate">평가 후보</option>
                <option value="draft">편집 중</option>
              </select>
            </label>
          </div>
          {visible.length ? (
            <ul className="detection-check-list">
              {visible.map((item) => (
                <li key={item.id}>
                  <Button
                    asChild
                    variant="ghost"
                    className="detection-list-button"
                  >
                    <Link
                      to={href({ candidate: item.id, result: null })}
                      aria-current={
                        item.id === candidateId ? "true" : undefined
                      }
                    >
                      <span>{item.name}</span>
                      <span className="detection-status">
                        {versionLabels[item.state]}
                      </span>
                    </Link>
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>
              {candidates.length
                ? "검색 조건에 맞는 후보가 없습니다."
                : "평가 후보가 없습니다."}
            </EmptyState>
          )}
        </section>
        <section
          className="detection-card"
          aria-labelledby="evaluation-condition-title"
        >
          <h2 id="evaluation-condition-title">평가 조건</h2>
          {!candidate ? (
            <DetailMissing />
          ) : (
            <>
              <p>{candidate.name}</p>
              <form onSubmit={(event) => event.preventDefault()}>
                <div className="detection-form-grid">
                  <label className="detection-field">
                    데이터셋
                    <select
                      value={condition.snapshotId}
                      onChange={(event) =>
                        change("snapshotId", event.target.value)
                      }
                    >
                      <option value="">선택해 주세요</option>
                      {data.snapshots.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name} ·{" "}
                          {item.state === "ready"
                            ? "준비됨"
                            : item.state === "failed"
                              ? "준비 실패"
                              : "준비 중"}
                        </option>
                      ))}
                    </select>
                  </label>
                  {(
                    [
                      ["protocolId", "평가 프로토콜"],
                      ["splitVersion", "분할 버전"],
                      ["scenario", "시나리오"],
                      ["purpose", "평가 목적"],
                    ] as const
                  ).map(([key, label]) => (
                    <label key={key} className="detection-field">
                      {label}
                      <input
                        value={condition[key]}
                        onChange={(event) => change(key, event.target.value)}
                        required
                      />
                    </label>
                  ))}
                </div>
                {errors.length > 0 && (
                  <ul className="detection-error" role="status">
                    {errors.map((error) => (
                      <li key={error}>{error}</li>
                    ))}
                  </ul>
                )}
                <div className="detection-actions">
                  <ServiceAction
                    key={candidateId}
                    operation="evaluate"
                    label="평가 실행"
                    available={data.capabilities.evaluate}
                    disabled={errors.length > 0}
                    payload={{ candidateId, condition }}
                  >
                    <p>선택 후보: {candidate.name}</p>
                    <dl className="detection-summary">
                      {Object.entries(condition).map(([key, value]) => (
                        <div key={key}>
                          <dt>
                            {conditionLabels[key as keyof EvaluationCondition]}
                          </dt>
                          <dd>{value}</dd>
                        </div>
                      ))}
                    </dl>
                    <p className="detection-note">
                      평가 요청은 운영 버전을 변경하지 않습니다. 결과는 평가
                      이력에서 확인합니다.
                    </p>
                  </ServiceAction>
                  <Button asChild variant="outline">
                    <Link
                      to={`/detection/versions?candidate=${encodeURIComponent(candidateId)}`}
                    >
                      버전 관리
                    </Link>
                  </Button>
                </div>
              </form>
            </>
          )}
        </section>
      </div>
      <section
        className="detection-card"
        aria-labelledby="evaluation-comparison-title"
      >
        <h2 id="evaluation-comparison-title">운영 구성과 후보 비교</h2>
        <p className="detection-note" role="status">
          {comparable
            ? "같은 데이터셋·프로토콜·분할·시나리오·목적의 결과입니다."
            : "비교 불가: 양쪽의 성공한 평가 결과와 동일한 평가 조건이 필요합니다."}
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
        {!comparable && (
          <p className="detection-note">
            각 결과에 기록된 값입니다. 조건이 다른 값의 차이로 성능의 우열을
            판단할 수 없습니다.
          </p>
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
                    to={href({ result: result.id })}
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
              <Link to={href({ result: null })}>평가 목록으로 돌아가기</Link>
            </Button>
          </div>
        </>
      )}
    </>
  );
};

export const EvaluationPage = () => {
  const query = useDetectionQuery();
  return (
    <section className="detection-page detection-stack">
      <header className="detection-page-header">
        <h1>성능 평가·비교</h1>
        <Button
          variant="outline"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          상태 새로고침
        </Button>
      </header>
      <DetectionQueryBoundary query={query}>
        {(data) => <EvaluationWorkbench data={data} />}
      </DetectionQueryBoundary>
    </section>
  );
};
