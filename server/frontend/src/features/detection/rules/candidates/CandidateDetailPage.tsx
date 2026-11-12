import type { ReactNode } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { paths } from "@/app/routes";
import { stampText } from "@/features/operations/format";
import type { DetectionReturnState } from "../../DetectionLayout";
import type { Approval, CandidateDetail, ResultHead } from "./candidateApi";
import {
  APPROVE, CANDIDATES_TITLE, PAIR_WARNING, THRESHOLD_BASIS, adapterText, approvalBlockedText, candidateErrorTitle, countLabels, kindLabels, limitationText, n, pathText, rateText,
  reasonText, relationStatusBadge, relationStatusLabels, thresholdStatusLabels, thresholdText,
} from "./candidateLabels";
import { useCandidateDetail } from "./useCandidates";

type Relation = Extract<CandidateDetail, { type: "relation" }>;
type Threshold = Extract<CandidateDetail, { type: "threshold" }>;

/** 판정 시점(Detector relation-rules.md 2절 evaluatedAt): successor = 뒤 단계 도착 때 1차·거래 행 도착 때 최종, transaction = 거래 완료 때. */
const evaluatedAtText = (value: string | null) =>
  value === "successor" ? "뒤 단계 도착 때 판정 · 거래 행 도착 때 최종" : value === "transaction" ? "거래 완료(거래 행 도착) 때 판정" : value ?? "—";

const Tally = ({ title, values }: { title: string; values: Record<string, number> }) => {
  const entries = Object.entries(values);
  return (
    <div className="grid gap-1 text-sm">
      <span className="text-xs text-muted-foreground">{title}</span>
      {entries.length ? (
        <ul className="grid gap-0.5">
          {entries.map(([code, value]) => (
            <li key={code} data-tally={code}>{reasonText(code)} {n(value)}건</li>
          ))}
        </ul>
      ) : (
        <span>—</span>
      )}
    </div>
  );
};

/** 승인 = 룰 세트 저장(새 버전, 되돌리기 대상). 저장 형식 3 전에는 단추를 끄고 사유를 그대로 보인다. */
const ApproveBar = ({ approval, approvable }: { approval: Approval; approvable: boolean }) => (
  <div data-approve-bar className="flex flex-wrap items-center gap-3 text-sm">
    <Button size="sm" disabled aria-describedby="approve-blocked">{APPROVE}</Button>
    <span id="approve-blocked" className="text-muted-foreground">
      {!approvable ? "승인 불가 · 후보 기준 미달" : approval.available ? "승인 저장 연결 전" : approvalBlockedText(approval.reason)}
    </span>
  </div>
);

const draftValue = (value: unknown) => (value === null || value === undefined ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value));
/** 승인 때 룰 세트에 들어갈 조건(서버 ruleDraft). id·위험도·이름은 승인 때 정한다. */
const DraftCard = ({ draft, keys }: { draft: Record<string, unknown> | null; keys: string[] }) => (
  <SectionCard title="승인 시 저장할 조건" headingLevel={3}>
    {draft ? (
      <DescriptionList items={keys.map((key) => ({ key, label: key, value: <span className="font-mono text-xs">{draftValue(draft[key])}</span> }))} />
    ) : (
      <EmptyState>제안 임계값이 없어 저장할 조건이 없습니다.</EmptyState>
    )}
  </SectionCard>
);

const HeadLine = ({ head }: { head: ResultHead & { isLatest: boolean } }) => (
  <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted-foreground">
    <span>후보 작업 완료 {stampText(head.job.finishedAt)}</span>
    <span>기준 기간 {stampText(head.period.from)} ~ {stampText(head.period.until)}</span>
    {!head.isLatest && <Badge variant="warning" data-not-latest>이전 작업 결과</Badge>}
  </div>
);

const RelationView = ({ detail }: { detail: Relation }) => {
  const location = useLocation();
  const item = detail.relation;
  const returnState: DetectionReturnState = { returnTo: (location.state as DetectionReturnState | null)?.returnTo ?? paths.detectionRuleCandidates };
  return (
    <>
      <SectionCard
        title={<span className="flex flex-wrap items-center gap-2">{kindLabels[item.kind]} <span className="font-mono">{pathText(item)}</span></span>}
        description={<HeadLine head={detail} />}
        action={<Badge variant={relationStatusBadge[item.status]}>{relationStatusLabels[item.status]}</Badge>}
      >
        <DescriptionList
          items={[
            { label: "인터페이스", value: item.interfaceId },
            { label: "어댑터", value: adapterText(item) },
            { label: "판정 시점", value: evaluatedAtText(item.evaluatedAt) },
            ...(["applicable", "satisfied", "violated", "undetermined"] as const).map((key) => ({ key, label: countLabels[key], value: `${n(item.counts[key])}건` })),
            { label: "충족률", value: rateText(item.satisfactionRate) },
            { label: "승인 가능", value: item.approvable ? "가능" : "불가" },
          ]}
        />
        {item.kind === "message_handoff" && item.pairApprovable === false && (
          <p data-pair-warning className="text-sm text-warning-text">{PAIR_WARNING}</p>
        )}
        {detail.pairedPrecedes && (
          <p data-paired className="text-sm">
            짝 선행 관계{" "}
            <Link to={paths.detectionRuleCandidate(detail.pairedPrecedes.candidateId)} state={returnState} className="text-link hover:underline">
              {relationStatusLabels[detail.pairedPrecedes.status]} · 충족률 {rateText(detail.pairedPrecedes.satisfactionRate)}
            </Link>
          </p>
        )}
        <ApproveBar approval={detail.approval} approvable={item.approvable} />
      </SectionCard>
      <SectionCard title="사유별 건수" headingLevel={3}>
        <div className="grid gap-4 sm:grid-cols-3">
          <Tally title="위반 사유" values={item.violationReasons} />
          <Tally title="보류 사유" values={item.undeterminedReasons} />
          <Tally title="거래 상태별 위반" values={item.violationsByTransactionStatus} />
        </div>
      </SectionCard>
      <SectionCard title="대표 반례" headingLevel={3} description={`위반 거래 앞 ${n(detail.parameters.maxCounterexamples ?? 5)}건까지(거래 ID 순)`}>
        {detail.counterexamples.length ? (
          <Table aria-label="대표 반례 표">
            <TableHeader>
              <TableRow>
                <TableHead>거래 ID</TableHead>
                <TableHead>사유</TableHead>
                <TableHead>거래 상태</TableHead>
                <TableHead>판정 상세</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {detail.counterexamples.map((row, index) => (
                <TableRow key={`${row.transactionId}-${index}`} data-counterexample={row.transactionId ?? ""}>
                  <TableCell className="font-mono text-sm">{row.transactionId ?? "—"}</TableCell>
                  <TableCell>{reasonText(row.reason)}</TableCell>
                  <TableCell>{row.transactionStatus ?? "—"}</TableCell>
                  <TableCell>
                    {row.decisionId ? (
                      <Link to={paths.analysisDecision(row.decisionId)} className="text-link hover:underline">실행 순서 보기</Link>
                    ) : (
                      <span className="text-muted-foreground">판정 없음</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <EmptyState>위반 거래가 없습니다.</EmptyState>
        )}
      </SectionCard>
      <DraftCard draft={detail.ruleDraft} keys={["kind", "scope", "from", "to", "match"]} />
    </>
  );
};

const ThresholdView = ({ detail }: { detail: Threshold }) => {
  const item = detail.threshold;
  const e = item.evidence;
  const value = (v: number | null) => (v === null ? "—" : `${v.toLocaleString("ko-KR")} ${item.unit}`.trim());
  return (
    <>
      <SectionCard
        title={<span className="flex flex-wrap items-center gap-2">처리 시간 <span className="font-mono">{item.scope.processId}</span></span>}
        description={<HeadLine head={detail} />}
        action={<Badge variant={item.status === "candidate" ? "success" : "outline"}>{thresholdStatusLabels[item.status]}</Badge>}
      >
        <DescriptionList
          items={[
            { label: "어댑터", value: item.scope.adapterType ?? "모든 어댑터" },
            { label: "인터페이스", value: item.interfaceIds.length ? item.interfaceIds.join(", ") : "—" },
            { label: "제안 임계값", value: <span><span className="font-mono">{thresholdText(item)}</span>{item.threshold !== null && <span className="block text-xs text-muted-foreground">{THRESHOLD_BASIS}</span>}</span> },
            { label: "근거 건수", value: `${n(e.n)}건` },
            { label: "학습 행 · 기준 표본 행", value: `${n(e.rows)} · ${n(e.referenceRows)}` },
            { label: "END_TIME 없음(기준 표본)", value: `${n(e.endTimeMissingReferenceRows)}건` },
            { label: "계산 불가(기준 표본)", value: `${n(e.unavailableReferenceRows)}건` },
            { label: "최소 · 최대 · p95", value: `${value(e.min)} · ${value(e.max)} · ${value(e.p95)}` },
            ...(item.reason ? [{ label: "사유", value: item.reason }] : []),
          ]}
        />
        {item.limitation && <p data-threshold-limitation className="text-sm text-muted-foreground">한계(Trainer) · {limitationText(item.limitation)}</p>}
        <ApproveBar approval={detail.approval} approvable={item.status === "candidate"} />
      </SectionCard>
      <DraftCard draft={detail.ruleDraft} keys={["field", "operator", "threshold", "unit", "scope"]} />
    </>
  );
};

/** 규칙 추천 후보 상세(contracts/rule-candidates-ui.md 8절). 상단 뒤로가기는 DetectionLayout(넘겨받은 목록 주소, 없으면 룰 설정). */
export const CandidateDetailPage = () => {
  const { candidateId = "" } = useParams();
  const query = useCandidateDetail(candidateId);
  const detail = query.data;
  let body: ReactNode;
  if (query.isPending && !detail) body = <LoadingState />;
  else if (!detail) body = <ErrorState title={candidateErrorTitle(query.error, "후보 상세")} onRetry={() => void query.refetch()} />;
  else body = detail.type === "relation" ? <RelationView detail={detail} /> : <ThresholdView detail={detail} />;
  return (
    <div className="grid min-w-0 gap-6">
      <ScreenTitle>{CANDIDATES_TITLE} 후보 {candidateId}</ScreenTitle>
      {body}
    </div>
  );
};
