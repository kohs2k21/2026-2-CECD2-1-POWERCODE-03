import { Link, useSearchParams } from "react-router-dom";
import { Button } from "../../../components/ui/button";
import { EmptyState } from "../../../components/ui/feedback";
import {
  DetectionQueryBoundary,
  DetailMissing,
  jobStateLabels,
  valueText,
} from "../components/DetectionQueryBoundary";
import { rawFeatures } from "../data/catalog";
import type {
  CollectionHistory,
  CollectionSource,
  DetectionData,
} from "../data/types";
import { useDetectionQuery } from "../data/useDetectionQuery";
import { CollectionObservations } from "./CollectionObservations";
import { validDateRange } from "./operationPresentation";
import {
  DetectionTabs,
  detectionTabHref,
  useDetectionTab,
} from "../components/DetectionTabs";

const collectionTabs = [
  { id: "status", label: "수집 상태" },
  { id: "fields", label: "원본 필드" },
  { id: "history", label: "수집 이력" },
  { id: "storage", label: "저장 공간" },
] as const;

const sourceNames: Record<CollectionSource["id"], string> = {
  T: "TRANSACTION",
  P: "PROCESS",
  M: "MESSAGE",
  B: "BODY",
};

const sourceLabels = {
  unknown: "수집 상태 미확인",
  collecting: "수집 중",
  delayed: "수집 지연",
  failed: "수집 실패",
  stopped: "수집 중지",
} as const;
const sourceFields = (id: CollectionSource["id"]) =>
  rawFeatures.filter(
    (field) =>
      field.source ===
      (id === "T" ? "transaction" : id === "P" ? "process" : ""),
  );

const CollectionHistoryDetails = ({ item }: { item: CollectionHistory }) => (
  <section className="detection-card" aria-labelledby="collection-detail-title">
    <h2 id="collection-detail-title">수집 이력 상세</h2>
    <dl className="detection-summary">
      <div>
        <dt>이력 ID</dt>
        <dd>{item.id}</dd>
      </div>
      <div>
        <dt>원본</dt>
        <dd>{sourceNames[item.source]}</dd>
      </div>
      <div>
        <dt>수집 범위</dt>
        <dd>{item.range}</dd>
      </div>
      <div>
        <dt>상태</dt>
        <dd>{jobStateLabels[item.state]}</dd>
      </div>
      <div>
        <dt>요청 시각</dt>
        <dd>{valueText(item.requestedAt)}</dd>
      </div>
      <div>
        <dt>저장 건수</dt>
        <dd>{valueText(item.count)}</dd>
      </div>
    </dl>
    {item.failure && (
      <p className="detection-error" role="alert">
        {item.failure}
      </p>
    )}
  </section>
);

const CollectionWorkbench = ({
  data,
  activeTab,
}: {
  data: DetectionData;
  activeTab: string;
}) => {
  const [params, setParams] = useSearchParams();
  const href = (values: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) =>
      value ? next.set(key, value) : next.delete(key),
    );
    return `?${next.toString()}`;
  };
  const patch = (values: Record<string, string | null>) =>
    setParams(href(values).slice(1));
  const start = params.get("from") ?? "";
  const end = params.get("to") ?? "";
  const source = params.get("source") ?? "all";
  const field = params.get("field") ?? "";
  const search = params.get("q") ?? "";
  const rangeValid = validDateRange(start, end);
  const visibleSources = data.collection.filter(
    (item) =>
      (source === "all" || item.id === source) &&
      (!field || sourceFields(item.id).some((value) => value.id === field)) &&
      `${item.id} ${item.name} ${item.description} ${sourceFields(item.id)
        .map((value) => value.name)
        .join(" ")}`
        .toLocaleLowerCase()
        .includes(search.toLocaleLowerCase()),
  );
  const availableFields = rawFeatures.filter(
    (item) =>
      source === "all" ||
      item.source ===
        (source === "T" ? "transaction" : source === "P" ? "process" : ""),
  );
  const visibleFields = availableFields.filter(
    (item) =>
      (!field || item.id === field) &&
      `${item.name} ${item.expression}`
        .toLocaleLowerCase()
        .includes(search.toLocaleLowerCase()),
  );
  const historyId = params.get("history");
  const detail = data.collectionHistory.find((item) => item.id === historyId);
  const history = rangeValid
    ? data.collectionHistory.filter((item) => {
        if (source !== "all" && item.source !== source) return false;
        if (
          !`${item.id} ${sourceNames[item.source]} ${item.range}`
            .toLocaleLowerCase()
            .includes(search.toLocaleLowerCase())
        )
          return false;
        const date =
          item.requestedAt && Number.isFinite(Date.parse(item.requestedAt))
            ? item.requestedAt.slice(0, 10)
            : null;
        return !date || ((!start || date >= start) && (!end || date <= end));
      })
    : [];

  return (
    <div className="detection-stack">
      {activeTab !== "storage" && (
        <section
          className="detection-card"
          aria-labelledby="collection-filter-title"
        >
          <h2 id="collection-filter-title">수집 조회 조건</h2>
          <div className="detection-form-grid">
            <label className="detection-field">
              시작일
              <input
                type="date"
                value={start}
                onChange={(event) => patch({ from: event.target.value })}
                aria-invalid={!rangeValid}
              />
            </label>
            <label className="detection-field">
              종료일
              <input
                type="date"
                value={end}
                onChange={(event) => patch({ to: event.target.value })}
                aria-invalid={!rangeValid}
              />
            </label>
            <label className="detection-field">
              원본
              <select
                value={source}
                onChange={(event) =>
                  patch({ source: event.target.value, field: null })
                }
              >
                <option value="all">전체</option>
                {data.collection.map((item) => (
                  <option key={item.id} value={item.id}>
                    {sourceNames[item.id]}
                  </option>
                ))}
              </select>
            </label>
            <label className="detection-field">
              필드
              <select
                value={field}
                onChange={(event) => patch({ field: event.target.value })}
              >
                <option value="">전체 필드</option>
                {availableFields.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.source.toUpperCase()} · {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="detection-field">
              검색
              <input
                value={search}
                onChange={(event) => patch({ q: event.target.value })}
                placeholder="원본·필드·이력"
              />
            </label>
          </div>
          {!rangeValid && (
            <p className="detection-error" role="alert">
              유효한 날짜와 시작일 이후의 종료일을 선택해 주세요.
            </p>
          )}
          <p className="detection-note">
            기간은 요청 시각이 기록된 수집 이력에 적용됩니다. 시각이 없는 이력은
            기간 미확인으로 표시됩니다. 현재 상태의 건수는 최근 관측값입니다.
          </p>
        </section>
      )}
      {activeTab === "status" && (
        <>
          <section aria-labelledby="collection-source-title">
            <h2 id="collection-source-title">원본별 최근 수집 상태</h2>
            {visibleSources.length ? (
              <div className="detection-grid">
                {visibleSources.map((item) => (
                  <article key={item.id} className="detection-card">
                    <h3>{sourceNames[item.id]}</h3>
                    <p
                      className={
                        item.state === "failed" || item.state === "delayed"
                          ? "detection-error"
                          : "detection-status"
                      }
                    >
                      {sourceLabels[item.state]}
                    </p>
                    <dl className="detection-summary">
                      <div>
                        <dt>저장 건수</dt>
                        <dd>{valueText(item.count)}</dd>
                      </div>
                      <div>
                        <dt>대기 건수</dt>
                        <dd>{valueText(item.pending)}</dd>
                      </div>
                      <div>
                        <dt>수집 공백</dt>
                        <dd>{valueText(item.gapCount)}</dd>
                      </div>
                      <div>
                        <dt>최근 성공 시각</dt>
                        <dd>{valueText(item.lastSuccessAt)}</dd>
                      </div>
                    </dl>
                    <p className="detection-note">{item.description}</p>
                    {item.error && (
                      <p className="detection-error" role="alert">
                        {item.error}
                      </p>
                    )}
                  </article>
                ))}
              </div>
            ) : (
              <EmptyState>
                {data.collection.length
                  ? "조회 조건에 맞는 원본이 없습니다."
                  : "원본 수집 상태가 없습니다."}
              </EmptyState>
            )}
            <p className="detection-note">
              BODY는 별도 저장·재시도합니다. 기본 PROCESS 판정은 BODY 수집을
              기다리지 않습니다. 미확인 건수는 0건으로 간주하지 않습니다.
            </p>
          </section>
          <CollectionObservations data={data} view="latency" />
        </>
      )}
      {activeTab === "fields" && (
        <section
          className="detection-card"
          aria-labelledby="collection-fields-title"
        >
          <h2 id="collection-fields-title">원본 필드</h2>
          {visibleFields.length ? (
            <div className="detection-table-wrap">
              <table className="detection-table">
                <caption>TRANSACTION·PROCESS 원본 필드 정의</caption>
                <thead>
                  <tr>
                    <th scope="col">원본</th>
                    <th scope="col">필드</th>
                    <th scope="col">유형</th>
                    <th scope="col">수집 확인</th>
                    <th scope="col">결측률</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleFields.map((item) => (
                    <tr key={item.id}>
                      <td>{item.source.toUpperCase()}</td>
                      <th scope="row">{item.name}</th>
                      <td>{item.type}</td>
                      <td>
                        {item.collected == null
                          ? "—"
                          : item.collected
                            ? "확인됨"
                            : "미수집"}
                      </td>
                      <td>
                        {item.missingRate == null
                          ? "—"
                          : `${(item.missingRate * 100).toFixed(1)}%`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState>
              {source === "M" || source === "B"
                ? "원본 필드의 수집·스키마 관측 정보가 없습니다."
                : "조회 조건에 맞는 필드가 없습니다."}
            </EmptyState>
          )}
        </section>
      )}
      {activeTab === "storage" && (
        <CollectionObservations data={data} view="storage" />
      )}
      {activeTab === "history" && (
        <>
          <section
            className="detection-card"
            aria-labelledby="collection-history-title"
          >
            <h2 id="collection-history-title">수집 이력</h2>
            {!rangeValid ? (
              <p className="detection-error">조회 기간을 먼저 확인해 주세요.</p>
            ) : history.length ? (
              <ul className="detection-check-list">
                {history.map((item) => (
                  <li key={item.id}>
                    <Button
                      asChild
                      variant="ghost"
                      className="detection-list-button"
                    >
                      <Link
                        to={href({ tab: "history", history: item.id })}
                        aria-current={
                          historyId === item.id ? "true" : undefined
                        }
                      >
                        <span>
                          {sourceNames[item.source]} · {item.range}
                        </span>
                        <span>
                          {jobStateLabels[item.state]} ·{" "}
                          {item.requestedAt ? item.requestedAt : "기간 미확인"}
                        </span>
                      </Link>
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState>
                {data.collectionHistory.length
                  ? "조건에 맞는 수집 이력이 없습니다."
                  : "수집 이력이 없습니다."}
              </EmptyState>
            )}
          </section>
          {historyId && (
            <>
              {detail ? (
                <CollectionHistoryDetails item={detail} />
              ) : (
                <DetailMissing />
              )}
              <div className="detection-actions">
                <Button asChild variant="outline">
                  <Link to={href({ tab: "history", history: null })}>
                    수집 목록으로 돌아가기
                  </Link>
                </Button>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
};

export const CollectionPage = () => {
  const query = useDetectionQuery();
  const [params] = useSearchParams();
  const fallback = params.has("history")
    ? "history"
    : params.has("field")
      ? "fields"
      : "status";
  const activeTab = useDetectionTab(
    collectionTabs.map((item) => item.id),
    fallback,
  );
  return (
    <section className="detection-page">
      <header className="detection-page-header">
        <h1>데이터 수집 현황</h1>
        <Button
          variant="outline"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          상태 다시 조회
        </Button>
      </header>
      <DetectionTabs
        items={collectionTabs.map((item) => ({
          ...item,
          to: detectionTabHref(params, item.id),
        }))}
        activeId={activeTab}
        ariaLabel="수집 현황 내용"
      />
      <DetectionQueryBoundary query={query}>
        {(data) => <CollectionWorkbench data={data} activeTab={activeTab} />}
      </DetectionQueryBoundary>
    </section>
  );
};
