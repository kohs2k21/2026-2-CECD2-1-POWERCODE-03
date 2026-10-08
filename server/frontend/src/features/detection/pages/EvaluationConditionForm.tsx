import { Link } from "react-router-dom";
import { Button } from "../../../components/ui/button";
import { ServiceAction } from "../components/ServiceAction";
import { currentConfigurationFingerprint } from "../data/configuration";
import type {
  DetectionData,
  EvaluationCondition,
  VersionBundle,
} from "../data/types";
import { ConditionDetails, conditionLabels } from "./EvaluationRecords";
import { evaluationErrors } from "./operationPresentation";

export const EvaluationConditionForm = ({
  data,
  target,
  localPreview,
  condition,
  change,
}: {
  data: DetectionData;
  target: VersionBundle;
  localPreview: boolean;
  condition: EvaluationCondition;
  change: (key: keyof EvaluationCondition, value: string) => void;
}) => {
  const errors = evaluationErrors(condition, data);
  const fingerprint = currentConfigurationFingerprint(target, data);
  const fixedConfiguration =
    fingerprint != null && fingerprint === target.configurationFingerprint;
  return (
    <form onSubmit={(event) => event.preventDefault()}>
      <p>
        {target.name} ·{" "}
        {localPreview ? "로컬 구성 미리보기" : "생성된 평가 후보"}
      </p>
      {localPreview && (
        <p className="detection-note">
          아직 후보가 생성되지 않았습니다. 새 후보 생성 완료 후 해당 후보를
          선택해 평가합니다.
        </p>
      )}
      {!fixedConfiguration && (
        <p className="detection-error">
          구성 내용이 변경되었거나 확인되지 않았습니다. 새 후보를 고정한 뒤
          평가해 주세요.
        </p>
      )}
      <div className="detection-form-grid">
        <label className="detection-field">
          데이터셋
          <select
            value={condition.snapshotId}
            onChange={(event) => change("snapshotId", event.target.value)}
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
        {(["protocolId", "splitVersion", "scenario", "purpose"] as const).map(
          (key) => (
            <label key={key} className="detection-field">
              {conditionLabels[key]}
              <input
                value={condition[key]}
                onChange={(event) => change(key, event.target.value)}
                required
              />
            </label>
          ),
        )}
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
          key={JSON.stringify({ id: target.id, fingerprint })}
          operation="evaluate"
          label="평가 실행"
          available={data.capabilities.evaluate}
          disabled={errors.length > 0 || localPreview || !fixedConfiguration}
          payload={{
            candidateId: target.id,
            condition,
            configurationFingerprint: fingerprint,
          }}
        >
          <p>선택 후보: {target.name}</p>
          <ConditionDetails condition={condition} />
          <p className="detection-note">
            평가 요청은 운영 버전을 변경하지 않습니다. 결과는 평가 이력에서
            확인합니다.
          </p>
        </ServiceAction>
        {!localPreview && (
          <Button asChild variant="outline">
            <Link
              to={`/detection/versions?candidate=${encodeURIComponent(target.id)}`}
            >
              버전 관리
            </Link>
          </Button>
        )}
      </div>
    </form>
  );
};
