import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { IconArrowLeft, IconArrowsMaximize, IconArrowsMinimize } from "@tabler/icons-react";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { analysisErrorTitle, type DecisionDetail, type DecisionFlow, type InterimHistory } from "../data/analysisApi";
import { readEvidence, readEvidenceV4, type EvidenceV3, type EvidenceV4, type MethodCategory } from "../data/evidence";
import { useMarkRead } from "../data/useAnalysis";
import {
  DASH,
  UNAVAILABLE,
  completenessText,
  legacyRuleLabel,
  methodLabels,
  resultText,
  resultVariant,
  riskLevelLabels,
  riskLevelVariant,
  runLabels,
  stageLabels,
  stampText,
  workflowLabels,
} from "../labels";
import { DecisionDiagnostics } from "./DecisionDiagnostics";
import { DecisionTimeline, timelineEntries } from "./DecisionTimeline";
import { FeedbackCard, WorkflowActions } from "./DecisionWorkflow";
import { ExplainPanel } from "./ExplainPanel";
import { MethodResults, categoryNames } from "./MethodResults";
import { ProcessFlowCard } from "./ProcessFlowCard";

const ruleResultLabels: Record<string, string> = { hit: "이상", no_hit: "정상 확인", unavailable: "미판정", not_applicable: "해당 없음" };
const severityLabels: Record<string, string> = { critical: "위험", warning: "주의", info: "참고" };
const run = (status: string | null) => (status ? (runLabels[status] ?? status) : DASH);

const inputText = (evidence: EvidenceV3) => {
  if (evidence.demo) return `과거 데이터 재생 데모${evidence.demo.runKey ? ` · ${evidence.demo.runKey}` : ""}`;
  if (!evidence.input) return UNAVAILABLE;
  const mode = evidence.input.mode === "csv" ? "CSV 재생" : evidence.input.mode === "api" ? "API 수집" : UNAVAILABLE;
  return `${mode} · ${evidence.input.configurationScope === "input-validation" ? "선택한 후보 구성으로 검증" : "운영 구성"}`;
};
/** 방법 ID 목록 → 표시 이름(ID는 툴팁). */
const methodsNode = (ids: string[]): ReactNode => {
  const names = methodLabels(ids);
  return names.length ? <span title={ids.join(", ")}>{names.join(", ")}</span> : null;
};
/**
 * 위험도(와이어프레임 dFields 위험도): 서버가 저장된 v4 composite.risk로 정한 단계만 보인다(재계산 없음).
 * 이상이 아니면 `— · 이상일 때만 표시`, 단계 없음 = 위험도 미정(v3 이상 포함, U6). 결정 근거·단계 없는 근거는 방법 표시 이름(ID는 툴팁).
 */
const riskNode = (detail: DecisionDetail, v4: EvidenceV4 | null): ReactNode => {
  if (detail.riskStatus !== "rated" && detail.riskStatus !== "unrated") return `${DASH} · 이상일 때만 표시`;
  const head = detail.riskStatus === "rated" && detail.riskLevel ? riskLevelLabels[detail.riskLevel] : "위험도 미정";
  const by = detail.riskStatus === "rated" ? methodsNode(v4?.risk?.determinedBy ?? []) : null;
  const unrated = methodsNode(v4?.risk?.unratedMethods ?? []);
  return (
    <>
      {head}
      {by && <> · 결정 근거 {by}</>}
      {unrated && <> · 단계 없는 근거 {unrated}</>}
    </>
  );
};
/** 판정 결과 + 근거 대분류(와이어프레임 `이상 · 근거 이상 규칙`): v4 basisMethods의 대분류 표시 이름. */
const resultNode = (detail: DecisionDetail, v4: EvidenceV4 | null) => {
  const categories = [...new Set((v4?.basisMethods ?? []).map((id) => id.split(":")[0]))].filter(
    (c): c is MethodCategory => c === "hard" || c === "soft" || c === "ml",
  );
  return detail.signal === "yes" && categories.length
    ? `${resultText(detail)} · 근거 ${categories.map((c) => categoryNames[c]).join(", ")}`
    : resultText(detail);
};

/**
 * 판정 요약: 와이어프레임 판정 상세 항목(판정 ID·판정 시각·당시 룰 리비전·당시 운영 머신러닝 모델·판정 단계·판정 결과·검사 완료·위험도·
 * 입력 출처·업무 분류·거래 ID·프로세스·인터페이스 · 채널·로그 상태 · 응답 코드) 중 저장값이 있는 것만 + 최종 판정의 판정 코드·이상 점수·수행 상태.
 * 처리 시간은 PROCESS 원천 속성이 없어 생략. 판정 기한·정책 k는 보이지 않는다(사용자 9.8·C23).
 */
const SummaryCard = ({ detail }: { detail: DecisionDetail }) => {
  const evidence = readEvidence(detail.evidence);
  const v4 = readEvidenceV4(detail.evidence);
  const ruleSetRevision = v4?.ruleSetRevision ?? null;
  const config = detail.configuration;
  const final = detail.kind === "final";
  const items: { label: string; value: ReactNode }[] = [
    { label: "판정 ID", value: detail.decisionId ?? DASH },
    { label: "판정 시각", value: detail.decidedAt ? stampText(detail.decidedAt) : DASH },
  ];
  if (ruleSetRevision !== null) items.push({ label: "당시 룰 리비전", value: `r${ruleSetRevision}` });
  if (config)
    items.push({
      label: "당시 운영 머신러닝 모델",
      value: config.models.length ? config.models.map((m) => (m.adapterName ? `${m.modelKey}(${m.adapterName})` : m.modelKey)).join(", ") : "없음",
    });
  items.push(
    { label: "판정 단계", value: stageLabels[detail.stage] },
    { label: "판정 결과", value: resultNode(detail, v4) },
    { label: "검사 완료", value: completenessText(detail) },
    { label: "위험도", value: riskNode(detail, v4) },
    { label: "입력 출처", value: final ? inputText(evidence) : UNAVAILABLE },
    { label: "업무 분류", value: workflowLabels[detail.workflow.state] },
    { label: "거래 ID", value: detail.transactionId ?? UNAVAILABLE },
    { label: "프로세스", value: detail.processId ?? UNAVAILABLE },
    { label: "인터페이스 · 채널", value: `${detail.interfaceId ?? DASH} · ${detail.channelId ?? DASH}` },
    { label: "로그 상태 · 응답 코드", value: `${detail.processStatus ?? DASH} · ${detail.responseCode ?? DASH}` },
  );
  if (final)
    items.push(
      { label: "판정 코드", value: detail.decisionCode ?? DASH },
      { label: "이상 점수", value: detail.anomalyScore ?? DASH },
      { label: "수행 상태", value: `Rule ${run(detail.ruleStatus)} · ML ${run(detail.mlStatus)}` },
    );
  return (
    <SectionCard
      title="판정 요약"
      headingLevel={2}
      action={
        <>
          <Badge variant={resultVariant(detail)}>{resultText(detail)}</Badge>
          <Badge variant={detail.stage === "failed" ? "critical" : detail.stage === "final" ? "secondary" : "info"}>
            {stageLabels[detail.stage]}
          </Badge>
          {detail.riskStatus === "rated" && detail.riskLevel && (
            <Badge variant={riskLevelVariant[detail.riskLevel]}>{riskLevelLabels[detail.riskLevel]}</Badge>
          )}
        </>
      }
    >
      <DescriptionList items={items.map((item) => ({ ...item, key: item.label }))} />
      {/* 와이어프레임 dRiskTable: 판정마다 기록된 위험도 단계표 버전(v4 composite.risk.levelTable이 있을 때만) */}
      {v4?.risk?.levelTable && <p className="text-xs text-muted-foreground">위험도 기준 표 {v4.risk.levelTable}</p>}
    </SectionCard>
  );
};

/**
 * 판정 근거: evidence v4가 있으면 종합 근거·세부 방법별 결과(중간 판정 스냅샷 포함), 없으면 v3 Rule·ML 항목 그대로(최종 판정만).
 * 최종 판정은 미판정 사유(DecisionDiagnostics)를 함께 보인다.
 */
const EvidenceCard = ({ detail }: { detail: DecisionDetail }) => {
  const evidence = readEvidence(detail.evidence);
  const v4 = readEvidenceV4(detail.evidence);
  const rules = evidence.rules.filter((rule) => rule.ruleKey);
  const models = evidence.models.filter((model) => model.modelKey);
  const observedStatus = rules.find((rule) => rule.ruleKey === "process-status")?.observedStatus ?? null;
  return (
    <SectionCard title="판정 근거" headingLevel={2}>
      {detail.kind === "final" && <DecisionDiagnostics decision={detail} />}
      {v4 ? (
        <MethodResults evidence={v4} observedStatus={observedStatus} />
      ) : (
        <LegacyEvidence rules={rules} models={models} />
      )}
    </SectionCard>
  );
};

/** v3 판정 근거(과거 판정): 방법은 표시 이름(상태 규칙 = 이상 규칙, 그 밖의 등록 Rule = 이상 징후 규칙(과거 등록 Rule), U6), Rule 키는 툴팁. */
const LegacyEvidence = ({ rules, models }: { rules: EvidenceV3["rules"]; models: EvidenceV3["models"] }) => (
    <>
      <div className="grid gap-2">
        <h3 className="text-sm font-medium">Rule 판정 근거</h3>
        {rules.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>방법</TableHead>
                <TableHead>버전</TableHead>
                <TableHead>결과</TableHead>
                <TableHead>측정값</TableHead>
                <TableHead>위험도 단계</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rules.map((rule, index) => (
                <TableRow key={`${rule.ruleKey}-${index}`}>
                  <TableHead scope="row" className="h-auto font-normal" title={rule.ruleKey ?? undefined}>
                    {legacyRuleLabel(rule.ruleKey as string)}
                  </TableHead>
                  <TableCell>{rule.version ?? DASH}</TableCell>
                  <TableCell>{rule.result ? ruleResultLabels[rule.result] : DASH}</TableCell>
                  <TableCell>{rule.observedStatus ? `상태 ${rule.observedStatus}` : DASH}</TableCell>
                  <TableCell>{rule.severity ? (severityLabels[rule.severity] ?? rule.severity) : DASH}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="text-sm text-muted-foreground">Rule 판정 근거: {UNAVAILABLE}</p>
        )}
      </div>
      <div className="grid gap-2">
        <h3 className="text-sm font-medium">ML 판정 근거</h3>
        {models.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>모델</TableHead>
                <TableHead>결과</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {models.map((model, index) => (
                <TableRow key={`${model.modelKey}-${index}`}>
                  <TableHead scope="row" className="h-auto font-normal">
                    {model.modelKey}
                  </TableHead>
                  <TableCell>{model.anomalous === true ? "이상" : model.anomalous === false ? "정상 확인" : "미판정"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="text-sm text-muted-foreground">ML 판정 근거: {UNAVAILABLE}</p>
        )}
      </div>
    </>
);

type Query<T> = { data: T | undefined; isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown };

export const DecisionDetailView = ({
  id,
  query,
  interims,
  flow,
  backHref,
  isWide,
  onToggleWide,
}: {
  id: string;
  query: Query<DecisionDetail>;
  interims: Query<InterimHistory>;
  flow: Query<DecisionFlow>;
  backHref: string;
  isWide: boolean;
  onToggleWide: (value: boolean) => void;
}) => {
  const detail = query.data;
  const { user } = useAuthSession();
  // 상세를 열면 요청자 기준 확인 처리(목록의 미확인 표시가 사라짐).
  useMarkRead(detail);
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <div className="flex min-w-0 items-center gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link to={backHref}>
              <IconArrowLeft aria-hidden="true" />
              목록
            </Link>
          </Button>
          <span className="text-muted-foreground">판정 상세</span>
          <span className="text-muted-foreground" aria-hidden="true">
            /
          </span>
          <strong className="min-w-0 truncate">{detail?.transactionId ?? id}</strong>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* 판정이 바뀔 때만 초기화한다(409 뒤 재조회로 상태가 바뀌어도 실패 표시는 남는다, 다음 요청 때 지워짐). */}
          {detail && <WorkflowActions key={detail.id} detail={detail} />}
          <Button
          variant="outline"
          size="icon-sm"
          onClick={() => onToggleWide(!isWide)}
          aria-label={isWide ? "콤팩트 화면으로 보기" : "넓은 화면으로 보기"}
        >
          {isWide ? <IconArrowsMinimize aria-hidden="true" /> : <IconArrowsMaximize aria-hidden="true" />}
          </Button>
        </div>
      </div>
      {query.isPending && !detail ? (
        <LoadingState />
      ) : !detail ? (
        <ErrorState title={analysisErrorTitle(query.error, "판정 상세")} onRetry={() => void query.refetch()} />
      ) : (
        <>
          {query.isError && (
            <ErrorState title={`${analysisErrorTitle(query.error, "판정 상세")} · 이전 관측값 표시`} onRetry={() => void query.refetch()} />
          )}
          <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] xl:items-start">
            <ProcessFlowCard key={`flow-${detail.id}`} query={flow} />
            <ExplainPanel key={detail.id} detail={detail} />
          </div>
          <SectionCard title="판정 기록" headingLevel={2}>
            {interims.isPending && !interims.data ? (
              <LoadingState />
            ) : interims.isError && !interims.data ? (
              <>
                <ErrorState title={analysisErrorTitle(interims.error, "판정 기록")} onRetry={() => void interims.refetch()} />
                <DecisionTimeline entries={timelineEntries(detail, undefined)} />
              </>
            ) : (
              <DecisionTimeline entries={timelineEntries(detail, interims.data)} />
            )}
          </SectionCard>
          <SummaryCard detail={detail} />
          {(detail.kind === "final" || readEvidenceV4(detail.evidence)) && <EvidenceCard detail={detail} />}
          {user?.userType === "admin" && <FeedbackCard key={detail.id} detail={detail} />}
        </>
      )}
    </div>
  );
};
