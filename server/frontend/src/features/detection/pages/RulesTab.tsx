import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button } from "../../../components/ui/button";
import { Modal } from "../../../components/ui/Modal";
import { EmptyState } from "../../../components/ui/feedback";
import { ServiceAction } from "../components/ServiceAction";
import { useCreateDraft, validateRule } from "../data/createDraft";
import { featureCatalog } from "../data/catalog";
import type { DetectionData, RuleDefinition } from "../data/types";
import { DetailMissing } from "../components/DetectionQueryBoundary";
const operatorLabels = {
  gt: "초과",
  gte: "이상",
  isMissing: "결측 여부",
  mismatch: "합계 불일치",
};
export const RulesTab = ({ data }: { data: DetectionData }) => {
  const { draft, update } = useCreateDraft();
  const [params, setParams] = useSearchParams();
  const ruleId = params.get("rule");
  const ruleQuery = params.get("ruleQ") ?? "";
  const recommendationId = params.get("recommendation");
  const [errors, setErrors] = useState<string[]>([]);
  const [retained, setRetained] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: key === "ruleQ" });
  };
  const allRules = {
    ...Object.fromEntries(data.rules.map((rule) => [rule.id, rule])),
    ...draft.rules,
  };
  const selected = ruleId ? allRules[ruleId] : undefined;
  const ruleErrors = selected ? validateRule(selected) : [];
  const rulePayload = selected
    ? Object.fromEntries(
        Object.entries(selected).filter(([key]) => key !== "enabled"),
      )
    : {};
  const change = (patch: Partial<RuleDefinition>) => {
    if (selected) {
      update({
        rules: { ...draft.rules, [selected.id]: { ...selected, ...patch } },
      });
      setRetained(false);
      setErrors([]);
    }
  };
  const rules = Object.values(allRules).filter((rule) =>
    `${rule.name} ${rule.field}`
      .toLowerCase()
      .includes(ruleQuery.toLowerCase()),
  );
  const recommendation = data.recommendations.find(
    (item) => item.id === recommendationId,
  );
  const add = () => {
    const id = `rule-${Object.keys(allRules).length + 1}`;
    update({
      rules: {
        ...draft.rules,
        [id]: {
          id,
          name: "새 룰",
          field: "A00",
          operator: "gt",
          threshold: null,
          unit: "ms",
          enabled: true,
          description: "",
        },
      },
    });
    setParam("rule", id);
  };
  const selectRecommendation = () => {
    if (recommendation)
      update({
        recommendationDecisions: {
          ...draft.recommendationDecisions,
          [recommendation.id]: "accepted",
        },
        rules: {
          ...draft.rules,
          [recommendation.rule.id]: {
            ...(draft.rules[recommendation.rule.id] ?? recommendation.rule),
          },
        },
      });
  };
  return (
    <div className="detection-stack">
      <div className="detection-grid">
        <section className="detection-card">
          <div className="detection-page-header">
            <h2>룰 목록</h2>
            <Button variant="outline" size="sm" onClick={add}>
              룰 추가
            </Button>
          </div>
          <label className="detection-field">
            룰 검색
            <input
              value={ruleQuery}
              onChange={(event) => setParam("ruleQ", event.target.value)}
            />
          </label>
          <div className="detection-table-wrap detection-feedback">
            <table className="detection-table">
              <thead>
                <tr>
                  <th>룰</th>
                  <th>조건</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => (
                  <tr key={rule.id}>
                    <td>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="detection-list-button"
                        onClick={() => {
                          setParam("rule", rule.id);
                          setErrors([]);
                          setRetained(false);
                        }}
                      >
                        {rule.name}
                      </Button>
                    </td>
                    <td>
                      {operatorLabels[rule.operator]} {rule.threshold ?? "—"}{" "}
                      {rule.unit}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rules.length === 0 && (
              <EmptyState>조건에 맞는 룰이 없습니다.</EmptyState>
            )}
          </div>
          <div className="detection-actions">
            <Button variant="outline" onClick={() => setResetOpen(true)}>
              룰 기본값 복원
            </Button>
          </div>
        </section>
        <section className="detection-card">
          <div className="detection-page-header">
            <h2>룰 편집</h2>
            {selected && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setParam("rule", "")}
              >
                목록으로
              </Button>
            )}
          </div>
          {selected ? (
            <>
              <div className="detection-form-grid">
                <label className="detection-field">
                  룰 이름
                  <input
                    value={selected.name}
                    maxLength={80}
                    onChange={(event) => change({ name: event.target.value })}
                  />
                </label>
                <label className="detection-field">
                  속성
                  <select
                    value={selected.field}
                    onChange={(event) => change({ field: event.target.value })}
                  >
                    {featureCatalog
                      .filter((feature) => feature.readiness !== "deferred")
                      .map((feature) => (
                        <option key={feature.id} value={feature.id}>
                          {feature.source === "derived"
                            ? feature.name
                            : `${feature.source}.${feature.name}`}
                        </option>
                      ))}
                  </select>
                </label>
                <label className="detection-field">
                  조건
                  <select
                    value={selected.operator}
                    onChange={(event) =>
                      change({
                        operator: event.target
                          .value as RuleDefinition["operator"],
                      })
                    }
                  >
                    {Object.entries(operatorLabels).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                {(selected.operator === "gt" ||
                  selected.operator === "gte") && (
                  <label className="detection-field">
                    임계값
                    <input
                      inputMode="decimal"
                      value={
                        draft.ruleThresholdInputs[selected.id] ??
                        (selected.threshold === null
                          ? ""
                          : String(selected.threshold))
                      }
                      onChange={(event) => {
                        const raw = event.target.value;
                        update({
                          rules: {
                            ...draft.rules,
                            [selected.id]: {
                              ...selected,
                              threshold: raw.trim() === "" ? null : Number(raw),
                            },
                          },
                          ruleThresholdInputs: {
                            ...draft.ruleThresholdInputs,
                            [selected.id]: raw,
                          },
                        });
                        setRetained(false);
                        setErrors([]);
                      }}
                    />
                  </label>
                )}
                <label className="detection-field">
                  단위
                  <select
                    value={selected.unit}
                    onChange={(event) => change({ unit: event.target.value })}
                  >
                    {["ms", "s", "건", "—"].map((unit) => (
                      <option key={unit} value={unit}>
                        {unit}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="detection-field">
                  설명
                  <textarea
                    value={selected.description}
                    onChange={(event) =>
                      change({ description: event.target.value })
                    }
                  />
                </label>
              </div>
              {errors.length > 0 && (
                <ul className="detection-error" role="alert">
                  {errors.map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                </ul>
              )}
              <div className="detection-actions">
                <Button
                  variant="outline"
                  onClick={() => {
                    const invalid = validateRule(selected);
                    setErrors(invalid);
                    if (!invalid.length) {
                      setRetained(true);
                    }
                  }}
                >
                  룰 입력 확인
                </Button>
                <ServiceAction
                  operation="createRule"
                  label="룰 생성"
                  available={data.capabilities.createRule}
                  payload={{ rule: rulePayload }}
                  requestDisabled={ruleErrors.length > 0}
                >
                  <p>
                    {selected.name} · {operatorLabels[selected.operator]}{" "}
                    {selected.threshold ?? "—"} {selected.unit}
                  </p>
                  {ruleErrors.map((error) => (
                    <p className="detection-error" key={error}>
                      {error}
                    </p>
                  ))}
                </ServiceAction>
              </div>
              {retained && (
                <p role="status" className="detection-note">
                  룰 입력 확인 완료 · 편집 내용 유지 중
                </p>
              )}
              <p className="detection-note">
                룰 편집은 현재 운영 버전을 변경하지 않습니다.
              </p>
            </>
          ) : (
            <DetailMissing>목록에서 룰을 선택해 주세요.</DetailMissing>
          )}
        </section>
      </div>
      <section className="detection-card">
        <div className="detection-page-header">
          <h2>추천 룰 검토</h2>
          <ServiceAction
            operation="recommendRules"
            label="룰 추천 요청"
            available={data.capabilities.recommendRules}
            payload={{
              snapshotId: draft.snapshotId,
              ruleIds: Object.keys(allRules),
            }}
          >
            <p>
              선택 데이터의 통계·SLA 근거를 사용한 추천 요청입니다. 운영 적용은
              별도 승인입니다.
            </p>
          </ServiceAction>
        </div>
        <div className="detection-table-wrap">
          <table className="detection-table">
            <thead>
              <tr>
                <th>추천</th>
                <th>검토 상태</th>
                <th>근거</th>
              </tr>
            </thead>
            <tbody>
              {data.recommendations.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="detection-list-button"
                      onClick={() => setParam("recommendation", item.id)}
                    >
                      {item.name}
                    </Button>
                  </td>
                  <td>
                    {draft.recommendationDecisions[item.id] === "accepted"
                      ? "초안으로 가져옴"
                      : draft.recommendationDecisions[item.id] === "rejected"
                        ? "검토 제외"
                        : "검토 필요"}
                  </td>
                  <td>{item.evidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.recommendations.length === 0 && (
            <EmptyState>검토할 추천이 없습니다.</EmptyState>
          )}
        </div>
        {recommendationId &&
          (recommendation ? (
            <div className="detection-feedback">
              <h3>{recommendation.name}</h3>
              <p className="detection-note">{recommendation.rationale}</p>
              <p className="detection-note">{recommendation.evidence}</p>
              <p>
                {recommendation.rule.name} ·{" "}
                {operatorLabels[recommendation.rule.operator]}{" "}
                {recommendation.rule.threshold ?? "—"}{" "}
                {recommendation.rule.unit}
              </p>
              <div className="detection-actions">
                <Button variant="outline" onClick={selectRecommendation}>
                  룰 초안으로 가져오기
                </Button>
                <Button
                  variant="ghost"
                  onClick={() =>
                    update({
                      recommendationDecisions: {
                        ...draft.recommendationDecisions,
                        [recommendation.id]: "rejected",
                      },
                    })
                  }
                >
                  추천 검토 제외
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => setParam("recommendation", "")}
                >
                  추천 목록으로
                </Button>
              </div>
            </div>
          ) : (
            <DetailMissing />
          ))}
      </section>
      <Modal
        isOpen={resetOpen}
        onOpenChange={setResetOpen}
        title="룰 기본값 복원"
        description="룰 편집과 추천 검토 내용을 버립니다. 모델·피처 초안과 운영 버전은 유지됩니다."
        size="sm"
      >
        <div className="detection-actions">
          <Button variant="outline" onClick={() => setResetOpen(false)}>
            취소
          </Button>
          <Button
            onClick={() => {
              update({
                rules: {},
                ruleThresholdInputs: {},
                recommendationDecisions: {},
              });
              setResetOpen(false);
              setRetained(false);
              setErrors([]);
            }}
          >
            기본값 복원
          </Button>
        </div>
      </Modal>
    </div>
  );
};
