import { Button } from "../../../components/ui/button";
import { EmptyState } from "../../../components/ui/feedback";
import { featureCatalog } from "../data/catalog";
import { scenarioLabels, scenarioErrors } from "../data/evaluationPreparation";
import type {
  EvaluationPreparationDraft,
  ScenarioKind,
  ScenarioRecipe,
} from "../data/evaluationTypes";

export const EvaluationPreparationScenarios = ({
  draft,
  change,
}: {
  draft: EvaluationPreparationDraft;
  change: (patch: Partial<EvaluationPreparationDraft>) => void;
}) => {
  const update = (id: string, patch: Partial<ScenarioRecipe>) =>
    change({
      scenarios: draft.scenarios.map((item) =>
        item.id === id ? { ...item, ...patch } : item,
      ),
    });
  return (
    <section
      className="detection-card"
      aria-labelledby="preparation-scenarios-title"
    >
      <h2 id="preparation-scenarios-title">이상 시나리오</h2>
      <p className="detection-note">
        시나리오 설정 미리보기입니다. 원본 값 변경이나 실제 이상 주입은 수행하지
        않습니다.
      </p>
      <div className="detection-actions">
        {(Object.keys(scenarioLabels) as ScenarioKind[]).map((kind) => (
          <Button
            key={kind}
            variant="outline"
            onClick={() =>
              change({
                scenarioSequence: draft.scenarioSequence + 1,
                scenarios: [
                  ...draft.scenarios,
                  {
                    id: `scenario-${draft.scenarioSequence + 1}`,
                    kind,
                    targetField: "",
                    condition: "",
                    intensity: "",
                    count: "",
                    seed: "",
                  },
                ],
              })
            }
          >
            {scenarioLabels[kind]} 추가
          </Button>
        ))}
      </div>
      {!draft.scenarios.length && (
        <EmptyState>이상 시나리오가 없습니다.</EmptyState>
      )}
      {draft.scenarios.map((recipe) => (
        <fieldset key={recipe.id} className="evaluation-recipe">
          <legend>
            {scenarioLabels[recipe.kind]} · {recipe.id}
          </legend>
          <div className="detection-form-grid">
            <label className="detection-field">
              대상 필드
              <select
                value={recipe.targetField}
                onChange={(event) =>
                  update(recipe.id, { targetField: event.target.value })
                }
              >
                <option value="">선택해 주세요</option>
                {featureCatalog
                  .filter((item) => item.source !== "derived")
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id} · {item.name}
                    </option>
                  ))}
              </select>
            </label>
            {(
              [
                ["condition", "주입 조건"],
                ["intensity", "강도·단위"],
                ["count", "건수"],
                ["seed", "seed"],
              ] as const
            ).map(([key, label]) => (
              <label key={key} className="detection-field">
                {label}
                <input
                  value={recipe[key]}
                  onChange={(event) =>
                    update(recipe.id, { [key]: event.target.value })
                  }
                  inputMode={
                    key === "count" || key === "seed" ? "numeric" : undefined
                  }
                />
              </label>
            ))}
          </div>
          <p>
            조건: {recipe.condition || "—"} · 강도: {recipe.intensity || "—"} ·{" "}
            {recipe.count || "—"}건 · seed {recipe.seed || "—"}
          </p>
          {scenarioErrors(recipe).length > 0 && (
            <ul className="detection-error" role="status">
              {scenarioErrors(recipe).map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          )}
          <Button
            variant="outline"
            onClick={() =>
              change({
                scenarios: draft.scenarios.filter(
                  (item) => item.id !== recipe.id,
                ),
              })
            }
          >
            시나리오 제거
          </Button>
        </fieldset>
      ))}
    </section>
  );
};
