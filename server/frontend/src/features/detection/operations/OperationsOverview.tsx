import { useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { paths } from "@/app/routes";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { methodName, versionLabel } from "@/features/detection/models/VersionList";
import type { CatalogMethod } from "@/features/detection/models/modelsApi";
import { HARD_TERM, SOFT_TERM, errorCode, softCondition } from "@/features/detection/rules/ruleDraft";
import type { RuleSetState, SoftRuleField } from "@/features/detection/rules/ruleSetApi";
import { DASH, msText, numberText, percentText, stampText } from "@/features/operations/format";
import { CAUSE_LABELS, hoursText, methodBadge, undecided } from "./methodStatus";
import type { ActiveModel, MethodPeriod, MethodStatus, MethodStatusReport, OperationalState } from "./operationsApi";
import { useMethodStatus } from "./useOperations";

const ML_TERM = "머신러닝 모델";
const PERIODS: { id: MethodPeriod; label: string }[] = [{ id: "1h", label: "최근 1시간" }, { id: "24h", label: "최근 24시간" }];
const STAT_HEAD = ["상태", "실행", "성공", "미판정", "이상 탐지", "미판정 사유 상위", "기한 초과", "마지막 실행", "완료 시각(시도 시작 기준) p95"];

const criteriaText = (waitMs: number) =>
  `상태 예시 기준: 미판정 2% 이상 주의, 10% 이상 점검 필요 · 실행 없음은 점검 필요(새로 적용한 규칙·모델은 적용 뒤 ${hoursText(waitMs)}까지 첫 실행 대기)`;
/**
 * 판정 없이 실패 내역(와이어프레임 '작업 실패 n · 룰 세트 읽기 실패 n'). 오류 코드 없음(job_failed)은 작업 실패, rule_set_* 코드는 룰 세트 읽기 실패로 묶고
 * 그 밖의 코드는 코드 그대로 뒤에 붙인다.
 */
const noVerdictText = (byCode: { code: string; count: number }[]) => {
  const sum = (match: (code: string) => boolean) => byCode.filter((item) => match(item.code)).reduce((total, item) => total + item.count, 0);
  const job = (code: string) => code === "job_failed";
  const ruleSet = (code: string) => code.startsWith("rule_set_");
  const rest = byCode.filter((item) => !job(item.code) && !ruleSet(item.code)).map((item) => ` · ${item.code} ${numberText(item.count)}`).join("");
  return `작업 실패 ${numberText(sum(job))} · 룰 세트 읽기 실패 ${numberText(sum(ruleSet))}${rest}`;
};
const ratio = (count: number, runs: number) => `${numberText(count)} (${percentText(runs ? count / runs : 0)})`;

/** 상태 배지 + 실행 수치 칸(와이어프레임 statCells). 실행 0이면 수치 칸은 비운다. */
const StatCells = ({ method, report, now }: { method: MethodStatus | undefined; report: MethodStatusReport; now: number }) => {
  if (!method) return <TableCell colSpan={STAT_HEAD.length}>{DASH}</TableCell>;
  const badge = methodBadge(method, report.firstRunWaitMs, now);
  const cells: ReactNode[] = method.runs === 0
    ? ["0", DASH, DASH, DASH, DASH, DASH, DASH, DASH]
    : [
      numberText(method.runs), ratio(method.succeeded, method.runs), ratio(undecided(method), method.runs), ratio(method.anomalous, method.runs),
      method.causes.length
        ? method.causes.slice(0, 3).map(({ cause, count }, index) => <span key={cause} title={cause} data-cause={cause}>{index ? " · " : ""}{CAUSE_LABELS[cause] ?? cause} {numberText(count)}</span>)
        : DASH,
      numberText(method.timeouts), stampText(method.lastRunAt, true), method.elapsedP95Ms === null ? DASH : msText(method.elapsedP95Ms),
    ];
  return (
    <>
      <TableCell>
        <span className="flex flex-wrap items-center gap-1.5" title={criteriaText(report.firstRunWaitMs)} data-status={badge.label}>
          <Badge variant={badge.tone}>{badge.label}</Badge>
          {badge.why && <span className="text-xs text-muted-foreground">{badge.why}</span>}
        </span>
      </TableCell>
      {cells.map((cell, index) => <TableCell key={STAT_HEAD[index + 1]} className="whitespace-normal">{cell}</TableCell>)}
    </>
  );
};

type RuleRow = { methodId: string; name: string; condition: string; category: string };
const ruleRows = (rules: RuleSetState, fields: SoftRuleField[]): RuleRow[] => [
  ...rules.hardRules.filter((rule) => rule.enabled).map((rule) => ({ methodId: rule.id, name: rule.displayName, condition: rule.conditionText, category: HARD_TERM })),
  ...rules.softRules.filter((rule) => rule.enabled).map((rule) => ({
    methodId: rule.id, name: rule.name, condition: softCondition(rule, fields.find((field) => field.fieldId === rule.field)), category: SOFT_TERM,
  })),
];

const NameCell = ({ name, note }: { name: ReactNode; note: string }) => (
  <TableCell className="whitespace-normal"><span className="grid"><span>{name}</span><span className="text-xs text-muted-foreground">{note}</span></span></TableCell>
);

/** 운영 버전 상태(사용자 9.6 운영 버전 overview): 지금 운영 중인 룰·모델과 방법별 작동 상태. 판정 정책은 보이지 않는다. */
export const OperationsOverview = ({ state, rules, fields, methods, onRetire }: {
  state: OperationalState;
  rules: RuleSetState;
  fields: SoftRuleField[];
  methods: CatalogMethod[] | undefined;
  onRetire: (model: ActiveModel) => void;
}) => {
  const location = useLocation();
  const [period, setPeriod] = useState<MethodPeriod>("1h");
  const status = useMethodStatus(period);
  const report = status.data;
  const now = Date.now();
  const label = PERIODS.find((item) => item.id === period)!.label;
  const statOf = (methodId: string, modelVersionId: string | null = null) =>
    report?.methods.find((item) => item.methodId === methodId && (item.modelVersionId ?? null) === modelVersionId);
  const rows = ruleRows(rules, fields);
  const models = state.activeModelSet.models;
  return (
    <SectionCard
      title="운영 버전 상태"
      action={(
        <div role="group" aria-label="상태 기간" className="flex gap-1">
          {PERIODS.map((item) => (
            <Button key={item.id} size="sm" variant={period === item.id ? "default" : "outline"} aria-pressed={period === item.id} onClick={() => setPeriod(item.id)}>{item.label}</Button>
          ))}
        </div>
      )}
    >
      <div className="grid gap-1 text-sm" aria-label="운영 버전 요약">
        <p>
          <strong className="font-semibold">룰 버전 {rules.revision}</strong> · 마지막 변경 {stampText(rules.savedAt)} · {rules.savedBy.displayName} ·{" "}
          <Link className="text-link underline-offset-2 hover:underline" to={paths.detectionRules}>룰 설정으로 이동</Link>
        </p>
        <p className="text-muted-foreground">
          {HARD_TERM} {rules.counts.hardEnabled}/{rules.counts.hardTotal} 켜짐 · {SOFT_TERM} {rules.counts.softEnabled}/{rules.counts.softTotal} 켜짐 · 운영 {ML_TERM} {models.length}개
          {report ? ` · ${label} 판정 ${numberText(report.decisionCount)}건` : ""}
          {report && report.legacyDecisionCount > 0 ? ` · 옛 형식 판정 ${numberText(report.legacyDecisionCount)}건은 방법별 집계 밖` : ""}
        </p>
      </div>
      {status.isPending && <LoadingState>작동 상태 조회 중…</LoadingState>}
      {status.isError && <ErrorState title={`작동 상태 조회 실패 · ${errorCode(status.error)}`} onRetry={() => void status.refetch()} retryLabel="다시 조회" />}
      {report && (
        <>
          <p className="text-sm" data-no-verdict>
            <strong className="font-semibold">판정 없이 실패 {numberText(report.failedWithoutDecision.count)}건</strong> · {label} · {noVerdictText(report.failedWithoutDecision.byCode)} · 방법별 집계와 별도
          </p>
          <h3 className="text-sm font-semibold">현재 운영 룰</h3>
          <div className="overflow-x-auto">
            <Table aria-label="룰 작동 상태">
              <TableCaption>켜진 규칙만 · {label}</TableCaption>
              <TableHeader><TableRow>{["규칙", "대분류", ...STAT_HEAD].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow></TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.methodId} data-method={row.methodId}>
                    <NameCell name={row.name} note={row.condition} />
                    <TableCell>{row.category}</TableCell>
                    <StatCells method={statOf(row.methodId)} report={report} now={now} />
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <h3 className="text-sm font-semibold">현재 운영 {ML_TERM}</h3>
          {models.length ? (
            <div className="overflow-x-auto">
              <Table aria-label="모델 작동 상태">
                <TableCaption>모델 종류마다 운영 버전 1개 · 여러 모델 동시 운영 · {label}</TableCaption>
                <TableHeader><TableRow>{["모델", "버전", ...STAT_HEAD, "운영"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow></TableHeader>
                <TableBody>
                  {models.map((model) => {
                    const stat = statOf(`ml:${model.modelKey}`, model.configVersionId);
                    return (
                      <TableRow key={model.configVersionId} data-method={`ml:${model.modelKey}`}>
                        <TableCell>{methodName(methods, model.modelKey)}</TableCell>
                        <NameCell
                          name={<Link className="text-link underline-offset-2 hover:underline" to={paths.detectionModel(model.configVersionId)} state={{ returnTo: location.pathname }}>{versionLabel({ id: model.configVersionId, displayName: model.displayName })}</Link>}
                          note={`적용 ${stampText(stat?.enabledSince)}`}
                        />
                        <StatCells method={stat} report={report} now={now} />
                        <TableCell><Button size="sm" variant="outline" onClick={() => onRetire(model)}>운영에서 제외</Button></TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground" data-no-model>운영 중인 {ML_TERM}이 없습니다. 룰만으로 판정합니다.</p>
          )}
          <p className="text-xs text-muted-foreground">{criteriaText(report.firstRunWaitMs)} · 판단하지 않은 경우(예: 종료 시각 없음)는 성공으로 셉니다</p>
        </>
      )}
    </SectionCard>
  );
};
