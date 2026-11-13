import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { IconChevronRight } from "@tabler/icons-react";
import { SectionCard } from "@/components/layout/SectionCard";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { paths } from "@/app/routes";
import { observeHeight } from "@/lib/observeHeight";
import { stampText } from "@/features/operations/format";
import { HttpError } from "@/services/api/client";
import { useDraftStore } from "@/stores/draftStore";
import { RuleAiBox, RuleAiControls, useRuleAi } from "../rules/RuleAiPanel";
import { RuleBanner, type Banner } from "../rules/RuleBanner";
import { ConfirmDialog, RuleAddDialog, RuleSaveDialog, type PendingSave } from "../rules/RuleDialogs";
import { HardRulesCard, RelationRulesCard, RuleHistoryCard, SoftRulesCard } from "../rules/RuleTables";
import {
  HARD_TERM, PROTOCOL_ITEM_TERM, PROTOCOL_TERM, RELATION_TERM, SOFT_TERM, draftChangeCount, draftFromState, draftIssues, errorCode, restoreRequest, saveRequest, softCondition,
  type DraftSoftRule, type RuleDraft,
} from "../rules/ruleDraft";
import { previewRuleSet, readRuleSetRevision, saveRuleSet, type RuleSetRequest, type RuleSetState, type Severity } from "../rules/ruleSetApi";
import { useRuleSet, useSoftRuleFields } from "../rules/useRuleSet";
import { RESPONSE_CODE_TERM, failedRule, summaryText } from "../rules/responsecodes/responseCodeLabels";

const RuleStatus = ({ state }: { state: RuleSetState }) => (
  <Card aria-label="현재 운영 룰">
    <CardContent className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
      <span>현재 운영 <strong className="font-semibold">버전 {state.revision}</strong></span>
      <span>마지막 변경 {stampText(state.savedAt)}</span>
      <span>변경자 {state.savedBy.displayName}</span>
      <span>
        켜진 규칙 · {HARD_TERM} {state.counts.hardEnabled}/{state.counts.hardTotal} · {SOFT_TERM} {state.counts.softEnabled}/{state.counts.softTotal}
        {state.counts.relationTotal !== undefined && <> · {RELATION_TERM} {state.counts.relationEnabled}/{state.counts.relationTotal}</>}
        {state.counts.protocolTotal !== undefined && <> · {PROTOCOL_ITEM_TERM} {state.counts.protocolEnabled}/{state.counts.protocolTotal}</>}
      </span>
    </CardContent>
  </Card>
);

/** 응답 코드 진입 카드: 거래 실패 규칙의 코드별 위험도 요약(현재 운영 룰 값) → 목록 → 상세(contracts/response-codes-ui.md 4절). */
const ResponseCodeCard = ({ state }: { state: RuleSetState }) => {
  const failed = failedRule(state);
  if (!failed) return null;
  return (
    <SectionCard
      title={RESPONSE_CODE_TERM}
      aria-label={RESPONSE_CODE_TERM}
      description={summaryText(failed.rule, failed.severity)}
      action={
        <Button asChild size="sm" variant="outline">
          <Link to={paths.detectionRuleResponseCodes}>
            목록 보기
            <IconChevronRight aria-hidden="true" />
          </Link>
        </Button>
      }
    />
  );
};

const storedDraft = () => {
  const stored = useDraftStore.getState().drafts[paths.detectionRules];
  return stored?.dirty ? (stored.value as RuleDraft) : null;
};

const RulesEditor = ({ state, reload, onSavedStale }: {
  state: RuleSetState;
  /** 다시 읽기. 실패하면 undefined(이전 데이터를 새 상태로 쓰지 않는다). */
  reload: () => Promise<RuleSetState | undefined>;
  /** 저장은 됐는데 새 상태를 읽지 못함. 옛 상태로 편집을 이어 가지 않게 화면이 닫는다. */
  onSavedStale: (revision: number) => void;
}) => {
  const [draft, setDraft] = useState<RuleDraft>(() => storedDraft() ?? draftFromState(state));
  const [banner, setBanner] = useState<Banner | null>(null);
  const [pending, setPending] = useState<PendingSave | null>(null);
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<DraftSoftRule | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [busy, setBusy] = useState(false);
  const fields = useSoftRuleFields();
  const fieldList = fields.data ?? [];
  const fieldName = (field: string) => fieldList.find((item) => item.fieldId === field)?.displayName ?? field;
  const changeCount = draftChangeCount(draft);
  const issues = draftIssues(draft, fieldName);
  const dirty = changeCount > 0;
  const ai = useRuleAi({ state, dirty, reload });
  // 저장 뒤에는 편집 여부와 상관없이 새 상태로 맞춘다.
  const resetOnNext = useRef(false);
  const stickyBlock = useRef<HTMLDivElement>(null);
  useEffect(() => observeHeight(stickyBlock.current, "--sticky-block-height"), []);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  useEffect(() => {
    if (draft.base.revision === state.revision) return;
    if (!resetOnNext.current && dirtyRef.current) return;
    resetOnNext.current = false;
    setDraft(draftFromState(state));
  }, [state, draft.base.revision]);
  useEffect(() => {
    const store = useDraftStore.getState();
    if (dirty) store.edit(paths.detectionRules, draft);
    else store.discard(paths.detectionRules);
  }, [draft, dirty]);

  const resetDraft = (next: RuleSetState) => {
    setDraft(draftFromState(next));
    setBanner(null);
  };
  /** 새로 불러오기: 서버에서 읽은 상태로만 맞춘다. 실패하면 편집과 안내를 두고 기존 조회 실패 표시가 뜬다. */
  const reloadFromServer = async () => {
    setBusy(true);
    const fresh = await reload();
    setBusy(false);
    if (fresh) resetDraft(fresh);
  };
  /** 편집 유지 안내는 저장 경로에서만 쓴다. 되돌리기는 초안과 무관한 명령이다. */
  const failWith = async (error: unknown, editsKept: boolean) => {
    if (error instanceof HttpError && error.status === 409 && error.code === "rule_set_revision_conflict") {
      // 재조회가 실패해도 응답의 현재 버전을 쓴다(재조회 결과는 이전 데이터일 수 있다).
      const reported = (error.payload as { currentRevision?: unknown } | undefined)?.currentRevision;
      const fresh = await reload();
      setBanner({ kind: "conflict", code: error.code, revision: typeof reported === "number" ? reported : (fresh?.revision ?? state.revision) });
      return;
    }
    setBanner({ kind: "failed", code: errorCode(error), revision: state.revision, editsKept });
  };
  const submit = async (request: RuleSetRequest, editsKept: boolean) => {
    setPending(null);
    setBanner({ kind: "saving" });
    setBusy(true);
    try {
      const saved = await saveRuleSet(request);
      resetOnNext.current = true;
      useDraftStore.getState().discard(paths.detectionRules);
      if (await reload()) setBanner({ kind: "applied", revision: saved.revision });
      else onSavedStale(saved.revision);
    } catch (error) {
      await failWith(error, editsKept);
    } finally {
      setBusy(false);
    }
  };
  /** 서버가 계산한 변경 요약으로 확인창을 연다. 검증 실패·충돌은 확인창을 열지 않고 코드 그대로 표시한다. */
  const openConfirm = async (request: (confirm: boolean) => RuleSetRequest, text: (changes: number) => Pick<PendingSave, "title" | "lead" | "actionText">, editsKept: boolean) => {
    setBusy(true);
    setBanner(null);
    try {
      const preview = await previewRuleSet(request(true));
      if (!preview.valid) {
        setBanner({ kind: "failed", code: preview.error.code, revision: state.revision, editsKept });
        return;
      }
      const newlyOff = preview.changes.filter((change) => change.type === "hard_disabled").map((change) => change.id);
      setPending({
        ...text(preview.changes.length),
        changes: preview.changes,
        warnings: preview.warnings ?? [],
        alreadyOff: state.hardRules.filter((rule) => preview.hardDisabledAfterSave.includes(rule.id) && !newlyOff.includes(rule.id)).map((rule) => rule.displayName),
        run: (confirm) => void submit(request(confirm), editsKept),
      });
    } catch (error) {
      await failWith(error, editsKept);
    } finally {
      setBusy(false);
    }
  };
  const openSave = () => void openConfirm(
    (confirm) => saveRequest(draft, confirm),
    (changes) => ({ title: "룰 변경 저장", lead: `변경 ${changes}건`, actionText: "저장하고 적용" }),
    true,
  );
  const openRevert = async (revision: number) => {
    setBusy(true);
    setBanner(null);
    try {
      const detail = await readRuleSetRevision(revision);
      await openConfirm(
        (confirm) => restoreRequest(detail, state, confirm),
        (changes) => ({
          title: `버전 ${revision} 상태로 되돌리기`,
          lead: `현재 버전 ${state.revision}에서 버전 ${revision} 상태를 새 버전으로 다시 적용합니다. 변경 ${changes}건${changeCount ? ` · 편집 중인 변경 ${changeCount}건은 버려집니다` : ""}`,
          actionText: "되돌리고 적용",
        }),
        false,
      );
    } catch (error) {
      await failWith(error, false);
    } finally {
      setBusy(false);
    }
  };

  const editSoft = (key: string, patch: Partial<DraftSoftRule>) =>
    setDraft((current) => ({ ...current, soft: current.soft.map((rule) => (rule.key === key ? { ...rule, ...patch } : rule)) }));
  const toggleHard = (id: string, enabled: boolean) =>
    setDraft((current) => ({ ...current, hard: current.hard.map((rule) => (rule.id === id ? { ...rule, enabled } : rule)) }));
  /** code null = 단일 단계 규칙의 단계 또는 `{byCode, default}` 규칙의 기본 단계. */
  const setHardSeverity = (id: string, code: string | null, level: Severity) =>
    setDraft((current) => ({
      ...current,
      hard: current.hard.map((rule) => {
        if (rule.id !== id || rule.severity === undefined) return rule;
        if (typeof rule.severity === "string") return { ...rule, severity: level };
        if (code === null) return { ...rule, severity: { ...rule.severity, default: level } };
        return { ...rule, severity: { ...rule.severity, byCode: { ...rule.severity.byCode, [code]: level } } };
      }),
    }));
  const editRelation = (id: string, patch: Partial<RuleDraft["relation"][number]>) =>
    setDraft((current) => ({ ...current, relation: current.relation.map((rule) => (rule.id === id ? { ...rule, ...patch } : rule)) }));
  const addRule = (rule: Pick<DraftSoftRule, "name" | "field" | "operator" | "threshold" | "severity">) => {
    setDraft((current) => ({ ...current, nextRef: current.nextRef + 1, soft: [...current.soft, { ...rule, key: `new-${current.nextRef}`, id: null, enabled: true, scope: null }] }));
    setAdding(false);
  };

  return (
    <>
      <RuleStatus state={state} />
      {/* 저장 줄·결과 안내·입력 확인은 스크롤과 무관하게 머리글 아래에 고정한다. 부모가 grid면 sticky가 자기 행 안에서만 움직여 RulesPage 바탕은 flex다. */}
      <div ref={stickyBlock} data-rule-sticky className="sticky top-(--top-nav-offset) z-(--z-sticky) grid gap-2 bg-background py-2">
        {banner && <RuleBanner banner={banner} onReload={() => void reloadFromServer()} />}
        {issues.length > 0 && (
          <ul className="grid gap-1 text-sm text-warning-text">
            {issues.map((issue) => <li key={issue}>확인 필요 · {issue}</li>)}
          </ul>
        )}
        <Card aria-live="polite" data-rule-save-bar className="py-3">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 text-sm">
            <span><strong className="font-semibold">변경 {changeCount}건</strong> · 저장하면 바로 운영에 적용됩니다</span>
            <span className="flex gap-2">
              <Button size="sm" variant="outline" disabled={!changeCount || busy} onClick={() => setCancelling(true)}>변경 취소</Button>
              <Button size="sm" disabled={!changeCount || issues.length > 0 || busy} onClick={openSave}>저장</Button>
            </span>
          </CardContent>
        </Card>
      </div>
      {/* 미리보기·저장 대기 중에는 편집을 잠근다. 요청은 누른 시점의 초안이고 성공하면 초안을 새 상태로 맞추므로, 그 사이 입력은 조용히 사라진다. */}
      <fieldset disabled={busy} aria-label={`${HARD_TERM}·${SOFT_TERM}`} className="grid min-w-0 gap-6">
        <HardRulesCard draft={draft} onToggle={toggleHard} onSeverity={setHardSeverity} />
        <SoftRulesCard
          draft={draft} fields={fieldList} fieldsError={fields.isError ? errorCode(fields.error) : null}
          aiControls={<RuleAiControls ai={ai} />}
          aiBox={<RuleAiBox ai={ai} state={state} draft={draft} fields={fieldList} onApply={(updates) => updates.forEach(({ key, patch }) => editSoft(key, patch))} />}
          onEdit={editSoft} onAdd={() => setAdding(true)} onDelete={setDeleting}
        />
        <RelationRulesCard draft={draft} onToggle={(id, enabled) => editRelation(id, { enabled })} onSeverity={(id, severity) => editRelation(id, { severity })} />
      </fieldset>
      <SectionCard
        title={PROTOCOL_TERM}
        aria-label={PROTOCOL_TERM}
        description={`인터페이스별 허용 경로 · ${PROTOCOL_ITEM_TERM} ${state.protocolRules?.length ?? 0}개(켜짐 ${state.protocolRules?.filter((rule) => rule.enabled).length ?? 0}개)`}
        action={
          <Button asChild size="sm" variant="outline">
            <Link to={paths.detectionRuleProtocols}>
              설정 보기
              <IconChevronRight aria-hidden="true" />
            </Link>
          </Button>
        }
      />
      <ResponseCodeCard state={state} />
      <SectionCard
        title="규칙 추천"
        aria-label="규칙 추천"
        description="Trainer 후보 작업 결과 · 관계 규칙(이상)·처리 시간(이상 징후) 후보 검토"
        action={
          <Button asChild size="sm" variant="outline">
            <Link to={paths.detectionRuleCandidates}>
              후보 보기
              <IconChevronRight aria-hidden="true" />
            </Link>
          </Button>
        }
      />
      <RuleHistoryCard key={state.revision} state={state} disabled={busy} onRevert={(revision) => void openRevert(revision)} />
      {pending && <RuleSaveDialog pending={pending} onCancel={() => setPending(null)} />}
      {adding && <RuleAddDialog fields={fieldList} onAdd={addRule} onCancel={() => setAdding(false)} />}
      {deleting && (
        <ConfirmDialog
          title={`${SOFT_TERM} 삭제`} destructive actionText="삭제"
          message={`${deleting.name} 규칙(${softCondition(deleting, fieldList.find((item) => item.fieldId === deleting.field))})을 삭제합니다. 저장해야 운영에 반영됩니다.`}
          onCancel={() => setDeleting(null)}
          onConfirm={() => {
            setDraft((current) => ({ ...current, soft: current.soft.filter((rule) => rule.key !== deleting.key) }));
            setDeleting(null);
          }}
        />
      )}
      {cancelling && (
        <ConfirmDialog
          title="변경 취소" actionText="변경 버리기"
          message={`편집한 변경 ${changeCount}건을 버리고 현재 운영 룰(버전 ${state.revision})로 되돌립니다.`}
          onCancel={() => setCancelling(false)}
          onConfirm={() => {
            resetDraft(state);
            setCancelling(false);
          }}
        />
      )}
    </>
  );
};

/** 탐지 설정 › 룰 설정(rules-ui.md): 현재 운영 룰, 이상 규칙·이상 징후 규칙 편집, 저장 즉시 적용, 변경 기록·되돌리기. */
export const RulesPage = () => {
  const rules = useRuleSet();
  const reload = async () => {
    const result = await rules.refetch();
    return result.isError ? undefined : result.data;
  };
  // 저장 뒤 재조회 실패: 저장된 버전만 알리고 옛 상태의 편집 화면은 닫는다. 재조회가 성공하면 새 상태로 다시 연다.
  const [savedStale, setSavedStale] = useState<number | null>(null);
  useEffect(() => {
    if (!rules.isError) setSavedStale(null);
  }, [rules.isError]);
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <ScreenTitle>룰 설정</ScreenTitle>
      {rules.isPending && <LoadingState>현재 운영 룰 조회 중…</LoadingState>}
      {rules.isError && <ErrorState title={`현재 운영 룰 조회 실패 · ${errorCode(rules.error)}`} onRetry={() => void rules.refetch()} retryLabel="다시 조회" />}
      {savedStale !== null && rules.isError
        ? <RuleBanner banner={{ kind: "applied", revision: savedStale }} onReload={() => void rules.refetch()} />
        : rules.data && <RulesEditor state={rules.data} reload={reload} onSavedStale={setSavedStale} />}
    </div>
  );
};
