import { useState } from "react";
import { Button } from "../../../components/ui/button";
import type { FeatureEditorValue } from "../data/createDraft";
import {
  compileFormula,
  evaluateFormula,
  type FormulaFeature,
  type FormulaValue,
  legacyEditorExpression,
} from "../data/formula";
import { validateFeatureEditor } from "../data/featureBuilder";
import { FormulaInput } from "./FormulaInput";

export const FormulaEditor = ({
  value,
  features,
  onChange,
  onDelete,
}: {
  value: FeatureEditorValue;
  features: FormulaFeature[];
  onChange: (patch: Partial<FeatureEditorValue>) => void;
  onDelete: () => void;
}) => {
  const [samples, setSamples] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<ReturnType<
    typeof evaluateFormula
  > | null>(null);
  const [retained, setRetained] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const expression = value.expression ?? legacyEditorExpression(value);
  const compiled = compileFormula(expression, features);
  const errors = validateFeatureEditor(value, features);
  const change = (patch: Partial<FeatureEditorValue>) => {
    onChange({ expression, ...patch });
    setRetained(false);
    setPreview(null);
  };
  const sampleFeatures = compiled.sampleInputIds
    .map((id) => features.find((feature) => feature.id === id))
    .filter((feature): feature is FormulaFeature => Boolean(feature));
  const calculate = () => {
    setShowErrors(true);
    if (errors.length) {
      setPreview(null);
      return;
    }
    const parsed: Record<string, FormulaValue> = {};
    for (const feature of sampleFeatures) {
      const raw = samples[feature.id]?.trim() ?? "";
      if (!raw) parsed[feature.id] = null;
      else if (feature.type === "number") {
        if (!Number.isFinite(Number(raw))) {
          setPreview({
            value: null,
            unit: compiled.unit,
            reason: `${feature.name}: 숫자를 입력해 주세요.`,
          });
          return;
        }
        parsed[feature.id] = raw;
      } else if (feature.type === "boolean") {
        if (!["true", "false"].includes(raw)) {
          setPreview({
            value: null,
            unit: compiled.unit,
            reason: `${feature.name}: true 또는 false를 입력해 주세요.`,
          });
          return;
        }
        parsed[feature.id] = raw === "true";
      } else parsed[feature.id] = raw;
    }
    setPreview(
      evaluateFormula(compiled, parsed, {
        timezone: value.timezone,
        missingPolicy: value.missingPolicy,
      }),
    );
  };
  return (
    <div className="detection-stack detection-feedback">
      <label className="detection-field">
        파생변수 이름
        <input
          value={value.name}
          maxLength={80}
          onChange={(event) => change({ name: event.target.value })}
        />
      </label>
      <FormulaInput
        value={expression}
        features={features}
        onChange={(expression) =>
          change({
            expression,
            unit: compileFormula(expression, features).unit,
          })
        }
      />
      <p className="detection-note">
        계산 결과: {compiled.outputType ?? "확인 필요"} ·{" "}
        {compiled.unit || "단위 없음"}
        {compiled.fitRequired ? " · 학습 통계 필요" : ""}
      </p>
      <fieldset className="formula-options">
        <legend>결측 처리</legend>
        {(
          [
            ["preserve", "NULL 유지"],
            ["reject", "필수 값 없으면 계산 보류"],
          ] as const
        ).map(([policy, label]) => (
          <label key={policy}>
            <input
              type="radio"
              name="feature-missing"
              checked={value.missingPolicy === policy}
              onChange={() => change({ missingPolicy: policy })}
            />
            {label}
          </label>
        ))}
      </fieldset>
      {compiled.requiresTimezone && (
        <fieldset className="formula-options">
          <legend>기준 시간대</legend>
          {["Asia/Seoul", "UTC"].map((timezone) => (
            <label key={timezone}>
              <input
                type="radio"
                name="feature-timezone"
                checked={value.timezone === timezone}
                onChange={() => change({ timezone })}
              />
              {timezone}
            </label>
          ))}
        </fieldset>
      )}
      <div className="formula-preview">
        <h3>계산 미리보기</h3>
        <div className="detection-form-grid">
          {sampleFeatures.map((feature) => (
            <label className="detection-field" key={feature.id}>
              {`${feature.id} 검증 입력`}
              <input
                value={samples[feature.id] ?? ""}
                onChange={(event) => {
                  setSamples({ ...samples, [feature.id]: event.target.value });
                  setPreview(null);
                }}
                placeholder={
                  feature.type === "timestamp"
                    ? "ISO 시각 + Z 또는 offset"
                    : "빈 입력은 NULL"
                }
              />
            </label>
          ))}
        </div>
        <div className="detection-actions">
          <Button variant="outline" onClick={calculate}>
            미리보기
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              setShowErrors(true);
              if (!errors.length) setRetained(true);
            }}
          >
            편집 내용 유지
          </Button>
          <Button variant="outline" onClick={onDelete}>
            파생변수 삭제
          </Button>
        </div>
        {preview && (
          <p className="detection-note" role="status">
            결과: {preview.value === null ? "—" : String(preview.value)}{" "}
            {preview.value !== null ? preview.unit : ""} · {preview.reason}
          </p>
        )}
      </div>
      {(showErrors || value.expression !== undefined) && errors.length > 0 && (
        <ul className="detection-error" role="alert">
          {Array.from(new Set(errors)).map((error) => (
            <li key={error}>{error}</li>
          ))}
        </ul>
      )}
      {retained && (
        <p className="detection-note" role="status">
          편집 내용 유지 중
        </p>
      )}
    </div>
  );
};
