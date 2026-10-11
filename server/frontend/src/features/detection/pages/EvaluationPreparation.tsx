import { useEffect, useRef, useState } from "react";
import { Button } from "../../../components/ui/button";
import { EmptyState } from "../../../components/ui/feedback";
import { ServiceAction } from "../components/ServiceAction";
import { valueText } from "../components/DetectionQueryBoundary";
import {
  inspectEvaluationCsv,
  evaluationCsvLimits,
} from "../data/evaluationCsv";
import { preparationErrors } from "../data/evaluationPreparation";
import type { DetectionData } from "../data/types";
import type {
  EvaluationPreparationDraft,
  EvaluationMetric,
} from "../data/evaluationTypes";
import { metricRows } from "./operationPresentation";
import { useEvaluationPreparation } from "./useEvaluationPreparation";
import { EvaluationPreparationScenarios } from "./EvaluationPreparationScenarios";
import { getStoredToken } from "../../../services/auth/session";

export const EvaluationPreparation = ({ data }: { data: DetectionData }) => {
  const { draft, change } = useEvaluationPreparation();
  const [csvErrors, setCsvErrors] = useState<string[]>([]);
  const [csvReading, setCsvReading] = useState(false);
  const readGeneration = useRef(0);
  useEffect(
    () => () => {
      readGeneration.current++;
    },
    [],
  );
  const split = data.evaluationSplits?.find(
    (item) => item.id === draft.splitManifestId,
  );
  const spec = data.evaluationSpecs?.find((item) => item.id === draft.specId);
  const errors = preparationErrors(draft, data);
  const fileChange = async (file: File | undefined) => {
    if (!file) return;
    const generation = ++readGeneration.current;
    const token = getStoredToken();
    change({ csv: null });
    setCsvErrors([]);
    setCsvReading(false);
    if (file.size > evaluationCsvLimits.bytes) {
      setCsvErrors(["CSV는 5 MiB 이하만 검사할 수 있습니다."]);
      return;
    }
    let inspected;
    setCsvReading(true);
    try {
      inspected = inspectEvaluationCsv(await file.text());
    } catch {
      if (generation === readGeneration.current && token === getStoredToken()) {
        setCsvErrors(["CSV 파일을 읽을 수 없습니다."]);
        setCsvReading(false);
      }
      return;
    }
    if (generation !== readGeneration.current || token !== getStoredToken())
      return;
    setCsvErrors(inspected.errors);
    setCsvReading(false);
    if (inspected.metadata) change({ csv: inspected.metadata });
  };
  return (
    <div className="detection-stack">
      <section
        className="detection-card"
        aria-labelledby="preparation-dataset-title"
      >
        <h2 id="preparation-dataset-title">데이터셋·고정 분할</h2>
        <div className="detection-form-grid">
          <label className="detection-field">
            평가 세트 이름
            <input
              maxLength={80}
              value={draft.name}
              onChange={(event) => change({ name: event.target.value })}
            />
          </label>
          <label className="detection-field">
            고정 데이터셋
            <select
              value={draft.snapshotId}
              onChange={(event) =>
                change({ snapshotId: event.target.value, splitManifestId: "" })
              }
            >
              <option value="">선택해 주세요</option>
              {data.snapshots.map((item) => (
                <option
                  key={item.id}
                  value={item.id}
                  disabled={item.state !== "ready"}
                >
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label className="detection-field">
            고정 분할
            <select
              value={draft.splitManifestId}
              onChange={(event) =>
                change({ splitManifestId: event.target.value })
              }
            >
              <option value="">선택해 주세요</option>
              {data.evaluationSplits
                ?.filter((item) => item.snapshotId === draft.snapshotId)
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.id} · {item.revision}
                  </option>
                ))}
            </select>
          </label>
          <label className="detection-field">
            평가 목적
            <select
              value={draft.purpose}
              onChange={(event) =>
                change({
                  purpose: event.target
                    .value as EvaluationPreparationDraft["purpose"],
                })
              }
            >
              <option value="validation">validation · 후보 비교</option>
              <option value="test">test · 최종 확인</option>
            </select>
          </label>
        </div>
        {split ? (
          <>
            <div className="detection-table-wrap">
              <table className="detection-table">
                <caption>고정 train / validation / test 분할</caption>
                <thead>
                  <tr>
                    <th>구간</th>
                    <th>시작</th>
                    <th>종료</th>
                    <th>행 수</th>
                    <th>실행 수</th>
                  </tr>
                </thead>
                <tbody>
                  {(["train", "validation", "test"] as const).map((part) => (
                    <tr key={part}>
                      <th scope="row">{part}</th>
                      <td>{valueText(split[part].start)}</td>
                      <td>{valueText(split[part].end)}</td>
                      <td>{valueText(split[part].rowCount)}</td>
                      <td>{valueText(split[part].executionCount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p>
              거래·실행 겹침 검사: {confirmed(split.overlapChecked)} ·
              train-only fit: {confirmed(split.trainOnlyFit)}
            </p>
            <p>
              fit 근거: {valueText(split.fitLineage)} · test 반복 횟수:{" "}
              {split.testRunCount ?? "미확인"}
            </p>
          </>
        ) : (
          <EmptyState>
            연결된 고정 분할이 없습니다. 분할 구간과 train-only fit 근거 확인이
            필요합니다.
          </EmptyState>
        )}
      </section>
      <section
        className="detection-card"
        aria-labelledby="preparation-spec-title"
      >
        <h2 id="preparation-spec-title">평가기준</h2>
        <label className="detection-field">
          기준 버전
          <select
            value={draft.specId}
            onChange={(event) => change({ specId: event.target.value })}
          >
            <option value="">기준 초안 작성</option>
            {data.evaluationSpecs?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} · {item.revision}
              </option>
            ))}
          </select>
        </label>
        {spec ? (
          <dl className="detection-summary">
            <div>
              <dt>지표·산식</dt>
              <dd>{spec.metricDefinition || "미확정"}</dd>
            </div>
            <div>
              <dt>라벨 근거</dt>
              <dd>{spec.labelSource || "미확정"}</dd>
            </div>
            <div>
              <dt>합격선</dt>
              <dd>
                {spec.acceptanceCriteria?.length
                  ? spec.acceptanceCriteria
                      .map(
                        (item) =>
                          `${item.metric} ${item.operator === "gte" ? "≥" : "≤"} ${item.value} · ${item.source}`,
                      )
                      .join(" / ")
                  : "미확정"}
              </dd>
            </div>
          </dl>
        ) : (
          <>
            <fieldset className="detection-options">
              <legend>평가 지표</legend>
              {metricRows.map((item) => (
                <label key={item.key} className="detection-checkbox">
                  <input
                    type="checkbox"
                    checked={draft.metrics.includes(item.key)}
                    onChange={(event) =>
                      change({
                        metrics: event.target.checked
                          ? [...draft.metrics, item.key as EvaluationMetric]
                          : draft.metrics.filter((key) => key !== item.key),
                      })
                    }
                  />
                  {item.label}
                </label>
              ))}
            </fieldset>
            <div className="detection-form-grid">
              <label className="detection-field">
                지표 산식·정의
                <textarea
                  value={draft.metricDefinition}
                  onChange={(event) =>
                    change({ metricDefinition: event.target.value })
                  }
                />
              </label>
              <label className="detection-field">
                라벨 출처·판정 근거
                <textarea
                  value={draft.labelSource}
                  onChange={(event) =>
                    change({ labelSource: event.target.value })
                  }
                />
              </label>
              <label className="detection-field">
                합격선 합의 근거
                <textarea
                  value={draft.acceptanceNote}
                  onChange={(event) =>
                    change({ acceptanceNote: event.target.value })
                  }
                />
              </label>
            </div>
            <p className="detection-note">
              입력 내용은 기준 초안입니다. 합격선은 서버에서 확인된 기준 버전이
              있어야 확정됩니다.
            </p>
          </>
        )}
      </section>
      <EvaluationPreparationScenarios draft={draft} change={change} />
      <section
        className="detection-card"
        aria-labelledby="preparation-csv-title"
      >
        <h2 id="preparation-csv-title">기존 시험 CSV 확인</h2>
        <p className="detection-note">
          브라우저 검사 형식: scenario(delay/stall/burst/normal),
          label(anomaly/normal/unknown). 5 MiB·10000행 이하. 서버 검증이나 이상
          주입 결과가 아닙니다.
        </p>
        <label className="detection-field">
          CSV 파일
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={(event) => {
              void fileChange(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </label>
        {csvReading && <p role="status">CSV 확인 중...</p>}
        {csvErrors.length > 0 && (
          <ul className="detection-error" role="alert">
            {csvErrors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        )}
        {draft.csv && (
          <>
            <p>
              확인한 행: {draft.csv.rowCount} · 헤더:{" "}
              {draft.csv.columns.join(", ")}
            </p>
            <p>
              라벨:{" "}
              {Object.entries(draft.csv.labels)
                .map(([key, count]) => `${key} ${count}`)
                .join(" / ")}
            </p>
            <div className="detection-table-wrap">
              <table className="detection-table">
                <caption>라벨·시나리오 미리보기</caption>
                <thead>
                  <tr>
                    <th>시나리오</th>
                    <th>라벨</th>
                  </tr>
                </thead>
                <tbody>
                  {draft.csv.samples.map((row, index) => (
                    <tr key={index}>
                      <td>{row.scenario}</td>
                      <td>{row.label}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Button
              variant="outline"
              onClick={() => {
                readGeneration.current++;
                setCsvReading(false);
                setCsvErrors([]);
                change({ csv: null });
              }}
            >
              CSV 확인 정보 제거
            </Button>
          </>
        )}
      </section>
      <section className="detection-card" aria-label="평가 세트 생성 확인">
        <p className="detection-note">
          편집 내용 유지 · 아직 서버에 고정된 평가 세트가 아닙니다.
        </p>
        {errors.length > 0 && (
          <ul className="detection-error" role="status">
            {errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        )}
        <ServiceAction
          operation="createEvaluationSet"
          label="고정 평가 세트 생성"
          available={data.preparationCapabilities?.createEvaluationSet === true}
          disabled={errors.length > 0 || csvReading || csvErrors.length > 0}
          payload={{ preparation: draft }}
        >
          <p>
            {draft.name} · {draft.purpose}
          </p>
          <p>생성 요청 접수와 고정 세트 생성 완료는 별개입니다.</p>
        </ServiceAction>
      </section>
    </div>
  );
};
const confirmed = (value: boolean | null) =>
  value === true ? "확인됨" : value === false ? "검사 실패" : "미확인";
