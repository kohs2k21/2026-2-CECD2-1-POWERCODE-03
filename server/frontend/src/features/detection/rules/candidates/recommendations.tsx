import { IconChevronRight } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { paths, urlOptions } from "@/app/routes";
import type { DetectionReturnState } from "../../DetectionLayout";
import type { CandidateList } from "./candidateApi";
import { recommendLinkText } from "./candidateLabels";
import { useCandidateSummary } from "./useCandidates";

/** 규칙 추천 탭에 넘기는 후보 결과 조회 상태. */
export type CandidateView = {
  data: CandidateList | undefined;
  /** 첫 조회 중(이전 결과 없음). 조회 실패는 화면 머리가 코드로 보인다. */
  pending: boolean;
  onInterface: (id: string) => void;
  /** 하위 화면(후보 상세·프로토콜 상세)에 넘길 돌아올 주소 = 지금 주소(탭·인터페이스 포함). */
  detailState: DetectionReturnState;
};

export type RecommendTab = (typeof urlOptions.ruleCandidatesTab)[number];
/** 실행 흐름 규칙 탭의 두 구역(주소 해시로 바로 가는 대상). */
export const recommendSections = { paths: "recommend-paths", relations: "recommend-relations" } as const;

/** 규칙 추천 화면의 한 탭(+ 구역) 주소. */
export const recommendPath = (tab: RecommendTab, section?: string) => `${paths.detectionRuleCandidates}?tab=${tab}${section ? `#${section}` : ""}`;

/**
 * 탭·진입 링크 건수(건수 요약 API, rule-candidates-ui.md 8절 — 사용자 11/15 결정 1): 최근 성공 후보 결과 중 현재 룰 세트에 아직 없는 것만.
 * 전체 인터페이스 합계라 인터페이스 선택과 무관하다. 큰 결과(그래프·후보 전체)는 읽지 않는다.
 * - 이상 징후: 처리 시간 후보 중 후보 상태이고 같은 대상 필드·범위의 규칙이 없는 것
 * - 관계 규칙: 승인 가능하고 같은 조건의 관계 규칙이 없는 것
 * - 프로토콜: 관측 경로 초안 인터페이스 중 근거 부족이 아니고 아직 등록되지 않은 것
 * 조회 전·실패는 null(—), 결과 없음은 0.
 */
export const sumCount = (...values: (number | null)[]) => (values.some((value) => value === null) ? null : values.reduce<number>((total, value) => total + (value ?? 0), 0));

/** 건수 요약 조회. `revision` = 지금 화면이 읽은 룰 세트 버전(바뀌면 다시 읽는다, 모르면 읽지 않고 —). */
export const useRecommendationCounts = (revision: number | undefined) => {
  const counts = useCandidateSummary(revision).data?.counts;
  return { anomaly: counts?.anomaly ?? null, relation: counts?.relation ?? null, protocol: counts?.protocol ?? null };
};

/** 카드의 "추천 n건 보기": 규칙 추천의 해당 탭(구역)으로. returnTo는 뒤로가기가 돌아올 곳(들어온 카드·목록). */
export const RecommendLink = ({ count, tab, section, returnTo }: { count: number | null; tab: RecommendTab; section?: string; returnTo: string }) => (
  <Button asChild size="sm" variant="outline">
    <Link to={recommendPath(tab, section)} state={{ returnTo } satisfies DetectionReturnState} data-recommend-link={tab}>
      {recommendLinkText(count)}
      <IconChevronRight aria-hidden="true" />
    </Link>
  </Button>
);
