import { IconChevronRight } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { paths, urlOptions } from "@/app/routes";
import type { DetectionReturnState } from "../../DetectionLayout";
import type { ProtocolDrafts } from "../ruleSetApi";
import { useProtocolDrafts } from "../protocols/useProtocolDrafts";
import type { CandidateList } from "./candidateApi";
import { recommendLinkText } from "./candidateLabels";
import { useCandidateList } from "./useCandidates";

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
 * 탭·진입 링크 건수(후보 결과의 후보 기준 충족 건수 — 현재 룰 세트에 이미 있는 것도 센다. 전체 인터페이스 합계라 인터페이스 선택과 무관).
 * - 이상 징후: 처리 시간 후보 중 후보 상태(`totals.thresholdApprovable`)
 * - 단계 조건: 관계 후보 중 승인 가능(`totals.approvable`)
 * - 경로: 관측 경로 초안의 인터페이스 중 근거 부족이 아닌 것(`status: "draft"`)
 * 조회 전·실패는 null(—), 결과 없음은 0.
 */
export const anomalyCount = (list: CandidateList | undefined) => (list ? (list.result?.totals.thresholdApprovable ?? 0) : null);
export const relationCount = (list: CandidateList | undefined) => (list ? (list.result?.totals.approvable ?? 0) : null);
export const protocolCount = (drafts: ProtocolDrafts | undefined) =>
  drafts ? (drafts.state === "available" ? drafts.interfaces.filter((item) => item.status === "draft").length : 0) : null;
export const sumCount = (...values: (number | null)[]) => (values.some((value) => value === null) ? null : values.reduce<number>((total, value) => total + (value ?? 0), 0));

/** 룰 설정 첫 화면 카드용 건수(후보 결과·관측 경로 초안을 읽는다 — 규칙 추천 화면과 같은 조회). */
export const useRecommendationCounts = () => {
  const list = useCandidateList(null);
  const drafts = useProtocolDrafts(null);
  return { anomaly: anomalyCount(list.data), relation: relationCount(list.data), protocol: protocolCount(drafts.data) };
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
