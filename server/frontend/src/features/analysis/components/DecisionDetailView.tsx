import { Link } from "react-router-dom";
import { IconArrowLeft, IconArrowsMaximize, IconArrowsMinimize } from "@tabler/icons-react";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { analysisErrorTitle, type DecisionDetail, type InterimHistory } from "../data/analysisApi";
import { readEvidence, readEvidenceV4, type EvidenceV3 } from "../data/evidence";
import {
  DASH,
  UNAVAILABLE,
  completenessText,
  resultText,
  resultVariant,
  runLabels,
  stageLabels,
  stampText,
} from "../labels";
import { DecisionDiagnostics } from "./DecisionDiagnostics";
import { DecisionTimeline, timelineEntries } from "./DecisionTimeline";
import { ExplainPanel } from "./ExplainPanel";

const ruleResultLabels: Record<string, string> = { hit: "이상", no_hit: "정상 확인", unavailable: "미판정", not_applicable: "해당 없음" };
const severityLabels: Record<string, string> = { critical: "위험", warning: "주의", info: "참고" };
const run = (status: string | null) => (status ? (runLabels[status] ?? status) : DASH);

const inputText = (evidence: EvidenceV3) => {
  if (evidence.demo) return `과거 데이터 재생 데모${evidence.demo.runKey ? ` · ${evidence.demo.runKey}` : ""}`;
  if (!evidence.input) return UNAVAILABLE;
  const mode = evidence.input.mode === "csv" ? "CSV 재생" : evidence.input.mode === "api" ? "API 수집" : UNAVAILABLE;
  return `${mode} · ${evidence.input.configurationScope === "input-validation" ? "선택한 후보 구성으로 검증" : "운영 구성"}`;
};
/**
 * 판정 요약: 와이어프레임 판정 상세 항목(판정 ID·판정 시각·당시 룰 리비전·당시 운영 머신러닝 모델·판정 단계·판정 결과·검사 완료·
 * 입력 출처·거래 ID·프로세스·인터페이스 · 채널·로그 상태 · 응답 코드) 중 저장값이 있는 것만 + 최종 판정의 판정 코드·이상 점수·수행 상태.
 * 위험도·업무 분류·처리 시간은 저장값이 없어(또는 보류 범위라) 생략. 판정 기한·정책 k는 보이지 않는다(사용자 9.8).
 */
const SummaryCard = ({ detail }: { detail: DecisionDetail }) => {
  const evidence = readEvidence(detail.evidence);
  const ruleSetRevision = readEvidenceV4(detail.evidence)?.ruleSetRevision ?? null;
  const config = detail.configuration;
  const final = detail.kind === "final";
  const items: { label: string; value: string }[] = [
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
    { label: "판정 결과", value: resultText(detail) },
    { label: "검사 완료", value: completenessText(detail) },
    { label: "입력 출처", value: final ? inputText(evidence) : UNAVAILABLE },
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
        </>
      }
    >
      <DescriptionList items={items.map((item) => ({ ...item, key: item.label }))} />
    </SectionCard>
  );
};

/** 판정 근거: evidence v3 Rule·ML 항목 그대로 + 미판정 사유. 최종 판정만. */
const EvidenceCard = ({ detail }: { detail: DecisionDetail }) => {
  const evidence = readEvidence(detail.evidence);
  const rules = evidence.rules.filter((rule) => rule.ruleKey);
  const models = evidence.models.filter((model) => model.modelKey);
  return (
    <SectionCard title="판정 근거" headingLevel={2}>
      <DecisionDiagnostics decision={detail} />
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
                  <TableHead scope="row" className="h-auto font-normal">
                    {rule.ruleKey}
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
    </SectionCard>
  );
};

export const DecisionDetailView = ({
  id,
  query,
  interims,
  backHref,
  isWide,
  onToggleWide,
}: {
  id: string;
  query: { data: DecisionDetail | undefined; isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown };
  interims: { data: InterimHistory | undefined; isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown };
  backHref: string;
  isWide: boolean;
  onToggleWide: (value: boolean) => void;
}) => {
  const detail = query.data;
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
        <Button
          variant="outline"
          size="icon-sm"
          onClick={() => onToggleWide(!isWide)}
          aria-label={isWide ? "콤팩트 화면으로 보기" : "넓은 화면으로 보기"}
        >
          {isWide ? <IconArrowsMinimize aria-hidden="true" /> : <IconArrowsMaximize aria-hidden="true" />}
        </Button>
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
            <SummaryCard detail={detail} />
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
          {detail.kind === "final" && <EvidenceCard detail={detail} />}
        </>
      )}
    </div>
  );
};
