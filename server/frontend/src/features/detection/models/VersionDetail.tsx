import { Link, useLocation, useParams } from "react-router-dom";
import { paths } from "@/app/routes";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { DASH, numberText, stampText } from "@/features/operations/format";
import { useAccountQuery } from "@/lib/query";
import { readVersion, type ModelVersionDetail } from "./modelsApi";
import { fullText } from "./period";
import { parameterLabel } from "./trainDraft";
import { useCapabilities, useTrainJob } from "./useModels";
import { ActiveBadge, MethodStatus, methodName, periodText, versionLabel } from "./VersionList";

const Mono = ({ children }: { children: string }) => <span className="font-mono text-xs break-all">{children}</span>;

const thresholdText = (spec: Record<string, unknown> | null | undefined) =>
  spec ? Object.entries(spec).map(([key, value]) => `${key} ${String(value)}`).join(" · ") : DASH;

const ParametersCard = ({ parameters }: { parameters: Record<string, unknown> | null }) => (
  <SectionCard title="학습 파라미터" headingLevel={3}>
    {parameters ? (
      <Table>
        <TableHeader><TableRow><TableHead>파라미터</TableHead><TableHead>값</TableHead></TableRow></TableHeader>
        <TableBody>
          {Object.entries(parameters).map(([key, value]) => (
            <TableRow key={key}>
              <TableCell><span className="grid"><span>{parameterLabel(key)}</span><span className="font-mono text-xs text-muted-foreground">{key}</span></span></TableCell>
              <TableCell>{String(value)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    ) : <p className="text-sm text-muted-foreground">이 버전에는 파라미터 기록이 없습니다.</p>}
  </SectionCard>
);

/** 와이어프레임 '이 버전으로 교체': 교체 2단계(비교 확인)로 간다. 실행 가능·운영 중 아님·운영 적용 평가 있음일 때만 누를 수 있다. */
const ReplaceAction = ({ version }: { version: ModelVersionDetail }) => {
  const location = useLocation();
  const usable = version.executable && !version.active && version.applicationEligibility?.eligible === true;
  return usable
    ? <Button asChild size="sm" variant="outline"><Link to={`${paths.detectionReplace}?step=2`} state={{ returnTo: location.pathname + location.search, targetVersionId: version.id }}>이 버전으로 교체</Link></Button>
    : <Button size="sm" variant="outline" disabled>이 버전으로 교체</Button>;
};

const VersionBody = ({ version }: { version: ModelVersionDetail }) => {
  const location = useLocation();
  const capabilities = useCapabilities();
  const trainJobId = version.createdFrom.trainJobId ?? null;
  const job = useTrainJob(trainJobId);
  const manifest = job.data?.resultSummary?.modelManifest;
  const method = version.methods[0];
  const model = version.models[0];
  const selection = version.trainingSelection;
  const excluded = model?.trainingSettings?.excludedIntervals ?? [];
  const features = model?.trainingSettings?.featureNames ?? [];
  const hash = (key: "modelHash" | "fitHash") => {
    if (job.isError) return `조회 실패 · ${errorCode(job.error)}`;
    const value = manifest?.[key];
    return typeof value === "string" ? <Mono>{value}</Mono> : DASH;
  };
  return (
    <>
      <SectionCard title={`버전 ${versionLabel(version)}`} action={version.modelKey ? <ReplaceAction version={version} /> : undefined}>
        <DescriptionList
          items={[
            { label: "모델", value: `${methodName(capabilities.data?.methodCatalog.methods, version.modelKey)}${method ? ` · ${method.methodId}` : ""}` },
            { label: "버전", value: <Mono>{version.id}</Mono> },
            { label: "상태", value: <MethodStatus status={method?.status ?? DASH} /> },
            { label: "운영", value: <ActiveBadge active={version.active} /> },
            { label: "방법 버전", value: method?.methodVersion ?? DASH },
            { label: "학습 작업", value: trainJobId ? <Link className="text-link" to={paths.detectionJob(trainJobId)} state={{ returnTo: location.pathname + location.search }}>{trainJobId.slice(0, 8)}</Link> : DASH },
            { label: "생성", value: stampText(version.createdAt) },
            { label: "최근 평가", value: version.latestEvaluation ? stampText(version.latestEvaluation.completedAt) : "평가 전" },
          ]}
        />
      </SectionCard>
      <SectionCard title="학습 기간" headingLevel={3}>
        <DescriptionList
          items={[
            { label: "기간", value: periodText(selection.trainingPeriod) },
            { label: "학습 건수", value: selection.selectedCount === undefined ? DASH : `${numberText(selection.selectedCount)}건 / 분할 ${numberText(selection.populationCount ?? 0)}건` },
            { label: "선정 정책", value: selection.policyVersion ?? DASH },
            { label: "제외 구간", value: `${selection.excludedIntervalCount}구간` },
          ]}
        />
        {excluded.length > 0 && (
          <ul className="grid gap-1 text-sm">
            {excluded.map((item) => <li key={item.from}>{fullText(item.from)} ~ {fullText(item.until)}{item.note ? ` · ${item.note}` : ""}</li>)}
          </ul>
        )}
      </SectionCard>
      <SectionCard title={`입력 변수 ${features.length}개`} headingLevel={3}>
        {features.length > 0 ? <p className="text-sm break-words">{features.join(", ")}</p> : <p className="text-sm text-muted-foreground">입력 변수 기록이 없습니다.</p>}
      </SectionCard>
      <ParametersCard parameters={method?.parameters ?? null} />
      <SectionCard title="산출물" headingLevel={3}>
        <DescriptionList
          items={[
            { label: "모델 파일", value: model?.modelArtifactId ? <Mono>{model.modelArtifactId}</Mono> : DASH },
            { label: "모델 hash", value: hash("modelHash") },
            { label: "전처리 파일", value: model?.fitArtifactId ? <Mono>{model.fitArtifactId}</Mono> : DASH },
            { label: "전처리 hash", value: hash("fitHash") },
            { label: "판정선", value: thresholdText(model?.thresholdSpec) },
            { label: "구성 hash", value: <Mono>{version.contentHash}</Mono> },
          ]}
        />
      </SectionCard>
    </>
  );
};

/** 모델 버전 상세: 조회만 한다. 교체·복원은 하위 7, 평가 이력은 하위 6에서 붙인다. */
export const ModelVersionDetailPage = () => {
  const { versionId = "" } = useParams();
  const version = useAccountQuery({ name: "model-version", params: [versionId], adminOnly: true, staleTime: 0, read: (signal) => readVersion(versionId, signal) });
  return (
    <div className="grid gap-6">
      <ScreenTitle>모델 버전 상세 {versionId}</ScreenTitle>
      {version.isPending && <LoadingState>버전 조회 중…</LoadingState>}
      {version.isError && <ErrorState title={`버전 조회 실패 · ${errorCode(version.error)}`} onRetry={() => void version.refetch()} retryLabel="다시 조회" />}
      {version.data && <VersionBody version={version.data} />}
    </div>
  );
};
