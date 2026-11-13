import { Link, useLocation, useNavigate } from "react-router-dom";
import { paths, urlOptions } from "@/app/routes";
import { LinkTabs } from "@/components/layout/LinkTabs";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { JOB_EMPTY, JOB_KINDS, JOB_TAB_LABELS, jobResultLink, jobStatusBadge, jobTarget, kindLabel, type JobNames } from "@/features/detection/jobs/jobFormat";
import type { JobListItem } from "@/features/detection/jobs/jobsApi";
import { useJobNames, useJobs } from "@/features/detection/jobs/useJobs";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { DASH, stampText } from "@/features/operations/format";
import { useUrlOption } from "@/hooks/useUrlState";

const COLUMNS = ["작업", "종류", "대상", "요청", "상태", "결과"];

const ResultCell = ({ job, names }: { job: JobListItem; names: JobNames }) => {
  if (job.status === "failed" || (job.status === "queued" && job.errorCode)) {
    return <span className="font-mono text-xs text-destructive-text">{job.errorCode ?? DASH}</span>;
  }
  const link = jobResultLink(job, job.result, names);
  return link ? <Link className="text-link underline-offset-2 hover:underline" to={link.to}>{link.label}</Link> : <>{DASH}</>;
};

const JobRow = ({ job, names, returnTo }: { job: JobListItem; names: JobNames; returnTo: string }) => {
  const badge = jobStatusBadge(job, job.result?.failedCount);
  return (
    <TableRow data-job={job.id}>
      <TableCell><Link className="font-mono text-xs text-link underline-offset-2 hover:underline" to={paths.detectionJob(job.id)} state={{ returnTo }}>{job.id.slice(0, 8)}</Link></TableCell>
      <TableCell>{kindLabel(job.kind)}</TableCell>
      <TableCell className="whitespace-normal">{jobTarget(job, names)}</TableCell>
      <TableCell>{stampText(job.createdAt)}</TableCell>
      <TableCell><Badge variant={badge.variant}>{badge.label}</Badge></TableCell>
      <TableCell><ResultCell job={job} names={names} /></TableCell>
    </TableRow>
  );
};

/**
 * 탐지 설정 › 운영 설정 › 작업 목록(와이어프레임 #93 '작업'): 운영과 별도로 실행하는 학습·평가·평가 세트·비교 평가·AI 규칙 추천·규칙 추천 작업.
 * 상태 탭(`status`)과 종류(`kind`)는 주소에 둔다. 최신순 20개씩 `더 보기`, 진행 중 작업이 있으면 5초마다 다시 읽는다. 행은 작업 상세로 간다.
 */
export const JobListPage = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const tab = useUrlOption("status", urlOptions.jobsTab);
  const kind = useUrlOption("kind", urlOptions.jobsKind);
  const list = useJobs({ tab: tab.value, kind: kind.value });
  const names = useJobNames();
  const { first, items } = list;
  const returnTo = location.pathname + location.search;
  return (
    <div className="grid gap-6">
      <ScreenTitle>작업 목록</ScreenTitle>
      <SectionCard title="작업" description="운영과 별도로 실행">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <LinkTabs label="작업 상태" activeId={tab.value} tabs={urlOptions.jobsTab.map((id) => ({ id, label: JOB_TAB_LABELS[id], to: tab.hrefFor(id) }))} />
          <div className="grid gap-1">
            <Label htmlFor="job-kind">종류</Label>
            <NativeSelect id="job-kind" size="sm" value={kind.value} onChange={(event) => navigate(kind.hrefFor(event.target.value as typeof kind.value), { state: location.state })}>
              <NativeSelectOption value="all">전체 종류</NativeSelectOption>
              {JOB_KINDS.map((value) => <NativeSelectOption key={value} value={value}>{kindLabel(value)}</NativeSelectOption>)}
            </NativeSelect>
          </div>
        </div>
        {first.isPending && <LoadingState>작업 조회 중…</LoadingState>}
        {first.isError && <ErrorState title={`작업 조회 실패 · ${errorCode(first.error)}`} onRetry={() => void first.refetch()} retryLabel="다시 조회" />}
        {first.data && items.length === 0 && !list.cursor && !first.isError && <EmptyState>{JOB_EMPTY[tab.value]}</EmptyState>}
        {items.length > 0 && (
          <Table aria-label="작업 목록">
            <TableHeader>
              <TableRow>{COLUMNS.map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
            </TableHeader>
            <TableBody>
              {items.map((job) => <JobRow key={job.id} job={job} names={names} returnTo={returnTo} />)}
            </TableBody>
          </Table>
        )}
        {list.cursor && <div><Button size="sm" variant="outline" disabled={list.loading} onClick={() => void list.loadMore()}>더 보기</Button></div>}
      </SectionCard>
    </div>
  );
};
