import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { accountQueryKey, useAccountQuery } from "@/lib/query";
import { readJob, type JobDetail } from "@/features/detection/jobs/jobsApi";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { getStoredToken } from "@/services/auth/session";
import { jobRequestKey } from "@/services/api/job-idempotency";
import { createPipelineJob } from "@/services/api/pipeline.api";
import { existingJob, readProtocolExplanationJobs, type ProtocolExplanationRequest } from "./protocolExplanation";

export const protocolExplanationJobsQueryName = "protocol-explanation-jobs";
export const protocolExplanationJobQueryName = "protocol-explanation-job";

/** 진행 중(5초 주기 조회·진행 중 표시) = queued·running만(판정 설명과 같은 규칙). */
export const explanationPending = (job: Pick<JobDetail, "status"> | undefined) => job?.status === "queued" || job?.status === "running";

/**
 * 프로토콜 경로 설명 작업(관리자). 판정 설명(useExplainJob)과 같은 흐름이다.
 * - 기존 결과: 최근 `explain_protocol` 작업 목록에서 같은 대상(입력 = 요청 본문)의 작업을 찾아 이어서 보여 준다(화면에 들어올 때마다 다시 읽음).
 *   목록 밖의 옛 작업은 요청하면 서버가 재사용해 돌려준다(protocol-explanation-job.md 1절 재사용)
 * - 요청: POST /api/jobs + `Idempotency-Key`(같은 계정·같은 본문 = 같은 키, job-idempotency). 보이는 작업이 실패면 새 키로 다시 요청한다
 *   (같은 키는 실패 작업을 그대로 돌려준다). 결과 조회는 진행 중일 때만 5초 주기
 */
export const useProtocolExplanation = (request: ProtocolExplanationRequest) => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  const fingerprint = JSON.stringify(request);
  const [requested, setRequested] = useState<{ for: string; id: string } | null>(null);
  const lookup = useAccountQuery({
    name: protocolExplanationJobsQueryName, adminOnly: true, staleTime: 0,
    read: (signal) => readProtocolExplanationJobs(signal),
  });
  const found = lookup.data ? existingJob(lookup.data.items, request) : null;
  // 대상이 바뀌면(저장으로 운영 버전이 바뀜 등) 방금 요청한 작업은 그 대상의 것이 아니다.
  const jobId = (requested?.for === fingerprint ? requested.id : null) ?? found?.id ?? null;
  const job = useAccountQuery({
    name: protocolExplanationJobQueryName, params: [jobId], adminOnly: true, enabled: Boolean(jobId), staleTime: 0,
    read: (signal) => readJob(jobId!, signal),
    isRunning: explanationPending,
  });
  const mutation = useMutation({
    mutationFn: async ({ body, failed }: { body: ProtocolExplanationRequest; failed: boolean }) => {
      if (!user) throw new Error("login_required");
      const token = getStoredToken();
      const created = await createPipelineJob(body, jobRequestKey(user.id, body, failed));
      return { created, token, for: JSON.stringify(body) };
    },
    onSuccess: ({ created, token, for: target }) => {
      if (token !== getStoredToken()) return;
      setRequested({ for: target, id: created.id });
      void cache.invalidateQueries({ queryKey: accountQueryKey(protocolExplanationJobQueryName, user?.id, created.id) });
    },
  });
  const current = job.data && job.data.id === jobId ? job.data : undefined;
  return {
    jobId,
    lookup,
    job,
    current,
    request: () => mutation.mutate({ body: request, failed: current?.status === "failed" }),
    mutation,
  };
};
