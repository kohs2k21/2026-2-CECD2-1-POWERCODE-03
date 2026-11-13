import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { stampText } from "@/features/operations/format";
import { HttpError } from "@/services/api/client";
import { errorCode, severities, severityLabels } from "../ruleDraft";
import type { RuleSetState, Severity } from "../ruleSetApi";
import { useRuleSet } from "../useRuleSet";
import { useRuleSetSave } from "../useRuleSetSave";
import {
  FOLLOW_DEFAULT, MESSAGE_CONFLICT, MESSAGE_SAVED, NOT_IN_DICTIONARY, NOT_IN_DICTIONARY_NOTE, RESPONSE_CODE_TERM, RULE_OFF_NOTE, SEVERITY_APPLIES,
  changeKindLabels, codeSeverityIn, codeSeverityText, failedRule, originLabels, severityRequest,
} from "./responseCodeLabels";
import { saveResponseCodeMessage, type ResponseCodeDetail } from "./responseCodesApi";
import { useResponseCode } from "./useResponseCodes";

const DASH = "—";
const MESSAGE_MAX = 500;

/** 위험도: 현재 룰 세트 값으로 고르고, 바꾸면 서버 미리보기 → 확인창(변경 요약) → 룰 세트 저장(새 버전). */
const SeverityCard = ({ code, state, onSaved }: { code: string; state: RuleSetState; onSaved: () => void }) => {
  const reloadRules = useRuleSet();
  const save = useRuleSetSave({
    state,
    reload: async () => {
      const result = await reloadRules.refetch();
      return result.isError ? undefined : result.data;
    },
    onSaved,
  });
  const failed = failedRule(state);
  if (!failed) return null;
  const current = codeSeverityIn(failed.severity, code);
  const value = current.source === "default" ? "" : current.level;
  const change = (next: string) => {
    const level = next === "" ? null : (next as Severity);
    const after = level === null ? { level: failed.severity.default, source: "default" as const } : { level, source: "code" as const };
    void save.open((confirm) => severityRequest(state, code, level, confirm), {
      title: `${RESPONSE_CODE_TERM} 위험도 변경`,
      lead: `${RESPONSE_CODE_TERM} ${code} 위험도 ${codeSeverityText(current)} → ${codeSeverityText(after)}`,
      actionText: "저장하고 적용",
    });
  };
  return (
    <SectionCard title="위험도" aria-label="위험도" headingLevel={3} description={`${failed.rule.displayName}(${failed.rule.id}) · ${SEVERITY_APPLIES}`}>
      {save.banner}
      <label className="flex items-center gap-2 text-sm">
        현재 운영 버전 {state.revision}
        <span className="w-44">
          <NativeSelect size="sm" aria-label={`${code} 위험도`} value={value} disabled={save.busy} onChange={(event) => change(event.target.value)}>
            {severities.map((item) => <NativeSelectOption key={item} value={item}>{severityLabels[item]}</NativeSelectOption>)}
            <NativeSelectOption value="">{FOLLOW_DEFAULT} ({severityLabels[failed.severity.default]})</NativeSelectOption>
          </NativeSelect>
        </span>
      </label>
      {!failed.rule.enabled && <p data-rule-off className="text-sm text-warning-text">{RULE_OFF_NOTE}</p>}
      {save.dialog}
    </SectionCard>
  );
};

/** 메시지 편집(관리자, 사전 행 수정 번호로 충돌 확인). 사전에 없는 코드는 저장하면 사전에 새로 들어간다. */
const MessageCard = ({ detail, onSaved }: { detail: ResponseCodeDetail; onSaved: () => void }) => {
  const [text, setText] = useState(detail.message ?? "");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: "saved" | "conflict" | "failed"; code?: string } | null>(null);
  useEffect(() => setText(detail.message ?? ""), [detail.message, detail.revision]);
  const trimmed = text.trim();
  const unchanged = trimmed === (detail.message ?? "");
  const submit = async () => {
    setBusy(true);
    setNotice(null);
    try {
      await saveResponseCodeMessage(detail.code, { message: trimmed, expectedRevision: detail.revision });
      onSaved();
      setNotice({ kind: "saved" });
    } catch (error) {
      if (error instanceof HttpError && error.status === 409 && error.code === "response_code_revision_conflict") {
        onSaved();
        setNotice({ kind: "conflict" });
      } else setNotice({ kind: "failed", code: errorCode(error) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <SectionCard title="메시지" aria-label="메시지" headingLevel={3}>
      {!detail.inDictionary && <p data-not-in-dictionary-note className="text-sm text-muted-foreground">{NOT_IN_DICTIONARY_NOTE}</p>}
      <div className="grid gap-2">
        <Label htmlFor="response-code-message">메시지</Label>
        <Textarea id="response-code-message" maxLength={MESSAGE_MAX} value={text} disabled={busy} onChange={(event) => setText(event.target.value.replace(/[\r\n]+/g, " "))} />
        <span className="text-xs text-muted-foreground">{[...trimmed].length}/{MESSAGE_MAX}자</span>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <Button size="sm" disabled={busy || !trimmed || unchanged} onClick={() => void submit()}>메시지 저장</Button>
        {notice?.kind === "saved" && <span role="status" data-message-saved>{MESSAGE_SAVED}</span>}
        {notice?.kind === "conflict" && <span role="alert" className="text-warning-text">{MESSAGE_CONFLICT}</span>}
        {notice?.kind === "failed" && <span role="alert" className="text-warning-text">메시지 저장 실패 · {notice.code}</span>}
      </div>
    </SectionCard>
  );
};

const HistoryCards = ({ detail }: { detail: ResponseCodeDetail }) => (
  <>
    <SectionCard title="위험도 변경 기록" aria-label="위험도 변경 기록" headingLevel={3} description="이 코드의 위험도가 바뀐 룰 세트 버전(최근 순)">
      {detail.severityHistory.length === 0 ? (
        <EmptyState>위험도 변경 기록이 없습니다.</EmptyState>
      ) : (
        <Table aria-label="위험도 변경 기록 표">
          <TableHeader><TableRow>{["버전", "저장 시각", "변경자", "위험도"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow></TableHeader>
          <TableBody>
            {detail.severityHistory.map((item) => (
              <TableRow key={item.revision} data-severity-history={item.revision}>
                <TableCell>버전 {item.revision}{item.restoredFromRevision !== null && ` · 버전 ${item.restoredFromRevision} 상태로 되돌림`}</TableCell>
                <TableCell>{stampText(item.savedAt)}</TableCell>
                <TableCell>{item.savedBy.displayName}</TableCell>
                <TableCell>{codeSeverityText(item.before)} → {codeSeverityText(item.after)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </SectionCard>
    <SectionCard title="메시지 변경 기록" aria-label="메시지 변경 기록" headingLevel={3}>
      {detail.messageHistory.length === 0 ? (
        <EmptyState>메시지 변경 기록이 없습니다.</EmptyState>
      ) : (
        <Table aria-label="메시지 변경 기록 표">
          <TableHeader><TableRow>{["시각", "변경자", "구분", "메시지"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow></TableHeader>
          <TableBody>
            {detail.messageHistory.map((item, index) => (
              <TableRow key={`${item.changedAt}:${index}`} data-message-history>
                <TableCell>{stampText(item.changedAt)}</TableCell>
                <TableCell>{item.changedBy.displayName}</TableCell>
                <TableCell>{changeKindLabels[item.kind]}</TableCell>
                <TableCell className="whitespace-normal">{item.messageBefore === null ? item.messageAfter : `${item.messageBefore} → ${item.messageAfter}`}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </SectionCard>
  </>
);

/** 룰 설정 › 응답 코드 › 코드 상세(contracts/response-codes-ui.md 4절). 상단 뒤로가기는 DetectionLayout(넘겨받은 목록 주소). */
export const ResponseCodeDetailPage = () => {
  const { code = "" } = useParams();
  const detail = useResponseCode(code);
  const rules = useRuleSet();
  const data = detail.data;
  const reloadAll = () => { void detail.refetch(); };
  return (
    <div className="grid min-w-0 gap-6">
      <ScreenTitle>{RESPONSE_CODE_TERM} · {code}</ScreenTitle>
      {(detail.isPending || rules.isPending) && <LoadingState>응답 코드 조회 중…</LoadingState>}
      {detail.isError && <ErrorState title={`응답 코드 조회 실패 · ${errorCode(detail.error)}`} onRetry={() => void detail.refetch()} retryLabel="다시 조회" />}
      {rules.isError && <ErrorState title={`현재 운영 룰 조회 실패 · ${errorCode(rules.error)}`} onRetry={() => void rules.refetch()} retryLabel="다시 조회" />}
      {data && (
        <>
          <SectionCard title={<span className="font-mono">{data.code}</span>} aria-label="코드 정보" headingLevel={3}>
            <DescriptionList
              items={[
                { label: "코드", value: <span className="font-mono">{data.code}</span> },
                { label: "코드명", value: data.enumName ?? DASH },
                { label: "유형", value: data.codeType ?? DASH },
                { label: "HTTP 상태", value: data.httpStatus ?? DASH },
                { label: "메시지 키", value: data.messageKey ?? DASH },
                { label: "출처", value: data.origin ? originLabels[data.origin] : <Badge variant="warning" data-not-in-dictionary>{NOT_IN_DICTIONARY}</Badge> },
                { label: "마지막 변경", value: data.updatedAt ? `${stampText(data.updatedAt)} · ${data.updatedBy?.displayName ?? DASH}` : DASH },
              ]}
            />
          </SectionCard>
          {rules.data && <SeverityCard code={data.code} state={rules.data} onSaved={reloadAll} />}
          <MessageCard key={data.code} detail={data} onSaved={reloadAll} />
          <HistoryCards detail={data} />
        </>
      )}
    </div>
  );
};

