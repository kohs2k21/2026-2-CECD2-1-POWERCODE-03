import { useEffect, useMemo } from "react";
import { Link, useLocation } from "react-router-dom";
import { Button } from "../../../components/ui/button";
import { ServiceAction } from "../components/ServiceAction";
import {
  composePreview,
  compositionErrors,
  type CompositionInput,
} from "../data/configuration";
import type { DetectionData, VersionBundle } from "../data/types";
import { BundleDetails } from "./VersionDetails";
import {
  compositionInputFrom,
  useCompositionDraft,
} from "./useCompositionDraft";

export const CompositionEditor = ({
  data,
  mode,
  onPreview,
}: {
  data: DetectionData;
  mode: "versions" | "evaluation";
  onPreview?: (preview: VersionBundle | null) => void;
}) => {
  const location = useLocation();
  const active = data.versions.find((item) => item.id === data.activeVersionId);
  const transferred =
    mode === "evaluation"
      ? compositionInputFrom(
          location.state && typeof location.state === "object"
            ? location.state.detectionComposition
            : null,
        )
      : null;
  const initial: CompositionInput = transferred ?? {
    name: mode === "versions" ? "새 운영 구성" : "새 평가 구성",
    modelArtifactId:
      active?.modelArtifactId ??
      data.modelArtifacts.find((item) => item.state === "succeeded")?.id ??
      "",
    ruleVersionIds: active?.ruleVersionIds ?? [],
    explanationVersion: active?.explanationVersion ?? "",
  };
  const {
    input: storedInput,
    change,
    replace,
  } = useCompositionDraft(location.pathname, initial);
  const relatedVersion =
    active?.modelArtifactId === storedInput.modelArtifactId
      ? active
      : data.versions.find(
          (item) =>
            item.modelArtifactId === storedInput.modelArtifactId &&
            item.explanationVersion,
        );
  const input = {
    ...storedInput,
    explanationVersion: relatedVersion?.explanationVersion ?? "",
  };
  const inputKey = JSON.stringify(input);
  const preview = useMemo(() => composePreview(input, data), [inputKey, data]);
  useEffect(() => onPreview?.(preview), [preview, onPreview]);
  const errors = compositionErrors(input, data);
  const artifacts = data.modelArtifacts.filter(
    (item) => item.state === "succeeded",
  );
  const rules = data.ruleVersions.filter((item) => item.state === "succeeded");
  return (
    <section
      className="detection-card"
      aria-labelledby={`${mode}-composition-title`}
    >
      <h2 id={`${mode}-composition-title`}>
        {mode === "versions" ? "운영할 모델·룰 조합" : "평가할 모델·룰 조합"}
      </h2>
      <p className="detection-note">
        생성 완료한 모델 산출물과 룰 버전을 선택합니다. 모델의 피처·학습된
        통계·전처리는 산출물에 고정됩니다.
      </p>
      {transferred && JSON.stringify(transferred) !== inputKey && (
        <div className="detection-actions">
          <Button variant="outline" onClick={() => replace(transferred)}>
            전달된 구성으로 검토
          </Button>
        </div>
      )}
      <div className="detection-form-grid">
        <label className="detection-field">
          구성 이름
          <input
            value={input.name}
            maxLength={80}
            onChange={(event) => change({ name: event.target.value })}
          />
        </label>
        <label className="detection-field">
          {mode === "versions" ? "운영할 모델 산출물" : "평가할 모델 산출물"}
          <select
            value={input.modelArtifactId}
            onChange={(event) =>
              change({ modelArtifactId: event.target.value })
            }
          >
            <option value="">완료한 산출물을 선택해 주세요</option>
            {artifacts.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} · {item.version}
              </option>
            ))}
          </select>
        </label>
        <div className="detection-field">
          <span>설명 기준</span>
          <span>{input.explanationVersion || "미확인"}</span>
          <span className="detection-note">
            선택한 모델 산출물에 연결된 구성의 설명 기준을 사용합니다.
          </span>
        </div>
      </div>
      <fieldset>
        <legend>
          {mode === "versions" ? "운영할 룰 버전" : "평가할 룰 버전"}
        </legend>
        {rules.length ? (
          rules.map((rule) => (
            <label key={rule.id} className="detection-field">
              <span>
                <input
                  type="checkbox"
                  checked={input.ruleVersionIds.includes(rule.id)}
                  onChange={(event) =>
                    change({
                      ruleVersionIds: event.target.checked
                        ? [...input.ruleVersionIds, rule.id]
                        : input.ruleVersionIds.filter((id) => id !== rule.id),
                    })
                  }
                />{" "}
                {rule.name} · {rule.version}
              </span>
            </label>
          ))
        ) : (
          <p>생성 완료한 룰 버전이 없습니다.</p>
        )}
      </fieldset>
      {errors.length > 0 && (
        <ul className="detection-error" role="status">
          {errors.map((error) => (
            <li key={error}>{error}</li>
          ))}
        </ul>
      )}
      {preview && (
        <>
          <h3>구성 미리보기</h3>
          <p className="detection-note">
            아직 생성된 후보가 아닙니다. 새 후보 생성 후 이 구성으로 평가할 수
            있습니다.
          </p>
          <BundleDetails bundle={preview} data={data} />
          <div className="detection-actions">
            <ServiceAction
              key={preview.configurationFingerprint}
              operation="createCandidate"
              label="새 후보 생성"
              available={data.capabilities.createCandidate}
              requestDisabled={!preview.explanationVersion}
              payload={{
                composition: input,
                configurationFingerprint: preview.configurationFingerprint,
              }}
            >
              <p>생성할 후보 구성: {preview.name}</p>
              <BundleDetails bundle={preview} data={data} />
              <p className="detection-note">
                요청 접수는 후보 생성 완료가 아닙니다. 완료한 후보를 목록에서
                선택한 뒤 평가해야 합니다.
              </p>
              {!preview.explanationVersion && (
                <p className="detection-error">
                  설명 기준이 미확인이라 후보 생성 요청을 실행할 수 없습니다.
                </p>
              )}
            </ServiceAction>
            {mode === "versions" && (
              <Button asChild variant="outline">
                <Link
                  to="/detection/evaluation?selection=composition"
                  state={{ detectionComposition: input }}
                >
                  이 구성 평가하기
                </Link>
              </Button>
            )}
          </div>
        </>
      )}
    </section>
  );
};
