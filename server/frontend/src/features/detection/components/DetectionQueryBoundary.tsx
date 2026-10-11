import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "../../../components/ui/feedback";
import type { DetectionData } from "../data/types";
export const DetectionQueryBoundary = ({
  query,
  children,
}: {
  query: UseQueryResult<DetectionData, Error>;
  children: (data: DetectionData) => ReactNode;
}) => {
  if (query.isPending)
    return <LoadingState>탐지 관리 데이터를 불러오는 중...</LoadingState>;
  if (!query.data)
    return (
      <ErrorState
        title={query.error?.message ?? "조회 결과가 없습니다"}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  return (
    <>
      {query.isError && (
        <ErrorState
          title={query.error.message}
          onRetry={() => {
            void query.refetch();
          }}
        >
          이전 조회 내용이 표시되고 있습니다.
        </ErrorState>
      )}
      {query.isFetching && (
        <LoadingState>최신 상태를 다시 확인하는 중...</LoadingState>
      )}
      {children(query.data)}
    </>
  );
};
export const DetailMissing = ({ children }: { children?: ReactNode }) => (
  <EmptyState>
    {children ?? "선택한 항목을 찾을 수 없습니다. 목록에서 다시 선택해 주세요."}
  </EmptyState>
);
export const valueText = (value: string | number | null | undefined) =>
  value == null ? "—" : String(value);
export const jobStateLabels = {
  accepted: "요청 접수",
  queued: "실행 대기",
  running: "실행 중",
  succeeded: "성공",
  failed: "실패",
} as const;
