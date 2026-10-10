import { useState } from "react";
import { Button } from "../../../components/ui/button";
import { Modal } from "../../../components/ui/Modal";
import { DialogFooter } from "../../../components/ui/dialog";
import { useSearchParams } from "react-router-dom";
import { featureCatalog } from "../data/catalog";
import {
  defaultFeatureEditor,
  canEditCanonicalFeature,
} from "../data/featureBuilder";
import {
  appendCustomFeature,
  removeCustomFeature,
  useCreateDraft,
} from "../data/createDraft";
import { featureOptions, legacyEditorExpression } from "../data/formula";
import { FormulaEditor } from "./FormulaEditor";
import { DetailMissing } from "../components/DetectionQueryBoundary";
export const FeatureEditor = ({ featureId }: { featureId: string | null }) => {
  const { draft, update } = useCreateDraft();
  const [params, setParams] = useSearchParams();
  const [deleteOpen, setDeleteOpen] = useState(false);
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
  const editable = custom;
  const change = (patch: Partial<typeof value>) => {
    update({
      featureEdits: {
        ...draft.featureEdits,
        [featureId!]: { ...value, ...patch },
      },
    });
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
            ? "원본 속성 상세"
            : editable
              ? "파생변수 편집"
              : "파생변수 상세"}
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
      {feature?.source === "derived" && !editable && (
        <>
          <p className="detection-note">
            원본 계산 규칙은 유지됩니다.{" "}
            {canEditCanonicalFeature(feature)
              ? "복사본을 만들어 수정할 수 있습니다."
              : "다른 계산 규칙이 필요하면 새 파생변수를 추가해 주세요."}
          </p>
          {canEditCanonicalFeature(feature) && (
            <div className="detection-actions">
              <Button
                variant="outline"
                onClick={() => {
                  const created = appendCustomFeature(draft, {
                    ...value,
                    name: `${value.name.slice(0, 75)} 복사`,
                    expression:
                      value.expression ?? legacyEditorExpression(value),
                  });
                  update(created.draft);
                  const next = new URLSearchParams(params);
                  next.set("feature", created.id);
                  setParams(next);
                }}
              >
                복제하여 편집
              </Button>
            </div>
          )}
        </>
      )}
      {editable && (
        <>
          <FormulaEditor
            value={value}
            features={featureOptions(draft)}
            onChange={change}
            onDelete={() => setDeleteOpen(true)}
          />
          <Modal
            isOpen={deleteOpen}
            onOpenChange={setDeleteOpen}
            size="sm"
            title="파생변수 삭제"
            description="학습 피처 선택에서 제외됩니다. 이 변수를 참조하는 입력은 다시 선택해야 합니다."
          >
            <p>{value.name || "이름 없는 파생변수"}</p>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeleteOpen(false)}>
                취소
              </Button>
              <Button
                onClick={() => {
                  update(removeCustomFeature(draft, featureId!));
                  setDeleteOpen(false);
                  close();
                }}
              >
                삭제
              </Button>
            </DialogFooter>
          </Modal>
        </>
      )}
    </section>
  );
};
