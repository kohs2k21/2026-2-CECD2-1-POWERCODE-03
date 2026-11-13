import { useAccountQuery } from "@/lib/query";
import { GENERATION_SERVICE_UNAVAILABLE, fetchLlmStatus } from "@/services/api/llm.api";

export const llmStatusQueryName = "llm-status";
/** LLM 요청 버튼이 있는 화면에 있는 동안의 재조회 간격(llm-recommendation-job.md '생성 서비스 가용성' 절). */
export const LLM_STATUS_POLL_MS = 30_000;

/**
 * LLM 생성 서비스 가용성. 화면에 들어갈 때 1회(staleTime 0) + 그 화면에 있는 동안 30초마다 조회한다(화면들이 같은 캐시를 쓴다).
 * `unavailableCode`는 서버가 꺼짐(`available: false`)이라고 답했을 때만 오류 코드이고, 그 밖에는 null이다.
 * 조회 전·조회 실패(네트워크·인증·서버 오류, 앞서 꺼짐을 받은 뒤의 재조회 실패 포함)는 막지 않고 기존 동작을 유지한다: 가용성을 모르는 상태로 버튼을 막으면 상태 API만의 장애가
 * LLM 기능 전체를 막게 되고, 서버가 POST /api/jobs 접수에서 같은 확인으로 LLM 작업을 503 `generation_service_unavailable`로 거절하므로
 * 꺼진 생성 서비스에 작업이 쌓이지는 않는다.
 */
export const useLlmStatus = () => {
  const status = useAccountQuery({ name: llmStatusQueryName, read: fetchLlmStatus, staleTime: 0, pollMs: LLM_STATUS_POLL_MS });
  // 재조회가 실패하면 react-query는 마지막 data(꺼짐)를 남기므로 isError를 먼저 본다: 조회 실패는 꺼짐이 아니라 '모름'이다.
  const unavailableCode =
    !status.isError && status.data && !status.data.available ? (status.data.errorCode ?? GENERATION_SERVICE_UNAVAILABLE) : null;
  // refetch는 react-query가 같은 함수를 돌려준다(effect 의존성으로 써도 다시 돌지 않음).
  return { unavailableCode, refresh: status.refetch };
};
