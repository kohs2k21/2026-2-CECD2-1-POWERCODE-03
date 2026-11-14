import { Link, useLocation } from "react-router-dom";
import { urlOptions, paths } from "@/app/routes";
import { LinkTabs } from "@/components/layout/LinkTabs";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { DASH, numberText, stampText } from "@/features/operations/format";
import { useUrlOption } from "@/hooks/useUrlState";
import { cn } from "@/lib/utils";
import type { CatalogMethod, ModelVersion, TrainingPeriod } from "./modelsApi";
import { fullText } from "./period";
import { modelKeyOf } from "./trainDraft";
import { useCapabilities, useVersions } from "./useModels";
import { detectionSections } from "../detectionToc";

export const TIME_BASIS_LABELS = { esb_occurred_at: "ESB 발생 시각 기준", persisted_at: "저장 시각 기준" } as const;
export const periodText = (period: TrainingPeriod | null) =>
  period ? `${fullText(period.from)} ~ ${fullText(period.until)} · ${TIME_BASIS_LABELS[period.timeBasis]}` : DASH;
export const versionLabel = (version: Pick<ModelVersion, "id" | "displayName">) =>
  `${version.id.slice(0, 8)}${version.displayName ? ` · ${version.displayName}` : ""}`;
export const methodName = (methods: CatalogMethod[] | undefined, modelKey: string | null) =>
  methods?.find((method) => modelKeyOf(method) === modelKey)?.displayName ?? modelKey ?? DASH;

export const MethodStatus = ({ status }: { status: string }) => {
  if (status === "supported") return <Badge variant="success">지원 · supported</Badge>;
  if (status === "research") return <Badge variant="warning">연구 중 · research</Badge>;
  return <Badge>{status}</Badge>;
};
export const ActiveBadge = ({ active }: { active: boolean }) => (active ? <Badge variant="success">운영 중</Badge> : <>{DASH}</>);
const MISSING = "없음";
/** 목록의 학습 파라미터: 조정 가능한 키만 `키=값`(와이어프레임). 고정 값은 상세에서 본다. */
export const parameterSummary = (method: CatalogMethod | undefined, parameters: Record<string, unknown> | null | undefined) => {
  if (!parameters) return MISSING;
  const fixed = (key: string) => Boolean(method && "const" in (method.parameterSchema.properties[key] ?? {}));
  return Object.entries(parameters).filter(([key]) => !fixed(key)).map(([key, value]) => `${key}=${String(value)}`).join(", ") || MISSING;
};
/** 판정선은 저장값이 긴 소수라 목록에서는 넷째 자리까지 보인다. 원값은 상세. */
export const thresholdSummary = (value: string | null | undefined) => {
  if (!value) return MISSING;
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("ko-KR", { maximumFractionDigits: 4 }) : value;
};
const EvaluationBadge = ({ evaluation }: { evaluation: ModelVersion["latestEvaluation"] }) =>
  evaluation ? <Badge variant="success">평가 완료 · {stampText(evaluation.completedAt)}</Badge> : <Badge>평가 전</Badge>;

const VersionRow = ({ version, methods, returnTo }: { version: ModelVersion; methods: CatalogMethod[] | undefined; returnTo: string }) => (
  <TableRow className={cn(!version.executable && "text-muted-foreground")}>
    <TableCell>
      <Link className="text-link underline-offset-2 hover:underline" to={paths.detectionModel(version.id)} state={{ returnTo }}>{versionLabel(version)}</Link>
    </TableCell>
    <TableCell>{methodName(methods, version.modelKey)}</TableCell>
    <TableCell><MethodStatus status={version.methods[0]?.status ?? DASH} /></TableCell>
    <TableCell><ActiveBadge active={version.active} /></TableCell>
    <TableCell>{version.modelSummary?.featureCount == null ? MISSING : `${numberText(version.modelSummary.featureCount)}개`}</TableCell>
    <TableCell className="whitespace-normal">{parameterSummary(methods?.find((method) => modelKeyOf(method) === version.modelKey), version.modelSummary?.parameters)}</TableCell>
    <TableCell>{thresholdSummary(version.modelSummary?.thresholdValue)}</TableCell>
    <TableCell><EvaluationBadge evaluation={version.latestEvaluation} /></TableCell>
    <TableCell>{stampText(version.createdAt)}</TableCell>
  </TableRow>
);

/** 버전 목록(사용자 9.6): 탭 2개 + 자체 스크롤 영역. 아래 새 학습 영역은 같은 화면이다. */
export const VersionsCard = () => {
  const location = useLocation();
  const tab = useUrlOption("tab", urlOptions.modelsTab);
  const versions = useVersions(tab.value);
  const capabilities = useCapabilities();
  const { first, items } = versions;
  return (
    <SectionCard id={detectionSections.modelsVersions.id} title={detectionSections.modelsVersions.label}>
      <LinkTabs
        label="버전 보기"
        activeId={tab.value}
        tabs={[{ id: "active", label: "현재 운영 버전", to: tab.hrefFor("active") }, { id: "all", label: "전체 버전", to: tab.hrefFor("all") }]}
      />
      {first.isPending && <LoadingState>버전 조회 중…</LoadingState>}
      {first.isError && <ErrorState title={`버전 조회 실패 · ${errorCode(first.error)}`} onRetry={() => void first.refetch()} retryLabel="다시 조회" />}
      {first.data && items.length === 0 && !versions.cursor && !first.isError && (
        <EmptyState>{tab.value === "active" ? "운영 중인 버전이 없습니다." : "학습된 버전이 없습니다."}</EmptyState>
      )}
      {items.length > 0 && (
        <div className="max-h-80 overflow-auto" tabIndex={0} aria-label="버전 목록">
          <Table>
            <TableHeader>
              <TableRow>{["버전", "모델", "상태", "운영", "입력 변수", "학습 파라미터", "판정선", "최근 평가", "생성"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
            </TableHeader>
            <TableBody>
              {items.map((version) => (
                <VersionRow key={version.id} version={version} methods={capabilities.data?.methodCatalog.methods} returnTo={location.pathname + location.search} />
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {versions.cursor && <div><Button size="sm" variant="outline" disabled={versions.loading} onClick={() => void versions.loadMore()}>더 보기</Button></div>}
      {versions.moreError && <ErrorState title={`버전 조회 실패 · ${versions.moreError}`} />}
    </SectionCard>
  );
};
