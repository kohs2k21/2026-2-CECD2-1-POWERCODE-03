import { Link } from "react-router-dom";
import { Button } from "../../../components/ui/button";
import { ServiceAction } from "../components/ServiceAction";
import { currentConfigurationFingerprint } from "../data/configuration";
import {
  conditionFromEvaluationSet,
  evaluationSetErrors,
} from "../data/evaluationPreparation";
import type {
  DetectionData,
  EvaluationCondition,
  VersionBundle,
} from "../data/types";
import { ConditionDetails } from "./EvaluationRecords";

export const EvaluationConditionForm = ({
  data,
  target,
  localPreview,
  condition,
  replace,
}: {
  data: DetectionData;
  target: VersionBundle;
  localPreview: boolean;
  condition: EvaluationCondition;
  replace: (condition: EvaluationCondition) => void;
}) => {
  const set = data.evaluationSets?.find(
    (item) =>
      item.id === condition.evaluationSetId &&
      item.revision === condition.evaluationSetRevision,
  );
  const authoritative = set ? conditionFromEvaluationSet(set) : condition;
  const errors = evaluationSetErrors(set, data);
  const fingerprint = currentConfigurationFingerprint(target, data);
  const fixedConfiguration =
    fingerprint != null && fingerprint === target.configurationFingerprint;
  return (
    <form onSubmit={(event) => event.preventDefault()}>
      <p>
        {target.name} · {localPreview ? "로컬 구성 미리보기" : "평가 대상"}
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
      <label className="detection-field">
        고정 평가 세트
        <select
          value={set ? `${set.id}@${set.revision}` : ""}
          onChange={(event) => {
            const selected = data.evaluationSets?.find(
              (item) => `${item.id}@${item.revision}` === event.target.value,
            );
            replace(
              selected
                ? conditionFromEvaluationSet(selected)
                : {
                    snapshotId: "",
                    protocolId: "",
                    splitVersion: "",
                    scenario: "",
                    purpose: "",
                  },
            );
          }}
        >
          <option value="">선택해 주세요</option>
          {data.evaluationSets?.map((item) => (
            <option
              key={`${item.id}@${item.revision}`}
              value={`${item.id}@${item.revision}`}
            >
              {item.name} · {item.revision} · {item.purpose}
            </option>
          ))}
        </select>
      </label>
      <ConditionDetails condition={authoritative} />
      <p className="detection-note">
        선택한 고정 세트의 데이터셋·기준·분할·시나리오·목적을 함께 사용합니다.
        validation은 후보 비교, test는 최종 확인입니다.
      </p>
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
            condition: authoritative,
            evaluationSetId: set?.id,
            evaluationSetRevision: set?.revision,
            configurationFingerprint: fingerprint,
          }}
        >
          <p>평가 대상: {target.name}</p>
          <ConditionDetails condition={authoritative} />
          <p>평가 요청은 운영 버전을 변경하지 않습니다.</p>
        </ServiceAction>
        <Button asChild variant="outline">
          <Link to="/detection/evaluation?tab=preparation">평가 세트 준비</Link>
        </Button>
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
