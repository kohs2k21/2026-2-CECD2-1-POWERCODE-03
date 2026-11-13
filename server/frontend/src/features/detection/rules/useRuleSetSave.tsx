import { useState } from "react";
import { HttpError } from "@/services/api/client";
import { RuleBanner, type Banner } from "./RuleBanner";
import { RuleSaveDialog, type PendingSave } from "./RuleDialogs";
import { errorCode } from "./ruleDraft";
import { previewRuleSet, saveRuleSet, type RuleSetRequest, type RuleSetSaved, type RuleSetState } from "./ruleSetApi";

/**
 * 룰 설정 화면 밖(프로토콜 설정·규칙 추천 승인)의 룰 세트 저장 흐름. 룰 설정과 같은 규칙(rule-set.md 저장 절):
 * 서버 미리보기(변경 요약·경고) → 확인창(꺼지는 이상 규칙 확인 체크) → PUT → 다시 읽기. 검증 실패·충돌은 코드 그대로 안내하고 운영 룰은 그대로.
 */
export const useRuleSetSave = ({ state, reload, onSaved }: {
  state: RuleSetState | undefined;
  /** 다시 읽기. 실패하면 undefined. */
  reload: () => Promise<RuleSetState | undefined>;
  onSaved?: (saved: RuleSetSaved) => void;
}) => {
  const [banner, setBanner] = useState<Banner | null>(null);
  const [pending, setPending] = useState<PendingSave | null>(null);
  const [busy, setBusy] = useState(false);
  const revision = state?.revision ?? 0;

  const fail = async (error: unknown, editsKept: boolean) => {
    if (error instanceof HttpError && error.status === 409 && error.code === "rule_set_revision_conflict") {
      const reported = (error.payload as { currentRevision?: unknown } | undefined)?.currentRevision;
      const fresh = await reload();
      setBanner({ kind: "conflict", code: error.code, revision: typeof reported === "number" ? reported : (fresh?.revision ?? revision) });
      return;
    }
    setBanner({ kind: "failed", code: errorCode(error), revision, editsKept });
  };
  const submit = async (request: RuleSetRequest) => {
    setPending(null);
    setBanner({ kind: "saving" });
    setBusy(true);
    try {
      const saved = await saveRuleSet(request);
      await reload();
      setBanner({ kind: "applied", revision: saved.revision });
      onSaved?.(saved);
    } catch (error) {
      await fail(error, true);
    } finally {
      setBusy(false);
    }
  };
  /** build(confirm): 확인 체크 여부를 받아 전체 저장 요청을 만든다. 미리보기는 확인 true로 보낸다(요약만 받음). */
  const open = async (build: (confirmHardRuleDisable: boolean) => RuleSetRequest, text: Pick<PendingSave, "title" | "actionText"> & { lead?: string }) => {
    if (!state) return;
    setBusy(true);
    setBanner(null);
    try {
      const preview = await previewRuleSet(build(true));
      if (!preview.valid) {
        setBanner({ kind: "failed", code: preview.error.code, revision, editsKept: true });
        return;
      }
      const newlyOff = preview.changes.filter((change) => change.type === "hard_disabled").map((change) => change.id);
      setPending({
        title: text.title, actionText: text.actionText, lead: text.lead ?? `변경 ${preview.changes.length}건`,
        changes: preview.changes, warnings: preview.warnings ?? [],
        alreadyOff: state.hardRules.filter((rule) => preview.hardDisabledAfterSave.includes(rule.id) && !newlyOff.includes(rule.id)).map((rule) => rule.displayName),
        run: (confirm) => void submit(build(confirm)),
      });
    } catch (error) {
      await fail(error, true);
    } finally {
      setBusy(false);
    }
  };
  return {
    open,
    busy,
    clearBanner: () => setBanner(null),
    banner: banner && <RuleBanner banner={banner} onReload={() => { setBanner(null); void reload(); }} />,
    dialog: pending && <RuleSaveDialog pending={pending} onCancel={() => setPending(null)} />,
  };
};
