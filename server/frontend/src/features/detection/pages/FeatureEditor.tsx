import { useState } from "react";
import { Button } from "../../../components/ui/button";
import { useSearchParams } from "react-router-dom";
import { featureCatalog } from "../data/catalog";
import {
  defaultFeatureEditor,
  allowedOperations,
  operationLabels,
  requiresRightInput,
  validateFeatureEditor,
  previewFeature,
} from "../data/featureBuilder";
import { useCreateDraft } from "../data/createDraft";
import type { FeatureDefinition } from "../data/types";
import { DetailMissing } from "../components/DetectionQueryBoundary";
export const FeatureEditor = ({ featureId }: { featureId: string | null }) => {
  const { draft, update } = useCreateDraft();
  const [params, setParams] = useSearchParams();
  const [samples, setSamples] = useState({ left: "", right: "" });
  const [preview, setPreview] = useState<ReturnType<
    typeof previewFeature
  > | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [retained, setRetained] = useState(false);
  const feature = featureCatalog.find((item) => item.id === featureId);
  const custom = Boolean(
    featureId && draft.customFeatureIds.includes(featureId),
  );
  if (!feature && !custom)
    return (
      <section className="detection-card">
        <h2>선택 속성·파생변수</h2>
        <DetailMissing>
          목록에서 속성을 선택하거나 파생변수를 추가해 주세요.
        </DetailMissing>
      </section>
    );
  const value = draft.featureEdits[featureId!] ?? defaultFeatureEditor(feature);
  const leftFeature = featureCatalog.find((item) => item.id === value.left);
  const change = (patch: Partial<typeof value>) => {
    update({
      featureEdits: {
        ...draft.featureEdits,
        [featureId!]: { ...value, ...patch },
      },
    });
    setRetained(false);
    setPreview(null);
  };
  const close = () => {
    const next = new URLSearchParams(params);
    next.delete("feature");
    setParams(next);
  };
  return (
    <section className="detection-card">
      <div className="detection-page-header">
        <h2>
          {feature?.source !== "derived" && !custom
            ? "원천 속성 상세"
            : "파생변수 편집"}
        </h2>
        <Button variant="ghost" size="sm" onClick={close}>
          목록으로
        </Button>
      </div>
      {feature && (
        <dl>
          <dt>속성·정의</dt>
          <dd>
            {feature.source !== "derived"
              ? `${feature.source}.${feature.name}`
              : feature.name}
          </dd>
            <dt>계산 규칙</dt>
          <dd>{feature.expression}</dd>
          <dt>타입·단위</dt>
          <dd>
            {feature.type} · {feature.unit}
          </dd>
          <dt>준비 상태</dt>
          <dd>
            {feature.reason}
            {feature.fitRequired && " · 학습된 전처리 통계 필요"}
          </dd>
          <dt>가용 시점</dt>
          <dd>{feature.availableAt}</dd>
          <dt>수집·결측률·서로 다른 값</dt>
          <dd>— · — · —</dd>
          {feature.aliases.length > 0 && (
            <>
              <dt>검색 별칭</dt>
              <dd>{feature.aliases.join(", ")}</dd>
            </>
          )}
        </dl>
      )}
      {(feature?.source === "derived" || custom) &&
        feature?.readiness !== "deferred" && (
          <>
            <div className="detection-form-grid detection-feedback">
              <label className="detection-field">
                파생변수 이름
                <input
                  value={value.name}
                  maxLength={80}
                  onChange={(event) => change({ name: event.target.value })}
                />
              </label>
              <label className="detection-field">
                원천 속성
                <select
                  value={value.left}
                  onChange={(event) => {
                    const next = featureCatalog.find(
                      (item) => item.id === event.target.value,
                    );
                    change({
                      left: event.target.value,
                      operation: allowedOperations(next)[0],
                      right: "",
                    });
                  }}
                >
                  {featureCatalog
                    .filter((item) => item.readiness !== "deferred")
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.source === "derived"
                          ? item.name
                          : `${item.source}.${item.name}`}
                      </option>
                    ))}
                </select>
              </label>
              <label className="detection-field">
                허용 연산
                <select
                  value={value.operation}
                  onChange={(event) =>
                    change({
                      operation: event.target.value as typeof value.operation,
                    })
                  }
                >
                  {allowedOperations(leftFeature).map((operation) => (
                    <option key={operation} value={operation}>
                      {operationLabels[operation]}
                    </option>
                  ))}
                </select>
              </label>
              {requiresRightInput(value.operation) && (
                <label className="detection-field">
                  두 번째 속성
                  <select
                    value={value.right}
                    onChange={(event) => change({ right: event.target.value })}
                  >
                    <option value="">선택</option>
                    {featureCatalog
                      .filter(
                        (item) =>
                          item.type === leftFeature?.type &&
                          item.readiness !== "deferred",
                      )
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.source === "derived"
                            ? item.name
                            : `${item.source}.${item.name}`}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              <label className="detection-field">
                단위
                <select
                  value={value.unit}
                  onChange={(event) => change({ unit: event.target.value })}
                >
                  {["unitless", "ms", "s", "건"].map((unit) => (
                    <option key={unit} value={unit}>
                      {unit === "unitless" ? "단위 없음" : unit}
                    </option>
                  ))}
                </select>
              </label>
              <label className="detection-field">
                결측 처리
                <select
                  value={value.missingPolicy}
                  onChange={(event) =>
                    change({
                      missingPolicy: event.target
                        .value as typeof value.missingPolicy,
                    })
                  }
                >
                  <option value="preserve">NULL 유지</option>
                  <option value="reject">필수 값 없으면 계산 보류</option>
                </select>
              </label>
              {leftFeature?.type === "timestamp" && (
                <label className="detection-field">
                  원천 시간대
                  <select
                    value={value.timezone}
                    onChange={(event) =>
                      change({ timezone: event.target.value })
                    }
                  >
                    <option value="">선택</option>
                    <option value="Asia/Seoul">Asia/Seoul</option>
                    <option value="UTC">UTC</option>
                  </select>
                </label>
              )}
            </div>
            <h3 className="detection-feedback">계산 규칙 미리보기</h3>
            <div className="detection-form-grid">
              <label className="detection-field">
                첫 번째 검증 입력
                <input
                  value={samples.left}
                  onChange={(event) => {
                    setSamples({ ...samples, left: event.target.value });
                    setPreview(null);
                  }}
                  placeholder={
                    leftFeature?.type === "timestamp"
                      ? "ISO 시각 + Z 또는 offset"
                      : "검증할 값"
                  }
                />
              </label>
              {requiresRightInput(value.operation) && (
                <label className="detection-field">
                  두 번째 검증 입력
                  <input
                    value={samples.right}
                    onChange={(event) => {
                      setSamples({ ...samples, right: event.target.value });
                      setPreview(null);
                    }}
                  />
                </label>
              )}
            </div>
            <div className="detection-actions">
              <Button
                variant="outline"
                onClick={() => {
                  setErrors(validateFeatureEditor(value));
                  setPreview(
                    previewFeature(value, samples.left, samples.right),
                  );
                }}
              >
                미리보기
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  const invalid = validateFeatureEditor(value);
                  setErrors(invalid);
                  if (!invalid.length) {
                    change({});
                    setRetained(true);
                  }
                }}
              >
                편집 내용 유지
              </Button>
            </div>
            {preview && (
              <p className="detection-note" role="status">
                결과: {preview.value ?? "—"}{" "}
                {preview.value !== null ? preview.unit : ""} · {preview.reason}
              </p>
            )}
            {errors.length > 0 && (
              <ul className="detection-error" role="alert">
                {errors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            )}
            {retained && (
              <p className="detection-note" role="status">
                편집 내용 유지 중
              </p>
            )}
          </>
        )}
    </section>
  );
};
