import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/Modal";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { RELATION_TERM, SOFT_TERM, requestFromState, severities, severityLabels } from "../ruleDraft";
import type { RelationRuleRequest, RuleSetRequest, RuleSetState, Severity, SoftRuleRequest } from "../ruleSetApi";
import { useRuleSet } from "../useRuleSet";
import { useRuleSetSave } from "../useRuleSetSave";
import { readCandidateDetail, type CandidateDetail } from "./candidateApi";
import { approvalRequest, approvedRelation, approvedThreshold } from "./candidateApproval";
import { APPROVED_TEXT, approvalBlockedText, candidateErrorTitle, kindLabels, notApprovableText, pathText, thresholdText } from "./candidateLabels";

/**
 * 규칙 추천 승인 흐름(rule-candidates-ui.md 7절) — 후보 상세와 규칙 추천 화면의 행 [승인]이 같이 쓴다.
 * [승인] → 입력(관계: 위험도, 처리 시간: 이름·위험도) → 서버 미리보기 확인창 → 저장(새 버전) → 다시 읽기.
 */

/** 승인 입력: 관계 규칙은 위험도, 처리 시간 규칙은 이름·위험도(id는 서버가 발급). 다음 → 서버 미리보기 확인창 → 저장. */
export const ApproveDialog = ({ kind, defaultName, onNext, onCancel }: {
  kind: "relation" | "threshold"; defaultName: string; onNext: (values: { name: string; severity: Severity }) => void; onCancel: () => void;
}) => {
  const [name, setName] = useState(defaultName);
  // 관계 규칙은 이상 규칙(하드) 기본 위험도 = 위험, 처리 시간 후보는 이상 징후 규칙 기본 = 주의(규칙 추가 창과 같음).
  const [severity, setSeverity] = useState<Severity>(kind === "relation" ? "critical" : "warning");
  return (
    <Modal isOpen onOpenChange={(open) => { if (!open) onCancel(); }} title={`${kind === "relation" ? RELATION_TERM : SOFT_TERM} 승인`}>
      <div className="grid gap-4 sm:grid-cols-2">
        {kind === "threshold" && (
          <div className="grid gap-2">
            <Label htmlFor="approve-name">규칙 이름</Label>
            <Input id="approve-name" maxLength={120} value={name} onChange={(event) => setName(event.target.value)} />
          </div>
        )}
        <div className="grid gap-2">
          <Label htmlFor="approve-severity">위험도</Label>
          <NativeSelect id="approve-severity" value={severity} onChange={(event) => setSeverity(event.target.value as Severity)}>
            {severities.map((item) => <NativeSelectOption key={item} value={item}>{severityLabels[item]}</NativeSelectOption>)}
          </NativeSelect>
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onCancel}>돌아가기</Button>
        <Button disabled={kind === "threshold" && !name.trim()} onClick={() => onNext({ name: name.trim(), severity })}>다음</Button>
      </DialogFooter>
    </Modal>
  );
};

/** 승인 흐름: 현재 룰 세트를 읽어 승인 여부를 판단하고, 승인은 현재 상태 + 새 규칙 1개를 저장한다. */
export const useApproval = () => {
  const rules = useRuleSet();
  const reload = async () => {
    const result = await rules.refetch();
    return result.isError ? undefined : result.data;
  };
  const save = useRuleSetSave({ state: rules.data, reload });
  const [asking, setAsking] = useState(false);
  const submit = (state: RuleSetState, add: (request: RuleSetRequest) => RuleSetRequest, title: string) =>
    void save.open((confirm) => add(requestFromState(state, confirm)), { title, actionText: "승인하고 적용" });
  return { rules, state: rules.data, stateError: rules.isError, save, asking, setAsking, submit };
};
export type ApprovalFlow = ReturnType<typeof useApproval>;

/** 처리 시간 후보 승인 입력창의 기본 이름. */
export const defaultApproveName = (detail: CandidateDetail) =>
  detail.type === "threshold" ? `처리 시간 ${detail.threshold.scope.processId} ${thresholdText(detail.threshold)}` : "";

/** 이미 같은 조건의 규칙이 현재 룰 세트에 있는가(승인됨). 처리 시간은 저장할 조건이 없으면 false. */
export const approvedDetail = (state: RuleSetState, detail: CandidateDetail) =>
  detail.type === "relation" ? approvedRelation(state, detail.ruleDraft) : detail.ruleDraft ? approvedThreshold(state, detail.ruleDraft) : false;

/** 승인 입력을 받아 저장 요청에 새 규칙 1개를 더하고 확인창을 연다. */
export const submitApproval = (approval: ApprovalFlow, state: RuleSetState, detail: CandidateDetail, { name, severity }: { name: string; severity: Severity }) => {
  if (detail.type === "relation") {
    const item = detail.relation;
    approval.submit(state, (request) => approvalRequest(request, { relation: { ...(detail.ruleDraft as unknown as RelationRuleRequest), id: null, clientRef: "candidate", severity, enabled: true } }),
      `${RELATION_TERM} 승인 · ${kindLabels[item.kind]} ${pathText(item)}`);
    return;
  }
  if (!detail.ruleDraft) return;
  const draft = detail.ruleDraft as unknown as Omit<SoftRuleRequest, "id" | "name" | "severity">;
  approval.submit(state, (request) => approvalRequest(request, {
    soft: { id: null, clientRef: "candidate", name, field: draft.field, operator: draft.operator, threshold: draft.threshold, severity, enabled: true, scope: draft.scope },
  }), `${SOFT_TERM} 승인 · ${detail.threshold.scope.processId}`);
};

/** 후보 자체의 승인 가능: 관계는 approvable, 처리 시간은 후보 상태 + 저장할 조건이 있음(저장 형식과 무관). */
const detailApprovable = (detail: CandidateDetail) =>
  detail.type === "relation" ? detail.relation.approvable : detail.threshold.status === "candidate" && detail.ruleDraft !== null;

export type ApprovalBlock = { kind: "approved" | "blocked"; text: string };
/**
 * 승인을 막는 이유 한 줄(후보 상세 승인 줄과 목록 행 [승인]이 같이 쓴다). 순서: 승인됨 → 후보 기준 미달(겹침 거부면 그 사유) → 저장 형식 닫힘.
 * 후보 자체의 이유를 저장 형식보다 먼저 보인다(형식이 열려도 승인할 수 없는 후보임을 알린다). null = 승인 가능(또는 승인됨 판단 전).
 */
export const approvalBlock = (detail: CandidateDetail, approved: boolean | null): ApprovalBlock | null => {
  if (approved) return { kind: "approved", text: APPROVED_TEXT };
  if (!detailApprovable(detail)) return { kind: "blocked", text: notApprovableText(detail.type === "threshold" ? detail.threshold.reason : null) };
  if (!detail.approval.available) return { kind: "blocked", text: approvalBlockedText(detail.approval.reason) };
  return null;
};

type RowNote = { candidateId: string; kind: "approved" | "blocked" | "error"; text: string };

/**
 * 목록 행 [승인]: 목록 응답에는 저장할 조건(ruleDraft)이 없어 누르면 그 후보 상세(GET /api/rule-set/candidates/:id)를 읽고 상세와 같은 흐름을 연다.
 * 승인됨·기준 미달·저장 형식 닫힘(approvalBlock 순서)이면 입력창을 열지 않고 그 행에 상세와 같은 문구를 보인다. 상세 조회 실패는 코드 그대로.
 */
export const useRowApproval = () => {
  const approval = useApproval();
  const [asking, setAsking] = useState<CandidateDetail | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [note, setNote] = useState<RowNote | null>(null);
  const start = async (candidateId: string) => {
    if (!approval.state) return;
    setLoading(candidateId);
    setNote(null);
    approval.save.clearBanner();
    try {
      const detail = await readCandidateDetail(candidateId);
      const block = approvalBlock(detail, approvedDetail(approval.state, detail));
      if (block) setNote({ candidateId, ...block });
      else setAsking(detail);
    } catch (error) {
      setNote({ candidateId, kind: "error", text: candidateErrorTitle(error, "후보 상세") });
    } finally {
      setLoading(null);
    }
  };
  const noteFor = (candidateId: string) => {
    if (note?.candidateId !== candidateId) return null;
    if (note.kind === "approved") return <Badge variant="success" data-approved>{note.text}</Badge>;
    return <span data-row-approve-note role={note.kind === "error" ? "alert" : undefined} className={note.kind === "error" ? "text-xs text-destructive-text" : "text-xs text-muted-foreground"}>{note.text}</span>;
  };
  return {
    /** 현재 운영 룰 조회(승인됨 판단·승인 저장의 기준). 화면이 같은 조회로 규칙 목록도 그린다. */
    rules: approval.rules,
    state: approval.state,
    stateError: approval.stateError,
    busy: approval.save.busy || loading !== null,
    loading,
    start: (candidateId: string) => void start(candidateId),
    noteFor,
    banner: approval.save.banner,
    dialogs: (
      <>
        {asking && approval.state && (
          <ApproveDialog kind={asking.type} defaultName={defaultApproveName(asking)} onCancel={() => setAsking(null)}
            onNext={(values) => {
              setAsking(null);
              submitApproval(approval, approval.state!, asking, values);
            }} />
        )}
        {approval.save.dialog}
      </>
    ),
  };
};
export type RowApproval = ReturnType<typeof useRowApproval>;
