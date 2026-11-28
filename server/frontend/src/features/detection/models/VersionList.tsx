import { Link, useLocation } from "react-router-dom";
import { paths, withSearchParam } from "@/app/routes";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { DASH, numberText, stampText } from "@/features/operations/format";
import { cn } from "@/lib/utils";
import type { CatalogMethod, ModelVersion, TrainingPeriod } from "./modelsApi";
import { fullText } from "./period";
import { modelKeyOf } from "./trainDraft";
import { useCapabilities, useVersions } from "./useModels";
import { frameSectionIds, sectionReturnTo } from "../detectionToc";
import { frameLabels, frameNotes, frameOfModelKey, frameText, frameTrainable, methodsOfFrame, notTrainableReason, type ModelFrame } from "./modelFrames";

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

/**
 * 틀 구역(models-ui.md 틀 절): 틀마다 머리(현재 운영 · 알고리즘 수 · [새 학습]) + 그 틀의 버전 목록(자체 스크롤).
 * 틀은 카탈로그 `inputRequirements.unit`(modelFrames.ts). 현재 운영은 `active=true` 조회, 목록은 전체 버전 최신순 커서를 틀로 나눠 보인다
 * (운영 중인 버전을 맨 위에). 아래 새 학습 영역은 같은 화면이고, [새 학습]은 그 틀로 새 학습을 연다(`frame`·`step=1`).
 * 그 틀에 학습할 수 있는 알고리즘이 없으면 [새 학습]은 비활성이고 카탈로그 사유 코드를 그대로 보인다.
 */
type Versions = ReturnType<typeof useVersions>;
/** 두 틀 구역이 같은 조회(현재 운영 `active=true`, 전체 버전 커서)를 나눠 쓴다: 한 구역에서 `더 보기`를 누르면 두 구역이 함께 늘어난다. */
export const useFrameVersions = () => ({ all: useVersions("all"), active: useVersions("active") });
export const FrameVersionsSection = ({ frame, versions }: { frame: ModelFrame; versions: { all: Versions; active: Versions } }) => {
  const location = useLocation();
  const capabilities = useCapabilities();
  const { all, active } = versions;
  const catalog = capabilities.data?.methodCatalog.methods;
  const methods = methodsOfFrame(catalog, frame);
  const inFrame = (version: ModelVersion) => frameOfModelKey(catalog, version.modelKey) === frame;
  const current = active.items.filter(inFrame);
  const items = all.items.filter(inFrame);
  const rows = [...items.filter((version) => version.active), ...items.filter((version) => !version.active)];
  const trainable = frameTrainable(methods);
  const section = { id: frameSectionIds[frame], label: frameLabels[frame] };
  const trainTo = paths.detectionModels + withSearchParam(withSearchParam(location.search, "frame", frame), "step", "1");
  return (
    <SectionCard
      id={section.id}
      title={section.label}
      description={frameNotes[frame]}
      action={capabilities.data && (trainable
        ? <Button asChild size="sm" data-new-train={frame}><Link to={trainTo}>{frameText.newTrain}</Link></Button>
        : <Button size="sm" disabled data-new-train={frame}>{frameText.newTrain}</Button>)}
    >
      {capabilities.isPending && <LoadingState>방법 카탈로그 조회 중…</LoadingState>}
      {capabilities.isError && <ErrorState title={`방법 카탈로그 조회 실패 · ${errorCode(capabilities.error)}`} onRetry={() => void capabilities.refetch()} retryLabel="다시 조회" />}
      {capabilities.data && (
        <div className="grid gap-1 text-sm" data-frame-head={frame}>
          <p data-current={frame}>
            {frameText.current}:{" "}
            {active.first.isError ? <span className="text-destructive-text">버전 조회 실패 · {errorCode(active.first.error)}</span>
              : !active.first.data ? DASH
              : current.length ? current.map((version, index) => (
                <strong key={version.id} className="font-semibold">
                  {index ? ", " : ""}{methodName(catalog, version.modelKey)} ·{" "}
                  <Link className="text-link underline-offset-2 hover:underline" to={paths.detectionModel(version.id)} state={{ returnTo: sectionReturnTo(location, section) }}>{versionLabel(version)}</Link>
                </strong>
              )) : <strong className="font-semibold">{frameText.none}</strong>}
          </p>
          <p className="text-muted-foreground" data-algorithms={frame}>
            {frameText.algorithms(methods.length, methods.filter((method) => method.executable.process).length)}
            {methods.length > 0 && ` · ${methods.map((method) => `${method.displayName}${method.status === "supported" ? "" : `(${frameText.research})`}`).join(", ")}`}
          </p>
          {!trainable && (
            <p className="text-muted-foreground" data-not-trainable={frame}>
              {frameText.notTrainable} · {methods.length ? methods.map((method) => `${method.displayName} ${notTrainableReason(method)}`).join(", ") : frameText.noAlgorithm}
            </p>
          )}
        </div>
      )}
      {all.first.isPending && <LoadingState>버전 조회 중…</LoadingState>}
      {all.first.isError && <ErrorState title={`버전 조회 실패 · ${errorCode(all.first.error)}`} onRetry={() => void all.first.refetch()} retryLabel="다시 조회" />}
      {all.first.data && capabilities.data && rows.length === 0 && !all.cursor && <EmptyState>{frameText.noVersions(frame)}</EmptyState>}
      {rows.length > 0 && capabilities.data && (
        <div className="max-h-80 overflow-auto" tabIndex={0} aria-label={`${frameLabels[frame]} 버전 목록`}>
          <Table>
            <TableHeader>
              <TableRow>{["버전", "모델", "상태", "운영", "입력 변수", "학습 파라미터", "판정선", "최근 평가", "생성"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((version) => <VersionRow key={version.id} version={version} methods={catalog} returnTo={sectionReturnTo(location, section)} />)}
            </TableBody>
          </Table>
        </div>
      )}
      {all.cursor && capabilities.data && <div><Button size="sm" variant="outline" disabled={all.loading} onClick={() => void all.loadMore()}>더 보기</Button></div>}
      {all.moreError && <ErrorState title={`버전 조회 실패 · ${all.moreError}`} />}
    </SectionCard>
  );
};
